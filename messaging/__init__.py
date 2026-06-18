"""Platform-agnostic messaging layer."""

from .event_parser import parse_cli_event
from .models import IncomingMessage
from .platforms.base import CLISession, MessagingPlatform, SessionManagerInterface
from .session import SessionStore
from .trees import MessageNode, MessageState, MessageTree, TreeQueueManager
from .workflow import MessagingWorkflow

__all__ = [
    "CLISession",
    "IncomingMessage",
    "MessageNode",
    "MessageState",
    "MessageTree",
    "MessagingPlatform",
    "MessagingWorkflow",
    "SessionManagerInterface",
    "SessionStore",
    "TreeQueueManager",
    "parse_cli_event",
]
