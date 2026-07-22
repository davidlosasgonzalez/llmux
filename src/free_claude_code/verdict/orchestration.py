"""The deliberation engine: propose -> review -> synthesise -> critique -> refine.

Depends only on a :class:`ModelInvoker`, so the CLI, the MCP server and the unit
tests drive identical logic. Anonymity is enforced here: reviewers and the
synthesiser only ever see labels (A, B, C, ...), never provider or model names.
"""

import asyncio
import datetime
import difflib
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger

from free_claude_code.core.quota import (
    QuotaTracker,
    classify_failure,
    retry_after_seconds,
)

from . import prompts
from .config import VerdictConfig
from .errors import DeliberationFailedError
from .invoker import InvocationResult, ModelInvoker
from .models import (
    Critique,
    Depth,
    FailureKind,
    ModelRef,
    Proposal,
    Review,
    Round,
    Synthesis,
    TaskType,
    Verdict,
    VerdictResult,
)
from .parsing import (
    parse_critique,
    parse_proposal,
    parse_review,
    parse_synthesis,
)
from .provider_limits import budget_multiplier
from .scoring import latency_penalty, score_model
from .selector import select_models
from .storage import ModelStats, VerdictStore

StatsLookup = Callable[[str, str], ModelStats]


@dataclass(frozen=True, slots=True)
class _LabeledProposal:
    label: str
    model: ModelRef
    proposal: Proposal


def _default_stats_lookup(model_key: str, category: str) -> ModelStats:
    return ModelStats(model_key=model_key, category=category)


class Orchestrator:
    """Runs the full verdict deliberation over an injected model invoker."""

    def __init__(
        self,
        invoker: ModelInvoker,
        config: VerdictConfig,
        *,
        store: VerdictStore | None = None,
        stats_lookup: StatsLookup | None = None,
        quota: QuotaTracker | None = None,
    ):
        self._invoker = invoker
        self._config = config
        self._store = store
        self._stats_lookup = stats_lookup or (
            store.stats_for if store is not None else _default_stats_lookup
        )
        self._quota = quota or QuotaTracker()
        # Models known to have exhausted their free quota earlier today; loaded
        # once per run so a fresh run skips them instead of re-hitting the 429.
        self._exhausted_today: set[str] = set()

    async def run(
        self,
        question: str,
        task_type: TaskType,
        candidates: list[ModelRef],
        *,
        depth: Depth | None = None,
        criteria: str = "",
        context: str = "",
        request_id: str = "verdict",
    ) -> VerdictResult:
        profile = self._config.depth_profile(depth)
        category = task_type.value
        result = VerdictResult(
            task_type=task_type,
            depth=depth or self._config.depth,
            started_at=time.time(),
        )
        if self._store is not None:
            self._exhausted_today = self._store.exhausted_keys(_today())
            # Skip models already out of quota today across every selection phase
            # (select_models does not route through _available).
            candidates = self._available(candidates)

        # --- Phase 3: independent proposals -------------------------------
        proponents = select_models(
            candidates,
            self._stats_lookup,
            self._config,
            category=category,
            count=profile.proponents,
            role="proponent",
        )
        labeled = await self._gather_proposals(
            question, task_type, proponents, criteria, context, request_id
        )
        if not labeled:
            raise DeliberationFailedError("No model produced a usable proposal.")
        result.proposals = [item.proposal for item in labeled]
        used_models = {item.model.key for item in labeled}
        used_providers = {item.model.provider for item in labeled}

        # --- Phase 4: anonymous cross-review ------------------------------
        proposal_families = {item.model.family for item in labeled}
        proposal_providers = {item.model.provider for item in labeled}
        reviewers = self._pick_panel(
            candidates,
            category,
            count=profile.reviewers,
            exclude_keys=used_models,
            avoid_families=proposal_families,
            avoid_providers=proposal_providers,
            role="reviewer",
        )
        result.reviews = await self._gather_reviews(
            question, labeled, reviewers, request_id
        )
        used_models.update(r.key for r in reviewers)
        used_providers.update(r.provider for r in reviewers)
        self._record_cross_review(labeled, result.reviews, category)

        # --- Phase 5-7: synthesis, critique, refinement -------------------
        synthesizer = self._pick_one(
            candidates,
            category,
            avoid_families=proposal_families,
            avoid_providers=set(),
            role="refiner",
        )
        proposals_by_label = {item.label: item.proposal for item in labeled}
        prior_critique: Critique | None = None
        last_score = -1.0
        stale_rounds = 0

        for index in range(profile.max_rounds):
            round_started_at = time.time()
            synthesis, synthesizer = await self._synthesise_with_fallback(
                question,
                proposals_by_label,
                result.reviews,
                synthesizer,
                prior_critique,
                category,
                request_id,
                index,
                context,
                candidates,
                proposal_families,
                set(),
            )
            used_models.add(synthesizer.key)
            used_providers.add(synthesizer.provider)

            critic = self._pick_one(
                candidates,
                category,
                avoid_families={synthesizer.family} | proposal_families,
                avoid_providers={synthesizer.provider},
                role="critic",
            )
            critique = await self._critique(
                question, synthesis, critic, category, request_id, index, context
            )
            used_models.add(critic.key)
            used_providers.add(critic.provider)

            if not critique.is_informative:
                # A degenerate critique (revise/reject, score 0, no issues) gives
                # no usable signal. Retry once with a different critic before we
                # give up on judging this round.
                alt = self._pick_one(
                    candidates,
                    category,
                    avoid_families={synthesizer.family} | proposal_families,
                    avoid_providers={synthesizer.provider},
                    role="critic",
                    exclude_keys=frozenset({critic.key}),
                )
                if alt.key != critic.key:
                    critique = await self._critique(
                        question, synthesis, alt, category, request_id, index, context
                    )
                    critic = alt
                    used_models.add(critic.key)
                    used_providers.add(critic.provider)

            result.rounds.append(
                Round(
                    index=index,
                    synthesis=synthesis,
                    critique=critique,
                    elapsed_s=round(time.time() - round_started_at, 1),
                )
            )

            if critique.verdict is Verdict.REJECT and self._store is not None:
                self._store.record_synthesis_rejected(synthesizer.key, category)

            if not critique.is_informative:
                # No trustworthy critic could judge this synthesis. Stop refining
                # rather than burning the remaining rounds against a null signal.
                result.stop_reason = "critique unavailable"
                break

            if self._is_acceptable(critique):
                result.stop_reason = "quality threshold met"
                break

            # If the synthesiser reproduced the previous answer almost verbatim,
            # further rounds cost calls without changing the text. Stop on this
            # textual convergence — a faster, score-independent signal than
            # waiting two stale rounds.
            if index > 0 and _texts_converged(
                result.rounds[index - 1].synthesis.final_answer,
                synthesis.final_answer,
                self._config.convergence_threshold,
            ):
                result.stop_reason = "synthesis converged"
                break

            improved = critique.score - last_score >= self._config.improvement_epsilon
            stale_rounds = 0 if improved else stale_rounds + 1
            last_score = critique.score
            prior_critique = critique

            if stale_rounds >= 2:
                result.stop_reason = "two rounds without material improvement"
                break
            if index + 1 >= profile.max_rounds:
                result.stop_reason = "max rounds reached"
                break

        result.models_used = sorted(used_models)
        result.providers_used = sorted(used_providers)
        result.finished_at = time.time()
        return result

    async def resynthesise_with_context(
        self,
        question: str,
        result: VerdictResult,
        candidates: list[ModelRef],
        category: str,
        context: str,
        request_id: str = "verdict",
    ) -> None:
        """Append one extra synthesis+critique round grounded in new evidence.

        Used by the service to escalate a factual disagreement (Phase 6/T6): it
        reuses the proposals and reviews already gathered, so only one synthesis
        and one critique are spent — never a full re-deliberation. Mutates
        ``result`` in place, adding a round and refreshing the timing/model sets.
        """
        if not result.proposals:
            return
        proposals_by_label = {
            _label(index): proposal for index, proposal in enumerate(result.proposals)
        }
        # Proposals only retain a model key, so avoid same-provider synthesis by
        # deriving providers from those keys (family avoidance is best-effort).
        proposal_providers = {p.model_key.split("/", 1)[0] for p in result.proposals}
        index = len(result.rounds)
        round_started_at = time.time()

        synthesizer = self._pick_one(
            candidates,
            category,
            avoid_families=set(),
            avoid_providers=proposal_providers,
            role="refiner",
        )
        synthesis, synthesizer = await self._synthesise_with_fallback(
            question,
            proposals_by_label,
            result.reviews,
            synthesizer,
            result.final_critique,
            category,
            request_id,
            index,
            context,
            candidates,
            set(),
            proposal_providers,
        )
        critic = self._pick_one(
            candidates,
            category,
            avoid_families={synthesizer.family},
            avoid_providers={synthesizer.provider},
            role="critic",
        )
        critique = await self._critique(
            question, synthesis, critic, category, request_id, index, context
        )
        result.rounds.append(
            Round(
                index=index,
                synthesis=synthesis,
                critique=critique,
                elapsed_s=round(time.time() - round_started_at, 1),
            )
        )
        result.models_used = sorted(
            set(result.models_used) | {synthesizer.key, critic.key}
        )
        result.providers_used = sorted(
            set(result.providers_used) | {synthesizer.provider, critic.provider}
        )
        result.stop_reason = "escalated with research"

    # ------------------------------------------------------------------ #
    # Phase 3
    # ------------------------------------------------------------------ #
    async def _gather_proposals(
        self,
        question: str,
        task_type: TaskType,
        proponents: list[ModelRef],
        criteria: str,
        context: str,
        request_id: str,
    ) -> list[_LabeledProposal]:
        system, user = prompts.propose_prompt(
            question, task_type, criteria=criteria, context=context
        )

        async def one(model: ModelRef) -> tuple[ModelRef, InvocationResult]:
            return model, await self._invoke(model, system, user, request_id, "propose")

        if self._config.parallel_proposals:
            pairs = await asyncio.gather(*(one(m) for m in proponents))
        else:
            pairs = [await one(m) for m in proponents]

        proposals: list[tuple[ModelRef, Proposal]] = []
        for model, invocation in pairs:
            proposal = (
                parse_proposal(model.key, invocation.text) if invocation.ok else None
            )
            self._record(model, "proposal", task_type.value, invocation, proposal)
            if proposal is not None and proposal.conclusion.strip():
                proposals.append((model, proposal))

        # Anonymise: order by content hash so provider identity/order never leaks.
        proposals.sort(key=lambda pair: _content_hash(pair[1].conclusion))
        return [
            _LabeledProposal(label=_label(index), model=model, proposal=proposal)
            for index, (model, proposal) in enumerate(proposals)
        ]

    # ------------------------------------------------------------------ #
    # Phase 4
    # ------------------------------------------------------------------ #
    async def _gather_reviews(
        self,
        question: str,
        labeled: list[_LabeledProposal],
        reviewers: list[ModelRef],
        request_id: str,
    ) -> list[Review]:
        if not reviewers:
            return []
        anon = {item.label: item.proposal.conclusion for item in labeled}
        system, user = prompts.review_prompt(question, anon)

        async def one(model: ModelRef) -> Review | None:
            invocation = await self._invoke(model, system, user, request_id, "review")
            review = parse_review(model.key, invocation.text) if invocation.ok else None
            # Record before returning: a failed review must still reach the
            # store (quota-exhaustion memory + selection stats), the same as
            # _gather_proposals already does. Skipping this on failure is why
            # a provider that only ever fails in review/synthesis/critique was
            # invisible to both cross-run quota memory and T2's scoring.
            self._record(model, "review", "review", invocation, review)
            if not invocation.ok:
                return None
            return review

        results = await asyncio.gather(*(one(m) for m in reviewers))
        return [review for review in results if review is not None]

    # ------------------------------------------------------------------ #
    # Phase 5 / 7
    # ------------------------------------------------------------------ #
    async def _synthesise(
        self,
        question: str,
        proposals_by_label: dict[str, Proposal],
        reviews: list[Review],
        synthesizer: ModelRef,
        prior_critique: Critique | None,
        category: str,
        request_id: str,
        index: int,
        context: str = "",
    ) -> Synthesis:
        system, user = prompts.synthesis_prompt(
            question,
            proposals_by_label,
            reviews,
            prior_critique=prior_critique,
            context=context,
        )
        invocation = await self._invoke(
            synthesizer, system, user, request_id, f"synthesis-{index}"
        )
        synthesis = (
            parse_synthesis(synthesizer.key, invocation.text) if invocation.ok else None
        )
        # Record before raising: a failed synthesis must still reach the store
        # (quota-exhaustion memory + selection stats), the same as
        # _gather_proposals already does.
        self._record(synthesizer, "synthesis", category, invocation, synthesis)
        if not invocation.ok:
            raise DeliberationFailedError(
                f"Synthesiser {synthesizer.key} failed: {invocation.detail}"
            )
        if synthesis is None or not synthesis.final_answer.strip():
            raise DeliberationFailedError(
                f"Synthesiser {synthesizer.key} returned no answer."
            )
        return synthesis

    async def _synthesise_with_fallback(
        self,
        question: str,
        proposals_by_label: dict[str, Proposal],
        reviews: list[Review],
        synthesizer: ModelRef,
        prior_critique: Critique | None,
        category: str,
        request_id: str,
        index: int,
        context: str,
        candidates: list[ModelRef],
        avoid_families: set[str],
        avoid_providers: set[str],
    ) -> tuple[Synthesis, ModelRef]:
        """Synthesise, retrying with a different model on provider failure.

        A single synthesiser timeout or provider-side rejection (e.g. a
        token-limit 413 tripped by a large research context) used to abort the
        whole deliberation, discarding every proposal and review already paid
        for. Mirrors the critic retry-on-uninformative-verdict a few lines up:
        exclude the failed model and try once more, bounded by the candidate
        pool so a fully-down provider set still surfaces the original error.
        """
        current = synthesizer
        tried: set[str] = set()
        max_attempts = min(3, len(candidates))
        for attempt in range(1, max_attempts + 1):
            tried.add(current.key)
            try:
                synthesis = await self._synthesise(
                    question,
                    proposals_by_label,
                    reviews,
                    current,
                    prior_critique,
                    category,
                    request_id,
                    index,
                    context,
                )
                return synthesis, current
            except DeliberationFailedError as exc:
                logger.warning(
                    "verdict.synthesis.failed model={} attempt={}/{} err={}",
                    current.key,
                    attempt,
                    max_attempts,
                    exc,
                )
                if attempt >= max_attempts:
                    raise
                nxt = self._pick_one(
                    candidates,
                    category,
                    avoid_families=avoid_families,
                    avoid_providers=avoid_providers,
                    role="refiner",
                    exclude_keys=frozenset(tried),
                )
                if nxt.key in tried:
                    raise
                current = nxt
        raise DeliberationFailedError(
            f"Synthesiser {synthesizer.key} failed and no alternate was available."
        )

    # ------------------------------------------------------------------ #
    # Phase 6
    # ------------------------------------------------------------------ #
    async def _critique(
        self,
        question: str,
        synthesis: Synthesis,
        critic: ModelRef,
        category: str,
        request_id: str,
        index: int,
        context: str = "",
    ) -> Critique:
        system, user = prompts.critique_prompt(question, synthesis, context=context)
        invocation = await self._invoke(
            critic, system, user, request_id, f"critique-{index}"
        )
        critique = (
            parse_critique(critic.key, invocation.text) if invocation.ok else None
        )
        # Record before returning: a failed critic must still reach the store
        # (quota-exhaustion memory + selection stats), the same as
        # _gather_proposals already does.
        self._record(critic, "critique", category, invocation, critique)
        if not invocation.ok or critique is None:
            # A missing critic must not pass a synthesis by default; force revise.
            return Critique(model_key=critic.key, verdict=Verdict.REVISE, score=0.0)
        return critique

    # ------------------------------------------------------------------ #
    # Invocation + stats
    # ------------------------------------------------------------------ #
    async def _invoke(
        self,
        model: ModelRef,
        system: str,
        user: str,
        request_id: str,
        phase: str,
    ) -> InvocationResult:
        if self._quota.is_blocked(model.provider):
            return InvocationResult.failure(
                model.key,
                classify_failure({"type": "rate_limited"}),
                detail=self._quota.block_reason(model.provider),
            )
        started_at = time.time()
        try:
            invocation = await asyncio.wait_for(
                self._invoker.invoke(
                    model,
                    system,
                    user,
                    max_tokens=self._config.max_tokens_per_call,
                    request_id=f"{request_id}:{phase}",
                ),
                timeout=self._config.call_timeout_s,
            )
        except TimeoutError:
            # A model that blows the per-call budget is a provider failure: it
            # trips the circuit breaker like any other, so the round can proceed
            # with the models that did answer instead of stalling on this one.
            self._quota.note_failure(model.provider, FailureKind.PROVIDER_FAILURE)
            logger.error(
                "verdict.invoke.timeout model={} phase={} timeout_s={}",
                model.key,
                phase,
                self._config.call_timeout_s,
            )
            return InvocationResult.failure(
                model.key,
                FailureKind.PROVIDER_FAILURE,
                detail=f"call exceeded {self._config.call_timeout_s:.0f}s timeout",
                latency_s=self._config.call_timeout_s,
            )
        except Exception as exc:
            kind = classify_failure(exc)
            self._quota.note_failure(
                model.provider, kind, retry_after=retry_after_seconds(exc)
            )
            logger.error(
                "verdict.invoke.error model={} phase={} kind={} detail={}",
                model.key,
                phase,
                kind.value,
                str(exc)[:500],
            )
            return InvocationResult.failure(model.key, kind, detail=str(exc))

        if invocation.ok:
            self._quota.note_success(model.provider)
        elif invocation.failure_kind is not None:
            self._quota.note_failure(model.provider, invocation.failure_kind)
        logger.info(
            "verdict.invoke.done model={} phase={} ok={} elapsed_s={}",
            model.key,
            phase,
            invocation.ok,
            round(time.time() - started_at, 1),
        )
        return invocation

    def _record(
        self,
        model: ModelRef,
        _phase: str,
        category: str,
        invocation: InvocationResult,
        parsed: object,
    ) -> None:
        if self._store is None:
            return
        rate_limited = (
            not invocation.ok
            and invocation.failure_kind is not None
            and invocation.failure_kind.value in ("rate_limited", "quota_exhausted")
        )
        self._store.record_invocation(
            model.key,
            category,
            ok=invocation.ok and parsed is not None,
            json_ok=parsed is not None,
            rate_limited=rate_limited,
            latency_s=invocation.latency_s,
        )
        # Usage/quota view: every call counts (failures burn free-tier quota too).
        self._store.record_usage(
            model.provider,
            model.key,
            _today(),
            input_tokens=invocation.input_tokens,
            output_tokens=invocation.output_tokens,
        )
        # Remember a hard quota exhaustion so later runs today skip this model.
        if invocation.failure_kind is FailureKind.QUOTA_EXHAUSTED:
            today = _today()
            self._store.record_exhaustion(model.key, model.provider, today)
            self._exhausted_today.add(model.key)

    def _record_cross_review(
        self,
        labeled: list[_LabeledProposal],
        reviews: list[Review],
        category: str,
    ) -> None:
        if self._store is None or not reviews:
            return
        label_to_model = {item.label: item.model.key for item in labeled}
        totals: dict[str, list[float]] = {label: [] for label in label_to_model}
        first_place: dict[str, int] = dict.fromkeys(label_to_model, 0)

        for review in reviews:
            ranking = [label for label in review.ranking if label in label_to_model]
            if not ranking:
                continue
            if ranking[0] in first_place:
                first_place[ranking[0]] += 1
            span = max(1, len(ranking) - 1)
            for position, label in enumerate(ranking):
                totals[label].append(1.0 - position / span)

        for label, scores in totals.items():
            if scores:
                self._store.record_cross_review_score(
                    label_to_model[label], category, sum(scores) / len(scores)
                )

        if first_place:
            best_label = max(first_place, key=lambda label: first_place[label])
            if first_place[best_label] > 0:
                self._store.record_selected_best(label_to_model[best_label], category)

    # ------------------------------------------------------------------ #
    # Selection helpers
    # ------------------------------------------------------------------ #
    def _available(self, models: list[ModelRef]) -> list[ModelRef]:
        """Drop models that are circuit-broken or already quota-exhausted today.

        Falls back to the full list if everything is filtered out, so selection
        never starves entirely.
        """
        live = [
            m
            for m in models
            if not self._quota.is_blocked(m.provider)
            and m.key not in self._exhausted_today
        ]
        return live or models

    def _pick_panel(
        self,
        candidates: list[ModelRef],
        category: str,
        *,
        count: int,
        exclude_keys: set[str],
        avoid_families: set[str],
        avoid_providers: set[str],
        role: str,
    ) -> list[ModelRef]:
        if count <= 0:
            return []
        available = self._available(candidates)
        pool = [m for m in available if m.key not in exclude_keys]
        # Prefer fresh models from unused families/providers, then any unused
        # model, and finally reuse proponents if the pool is too small to fill
        # the review panel — a model reviewing anonymised proposals is fine.
        preferred = [
            m
            for m in pool
            if m.family not in avoid_families and m.provider not in avoid_providers
        ]
        chosen: list[ModelRef] = []
        chosen_keys: set[str] = set()
        for source in (preferred, pool, available):
            for model in self._rank(source, category, role):
                if model.key not in chosen_keys:
                    chosen.append(model)
                    chosen_keys.add(model.key)
                if len(chosen) >= count:
                    return chosen
        return chosen

    def _pick_one(
        self,
        candidates: list[ModelRef],
        category: str,
        *,
        avoid_families: set[str],
        avoid_providers: set[str],
        role: str,
        exclude_keys: frozenset[str] = frozenset(),
    ) -> ModelRef:
        available = [
            m for m in self._available(candidates) if m.key not in exclude_keys
        ]
        preferred = [
            m
            for m in available
            if m.family not in avoid_families and m.provider not in avoid_providers
        ]
        ranked = self._rank(preferred or available, category, role)
        if ranked:
            return ranked[0]
        # exclude_keys may have emptied the pool; fall back to any candidate.
        return available[0] if available else candidates[0]

    def _rank(self, models: list[ModelRef], category: str, role: str) -> list[ModelRef]:
        # Refiner and critic are invoked in series up to max_rounds times, so a
        # slow model hurts wall-clock far more in those roles than as one of many
        # parallel proponents — apply the latency penalty a second time there.
        serial_role = role in ("refiner", "critic")

        def rank_key(model: ModelRef) -> float:
            stats = self._stats_lookup(model.key, category)
            score = score_model(model, stats) * budget_multiplier(model.provider, role)
            if serial_role:
                score *= latency_penalty(stats)
            return score

        return sorted(models, key=rank_key, reverse=True)

    def _is_acceptable(self, critique: Critique) -> bool:
        return (
            critique.verdict is Verdict.PASS
            and critique.score >= self._config.quality_threshold
            and not critique.critical_issues
            and not critique.material_issues
        )


def _label(index: int) -> str:
    return chr(ord("A") + index) if index < 26 else f"P{index}"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def _texts_converged(previous: str, current: str, threshold: float) -> bool:
    """True when two synthesis answers are near-identical after normalisation.

    Uses stdlib ``difflib`` (deterministic, no dependency) on whitespace- and
    case-normalised text so trivial reformatting does not read as a change.
    """
    prev, curr = _normalise(previous), _normalise(current)
    if not prev or not curr:
        return False
    return difflib.SequenceMatcher(None, prev, curr).ratio() >= threshold


def _today() -> str:
    """Local calendar day (YYYY-MM-DD) used to bucket daily usage."""
    return datetime.datetime.now().date().isoformat()
