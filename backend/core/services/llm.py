import os

from langchain_core.language_models.chat_models import BaseChatModel

from core.adapters.llm_provider import create_adapter
from core.config import settings
from core.utils.logger import logger
from core.utils.retry import with_exponential_backoff

_llm_init_logged = False
_llm_cache: dict[str, BaseChatModel] = {}
_judge_cache: dict[str, BaseChatModel] = {}


def _resolve_api_key(*env_vars: str) -> str:
    """Return the first non-empty env var value, or fallback if base_url is set."""
    for var in env_vars:
        val = (os.environ.get(var) or "").strip()
        if val:
            return val
    if os.environ.get("OPENAI_BASE_URL") or os.environ.get("LOCAL_LLM_URL"):
        return "not-needed"
    raise ValueError(
        f"None of {list(env_vars)} are set. "
        "Please configure an API key."
    )


def get_llm() -> BaseChatModel:
    """Returns the configured LLM instance using the Adapter pattern."""
    global _llm_init_logged, _llm_cache

    provider = settings.LLM_PROVIDER
    model = settings.LLM_MODEL
    cache_key = f"{provider}:{model}"

    if cache_key in _llm_cache:
        return _llm_cache[cache_key]

    if not _llm_init_logged:
        logger.info("llm_initialized", provider=provider, model=model)
        _llm_init_logged = True

    kwargs: dict = {"model": model}

    if provider == "openai":
        kwargs["api_key"] = _resolve_api_key("TEMP_API_KEY", "OPENAI_API_KEY")
        base_url = os.environ.get("OPENAI_BASE_URL") or None
        if base_url:
            kwargs["base_url"] = base_url
            kwargs["model_kwargs"] = {"parallel_tool_calls": False}
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
            kwargs["max_tokens"] = 4096
            kwargs["timeout"] = 300
            kwargs["max_retries"] = 1
        else:
            kwargs["model_kwargs"] = {"parallel_tool_calls": False}
            kwargs["max_tokens"] = 4096
            kwargs["timeout"] = 300
            kwargs["max_retries"] = 1
    elif provider == "anthropic":
        kwargs["api_key"] = _resolve_api_key("TEMP_API_KEY", "ANTHROPIC_API_KEY")
    elif provider == "local":
        kwargs["base_url"] = os.environ.get("LOCAL_LLM_URL", "http://localhost:8000/v1").strip()
        kwargs["model_kwargs"] = {"parallel_tool_calls": False}
        kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        kwargs["max_tokens"] = 4096
    elif provider == "google":
        kwargs["api_key"] = _resolve_api_key("TEMP_API_KEY", "GOOGLE_API_KEY")

    adapter = create_adapter(provider, **kwargs)
    _llm_cache[cache_key] = adapter.get_model()
    return _llm_cache[cache_key]


def get_judge_llm(temperature: float = 0.0) -> BaseChatModel:
    """Returns the configured LLM instance for judging/evaluations."""
    global _judge_cache

    provider = settings.JUDGE_PROVIDER or settings.LLM_PROVIDER
    judge_model = settings.JUDGE_MODEL or settings.LLM_MODEL
    cache_key = f"{provider}:{judge_model}:{temperature}"

    if cache_key in _judge_cache:
        return _judge_cache[cache_key]

    kwargs: dict = {"model": judge_model, "temperature": temperature}

    if provider == "openai":
        kwargs["api_key"] = _resolve_api_key("JUDGE_API_KEY", "TEMP_API_KEY", "OPENAI_API_KEY")
        base_url = settings.JUDGE_BASE_URL or os.environ.get("OPENAI_BASE_URL") or None
        if base_url:
            kwargs["base_url"] = base_url
            kwargs["model_kwargs"] = {"parallel_tool_calls": False}
            kwargs["extra_body"] = {"chat_template_kwargs": {"preserve_thinking": True}}
        else:
            kwargs["model_kwargs"] = {"parallel_tool_calls": False}
    elif provider == "anthropic":
        kwargs["api_key"] = _resolve_api_key("JUDGE_API_KEY", "TEMP_API_KEY", "ANTHROPIC_API_KEY")
    elif provider == "local":
        kwargs["base_url"] = settings.JUDGE_BASE_URL or os.environ.get("LOCAL_LLM_URL", "http://localhost:8000/v1").strip()
        kwargs["model_kwargs"] = {"parallel_tool_calls": False}
        kwargs["extra_body"] = {"chat_template_kwargs": {"preserve_thinking": True}}
    elif provider == "google":
        kwargs["api_key"] = _resolve_api_key("JUDGE_API_KEY", "TEMP_API_KEY", "GOOGLE_API_KEY")

    adapter = create_adapter(provider, **kwargs)
    _judge_cache[cache_key] = adapter.get_model()
    return _judge_cache[cache_key]


# We replace the manual `with_rate_limit_retry` with our robust decorator
with_rate_limit_retry = with_exponential_backoff
