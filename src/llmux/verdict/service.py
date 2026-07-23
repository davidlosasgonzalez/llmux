"""The single verdict core shared by the CLI and the MCP server.

Ties configuration, discovery, the free-only gate, orchestration and storage
together behind two entry points: :meth:`evaluate` and :meth:`validate_providers`.
Both the CLI and MCP call this exact object, satisfying "CLI and MCP use the same
core".
"""

import datetime
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from loguru import logger

from llmux.config.settings import Settings, get_settings
from llmux.core.quota import QuotaTracker

from .classification import classify_task
from .config import VerdictConfig, load_config, verdict_db_path, verdict_reports_dir
from .discovery import ModelLister, discover_models, provider_has_credential
from .errors import InsufficientFreeModelsError
from .invoker import ModelInvoker
from .models import (
    Depth,
    ModelRef,
    Privacy,
    QuotaFailure,
    ResearchSummary,
    TaskType,
    VerdictResult,
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
from .redaction import PathPolicy, apply_privacy
from .research import (
    ResearchService,
    build_research_service,
    format_sources,
    mark_unverified_citations,
    research_needed,
)
from .storage import UsageRow, VerdictStore, save_report


@dataclass(frozen=True, slots=True)
class ProviderValidation:
    """One row of ``llmux-verdict providers validate`` output."""

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
class VerdictService:
    """Stateful facade over one verdict configuration."""

    config: VerdictConfig
    settings: Settings
    invoker: ModelInvoker
    lister: ModelLister
    store: VerdictStore | None = None
    allowed_roots: tuple[Path, ...] = field(default_factory=lambda: (Path.cwd(),))
    research_service: ResearchService | None = None
    _provider_invoker: ProviderModelInvoker | None = None

    @classmethod
    def create(
        cls,
        *,
        config: VerdictConfig | None = None,
        settings: Settings | None = None,
        store: VerdictStore | None = None,
        allowed_roots: tuple[Path, ...] | None = None,
    ) -> VerdictService:
        """Build a production service backed by the real provider stack."""
        resolved_config = config or load_config()
        resolved_settings = settings or get_settings()
        provider_invoker = ProviderModelInvoker(
            resolved_settings, privacy=resolved_config.privacy
        )
        resolved_store = store or VerdictStore(verdict_db_path())
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
        research: bool | str = "auto",
        request_id: str = "verdict",
    ) -> tuple[VerdictResult, Path | None]:
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

        research_summary, research_context = await self._run_research(
            safe_prompt, resolved_privacy, research
        )
        if research_context:
            context = (
                f"{research_context}\n\n{context}" if context else research_context
            )

        resolved_task = (
            classify_task(prompt, has_files=bool(files))
            if task_type == "auto"
            else TaskType(task_type)
        )

        if self._provider_invoker is not None:
            self._provider_invoker.privacy = resolved_privacy

        candidates, failures = await self._discover(resolved_privacy)
        self._guard_enough_models(candidates, failures)
        candidates = self._fit_context_window(candidates, context)

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
        result.research = research_summary

        await self._maybe_escalate(
            orchestrator,
            safe_prompt,
            resolved_task.value,
            candidates,
            result,
            research_summary,
            research,
            resolved_privacy,
        )
        self._apply_citation_discipline(result)

        report_path = self._save_report(result)
        return result, report_path

    def _fit_context_window(
        self, candidates: list[ModelRef], context: str
    ) -> list[ModelRef]:
        """Drop models whose context window cannot hold the context (e.g. research).

        A short-context free model (Cerebras ≈ 8K) would truncate a large research
        block and answer from a partial view. This removes models with a *known*
        window smaller than needed — but only if enough remain to satisfy the
        free-only minimum; otherwise it keeps them all rather than fail (a
        truncated call is handled downstream as an ordinary provider failure).
        Models with an unknown window are always kept.

        Also checks each provider's ``max_request_tokens`` (provider_limits.py):
        a free tier can reject a request well inside the model's real context
        window (observed: GitHub Models 413s deepseek-r1-0528 at 4K tokens/req,
        far below its actual window) — that is a provider-imposed cap, not a
        model capability, so it is checked independently of ``context_length``.
        """
        if not context:
            return candidates
        needed = len(context) // 4 + self.config.max_tokens_per_call + 1000
        fitted = [
            m
            for m in candidates
            if (m.context_length is None or m.context_length >= needed)
            and (
                (cap := daily_limit(m.provider).max_request_tokens) is None
                or cap >= needed
            )
        ]
        if len(fitted) == len(candidates):
            return candidates
        distinct = {m.provider for m in fitted}
        if (
            len(fitted) >= self.config.minimum_models
            and len(distinct) >= self.config.minimum_distinct_providers
        ):
            logger.info(
                "verdict.context.fit needed_tokens={} kept={} of {}",
                needed,
                len(fitted),
                len(candidates),
            )
            return fitted
        logger.warning(
            "verdict.context.fit_skipped needed_tokens={} would_keep={} of {} "
            "(below free-only minimum; keeping all)",
            needed,
            len(fitted),
            len(candidates),
        )
        return candidates

    def _apply_citation_discipline(self, result: VerdictResult) -> None:
        """Rewrite any URL the final synthesis cites but research never fetched.

        Never trusts the prompt contract: every URL in ``final_answer`` and
        ``recommended_action`` is checked against what research actually fetched.
        Without research (or with none surviving), every URL is marked — a
        model cannot earn trust for a citation the local process never verified.
        """
        if not result.rounds:
            return
        verified = set(result.research.sources_fetched) if result.research else set()
        last_round = result.rounds[-1]
        synthesis = last_round.synthesis
        new_answer = mark_unverified_citations(synthesis.final_answer, verified)
        new_action = mark_unverified_citations(synthesis.recommended_action, verified)
        if new_answer == synthesis.final_answer and new_action == (
            synthesis.recommended_action
        ):
            return
        updated_synthesis = replace(
            synthesis, final_answer=new_answer, recommended_action=new_action
        )
        result.rounds[-1] = replace(last_round, synthesis=updated_synthesis)

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

    async def _run_research(
        self, prompt: str, privacy: Privacy, research: bool | str
    ) -> tuple[ResearchSummary | None, str]:
        """Optionally search+fetch sources for the prompt (Phase 2.5).

        Returns ``(summary, context_block)``. The summary is stored on the result;
        the block (empty unless sources were fetched) is prepended to the panel's
        context. Never raises: any failure degrades to a summary with a note.
        """
        mode = _research_mode(research)
        if not self.config.research_enabled or mode == "off":
            return None, ""
        # Research reaches an external search engine, so it is incompatible with
        # local-only privacy even though the query is already redacted.
        if privacy is Privacy.LOCAL_ONLY:
            if mode == "on":
                return (
                    ResearchSummary(
                        backend="none",
                        note="research skipped: privacy=local_only",
                    ),
                    "",
                )
            return None, ""
        if mode == "auto" and not research_needed(prompt):
            return None, ""

        result = await self._research_service().investigate(prompt)
        today = datetime.date.today().isoformat()
        block = format_sources(result, fetched_on=today) if result.sources else ""
        return result.summary(), block

    def _research_service(self) -> ResearchService:
        """The injected research service, or the production DuckDuckGo one."""
        return self.research_service or build_research_service(
            max_sources=self.config.research_max_sources,
            tokens_per_source=self.config.research_tokens_per_source,
            tokens_total=self.config.research_tokens_total,
            fetch_timeout_s=self.config.research_fetch_timeout_s,
            brave_api_key=self.settings.brave_search_api_key,
        )

    async def _maybe_escalate(
        self,
        orchestrator: Orchestrator,
        prompt: str,
        category: str,
        candidates: list[ModelRef],
        result: VerdictResult,
        research_summary: ResearchSummary | None,
        research: bool | str,
        privacy: Privacy,
    ) -> None:
        """Resolve a factual disagreement with evidence instead of majority (T6).

        Only fires when the panel reported a material disagreement AND the run
        was not already grounded in sources: it runs research targeted at the
        disagreement text and, if that finds sources, spends exactly one extra
        synthesis round over the new evidence. Cost is bounded to that one round.
        """
        synthesis = result.final_synthesis
        if synthesis is None or not synthesis.material_disagreements:
            return
        already_grounded = (
            research_summary is not None and not research_summary.unavailable
        )
        if already_grounded:
            return
        mode = _research_mode(research)
        if (
            not self.config.research_enabled
            or mode == "off"
            or privacy is Privacy.LOCAL_ONLY
        ):
            return

        directed = "; ".join(synthesis.material_disagreements)
        directed_result = await self._research_service().investigate(directed)
        if not directed_result.sources:
            return
        today = datetime.date.today().isoformat()
        block = format_sources(directed_result, fetched_on=today)
        await orchestrator.resynthesise_with_context(
            prompt, result, candidates, category, block
        )
        result.research = directed_result.summary()

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

    def _save_report(self, result: VerdictResult) -> Path | None:
        if self.store is None:
            return None
        report = _full_report(result)
        name = f"verdict-{int(time.time())}-{result.task_type.value}"
        try:
            return save_report(verdict_reports_dir(), report, name=name)
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


def _research_mode(research: bool | str) -> str:
    """Normalise the research flag to ``"on" | "off" | "auto"``."""
    if research is True:
        return "on"
    if research is False:
        return "off"
    value = str(research).strip().lower()
    if value in ("on", "true", "yes", "1"):
        return "on"
    if value in ("off", "false", "no", "0"):
        return "off"
    return "auto"


def _iso(timestamp: float) -> str:
    return datetime.datetime.fromtimestamp(timestamp, tz=datetime.UTC).isoformat()


def _full_report(result: VerdictResult) -> dict[str, object]:
    """Serialise the entire deliberation for on-disk archival."""
    research = None
    if result.research is not None:
        research = {
            "backend": result.research.backend,
            "queries": result.research.queries,
            "sources_fetched": result.research.sources_fetched,
            "note": result.research.note,
        }
    return {
        "task_type": result.task_type.value,
        "depth": result.depth.value,
        "stop_reason": result.stop_reason,
        "research": research,
        "started_at": _iso(result.started_at),
        "finished_at": (
            _iso(result.finished_at) if result.finished_at is not None else None
        ),
        "elapsed_s": (
            round(result.finished_at - result.started_at, 1)
            if result.finished_at is not None
            else None
        ),
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
                "evidence": p.evidence,
                "risks": p.risks,
                "unknowns": p.unknowns,
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
                "elapsed_s": rnd.elapsed_s,
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
                    "minor_issues": rnd.critique.minor_issues,
                    "missing_evidence": rnd.critique.missing_evidence,
                },
            }
            for rnd in result.rounds
        ],
        "compact": result.compact(),
    }
