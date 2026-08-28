import os
from abc import ABC, abstractmethod
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from core.utils.logger import logger


class LLMAdapter(ABC):
    """Abstract interface for LLM instantiation."""

    @abstractmethod
    def get_model(self) -> BaseChatModel:
        pass


class OpenAIAdapter(LLMAdapter):
    def __init__(
        self,
        api_key: str,
        model: str,
        temperature: float = 0.2,
        base_url: str | None = None,
        max_tokens: int | None = None,
        max_retries: int = 5,
        timeout: float = 180.0,
        model_kwargs: dict = None,
        extra_body: dict = None,
    ):
        from langchain_openai import ChatOpenAI

        kwargs = dict(
            model=model,
            api_key=api_key,
            temperature=temperature,
            max_retries=max_retries,
            timeout=timeout,
        )
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if base_url:
            kwargs["base_url"] = base_url
        if model_kwargs:
            kwargs["model_kwargs"] = model_kwargs
        if extra_body:
            kwargs["extra_body"] = extra_body
        self._model_name = model
        self.model = ChatOpenAI(**kwargs)
        logger.debug("adapter_created", adapter="openai", model=model)

    def get_model(self) -> BaseChatModel:
        return self.model

    def __repr__(self) -> str:
        return f"OpenAIAdapter(model='{self._model_name}')"


class AnthropicAdapter(LLMAdapter):
    def __init__(
        self,
        api_key: str,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 16384,
        max_retries: int = 5,
        timeout: float = 180.0,
    ):
        from langchain_anthropic import ChatAnthropic

        self._model_name = model
        self.model = ChatAnthropic(
            model=model,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries=max_retries,
            timeout=timeout,
        )
        logger.debug("adapter_created", adapter="anthropic", model=model)

    def get_model(self) -> BaseChatModel:
        return self.model

    def __repr__(self) -> str:
        return f"AnthropicAdapter(model='{self._model_name}')"


class LocalLLMAdapter(LLMAdapter):
    def __init__(
        self,
        base_url: str,
        model: str,
        temperature: float = 0.2,
        max_retries: int = 5,
        timeout: float = 180.0,
        model_kwargs: dict = None,
        extra_body: dict = None,
    ):
        from langchain_openai import ChatOpenAI

        api_key = (
            os.environ.get("TEMP_API_KEY")
            or os.environ.get("LOCAL_LLM_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or "not-needed"
        )
        self._model_name = model
        kwargs = dict(
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            max_retries=max_retries,
            timeout=timeout,
        )
        if model_kwargs:
            kwargs["model_kwargs"] = model_kwargs
        if extra_body:
            kwargs["extra_body"] = extra_body
        self.model = ChatOpenAI(**kwargs)
        logger.debug("adapter_created", adapter="local", model=model, base_url=base_url)

    def get_model(self) -> BaseChatModel:
        return self.model

    def __repr__(self) -> str:
        return f"LocalLLMAdapter(model='{self._model_name}')"


class GoogleAdapter(LLMAdapter):
    def __init__(
        self,
        api_key: str,
        model: str,
        temperature: float = 0.2,
        max_output_tokens: int = 16384,
        max_retries: int = 5,
        timeout: float = 180.0,
    ):
        from langchain_google_genai import ChatGoogleGenerativeAI

        self._model_name = model
        self.model = ChatGoogleGenerativeAI(
            model=model,
            google_api_key=api_key,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            max_retries=max_retries,
            timeout=timeout,
        )
        logger.debug("adapter_created", adapter="google", model=model)

    def get_model(self) -> BaseChatModel:
        return self.model

    def __repr__(self) -> str:
        return f"GoogleAdapter(model='{self._model_name}')"


# ── Factory ──────────────────────────────────────────────────────────────────

_ADAPTER_MAP = {
    "openai": OpenAIAdapter,
    "anthropic": AnthropicAdapter,
    "local": LocalLLMAdapter,
    "google": GoogleAdapter,
}

SUPPORTED_PROVIDERS = list(_ADAPTER_MAP.keys())


def create_adapter(provider: str, **kwargs: Any) -> LLMAdapter:
    """Factory to instantiate the correct adapter by provider name.

    Raises ValueError for unknown providers.
    """
    cls = _ADAPTER_MAP.get(provider)
    if cls is None:
        raise ValueError(
            f"Unknown LLM provider: '{provider}'. "
            f"Supported: {SUPPORTED_PROVIDERS}"
        )
    return cls(**kwargs)
