"""Tests for provider error mapping and core error formatting."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest
from httpx import HTTPStatusError, ReadTimeout, Request, Response

from core.anthropic import (
    append_request_id,
    format_user_error_preview,
    get_user_facing_error_message,
)
from providers.error_mapping import (
    map_error,
    user_visible_message_for_mapped_provider_error,
)
from providers.exceptions import (
    APIError,
    AuthenticationError,
    InvalidRequestError,
    OverloadedError,
    RateLimitError,
)


def _make_openai_error(cls, message="test error", status_code=None):
    """Helper to create openai exceptions with required httpx objects."""
    response = Response(
        status_code=status_code or 500, request=Request("POST", "http://test")
    )
    body = {"error": {"message": message}}
    # openai.APIError base class has a different constructor signature
    if cls is openai.APIError:
        return cls(message, request=Request("POST", "http://test"), body=body)
    return cls(message, response=response, body=body)


def _make_http_status_error(status_code: int, message: str = "upstream error"):
    request = Request("POST", "http://test")
    response = Response(status_code=status_code, request=request)
    return HTTPStatusError(message, request=request, response=response)


class TestMapError:
    """Tests for map_error function."""

    def test_authentication_error(self):
        """openai.AuthenticationError -> AuthenticationError."""
        exc = _make_openai_error(openai.AuthenticationError, status_code=401)
        result = map_error(exc)
        assert isinstance(result, AuthenticationError)
        assert result.status_code == 401

    def test_rate_limit_error(self):
        """openai.RateLimitError -> RateLimitError and triggers global block."""
        exc = _make_openai_error(openai.RateLimitError, status_code=429)
        with patch("providers.error_mapping.GlobalRateLimiter") as mock_rl:
            mock_instance = MagicMock()
            mock_rl.get_instance.return_value = mock_instance
            result = map_error(exc)
            assert isinstance(result, RateLimitError)
            assert result.status_code == 429
            mock_instance.set_blocked.assert_called_once_with(60)

    def test_bad_request_error(self):
        """openai.BadRequestError -> InvalidRequestError."""
        exc = _make_openai_error(openai.BadRequestError, status_code=400)
        result = map_error(exc)
        assert isinstance(result, InvalidRequestError)
        assert result.status_code == 400

    @pytest.mark.parametrize(
        ("error_cls", "status_code", "result_cls", "message_substr"),
        [
            (
                openai.PermissionDeniedError,
                403,
                AuthenticationError,
                "model access",
            ),
            (
                openai.NotFoundError,
                404,
                APIError,
                "configured provider model",
            ),
            (openai.ConflictError, 409, InvalidRequestError, "conflict"),
            (
                openai.UnprocessableEntityError,
                422,
                InvalidRequestError,
                "unsupported request content",
            ),
        ],
    )
    def test_openai_status_errors_map_to_safe_specific_messages(
        self, error_cls, status_code, result_cls, message_substr
    ):
        exc = _make_openai_error(error_cls, status_code=status_code)
        result = map_error(exc)
        assert isinstance(result, result_cls)
        assert result.status_code == status_code
        assert message_substr in result.message

    @pytest.mark.parametrize(
        ("exc", "message_substr"),
        [
            (
                openai.APITimeoutError(request=Request("POST", "http://test")),
                "timed out",
            ),
            (
                openai.APIConnectionError(request=Request("POST", "http://test")),
                "Could not connect",
            ),
            (
                httpx.ConnectError("boom", request=Request("POST", "http://test")),
                "Could not connect",
            ),
            (
                httpx.RemoteProtocolError(
                    "closed", request=Request("POST", "http://test")
                ),
                "interrupted",
            ),
        ],
    )
    def test_transport_errors_map_to_safe_specific_messages(self, exc, message_substr):
        result = map_error(exc)
        assert isinstance(result, APIError)
        assert message_substr in result.message

    def test_http_403_preserves_authorization_context(self):
        """HTTP 403 stays distinguishable from missing/invalid API key errors."""
        exc = _make_http_status_error(403, "Forbidden")
        result = map_error(exc)
        assert isinstance(result, AuthenticationError)
        assert result.status_code == 403
        assert "model access" in result.message

    def test_http_404_mentions_configured_model(self):
        """Removed provider models should produce an actionable user message."""
        exc = _make_http_status_error(404, "Not Found")
        result = map_error(exc)
        assert isinstance(result, APIError)
        assert result.status_code == 404
        assert "configured provider model" in result.message

    @pytest.mark.parametrize(
        ("status_code", "result_cls", "message_substr"),
        [
            (400, InvalidRequestError, "Invalid request"),
            (408, APIError, "timed out"),
            (409, InvalidRequestError, "conflict"),
            (422, InvalidRequestError, "unsupported request content"),
            (429, RateLimitError, "rate limit"),
            (500, APIError, "HTTP 500"),
            (599, APIError, "HTTP 599"),
        ],
    )
    def test_http_status_errors_map_to_safe_specific_messages(
        self, status_code, result_cls, message_substr
    ):
        exc = _make_http_status_error(status_code)
        with patch("providers.error_mapping.GlobalRateLimiter") as mock_rl:
            mock_instance = MagicMock()
            mock_rl.get_instance.return_value = mock_instance
            result = map_error(exc)
        assert isinstance(result, result_cls)
        assert result.status_code == status_code
        assert message_substr in result.message
        if status_code == 429:
            mock_instance.set_blocked.assert_called_once_with(60)

    @pytest.mark.parametrize(
        "message",
        ["Server overloaded", "No capacity available"],
        ids=["overloaded", "capacity"],
    )
    def test_internal_server_error_overloaded(self, message):
        """InternalServerError with overloaded/capacity keywords -> OverloadedError."""
        exc = _make_openai_error(
            openai.InternalServerError, message=message, status_code=500
        )
        result = map_error(exc)
        assert isinstance(result, OverloadedError)
        assert result.status_code == 529

    def test_internal_server_error_generic(self):
        """InternalServerError without keywords maps to APIError preserving 5xx."""
        exc = _make_openai_error(
            openai.InternalServerError, message="Unknown error", status_code=500
        )
        result = map_error(exc)
        assert isinstance(result, APIError)
        assert result.status_code == 500

    @pytest.mark.parametrize(
        ("status_code", "expect_substr"),
        [
            (500, "provider api request failed"),
            (502, "temporarily unavailable"),
            (503, "temporarily unavailable"),
            (504, "temporarily unavailable"),
            (599, "provider api request failed"),
        ],
    )
    def test_internal_server_error_preserves_5xx_status_for_messaging(
        self, status_code, expect_substr
    ):
        """InternalServerError carrying HTTP 5xx retains status for stable user messaging."""
        exc = _make_openai_error(
            openai.InternalServerError,
            message=f"upstream {status_code}",
            status_code=status_code,
        )
        result = map_error(exc)
        assert isinstance(result, APIError)
        assert result.status_code == status_code
        assert expect_substr in result.message.lower()

    def test_generic_api_error(self):
        """openai.APIError -> APIError with original status_code."""
        exc = _make_openai_error(
            openai.APIError, message="Bad gateway", status_code=502
        )
        result = map_error(exc)
        assert isinstance(result, APIError)

    def test_unmapped_exception_passthrough(self):
        """Non-openai exceptions are returned as-is."""
        exc = RuntimeError("unexpected")
        result = map_error(exc)
        assert result is exc
        assert isinstance(result, RuntimeError)

    def test_value_error_passthrough(self):
        """ValueError passes through unchanged."""
        exc = ValueError("bad value")
        result = map_error(exc)
        assert result is exc


def test_user_facing_message_read_timeout_empty_string():
    """ReadTimeout wrapping TimeoutError should still produce readable text."""
    timeout_exc = ReadTimeout("")
    message = get_user_facing_error_message(timeout_exc, read_timeout_s=60)
    assert message == "Provider request timed out after 60s."


def test_user_facing_message_http_status_error_includes_status():
    exc = _make_http_status_error(500, "Server Error")
    assert get_user_facing_error_message(exc) == (
        "Provider API request failed (HTTP 500)."
    )


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (400, "Invalid request sent to provider."),
        (401, "Provider authentication failed. Check API key."),
        (
            403,
            "Provider authorization failed (HTTP 403). Check API key, account "
            "permissions, or model access.",
        ),
        (
            404,
            "Provider endpoint or model was not found (HTTP 404). Check the "
            "configured provider model.",
        ),
        (408, "Provider request timed out after 60s."),
        (409, "Provider rejected the request due to a conflict (HTTP 409)."),
        (422, "Provider rejected unsupported request content (HTTP 422)."),
        (429, "Provider rate limit reached. Please retry shortly."),
        (502, "Provider is temporarily unavailable. Please retry."),
        (599, "Provider API request failed (HTTP 599)."),
    ],
)
def test_user_facing_message_http_status_matrix(status_code, expected):
    exc = _make_http_status_error(status_code)
    assert get_user_facing_error_message(exc, read_timeout_s=60) == expected


def test_user_facing_message_mapped_provider_error_uses_safe_message():
    mapped = APIError("Could not connect to provider.")
    assert get_user_facing_error_message(mapped) == "Could not connect to provider."


def test_append_request_id_suffix():
    """Request id suffix should be appended deterministically."""
    message = append_request_id("Provider request failed.", "req_abc123")
    assert message == "Provider request failed. (request_id=req_abc123)"


def test_user_facing_message_bad_request_prefers_mapped_text_over_sdk_string():
    """BadRequestError should map to stable wording even when str(exc) is non-empty."""
    exc = _make_openai_error(
        openai.BadRequestError, message="leaky-upstream-detail", status_code=400
    )
    assert get_user_facing_error_message(exc) == "Invalid request sent to provider."


def test_format_user_error_preview_truncates():
    exc = ValueError("x" * 500)
    preview = format_user_error_preview(exc, max_len=20)
    assert len(preview) == 20
    assert preview == "x" * 20


def test_user_visible_message_for_mapped_provider_error_405():
    mapped = APIError("ignored", status_code=405, raw_error="")
    msg = user_visible_message_for_mapped_provider_error(
        mapped, provider_name="ACME", read_timeout_s=30.0
    )
    assert "ACME" in msg and "405" in msg


def test_streaming_transports_pass_scoped_rate_limiter_to_map_error():
    """Guardrail: streaming adapters must scope reactive 429 handling per provider."""
    root = Path(__file__).resolve().parents[2]
    for name in ("anthropic_messages.py", "openai_compat.py"):
        text = (root / "providers" / name).read_text(encoding="utf-8")
        assert "map_error(" in text, name
        assert "rate_limiter=self._global_rate_limiter" in text, name
