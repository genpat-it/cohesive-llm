from typing import Any

import requests
from openai import RateLimitError as OpenAIRateLimitError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from core.utils.logger import logger


def log_retry_attempt(retry_state: Any) -> None:
    """Log when a retry happens."""
    logger.warning(
        "retrying_operation",
        attempt=retry_state.attempt_number,
        exception=str(retry_state.outcome.exception()),
        func_name=retry_state.fn.__name__
    )

def with_exponential_backoff(max_attempts: Any=5, min_wait: int = 2, max_wait: int = 60) -> Any:
    """
    Decorator for robust exponential backoff retries on external calls.
    Specifically handles rate limits, connection errors, and timeouts.

    Can be used as:
        @with_exponential_backoff(max_attempts=5)
        def func(): ...
    Or directly as:
        with_exponential_backoff(func)(*args, **kwargs)
    """
    if callable(max_attempts):
        func = max_attempts
        r = retry(
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=min_wait, max=max_wait),
            retry=(
                retry_if_exception_type(requests.exceptions.RequestException) |
                retry_if_exception_type(OpenAIRateLimitError) |
                retry_if_exception_type(Exception)
            ),
            before_sleep=log_retry_attempt,
            reraise=True
        )
        return r(func)

    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=min_wait, max=max_wait),
        retry=(
            retry_if_exception_type(requests.exceptions.RequestException) |
            retry_if_exception_type(OpenAIRateLimitError) |
            retry_if_exception_type(Exception)  # Broad fallback, but allows specific hooking
        ),
        before_sleep=log_retry_attempt,
        reraise=True
    )

