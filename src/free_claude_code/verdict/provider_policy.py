"""Provider free-access policy — the single gate for the economic constraint.

Every provider maps to a :class:`ProviderFreeAccess` record. In free-only mode
(``ALLOW_PAID_MODELS=false``) a provider is usable only when it is enabled AND
it never requires a card, deposit or billing to obtain a genuine daily free
allowance. Providers that need money are excluded even if a user enables them in
``verdict.yaml`` — enabling flips visibility, it does not override cost safety.

Having an API key only authenticates the user; it never implies authorisation.
Authorisation is this table plus per-model cost classification.
"""

from dataclasses import dataclass

from .models import CostStatus

# The five explicit provider tiers requested by the operator.
TIER_A = "A"  # primary, enabled by default, generous free daily, no card
TIER_B = "B"  # secondary, free without a card, opt-in via config
TIER_DISABLED = "disabled"  # off until validated as genuinely free
TIER_PAID = "paid"  # needs money/card/balance — never auto-used in free mode
TIER_LOCAL = "local"  # runs on the user's machine, always free


@dataclass(frozen=True, slots=True)
class ProviderFreeAccess:
    """Static free-access classification for one provider id."""

    provider: str
    tier: str
    requires_card: bool
    default_enabled: bool
    free_daily: str
    local: bool = False
    # OpenRouter and similar aggregators expose both free and paid model ids;
    # when set, only model ids matching this suffix are treated as free.
    free_model_suffix: str | None = None

    @property
    def default_cost_status(self) -> CostStatus:
        """Cost status assumed before dynamic per-model verification."""
        if self.requires_card:
            return CostStatus.PAID
        if self.local:
            return CostStatus.VERIFIED_FREE
        if self.free_model_suffix is not None:
            # Aggregator: a model is only free once its id is verified.
            return CostStatus.UNKNOWN
        if self.tier in (TIER_A, TIER_B):
            return CostStatus.FREE_TIER
        return CostStatus.UNKNOWN


# Notes reflect the state of each provider's free daily offer without a card as
# of authoring; validation at runtime (`fcc-verdict providers validate`) is the
# source of truth. Anything needing money is marked requires_card=True so it can
# never be selected in free-only mode.
_POLICY: dict[str, ProviderFreeAccess] = {
    # ---- Tier A: primary, enabled by default ----
    "groq": ProviderFreeAccess(
        provider="groq",
        tier=TIER_A,
        requires_card=False,
        default_enabled=True,
        free_daily="Free key, generous per-day request/token limits, no card.",
    ),
    "nvidia_nim": ProviderFreeAccess(
        provider="nvidia_nim",
        tier=TIER_A,
        requires_card=False,
        default_enabled=True,
        free_daily="Free NIM endpoint credits, no card required.",
    ),
    "cerebras": ProviderFreeAccess(
        provider="cerebras",
        tier=TIER_A,
        requires_card=False,
        default_enabled=True,
        free_daily="Free key with a daily token allowance, no card.",
    ),
    "gemini": ProviderFreeAccess(
        provider="gemini",
        tier=TIER_A,
        requires_card=False,
        default_enabled=True,
        free_daily="Google AI Studio free tier, per-day request limits, no card.",
    ),
    # ---- Tier B: free without a card, opt-in ----
    "mistral": ProviderFreeAccess(
        provider="mistral",
        tier=TIER_B,
        requires_card=False,
        default_enabled=False,
        free_daily="La Plateforme free experiment tier, rate-limited, no card.",
    ),
    "open_router": ProviderFreeAccess(
        provider="open_router",
        tier=TIER_B,
        requires_card=False,
        default_enabled=False,
        free_daily="Only ':free' model ids; zero input/output price required.",
        free_model_suffix=":free",
    ),
    "github_models": ProviderFreeAccess(
        provider="github_models",
        tier=TIER_B,
        requires_card=False,
        default_enabled=False,
        free_daily="Free with a GitHub account, daily request limits, no card.",
    ),
    "cloudflare": ProviderFreeAccess(
        provider="cloudflare",
        tier=TIER_B,
        requires_card=False,
        default_enabled=False,
        free_daily="Workers AI daily free neuron allocation, no card.",
    ),
    "cohere": ProviderFreeAccess(
        provider="cohere",
        tier=TIER_B,
        requires_card=False,
        default_enabled=False,
        free_daily="Trial keys, rate-limited monthly free calls, no card.",
    ),
    # ---- Disabled by default: plausibly free but unvalidated. Not banned:
    # they can be enabled in verdict.yaml once confirmed generous without a card.
    "huggingface": ProviderFreeAccess(
        provider="huggingface",
        tier=TIER_DISABLED,
        requires_card=False,
        default_enabled=False,
        free_daily="Inference Providers free monthly credits; validate before use.",
    ),
    "sambanova": ProviderFreeAccess(
        provider="sambanova",
        tier=TIER_DISABLED,
        requires_card=False,
        default_enabled=False,
        free_daily="Free tier exists; validate limits before enabling.",
    ),
    "ollama_cloud": ProviderFreeAccess(
        provider="ollama_cloud",
        tier=TIER_DISABLED,
        requires_card=False,
        default_enabled=False,
        free_daily="Free hosted tier exists; validate before enabling.",
    ),
    # ---- Paid / needs money or card: excluded in free-only mode ----
    "deepseek": ProviderFreeAccess(
        provider="deepseek",
        tier=TIER_PAID,
        requires_card=True,
        default_enabled=False,
        free_daily="Direct DeepSeek requires prepaid balance. Use it via NIM/OpenRouter free instead.",
    ),
    "fireworks": ProviderFreeAccess(
        provider="fireworks",
        tier=TIER_PAID,
        requires_card=True,
        default_enabled=False,
        free_daily="Trial credit then paid; treated as paid.",
    ),
    "kimi": ProviderFreeAccess(
        provider="kimi",
        tier=TIER_PAID,
        requires_card=True,
        default_enabled=False,
        free_daily="Moonshot platform is prepaid.",
    ),
    "minimax": ProviderFreeAccess(
        provider="minimax",
        tier=TIER_PAID,
        requires_card=True,
        default_enabled=False,
        free_daily="Prepaid platform.",
    ),
    "wafer": ProviderFreeAccess(
        provider="wafer",
        tier=TIER_PAID,
        requires_card=True,
        default_enabled=False,
        free_daily="Cost unknown; treated as paid until proven free.",
    ),
    "zai": ProviderFreeAccess(
        provider="zai",
        tier=TIER_PAID,
        requires_card=True,
        default_enabled=False,
        free_daily="Direct Z.ai is prepaid.",
    ),
    "vercel": ProviderFreeAccess(
        provider="vercel",
        tier=TIER_PAID,
        requires_card=True,
        default_enabled=False,
        free_daily="AI Gateway billed usage.",
    ),
    "mistral_codestral": ProviderFreeAccess(
        provider="mistral_codestral",
        tier=TIER_PAID,
        requires_card=True,
        default_enabled=False,
        free_daily="Codestral key billed separately; treated as paid.",
    ),
    # ---- Local providers: always free, used only when configured/running ----
    "lmstudio": ProviderFreeAccess(
        provider="lmstudio",
        tier=TIER_LOCAL,
        requires_card=False,
        default_enabled=False,
        free_daily="Local models, no cost.",
        local=True,
    ),
    "llamacpp": ProviderFreeAccess(
        provider="llamacpp",
        tier=TIER_LOCAL,
        requires_card=False,
        default_enabled=False,
        free_daily="Local models, no cost.",
        local=True,
    ),
    "ollama": ProviderFreeAccess(
        provider="ollama",
        tier=TIER_LOCAL,
        requires_card=False,
        default_enabled=False,
        free_daily="Local models, no cost.",
        local=True,
    ),
}


def policy_for(provider: str) -> ProviderFreeAccess:
    """Return the free-access policy for ``provider``.

    Unknown providers default to the safest possible classification: paid,
    disabled, card required — so a provider added upstream without a policy is
    never silently used in free-only mode.
    """
    known = _POLICY.get(provider)
    if known is not None:
        return known
    return ProviderFreeAccess(
        provider=provider,
        tier=TIER_PAID,
        requires_card=True,
        default_enabled=False,
        free_daily="Unclassified provider; treated as paid until validated.",
    )


def all_policies() -> tuple[ProviderFreeAccess, ...]:
    return tuple(_POLICY.values())


def default_enabled_providers() -> tuple[str, ...]:
    return tuple(p.provider for p in _POLICY.values() if p.default_enabled)


def classify_model_cost(provider: str, model_id: str) -> CostStatus:
    """Classify a concrete model's cost from static policy alone.

    Dynamic pricing (e.g. OpenRouter catalogue) can refine this later, but this
    conservative static pass already guarantees no paid model slips through.
    """
    policy = policy_for(provider)
    if policy.requires_card:
        return CostStatus.PAID
    if policy.free_model_suffix is not None:
        return (
            CostStatus.VERIFIED_FREE
            if model_id.endswith(policy.free_model_suffix)
            else CostStatus.PAID
        )
    return policy.default_cost_status


def is_provider_eligible(
    provider: str, *, allow_paid: bool, enabled_providers: frozenset[str]
) -> bool:
    """True when ``provider`` may be used under the current cost regime."""
    policy = policy_for(provider)
    if provider not in enabled_providers:
        return False
    if allow_paid:
        return True
    return not policy.requires_card


def is_model_eligible(
    provider: str,
    model_id: str,
    *,
    allow_paid: bool,
    enabled_providers: frozenset[str],
) -> bool:
    """Full free-only gate for a concrete provider/model pair."""
    if not is_provider_eligible(
        provider, allow_paid=allow_paid, enabled_providers=enabled_providers
    ):
        return False
    if allow_paid:
        return True
    cost = classify_model_cost(provider, model_id)
    return cost in (CostStatus.VERIFIED_FREE, CostStatus.FREE_TIER)
