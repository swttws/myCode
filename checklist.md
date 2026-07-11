# myCode Pure Chat TUI Checklist

## Configuration

- [ ] Running with `--config path/to/config.yaml` uses that file instead of other config locations.
- [ ] When `--config` is omitted and `mycode.yaml` exists in the current directory, that file is used.
- [ ] When no explicit or current-directory config exists, `~/.mycode/config.yaml` is used.
- [ ] A YAML config containing `protocol`, `model`, `base_url`, and `api_key` passes validation.
- [ ] A YAML config missing any one of `protocol`, `model`, `base_url`, or `api_key` fails validation before the TUI starts.
- [ ] `api_key: ${MYCODE_TEST_API_KEY}` resolves to the value of `MYCODE_TEST_API_KEY`.
- [ ] `api_key: sk-test-literal` is accepted as a literal value.
- [ ] If `api_key` references an unset environment variable, startup fails before any HTTP request is attempted.
- [ ] Error output never prints the resolved API key value.

## Provider Selection

- [ ] `protocol: anthropic` creates the Anthropic provider.
- [ ] `protocol: openai_responses` creates the OpenAI Responses provider.
- [ ] `protocol: openai_chat` creates the OpenAI Chat Completions provider.
- [ ] An unknown `protocol` value fails before the TUI starts.
- [ ] The TUI consumes only shared provider stream events and does not inspect raw Anthropic or OpenAI SSE payloads.

## Streaming

- [ ] Mock SSE input with two text deltas prints two visible chunks before the stream completes.
- [ ] Anthropic text deltas are mapped to assistant text stream events.
- [ ] Anthropic extended-thinking deltas are mapped to thinking stream events.
- [ ] OpenAI Responses output text deltas are mapped to assistant text stream events.
- [ ] OpenAI Chat Completions content deltas are mapped to assistant text stream events.
- [ ] A stream parsing failure returns control to the input prompt without crashing the process.

## Claude Extended Thinking

- [ ] With no thinking display option enabled, Anthropic thinking deltas are not printed in the terminal.
- [ ] With thinking display enabled, Anthropic thinking deltas are printed with a visually distinct weak style.
- [ ] Thinking deltas are not appended to the assistant message text used as normal conversation history.

## TUI Behavior

- [ ] Starting `mycode` opens an interactive terminal prompt.
- [ ] Typing a user message sends that message to the configured provider.
- [ ] Assistant output appears progressively while the provider stream is still active.
- [ ] After a successful reply, the next request includes the previous user message and assistant reply in memory.
- [ ] Entering `/clear` removes current in-process conversation history.
- [ ] Entering `/exit` terminates the TUI with exit code `0`.
- [ ] A failed provider request displays a terminal error and returns to the prompt.

## End-To-End Acceptance

- [ ] `pytest` passes without real API keys by using mocked HTTP streams.
- [ ] A mocked end-to-end run starts the public command, sends `hello`, receives streamed `hi`, sends a second message, and verifies the second provider request includes the first turn.
- [ ] A mocked end-to-end run enters `/clear` and verifies the next provider request does not include previous turns.
- [ ] A mocked end-to-end run enters `/exit` and verifies the process exits cleanly.

## Documentation

- [ ] `README.md` shows one Anthropic config example.
- [ ] `README.md` shows one OpenAI Responses config example.
- [ ] `README.md` shows one OpenAI Chat Completions config example.
- [ ] Example configs use `${ANTHROPIC_API_KEY}` or `${OPENAI_API_KEY}` instead of real secrets.
- [ ] README states that tool use, file operations, code editing, and persistent sessions are outside this milestone.
