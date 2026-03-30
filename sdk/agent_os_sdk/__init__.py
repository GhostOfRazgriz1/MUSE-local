"""Agent OS Skill SDK — capability-gated abstractions for skill development."""

from agent_os_sdk.context import SkillContext, SkillResult
from agent_os_sdk.errors import (
    PermissionDenied,
    UserCancelled,
    ExternalServiceError,
    SkillError,
)

__all__ = [
    "SkillContext",
    "SkillResult",
    "PermissionDenied",
    "UserCancelled",
    "ExternalServiceError",
    "SkillError",
]
