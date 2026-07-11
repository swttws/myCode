# myCode Pure Chat TUI Spec

## Background

myCode is a Python terminal AI coding assistant project. The first milestone is not a full coding agent yet. It should provide a comfortable terminal conversation experience, stream model output as it is generated, and establish a clean provider boundary for future agent features.

This milestone focuses on pure conversation. It does not read project files, run shell commands for the model, edit code, or expose tool use.

## Target Users

- Developers who want to start myCode from a terminal and chat with an LLM inside the current working context.
- Project maintainers who need a provider abstraction that can later support tool use, code editing, and additional model backends.
- Users who may switch between Anthropic Claude, OpenAI native APIs, and OpenAI-compatible services through configuration.

## Goals

- Start myCode from the terminal and enter an interactive TUI conversation.
- Let the user type questions and receive streamed model output immediately.
- Preserve multi-turn conversation history during the current process.
- Support Anthropic Claude and OpenAI protocol families through configuration.
- Support Anthropic extended thinking without making it part of normal chat output by default.
- Keep provider implementations behind one unified streaming interface.
- Use YAML configuration for provider selection, model selection, endpoint selection, and authentication.
- Allow authentication secrets to come from either the YAML file or environment variables.

## Capability List

- Launches a terminal chat session from a command.
- Loads configuration from an explicitly provided file, the current directory, or a user-level config location.
- Selects a provider implementation from the configured protocol.
- Sends the full in-memory conversation history on each user turn.
- Streams assistant text into the terminal as chunks arrive.
- Converts provider-specific stream data into shared internal stream events.
- Supports Anthropic Claude streaming responses.
- Supports Claude extended thinking as an optional Anthropic-specific capability.
- Supports OpenAI Responses API streaming.
- Supports OpenAI Chat Completions streaming for compatibility-oriented backends.
- Provides basic in-session commands for leaving the TUI and clearing memory.
- Reports configuration, authentication, network, and stream parsing failures without exposing secrets.

## Non-Functional Requirements

- The first response token should appear as soon as the provider stream yields content.
- The TUI should remain usable after a failed request.
- Provider-specific logic should not leak into the TUI layer.
- Adding a future provider should not require changing the chat loop design.
- The project should be testable without real API credentials by using mocked streams.
- Configuration parsing should be deterministic and easy to diagnose.
- The terminal UI should favor readability over complex layout.

## Design Skeleton

The system is organized into five layers.

The CLI entry layer parses startup options and begins the session.

The configuration layer locates and validates the YAML file, resolves authentication values, and exposes provider settings to the rest of the application.

The TUI layer handles prompt input, terminal rendering, in-memory history, and built-in session commands.

The provider abstraction layer defines the shared streaming contract consumed by the TUI.

The provider implementation layer translates Anthropic and OpenAI protocol streams into the shared internal event model.

## Out Of Scope

- Tool use and function calling.
- Reading, searching, or editing local files.
- Running shell commands on behalf of the model.
- Persistent session storage.
- Project indexing or retrieval.
- Multi-agent workflows.
- Rich code diff rendering.
- Authentication setup wizards.
- Model pricing, quota tracking, or cost estimation.
- A full-screen terminal application with panes, tabs, or scrollback management.

## Completion Definition

This milestone is complete when a user can configure one supported provider, start myCode, ask multiple questions in one terminal session, see streamed output, clear the in-memory conversation, and exit cleanly. It is also complete only if provider selection, stream parsing, configuration loading, environment-variable authentication, hidden-by-default Claude thinking, and at least one mocked end-to-end conversation are covered by automated checks.
