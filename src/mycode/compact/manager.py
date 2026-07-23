from __future__ import annotations

from mycode.compact.archive import ArchiveSession
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
from mycode.compact.summary import ConversationCompactor
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
                ),
            )

        return await self._prepare_heavy(
            history,
            build_request=build_request,
            run_deadline=run_deadline,
            before_tokens=before_tokens,
            prior_actions=actions,
            prior_archived_count=archived_count,
        )

    async def _prepare_heavy(
        self,
        history,
        *,
        build_request,
        run_deadline: float | None,
        before_tokens: int,
        prior_actions: tuple[CompactAction, ...],
        prior_archived_count: int,
    ) -> PreparedContext:
        last_error: CompactError | None = None
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
                    ),
                )
            except CompactError as exc:
                transaction.rollback()
                self._failure_count += 1
                last_error = exc
                continue
            except OSError as exc:
                transaction.rollback()
                self._failure_count += 1
                last_error = CompactError(
                    _report(
                        status=CompactStatus.FAILED,
                        actions=prior_actions,
                        before_tokens=before_tokens,
                        after_tokens=before_tokens,
                        archived_count=prior_archived_count,
                        attempts=attempt,
                        failure_code=CompactFailureCode.ARCHIVE_ERROR,
                        message_zh="重量压缩归档提交失败。",
                    )
                )
                continue

        if last_error is not None:
            raise last_error
        raise CompactError(
            _report(
                status=CompactStatus.FAILED,
                actions=prior_actions,
                before_tokens=before_tokens,
                after_tokens=before_tokens,
                archived_count=prior_archived_count,
                attempts=self._policy.max_attempts,
                failure_code=CompactFailureCode.BUDGET_NOT_RECOVERED,
                message_zh="重量压缩未恢复预算。",
            )
        )


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
) -> CompactReport:
    return CompactReport(
        status=status,
        actions=actions,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        archived_count=archived_count,
        attempts=attempts,
        circuit_open=False,
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
