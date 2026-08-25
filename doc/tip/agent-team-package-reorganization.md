# Agent Team Package Reorganization Notes

## 2026-08-21 - Domain package compatibility boundary

- Scope: Agent Team package structure only.
- Problem: domain records, state enums, message contracts, and application/runtime
  concerns are all exposed from one flat package, which obscures dependency
  boundaries.
- Change: introduce a `team.domain` compatibility boundary that re-exports the
  existing domain contracts without changing their identity, persistence format,
  or legacy import paths.
- Verification: a focused compatibility test must prove that both import paths
  resolve to the same objects before broader package migration starts.

## 2026-08-21 - Infrastructure package compatibility boundary

- Problem: storage, event persistence, request persistence, locking, and
  conversation persistence are currently exposed as unrelated flat modules.
- Change: introduce a `team.infrastructure` compatibility boundary without
  changing file formats, storage paths, or the legacy import paths.
- Verification: the new and legacy imports must resolve to identical classes.

## 2026-08-21 - Application package compatibility boundary

- Problem: team lifecycle, task orchestration, and integration orchestration
  are exposed from separate flat modules without a shared application-layer
  boundary.
- Change: introduce `team.application` as the compatibility entry point for
  those orchestrators; do not alter their public constructor or method
  contracts.
- Verification: the new and legacy imports must resolve to identical classes.

## 2026-08-21 - Execution package compatibility boundary

- Problem: backend startup, event consumption, notification, Lead supervision,
  and member worker execution are currently mixed across flat runtime modules.
- Change: introduce `team.execution` as the runtime-facing boundary.  The
  existing `team.runtime` module remains unchanged to avoid import ambiguity
  and preserve all current callers.
- Verification: runtime-facing classes must retain identity through the new
  boundary and existing runtime tests must pass.

## 2026-08-21 - Tooling package compatibility boundary

- Problem: Lead tools, member tools, generic TeamTool dispatch, tool names, and
  argument/result helpers are exposed as unrelated root modules.
- Change: introduce `team.tooling` as the tool-facing boundary while keeping
  all existing root-module imports and tool behaviour intact.
- Verification: registration functions, the generic tool, names, and helper
  functions must retain identity through the new boundary.

## 2026-08-21 - Extract message routing from TeamService

- Problem: TeamService owns both lifecycle orchestration and message recipient
  validation/routing rules.
- Change: move the pure routing decisions into `team.application.messaging`;
  keep TeamService's private methods as thin compatibility delegates.
- Compatibility: recipient ordering, unknown-role errors, backend support
  checks, and broadcast expansion remain unchanged.
- Verification: focused routing tests plus the existing TeamService suite.
- Note: the new broadcast test intentionally follows the existing contract of
  excluding the sender from the expanded recipient set.

## 2026-08-21 - Static guard line mapping

- Problem: adding the application routing import shifted the existing
  `_event_value` dynamic-access whitelist entry by one line.
- Change: update only the static test's line mapping; no dynamic access was
  added and no whitelist scope was broadened.
- Verification: the complete team static guard suite must pass.

## Verification

- `pytest (Get-ChildItem tests -Filter 'test_team_*.py').FullName -q`
  -> `219 passed in 44.44s`
- `python -m compileall -q src\\mycode\\team`
  -> exit code `0`

## 2026-08-21 - Guard event acknowledgement failures

- Problem: `RoleEventConsumer._process_event` used `try/except/else`, leaving
  `ack_event` outside the exception path. A persistence or sequence error while
  acknowledging a successfully handled event could escape the consumer task
  and stop the event loop without applying retry handling.
- Change: replace the `else` branch with an explicit acknowledgement step and
  route acknowledgement failures through the existing event failure workflow
  using `reason_code="ack_error"`. Handler and acknowledgement failures now
  share the same retry and terminal-failure behavior while remaining
  distinguishable in persisted failure records and logs.
- Compatibility: handler ordering, three-attempt retry limits, notifier wakeups,
  terminal callbacks, and event persistence formats remain unchanged.
- Verification: the consumer regression suite covers acknowledgement failure
  retries and terminal failure; an AST scan confirms no other Agent Team
  `try/except/else` block leaves event work outside its intended exception path.

## 2026-08-24 - Simplify Agent Team logs and add Chinese execution results

- Problem: configured Team logs mixed English event fields with repeated
  lifecycle entries for locks, wakeups, idle transitions, and message wrappers.
  Lead background model logs also lacked a reliable role marker, and member
  task completion did not record the returned text summary.
- Change: keep the existing machine event codes on log records while rendering
  configured output with Chinese field labels and event summaries. Add focused
  `team.lead.started/result/failed` and `team.task.started/result/failed` records
  with role, team, member, task, batch, event, message, status, duration, and a
  bounded result summary. Lead and member model calls now carry their role and
  event context through `LogIdentity`.
- Noise reduction: configured file and console handlers suppress low-value
  lock, wake, idle, startup-wrapper, status, and duplicate task message logs;
  warnings and errors remain visible, with task failures taking precedence over
  duplicate runtime message failures.
- Compatibility: logger names and raw `LogRecord` event messages remain
  unchanged for existing integrations and `caplog` assertions; only rendered
  handler output is simplified. Result summaries are whitespace-normalized and
  capped to avoid dumping full model responses or secrets.
- Verification: focused logging/runtime/supervisor tests, Team runtime/service/
  E2E regression tests, Ruff, and compilation.

## 2026-08-21 - Move runtime primitives behind execution package

- Problem: the notifier and event consumer implementations still lived at the
  Team package root after the execution boundary was introduced.
- Change: make `team.execution.notifier` and `team.execution.consumer` the
  canonical implementations; retain root modules as compatibility shims.
- Verification: old and new imports resolve to the same classes and all event
  consumer/notifier tests pass.

## 2026-08-21 - Resolve execution package import cycle

- Problem: importing a compatibility consumer/notifier shim initialized the
  eager execution package, which loaded legacy runtime modules that imported
  the shim back before it finished.
- Change: initialize canonical consumer/notifier modules first and update Team
  runtime modules to import those canonical implementations directly.
- Verification: package collection succeeds and the consumer/notifier suites
  pass without circular-import errors.

## 2026-08-21 - Root module implementation guard

- Problem: the new layer packages still re-exported most implementations while
  large implementation files remained at `team/` root.
- Change: migrate each root implementation into its owning layer package and
  leave only compatibility shims at the root.
- Verification: a structural test checks every migrated root module is a small
  forwarding shim, then the complete Team test suite validates behavior.

## 2026-08-21 - Fix domain shim initialization cycle

- Problem: after moving `models.py`, `domain/__init__.py` still imported the
  legacy root shim, creating a partial-module cycle during package import.
- Change: domain exports now resolve from canonical `domain.models` directly.
- Verification: package layout imports must collect without partial-module
  errors.

## 2026-08-21 - Make layer package initializers lightweight

- Problem: eager exports from every layer initializer caused unrelated imports
  to load the full runtime and introduced cycles between compatibility shims.
- Change: layer `__init__.py` files become lightweight namespaces; canonical
  consumers import concrete modules directly.  Legacy root modules remain the
  compatibility surface.
- Verification: package layout tests and all Team behavior tests must collect
  without eager-import cycles.

## 2026-08-21 - Preserve compatibility module identity

- Problem: simple star-import shims lost private helpers, monkeypatch targets,
  and legacy logger names after implementation files moved.
- Change: root shims now alias the canonical module object; canonical loggers
  keep their historical names so existing integrations and diagnostics remain
  stable.
- Verification: worker/service dependency injection, locking private helper
  access, and logger assertions must pass unchanged.

## 2026-08-21 - Canonical layer import cleanup

- Problem: several files under the new layer packages still imported the old
  root modules, so the canonical implementation depended on compatibility
  shims and the package boundaries were not actually enforced.
- Change: update application, domain, execution, infrastructure, and tooling
  modules to import directly from their owning canonical packages.
- Compatibility: root modules remain forwarding aliases for existing callers;
  no public import path or runtime behavior is removed in this step.
- Verification: the package-layout/static guards and the full Team test suite
  must pass.

## 2026-08-21 - Make the package facade canonical

- Problem: `mycode.team` itself still initialized through root compatibility
  modules even though the implementations had moved into layer packages.
- Change: package-level exports now import directly from `domain.models` and
  `infrastructure.requests`; the root module aliases remain available only for
  legacy submodule imports.
- Compatibility: exported classes and enums retain the same objects and names.
- Verification: package-layout imports and the complete Team test suite.

## 2026-08-21 - Remove root forwarding modules and legacy mailbox metadata

- Problem: root-level Team files were only forwarding shells, while the event
  driven runtime still carried the obsolete `MailboxStore` contract through
  `mailbox_path` fields, mailbox path construction, and unused mailbox config
  limits. This caused canonical imports to appear broken and made member wake
  metadata depend on a removed subsystem.
- Change: switch production and Team tests to canonical layer imports, delete
  the root forwarding modules, remove mailbox metadata from `MemberRecord` and
  `MemberLaunchSpec`, remove `TeamStore.mailbox_path`, and remove unused
  mailbox-size configuration fields. Legacy JSON fields are ignored by the
  explicit decoders, so existing state remains loadable.
- Compatibility: `mycode.team` remains a real facade for public contracts;
  canonical module paths are now the supported import surface.
- Verification: structural import checks, no-mailbox contract test,
  compilation, and the full Team test suite.

## 2026-08-21 - Remove empty layer initializers

- Problem: four layer directories contained `__init__.py` files with only a
  docstring and an empty `__all__`, adding no package behavior.
- Change: remove the empty initializers from `application`, `execution`,
  `infrastructure`, and `tooling`; Python 3 namespace-package imports keep the
  existing module paths working.
- Compatibility: keep `team/__init__.py` and `domain/__init__.py` because they
  provide real public exports.
- Verification: package-layout checks, compilation, and the complete Team test
  suite.

## 2026-08-21 - Remove confirmed dead Team helpers

- Problem: the layer migration left one unused path conversion helper and an
  unreferenced process-backend launch helper.
- Change: remove `tooling.tool_helpers.as_path_list` and
  `execution.backends._ProcessBackend._launch`, along with the now-unused
  `Path` import.
- Compatibility: both symbols had no callers in source or tests and were not
  part of the registered Team tool or backend interfaces.
- Verification: rerun the Team package/static checks and the complete Team
  test suite.

## 2026-08-21 - Fix package-level import consumers after shell removal

- Problem: a few consumers imported removed modules as package attributes
  (`from mycode.team import worker/locking`), and bulk import migration had
  changed log-capture filters away from the historical logger names.
- Change: import worker/locking from `execution`/`infrastructure` directly and
  keep logger names unchanged while moving code imports to canonical modules.
  Static whitelist paths now follow the new layer locations.
- Verification: canonical import smoke test, Team static checks, and Team
  regression suite (`215 passed`).

## 2026-08-21 - Remove stale registered worktrees

- Problem: the repository retained clean, old Team and explore worktrees under
  `.worktrees`, making obsolete worktree content appear as active source code.
- Evidence: all eight registered worktrees had clean status; Team worktrees
  pointed at `master`, while the explore worktrees were ancestors of `master`
  with no unique commits.
- Change: remove the registered worktrees using `git worktree remove`, then
  delete their fully merged local branches and prune stale Git metadata.
- Compatibility: retain the empty `.worktrees` root because the Worktree
  feature creates future isolated task workspaces there.
