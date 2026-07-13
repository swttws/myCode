import pytest

from mycode.config import LLMConfig
from mycode.llm import BaseLLM
from mycode.protocols import (
    AnthropicLLM,
    OpenAIChatLLM,
    OpenAIResponsesLLM,
    ProtocolError,
    create_llm,
)


def make_config(protocol):
    return LLMConfig(
        protocol=protocol,
        model="model-test",
        base_url="https://example.com",
        api_key="sk-test",
    )


@pytest.mark.parametrize(
    ("protocol", "expected_type"),
    [
        ("anthropic", AnthropicLLM),
        ("openai_responses", OpenAIResponsesLLM),
        ("openai_chat", OpenAIChatLLM),
    ],
)
def test_create_llm_returns_configured_protocol_client(protocol, expected_type):
    llm = create_llm(make_config(protocol))

    assert isinstance(llm, expected_type)
    assert isinstance(llm, BaseLLM)


def test_create_llm_rejects_unknown_protocol():
    with pytest.raises(ProtocolError, match="unknown"):
        create_llm(make_config("unknown"))
