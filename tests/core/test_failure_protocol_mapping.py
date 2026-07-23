"""Canonical execution failures and their protocol-owned wire mappings."""

import pytest

from llmux.core.anthropic.errors import (
    anthropic_error_type_for_failure,
    anthropic_failure_payload,
)
from llmux.core.failures import ExecutionFailure, FailureKind


@pytest.mark.parametrize(
    ("kind", "anthropic_type"),
    [
        (FailureKind.INVALID_REQUEST, "invalid_request_error"),
        (FailureKind.AUTHENTICATION, "authentication_error"),
        (FailureKind.PERMISSION, "permission_error"),
        (FailureKind.RATE_LIMIT, "rate_limit_error"),
        (FailureKind.OVERLOADED, "overloaded_error"),
        # Existing finalized transport timeouts are exposed as api_error; the
        # semantic kind becomes more precise without changing that wire contract.
        (FailureKind.TIMEOUT, "api_error"),
        (FailureKind.UPSTREAM, "api_error"),
        (FailureKind.UNAVAILABLE, "api_error"),
    ],
)
def test_each_protocol_owns_failure_kind_to_wire_type_mapping(
    kind: FailureKind,
    anthropic_type: str,
) -> None:
    assert anthropic_error_type_for_failure(kind) == anthropic_type


def test_finalized_status_502_timeout_keeps_existing_api_error_wire_type() -> None:
    failure = ExecutionFailure(
        kind=FailureKind.TIMEOUT,
        status_code=502,
        message="Provider request timed out.",
        retryable=True,
    )

    assert anthropic_failure_payload(failure)["error"]["type"] == "api_error"
