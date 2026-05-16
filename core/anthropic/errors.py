"""User-facing error formatting shared by API, providers, and integrations."""

import httpx
import openai


def get_user_facing_error_message(
    e: Exception,
    *,
    read_timeout_s: float | None = None,
) -> str:
    """Return a readable, non-empty error message for users.

    Known transport and OpenAI SDK exception types are mapped to stable wording
    before falling back to ``str(e)``, so empty or noisy SDK messages do not skip
    the mapped path.
    """
    if isinstance(e, httpx.ReadTimeout):
        if read_timeout_s is not None:
            return f"Provider request timed out after {read_timeout_s:g}s."
        return "Provider request timed out."
    if isinstance(e, httpx.ConnectTimeout):
        return "Could not connect to provider."
    if isinstance(e, httpx.TimeoutException):
        if read_timeout_s is not None:
            return f"Provider request timed out after {read_timeout_s:g}s."
        return "Provider request timed out."
    if isinstance(e, httpx.ConnectError):
        return "Could not connect to provider."
    if isinstance(e, httpx.RemoteProtocolError):
        return "Provider connection was interrupted."
    if isinstance(e, httpx.ProtocolError):
        return "Provider stream failed."
    if isinstance(e, TimeoutError):
        if read_timeout_s is not None:
            return f"Provider request timed out after {read_timeout_s:g}s."
        return "Request timed out."

    if isinstance(e, openai.APITimeoutError):
        if read_timeout_s is not None:
            return f"Provider request timed out after {read_timeout_s:g}s."
        return "Provider request timed out."
    if isinstance(e, openai.APIConnectionError):
        return "Could not connect to provider."
    if isinstance(e, openai.RateLimitError):
        return "Provider rate limit reached. Please retry shortly."
    if isinstance(e, openai.AuthenticationError):
        return "Provider authentication failed. Check API key."
    if isinstance(e, openai.PermissionDeniedError):
        return (
            "Provider authorization failed (HTTP 403). Check API key, account "
            "permissions, or model access."
        )
    if isinstance(e, openai.NotFoundError):
        return (
            "Provider endpoint or model was not found (HTTP 404). Check the "
            "configured provider model."
        )
    if isinstance(e, openai.BadRequestError):
        return "Invalid request sent to provider."
    if isinstance(e, openai.ConflictError):
        return "Provider rejected the request due to a conflict (HTTP 409)."
    if isinstance(e, openai.UnprocessableEntityError):
        return "Provider rejected unsupported request content (HTTP 422)."

    name = type(e).__name__
    status_code = _status_code_from_exception(e)
    provider_message = _mapped_provider_error_message(e)
    if provider_message is not None:
        return provider_message
    if name == "RateLimitError":
        return "Provider rate limit reached. Please retry shortly."
    if name == "AuthenticationError":
        return "Provider authentication failed. Check API key."
    if name == "PermissionDeniedError" or status_code == 403:
        return (
            "Provider authorization failed (HTTP 403). Check API key, account "
            "permissions, or model access."
        )
    if name == "InvalidRequestError":
        return "Invalid request sent to provider."
    if name == "OverloadedError":
        return "Provider is currently overloaded. Please retry."
    if status_code == 400:
        return "Invalid request sent to provider."
    if status_code == 401:
        return "Provider authentication failed. Check API key."
    if status_code == 408:
        if read_timeout_s is not None:
            return f"Provider request timed out after {read_timeout_s:g}s."
        return "Provider request timed out."
    if status_code == 409:
        return "Provider rejected the request due to a conflict (HTTP 409)."
    if status_code == 404:
        return (
            "Provider endpoint or model was not found (HTTP 404). Check the "
            "configured provider model."
        )
    if status_code == 422:
        return "Provider rejected unsupported request content (HTTP 422)."
    if status_code == 429:
        return "Provider rate limit reached. Please retry shortly."
    if status_code in (502, 503, 504):
        return "Provider is temporarily unavailable. Please retry."
    if isinstance(status_code, int):
        return f"Provider API request failed (HTTP {status_code})."
    if name == "APIError":
        return "Provider API request failed."
    if name.endswith("ProviderError") or name == "ProviderError":
        return "Provider request failed."

    message = str(e).strip()
    if message:
        return message

    return "Provider request failed unexpectedly."


def _mapped_provider_error_message(e: Exception) -> str | None:
    """Return this app's already-sanitized provider message when meaningful."""
    if type(e).__module__ != "providers.exceptions":
        return None
    provider_message = getattr(e, "message", "")
    if not isinstance(provider_message, str):
        return None
    message = provider_message.strip()
    if not message or message == "_":
        return None
    return message


def _status_code_from_exception(e: Exception) -> int | None:
    """Extract an HTTP status code from SDK, provider, or httpx exceptions."""
    status_code = getattr(e, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    response = getattr(e, "response", None)
    response_status = getattr(response, "status_code", None)
    if isinstance(response_status, int):
        return response_status
    return None


def format_user_error_preview(exc: Exception, *, max_len: int = 200) -> str:
    """Truncate a user-facing error string for short chat replies."""
    return get_user_facing_error_message(exc)[:max_len]


def append_request_id(message: str, request_id: str | None) -> str:
    """Append request_id suffix when available."""
    base = message.strip() or "Provider request failed unexpectedly."
    if request_id:
        return f"{base} (request_id={request_id})"
    return base
