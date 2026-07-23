from __future__ import annotations

from pathlib import Path

from mycode.compact.archive import ArchiveSession, ReadCompactArtifactTool
from mycode.compact.estimator import TokenEstimator
from mycode.compact.light import ToolResultCompactor
from mycode.compact.models import (
    CompactAction,
    CompactConfig,
    CompactError,
    CompactFailureCode,
    CompactPolicy,
    CompactReport,
    CompactStatus,
    PreparedContext,
)
from mycode.compact.summary import ConversationCompactor, select_recent_messages, summary_input_messages
from mycode.llm import UsageObservation
from mycode.memory import ConversationMemory


class ContextManager:
    def __init__(
        self,
        *,
        llm,
        memory: ConversationMemory,
        config: CompactConfig,
        store: ArchiveSession,
        policy: CompactPolicy | None = None,
        estimator: TokenEstimator | None = None,
        model_timeout_seconds: float | None = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._config = config
        self._store = store
        self._policy = policy or CompactPolicy()
        self._estimator = estimator or TokenEstimator()
        self._light = ToolResultCompactor(
            config,
            policy=self._policy,
            estimator=self._estimator,
        )
        self._conversation = ConversationCompactor(
            llm,
            config,
            policy=self._policy,
            estimator=self._estimator,
            model_timeout_seconds=model_timeout_seconds,
        )
        self._failure_count = 0
        self._circuit_open = False

    async def prepare_auto(
        self,
        *,
        build_request,
        run_deadline: float | None,
    ) -> PreparedContext:
        original_history = tuple(self._memory.messages())
        before_tokens = sum(self._estimator.estimate_text(message.content) for message in original_history)
        transaction = self._store.begin()
        light_result = self._light.compact(original_history, transaction)
        if light_result.changed:
            try:
                transaction.commit()
            except OSError as exc:
                transaction.rollback()
                raise CompactError(
                    _report(
                        status=CompactStatus.FAILED,
                        actions=(CompactAction.LIGHT,),
                        before_tokens=before_tokens,
                        after_tokens=before_tokens,
                        archived_count=0,
                        failure_code=CompactFailureCode.ARCHIVE_ERROR,
                        message_zh="轻量归档提交失败。",
                    )
                ) from exc
            self._memory.replace(light_result.history)
            history = light_result.history
            actions = (CompactAction.LIGHT,)
            status = CompactStatus.COMPACTED
            archived_count = len(light_result.artifacts)
        else:
            transaction.rollback()
            history = original_history
            actions = (CompactAction.NONE,)
            status = CompactStatus.SAFE
            archived_count = 0

        request = build_request(tuple(history))
        snapshot = self._estimator.snapshot(request.messages, request.tools)
        estimate = self._estimator.estimate(snapshot)
        if estimate.tokens < self._config.context_window_tokens - self._policy.auto_reserve_tokens:
            return PreparedContext(
                request=request,
                snapshot=snapshot,
                estimate=estimate,
                report=_report(
                    status=status,
                    actions=actions,
                    before_tokens=before_tokens,
                    after_tokens=estimate.tokens,
                    archived_count=archived_count,
                    circuit_open=self._circuit_open,
                ),
            )

        if self._circuit_open:
            return self._prepare_emergency(
                history,
                build_request=build_request,
                before_tokens=before_tokens,
                prior_actions=actions,
                prior_archived_count=archived_count,
                attempts=0,
            )

        return await self._prepare_heavy(
            history,
            build_request=build_request,
            run_deadline=run_deadline,
            before_tokens=before_tokens,
            prior_actions=actions,
            prior_archived_count=archived_count,
            fallback_to_emergency=True,
        )

    @property
    def artifact_tool(self) -> ReadCompactArtifactTool:
        return ReadCompactArtifactTool(self._store)

    async def compact_manual(
        self,
        *,
        build_request,
        run_deadline: float | None,
    ) -> CompactReport:
        history = tuple(self._memory.messages())
        recent_messages = select_recent_messages(
            history,
            keep_recent_tokens=self._policy.keep_recent_tokens,
            min_recent_messages=self._policy.min_recent_messages,
            estimator=self._estimator,
        )
        if not summary_input_messages(history, recent_messages):
            return _report(
                status=CompactStatus.NO_OP,
                actions=(CompactAction.NONE,),
                before_tokens=sum(self._estimator.estimate_text(message.content) for message in history),
                after_tokens=sum(self._estimator.estimate_text(message.content) for message in history),
                archived_count=0,
                circuit_open=self._circuit_open,
                message_zh="没有可压缩的旧历史。",
            )

        before_tokens = sum(self._estimator.estimate_text(message.content) for message in history)
        try:
            prepared = await self._prepare_heavy(
                history,
                build_request=build_request,
                run_deadline=run_deadline,
                before_tokens=before_tokens,
                prior_actions=(CompactAction.NONE,),
                prior_archived_count=0,
                fallback_to_emergency=False,
            )
        except CompactError as exc:
            return _report(
                status=CompactStatus.FAILED,
                actions=(CompactAction.HEAVY,),
                before_tokens=before_tokens,
                after_tokens=exc.report.after_tokens,
                archived_count=exc.report.archived_count,
                attempts=exc.report.attempts,
                circuit_open=self._circuit_open,
                failure_code=exc.report.failure_code,
                message_zh=exc.report.message_zh,
            )

        self._failure_count = 0
        self._circuit_open = False
        return _report(
            status=prepared.report.status,
            actions=prepared.report.actions,
            before_tokens=prepared.report.before_tokens,
            after_tokens=prepared.report.after_tokens,
            archived_count=prepared.report.archived_count,
            attempts=prepared.report.attempts,
            circuit_open=False,
            failure_code=prepared.report.failure_code,
            message_zh=prepared.report.message_zh,
        )

    def record_usage(self, snapshot, usage: UsageObservation) -> None:
        self._estimator.record_usage(snapshot, usage)

    def clear(self) -> None:
        self._memory.clear()
        self._estimator.reset()
        self._failure_count = 0
        self._circuit_open = False
        self._store.reset_session()

    def close(self) -> None:
        self._store.close()

    async def _prepare_heavy(
        self,
        history,
        *,
        build_request,
        run_deadline: float | None,
        before_tokens: int,
        prior_actions: tuple[CompactAction, ...],
        prior_archived_count: int,
        fallback_to_emergency: bool,
    ) -> PreparedContext:
        for attempt in range(1, self._policy.max_attempts + 1):
            transaction = self._store.begin()
            try:
                heavy = await self._conversation.compact(
                    history,
                    mode="auto",
                    build_request=build_request,
                    transaction=transaction,
                    run_deadline=run_deadline,
                )
                request = build_request(heavy.history)
                snapshot = self._estimator.snapshot(request.messages, request.tools)
                estimate = self._estimator.estimate(snapshot)
                if estimate.tokens >= self._config.context_window_tokens - self._policy.auto_reserve_tokens:
                    raise CompactError(
                        _report(
                            status=CompactStatus.FAILED,
                            actions=_merge_actions(prior_actions, heavy.actions),
                            before_tokens=before_tokens,
                            after_tokens=estimate.tokens,
                            archived_count=prior_archived_count + len(heavy.artifacts),
                            attempts=attempt,
                            failure_code=CompactFailureCode.BUDGET_NOT_RECOVERED,
                            message_zh="压缩后请求仍超过自动安全线。",
                        )
                    )
                transaction.commit()
                self._memory.replace(heavy.history)
                self._failure_count = 0
                self._circuit_open = False
                return PreparedContext(
                    request=request,
                    snapshot=snapshot,
                    estimate=estimate,
                    report=_report(
                        status=CompactStatus.COMPACTED,
                        actions=_merge_actions(prior_actions, heavy.actions),
                        before_tokens=before_tokens,
                        after_tokens=estimate.tokens,
                        archived_count=prior_archived_count + len(heavy.artifacts),
                        attempts=attempt,
                        circuit_open=False,
                    ),
                )
            except CompactError as exc:
                transaction.rollback()
                self._failure_count += 1
                continue
            except OSError as exc:
                transaction.rollback()
                self._failure_count += 1
                continue

        if fallback_to_emergency:
            self._circuit_open = True
            # 第三次完整失败后立即切换到本地应急压缩，避免继续消耗摘要调用。
            return self._prepare_emergency(
                history,
                build_request=build_request,
                before_tokens=before_tokens,
                prior_actions=_merge_actions(prior_actions, (CompactAction.HEAVY,)),
                prior_archived_count=prior_archived_count,
                attempts=self._policy.max_attempts,
            )

        raise CompactError(
            _report(
                status=CompactStatus.FAILED,
                actions=_merge_actions(prior_actions, (CompactAction.HEAVY,)),
                before_tokens=before_tokens,
                after_tokens=before_tokens,
                archived_count=prior_archived_count,
                attempts=self._policy.max_attempts,
                circuit_open=self._circuit_open,
                failure_code=CompactFailureCode.INVALID_FORMAT,
                message_zh="手动压缩失败。",
            )
        )

    def _prepare_emergency(
        self,
        history,
        *,
        build_request,
        before_tokens: int,
        prior_actions: tuple[CompactAction, ...],
        prior_archived_count: int,
        attempts: int,
    ) -> PreparedContext:
        transaction = self._store.begin()
        try:
            emergency = self._conversation.emergency(
                history,
                build_request=build_request,
                transaction=transaction,
            )
            request = build_request(emergency.history)
            snapshot = self._estimator.snapshot(request.messages, request.tools)
            estimate = self._estimator.estimate(snapshot)
            if estimate.tokens >= self._config.context_window_tokens - self._policy.auto_reserve_tokens:
                raise CompactError(
                    _report(
                        status=CompactStatus.FAILED,
                        actions=_merge_actions(prior_actions, emergency.actions),
                        before_tokens=before_tokens,
                        after_tokens=estimate.tokens,
                        archived_count=prior_archived_count + len(emergency.artifacts),
                        attempts=attempts,
                        circuit_open=True,
                        failure_code=CompactFailureCode.BUDGET_NOT_RECOVERED,
                        message_zh="应急压缩后仍超过自动安全线。",
                    )
                )
            transaction.commit()
            self._memory.replace(emergency.history)
            return PreparedContext(
                request=request,
                snapshot=snapshot,
                estimate=estimate,
                report=_report(
                    status=CompactStatus.COMPACTED,
                    actions=_merge_actions(prior_actions, emergency.actions),
                    before_tokens=before_tokens,
                    after_tokens=estimate.tokens,
                    archived_count=prior_archived_count + len(emergency.artifacts),
                    attempts=attempts,
                    circuit_open=True,
                ),
            )
        except CompactError:
            transaction.rollback()
            raise
        except OSError as exc:
            transaction.rollback()
            raise CompactError(
                _report(
                    status=CompactStatus.FAILED,
                    actions=_merge_actions(prior_actions, (CompactAction.EMERGENCY,)),
                    before_tokens=before_tokens,
                    after_tokens=before_tokens,
                    archived_count=prior_archived_count,
                    attempts=attempts,
                    circuit_open=True,
                    failure_code=CompactFailureCode.ARCHIVE_ERROR,
                    message_zh="应急归档提交失败。",
                )
            ) from exc


def _report(
    *,
    status: CompactStatus,
    actions: tuple[CompactAction, ...],
    before_tokens: int,
    after_tokens: int,
    archived_count: int,
    failure_code: CompactFailureCode | None = None,
    message_zh: str = "",
    attempts: int = 0,
    circuit_open: bool = False,
) -> CompactReport:
    return CompactReport(
        status=status,
        actions=actions,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        archived_count=archived_count,
        attempts=attempts,
        circuit_open=circuit_open,
        failure_code=failure_code,
        message_zh=message_zh,
    )


def _merge_actions(*action_groups: tuple[CompactAction, ...]) -> tuple[CompactAction, ...]:
    merged: list[CompactAction] = []
    for action_group in action_groups:
        for action in action_group:
            if action is CompactAction.NONE:
                continue
            if action not in merged:
                merged.append(action)
    return tuple(merged or [CompactAction.NONE])


def create_context_manager(
    *,
    workspace_root: str | Path,
    home: str | Path,
    llm,
    memory: ConversationMemory,
    config: CompactConfig,
    model_timeout_seconds: float | None,
) -> ContextManager:
    return ContextManager(
        llm=llm,
        memory=memory,
        config=config,
        store=ArchiveSession(workspace_root, home=home),
        model_timeout_seconds=model_timeout_seconds,
    )
