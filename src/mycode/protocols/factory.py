from __future__ import annotations

from mycode.config import LLMConfig
from mycode.llm import BaseLLM
from mycode.protocols.anthropic import AnthropicLLM
from mycode.protocols.openai_chat import OpenAIChatLLM
from mycode.protocols.openai_responses import OpenAIResponsesLLM


class ProtocolError(ValueError):
    """协议选择或协议配置错误。"""


def create_llm(config: LLMConfig) -> BaseLLM:
    # 工厂是具体协议进入应用主流程的唯一入口。
    if config.protocol == "anthropic":
        return AnthropicLLM(config)
    if config.protocol == "openai_responses":
        return OpenAIResponsesLLM(config)
    if config.protocol == "openai_chat":
        return OpenAIChatLLM(config)
    raise ProtocolError(f"unknown protocol: {config.protocol}")
