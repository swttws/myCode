# myCode Pure Chat TUI Tasks

## 1. Initialize Python package structure

- Affected files: `pyproject.toml`, `src/mycode/__init__.py`, `src/mycode/__main__.py`
- Depends on: none
- Work: create a installable Python package with a console entry path and test dependencies.
- Reference: Python packaging user guide, project layout conventions.

## 2. Add CLI entry and startup option parsing

- Affected files: `src/mycode/cli.py`, `src/mycode/__main__.py`, `tests/test_cli.py`
- Depends on: task 1
- Work: support launching the chat app and accepting an optional config file path.
- Reference: `spec.md` sections "Goals" and "Design Skeleton".

## 3. Implement YAML configuration loading

- Affected files: `src/mycode/config.py`, `tests/test_config.py`
- Depends on: task 1
- Work: load YAML from explicit path, current working directory, or user-level location; validate required provider settings.
- Reference: `checklist.md` configuration lookup and required-field checks.

## 4. Implement environment-variable authentication expansion

- Affected files: `src/mycode/config.py`, `tests/test_config.py`
- Depends on: task 3
- Work: allow authentication values in YAML to reference environment variables while still allowing literal values.
- Reference: `checklist.md` authentication checks.

## 5. Define shared provider event model and provider factory

- Affected files: `src/mycode/providers/base.py`, `src/mycode/providers/__init__.py`, `tests/test_provider_factory.py`
- Depends on: task 3
- Work: define stream event types for assistant text, thinking text, completion, and errors; select provider by protocol.
- Reference: `spec.md` sections "Capability List" and "Design Skeleton".

## 6. Implement SSE parsing helper

- Affected files: `src/mycode/sse.py`, `tests/test_sse.py`
- Depends on: task 1
- Work: parse server-sent event data incrementally from HTTP response lines and expose provider-neutral event records.
- Reference: Anthropic streaming docs at `https://platform.claude.com/docs/en/build-with-claude/streaming`; OpenAI streaming docs at `https://developers.openai.com/api/docs/guides/streaming-responses`.

## 7. Implement Anthropic provider

- Affected files: `src/mycode/providers/anthropic.py`, `tests/test_anthropic_provider.py`
- Depends on: tasks 4, 5, 6
- Work: send conversation history to the Anthropic Messages API, consume SSE, map text and extended-thinking deltas into shared events.
- Reference: Anthropic Messages streaming and extended thinking docs.

## 8. Implement OpenAI Responses provider

- Affected files: `src/mycode/providers/openai_responses.py`, `tests/test_openai_responses_provider.py`
- Depends on: tasks 4, 5, 6
- Work: call the OpenAI Responses API in streaming mode and map response output deltas into shared events.
- Reference: OpenAI Responses API streaming docs.

## 9. Implement OpenAI Chat Completions provider

- Affected files: `src/mycode/providers/openai_chat.py`, `tests/test_openai_chat_provider.py`
- Depends on: tasks 4, 5, 6
- Work: call the OpenAI Chat Completions API in streaming mode and map chat deltas into shared events.
- Reference: OpenAI Chat Completions API reference and OpenAI-compatible gateway behavior.

## 10. Build TUI chat loop

- Affected files: `src/mycode/tui.py`, `tests/test_tui.py`
- Depends on: tasks 2, 5
- Work: provide prompt input, streamed rich terminal output, current-process message history, and basic session commands.
- Reference: `spec.md` sections "Goals", "Capability List", and "Out Of Scope".

## 11. Connect stream output to conversation memory

- Affected files: `src/mycode/tui.py`, `tests/test_tui.py`
- Depends on: tasks 7, 8, 9, 10
- Work: collect streamed assistant text into a complete assistant message and append it to in-memory history after each successful turn.
- Reference: `checklist.md` multi-turn and stream-output checks.

## 12. Add user-facing examples and minimal documentation

- Affected files: `README.md`, `examples/mycode.anthropic.yaml`, `examples/mycode.openai-responses.yaml`, `examples/mycode.openai-chat.yaml`
- Depends on: tasks 3, 7, 8, 9, 10
- Work: document supported protocols, configuration examples, environment-variable authentication, and first-run commands.
- Reference: `checklist.md` example-config checks.

## 13. 接入主流程

- Affected files: `src/mycode/cli.py`, `src/mycode/tui.py`, `src/mycode/providers/__init__.py`, `tests/test_cli.py`
- Depends on: tasks 2 through 12
- Work: wire configuration loading, provider creation, TUI startup, and graceful shutdown into the actual command users run.
- Reference: `spec.md` "Completion Definition".

## 14. 端到端验证

- Affected files: `tests/test_e2e_chat.py`, `README.md`
- Depends on: task 13
- Work: run a mocked streaming conversation through the public command path and verify streamed output, history, clear, and exit behavior.
- Reference: `checklist.md` end-to-end acceptance checks.
