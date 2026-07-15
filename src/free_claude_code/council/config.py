"""Council configuration: depth presets, roles, paths and the paid-model gate.

Configuration is layered: built-in defaults -> ``~/.fcc/council.yaml`` (if
present) -> the ``ALLOW_PAID_MODELS`` environment variable, which always wins
when set. The default is fully free-only.
"""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from free_claude_code.config.paths import config_dir_path

from .models import Depth, Privacy
from .provider_policy import default_enabled_providers

COUNCIL_CONFIG_FILENAME = "council.yaml"
COUNCIL_DB_FILENAME = "council.db"
COUNCIL_REPORTS_DIRNAME = "council_reports"

ALLOW_PAID_ENV = "ALLOW_PAID_MODELS"


def council_config_path() -> Path:
    return config_dir_path() / COUNCIL_CONFIG_FILENAME


def council_db_path() -> Path:
    return config_dir_path() / COUNCIL_DB_FILENAME


def council_reports_dir() -> Path:
    return config_dir_path() / COUNCIL_REPORTS_DIRNAME


class DepthProfile(BaseModel):
    """How many models and rounds a depth level uses."""

    proponents: int = Field(ge=1)
    reviewers: int = Field(ge=0)
    max_rounds: int = Field(ge=1)


DEPTH_PRESETS: dict[Depth, DepthProfile] = {
    Depth.QUICK: DepthProfile(proponents=2, reviewers=1, max_rounds=1),
    Depth.STANDARD: DepthProfile(proponents=3, reviewers=2, max_rounds=2),
    Depth.DEEP: DepthProfile(proponents=4, reviewers=3, max_rounds=3),
}


class RoleSpec(BaseModel):
    """Capabilities preferred for each role within a task category."""

    proposer_capabilities: list[str] = Field(default_factory=list)
    critic_capabilities: list[str] = Field(default_factory=list)
    synthesis_capabilities: list[str] = Field(default_factory=list)


class CouncilConfig(BaseModel):
    """Fully resolved council configuration."""

    allow_paid_models: bool = False
    depth: Depth = Depth.DEEP
    max_rounds: int = Field(default=3, ge=1, le=5)
    quality_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    parallel_proposals: bool = True
    privacy: Privacy = Privacy.REDACTED

    provider_priority: list[str] = Field(
        default_factory=lambda: [
            "groq",
            "nvidia_nim",
            "cerebras",
            "gemini",
            "mistral",
            "open_router",
            "github_models",
            "cloudflare",
            "cohere",
        ]
    )
    # Providers the operator has turned on. Defaults to the tier-A set; adding a
    # provider here still cannot bypass the free-only cost gate.
    enabled_providers: list[str] = Field(
        default_factory=lambda: list(default_enabled_providers())
    )

    minimum_models: int = Field(default=3, ge=1)
    maximum_models: int = Field(default=5, ge=1)
    minimum_distinct_providers: int = Field(default=3, ge=1)

    # Stop refining once two consecutive rounds fail to improve the score by at
    # least this margin.
    improvement_epsilon: float = Field(default=0.02, ge=0.0)

    roles: dict[str, RoleSpec] = Field(default_factory=dict)

    # Per-request output cap for each model call.
    max_tokens_per_call: int = Field(default=2048, ge=256)

    # Hard wall-clock ceiling for any single model call. A model that exceeds it
    # is treated as a provider failure, so one pathologically slow model cannot
    # stall the whole deliberation (observed: a 159s/call model as synthesiser
    # dragged a deep run past 10 minutes).
    call_timeout_s: float = Field(default=90.0, gt=0.0)

    def depth_profile(self, depth: Depth | None = None) -> DepthProfile:
        chosen = depth or self.depth
        profile = DEPTH_PRESETS[chosen]
        # Never let a depth preset exceed the configured hard round ceiling.
        capped = min(profile.max_rounds, self.max_rounds)
        return DepthProfile(
            proponents=profile.proponents,
            reviewers=profile.reviewers,
            max_rounds=capped,
        )

    def enabled_provider_set(self) -> frozenset[str]:
        return frozenset(self.enabled_providers)


def _env_allow_paid() -> bool | None:
    """Return the ALLOW_PAID_MODELS override, or None when unset."""
    raw = os.getenv(ALLOW_PAID_ENV)
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


def load_config(path: Path | None = None) -> CouncilConfig:
    """Load council config from YAML, applying the env override last.

    Missing files yield the free-only defaults. A malformed file raises so the
    operator is never silently downgraded into an unexpected regime.
    """
    config_path = path or council_config_path()
    data: dict[str, object] = {}
    if config_path.is_file():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if loaded is not None:
            if not isinstance(loaded, dict):
                raise ValueError(
                    f"{config_path} must contain a YAML mapping at the top level"
                )
            data = loaded

    config = CouncilConfig.model_validate(data)

    override = _env_allow_paid()
    if override is not None:
        config = config.model_copy(update={"allow_paid_models": override})
    return config
