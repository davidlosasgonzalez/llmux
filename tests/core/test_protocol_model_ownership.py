"""Protocol models live with the protocol logic that consumes them."""

from free_claude_code.core.anthropic import (
    MessagesRequest as PublicMessagesRequest,
)
from free_claude_code.core.anthropic import (
    MessagesResponse,
    TokenCountResponse,
)
from free_claude_code.core.anthropic.models import MessagesRequest


def test_anthropic_request_model_is_core_owned_and_permissive() -> None:
    request = MessagesRequest.model_validate(
        {
            "model": "provider-model",
            "messages": [{"role": "user", "content": "hello"}],
            "provider_extension": {"enabled": True},
        }
    )

    assert MessagesRequest.__module__ == "free_claude_code.core.anthropic.models"
    assert PublicMessagesRequest is MessagesRequest
    assert request.model_extra == {"provider_extension": {"enabled": True}}


def test_anthropic_response_models_are_protocol_owned() -> None:
    assert MessagesResponse.__module__ == "free_claude_code.core.anthropic.models"
    assert TokenCountResponse.__module__ == "free_claude_code.core.anthropic.models"
