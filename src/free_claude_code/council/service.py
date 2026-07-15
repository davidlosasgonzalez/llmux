"""The single council core shared by the CLI and the MCP server.

Ties configuration, discovery, the free-only gate, orchestration and storage
together behind two entry points: :meth:`evaluate` and :meth:`validate_providers`.
Both the CLI and MCP call this exact object, satisfying "CLI and MCP use the same
core".
"""

import datetime
import time
from dataclasses import dataclass, field
from pathlib import Path

from free_claude_code.config.settings import Settings, get_settings

from .classification import classify_task
from .config import CouncilConfig, council_db_path, council_reports_dir, load_config
from .discovery import ModelLister, discover_models, provider_has_credential
from .errors import InsufficientFreeModelsError
from .invoker import ModelInvoker
from .models import (
    CouncilResult,
    Depth,
    ModelRef,
    Privacy,
    QuotaFailure,
    TaskType,
)
from .orchestration import Orchestrator
from .provider_invoker import ProviderModelInvoker
from .provider_limits import daily_limit
from .provider_policy import (
    ProviderFreeAccess,
    all_policies,
    is_provider_eligible,
    policy_for,
)
from .quota import QuotaTracker
from .redaction import PathPolicy, apply_privacy
from .storage import CouncilStore, UsageRow, save_report


@dataclass(frozen=True, slots=True)
class ProviderValidation:
    """One row of ``fcc-council providers validate`` output."""

    provider: str
    authenticated: bool
    free_status: str
    usable: bool
    tier: str
    note: str


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """Aggregated daily usage for one provider, vs its approximate free limit."""

    provider: str
    requests: int
    total_tokens: int
    rpd_limit: int | None
    budget_class: str
    pct_of_rpd: float | None
    note: str


@dataclass(slots=True)
class CouncilService:
    """Stateful facade over one council configuration."""

    config: CouncilConfig
    settings: Settings
    invoker: ModelInvoker
    lister: ModelLister
    store: CouncilStore | None = None
    allowed_roots: tuple[Path, ...] = field(default_factory=lambda: (Path.cwd(),))
    _provider_invoker: ProviderModelInvoker | None = None

    @classmethod
    def create(
        cls,
        *,
        config: CouncilConfig | None = None,
        settings: Settings | None = None,
        store: CouncilStore | None = None,
        allowed_roots: tuple[Path, ...] | None = None,
    ) -> CouncilService:
        """Build a production service backed by the real provider stack."""
        resolved_config = config or load_config()
        resolved_settings = settings or get_settings()
        provider_invoker = ProviderModelInvoker(
            resolved_settings, privacy=resolved_config.privacy
        )
        resolved_store = store or CouncilStore(council_db_path())
        return cls(
            config=resolved_config,
            settings=resolved_settings,
            invoker=provider_invoker,
            lister=provider_invoker.list_models,
            store=resolved_store,
            allowed_roots=allowed_roots or (Path.cwd(),),
            _provider_invoker=provider_invoker,
        )

    # ------------------------------------------------------------------ #
    async def evaluate(
        self,
        prompt: str,
        *,
        task_type: str = "auto",
        depth: str | None = None,
        files: list[str] | None = None,
        privacy: str | None = None,
        max_rounds: int | None = None,
        criteria: str = "",
        extra_context: str = "",
        request_id: str = "council",
    ) -> tuple[CouncilResult, Path | None]:
        """Run a full deliberation and return (result, report_path)."""
        resolved_privacy = (
            Privacy(privacy) if privacy is not None else self.config.privacy
        )
        resolved_depth = Depth(depth) if depth is not None else self.config.depth
        if max_rounds is not None:
            self.config = self.config.model_copy(
                update={"max_rounds": max(1, min(5, max_rounds))}
            )

        context = self._gather_context(files or [], extra_context, resolved_privacy)
        safe_prompt = apply_privacy(prompt, resolved_privacy)

        resolved_task = (
            classify_task(prompt, has_files=bool(files))
            if task_type == "auto"
            else TaskType(task_type)
        )

        if self._provider_invoker is not None:
            self._provider_invoker.privacy = resolved_privacy

        candidates, failures = await self._discover(resolved_privacy)
        self._guard_enough_models(candidates, failures)

        orchestrator = Orchestrator(
            self.invoker,
            self.config,
            store=self.store,
            quota=QuotaTracker(),
        )
        result = await orchestrator.run(
            safe_prompt,
            resolved_task,
            candidates,
            depth=resolved_depth,
            criteria=criteria,
            context=context,
            request_id=request_id,
        )
        result.quota_failures.extend(failures)

        report_path = self._save_report(result)
        return result, report_path

    async def _discover(
        self, privacy: Privacy
    ) -> tuple[list[ModelRef], list[QuotaFailure]]:
        providers = self._provider_search_order()
        candidates, failures = await discover_models(
            providers,
            self.lister,
            self.settings,
            allow_paid=self.config.allow_paid_models,
            enabled_providers=self.config.enabled_provider_set(),
        )
        if privacy is Privacy.LOCAL_ONLY:
            candidates = [m for m in candidates if policy_for(m.provider).local]
        return candidates, failures

    def _provider_search_order(self) -> list[str]:
        ordered = list(self.config.provider_priority)
        for provider in self.config.enabled_providers:
            if provider not in ordered:
                ordered.append(provider)
        return ordered

    def _guard_enough_models(
        self, candidates: list[ModelRef], failures: list[QuotaFailure]
    ) -> None:
        distinct_providers = {m.provider for m in candidates}
        reasons = [f"{f.provider}: {f.reason}" for f in failures]
        if len(candidates) < self.config.minimum_models:
            raise InsufficientFreeModelsError(
                f"Need at least {self.config.minimum_models} free models, "
                f"found {len(candidates)}.",
                reasons=reasons,
            )
        if len(distinct_providers) < self.config.minimum_distinct_providers:
            raise InsufficientFreeModelsError(
                f"Need at least {self.config.minimum_distinct_providers} distinct "
                f"free providers, found {len(distinct_providers)} "
                f"({', '.join(sorted(distinct_providers))}).",
                reasons=reasons,
            )

    def _gather_context(
        self, files: list[str], extra_context: str, privacy: Privacy
    ) -> str:
        policy = PathPolicy.from_paths(self.allowed_roots)
        chunks: list[str] = []
        if extra_context.strip():
            chunks.append(apply_privacy(extra_context, privacy))
        for raw in files:
            try:
                content = policy.read_text(raw)
            except (PermissionError, ValueError, OSError) as exc:
                chunks.append(f"[skipped file {raw}: {exc}]")
                continue
            chunks.append(
                f"--- FILE: {Path(raw).name} ---\n{apply_privacy(content, privacy)}"
            )
        return "\n\n".join(chunks)

    def _save_report(self, result: CouncilResult) -> Path | None:
        if self.store is None:
            return None
        report = _full_report(result)
        name = f"council-{int(time.time())}-{result.task_type.value}"
        try:
            return save_report(council_reports_dir(), report, name=name)
        except OSError:
            return None

    # ------------------------------------------------------------------ #
    async def validate_providers(self) -> list[ProviderValidation]:
        """Report auth and free-status for every catalogued provider.

        Never prints or returns API keys — only whether one is present.
        """
        rows: list[ProviderValidation] = []
        enabled = self.config.enabled_provider_set()
        for policy in _ordered_policies(self._provider_search_order()):
            authenticated = provider_has_credential(policy.provider, self.settings)
            eligible = is_provider_eligible(
                policy.provider,
                allow_paid=self.config.allow_paid_models,
                enabled_providers=enabled,
            )
            usable = authenticated and eligible
            rows.append(
                ProviderValidation(
                    provider=policy.provider,
                    authenticated=authenticated,
                    free_status=(
                        "paid" if policy.requires_card else f"free ({policy.tier})"
                    ),
                    usable=usable,
                    tier=policy.tier,
                    note=policy.free_daily,
                )
            )
        return rows

    async def list_free_models(self) -> list[ModelRef]:
        candidates, _ = await self._discover(self.config.privacy)
        return candidates

    def usage(
        self, day: str | None = None
    ) -> tuple[list[ProviderUsage], list[UsageRow]]:
        """Return (per-provider summary, per-model rows) for a day.

        Defaults to today. Requests and tokens are approximate — provider-reported
        where available, estimated otherwise — and compared against the
        approximate free-tier RPD so the user gets a rough ``/usage``-style view.
        """
        target_day = day or datetime.datetime.now().date().isoformat()
        rows = self.store.usage_rows(target_day) if self.store is not None else []

        by_provider: dict[str, list[UsageRow]] = {}
        for row in rows:
            by_provider.setdefault(row.provider, []).append(row)

        summaries: list[ProviderUsage] = []
        for provider, provider_rows in by_provider.items():
            requests = sum(r.requests for r in provider_rows)
            tokens = sum(r.total_tokens for r in provider_rows)
            limit = daily_limit(provider)
            pct = (
                round(100.0 * requests / limit.rpd, 1)
                if limit.rpd not in (None, 0)
                else None
            )
            summaries.append(
                ProviderUsage(
                    provider=provider,
                    requests=requests,
                    total_tokens=tokens,
                    rpd_limit=limit.rpd,
                    budget_class=limit.budget_class,
                    pct_of_rpd=pct,
                    note=limit.note,
                )
            )
        summaries.sort(key=lambda s: s.requests, reverse=True)
        return summaries, rows

    async def cleanup(self) -> None:
        if self._provider_invoker is not None:
            await self._provider_invoker.cleanup()
        if self.store is not None:
            self.store.close()


def _ordered_policies(order: list[str]) -> list[ProviderFreeAccess]:
    policies = {p.provider: p for p in all_policies()}
    seen: set[str] = set()
    result: list[ProviderFreeAccess] = []
    for provider in order:
        policy = policies.get(provider)
        if policy is not None:
            result.append(policy)
            seen.add(provider)
    for provider, policy in policies.items():
        if provider not in seen:
            result.append(policy)
    return result


def _full_report(result: CouncilResult) -> dict[str, object]:
    """Serialise the entire deliberation for on-disk archival."""
    return {
        "task_type": result.task_type.value,
        "depth": result.depth.value,
        "stop_reason": result.stop_reason,
        "models_used": result.models_used,
        "providers_used": result.providers_used,
        "quota_failures": [
            {"provider": qf.provider, "reason": qf.reason}
            for qf in result.quota_failures
        ],
        "proposals": [
            {
                "model": p.model_key,
                "conclusion": p.conclusion,
                "reasoning_summary": p.reasoning_summary,
                "assumptions": p.assumptions,
                "risks": p.risks,
                "confidence": p.confidence,
            }
            for p in result.proposals
        ],
        "reviews": [
            {
                "reviewer": r.reviewer_key,
                "ranking": r.ranking,
                "material_errors": r.material_errors,
                "best_elements": r.best_elements,
            }
            for r in result.reviews
        ],
        "rounds": [
            {
                "index": rnd.index,
                "synthesis": {
                    "model": rnd.synthesis.model_key,
                    "final_answer": rnd.synthesis.final_answer,
                    "consensus": rnd.synthesis.consensus,
                    "material_disagreements": rnd.synthesis.material_disagreements,
                    "uncertainties": rnd.synthesis.uncertainties,
                    "recommended_action": rnd.synthesis.recommended_action,
                    "quality_score": rnd.synthesis.quality_score,
                },
                "critique": {
                    "model": rnd.critique.model_key,
                    "verdict": rnd.critique.verdict.value,
                    "score": rnd.critique.score,
                    "critical_issues": rnd.critique.critical_issues,
                    "material_issues": rnd.critique.material_issues,
                },
            }
            for rnd in result.rounds
        ],
        "compact": result.compact(),
    }
