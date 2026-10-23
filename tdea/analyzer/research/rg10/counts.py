"""SmellCounter: FR-61's smell counts and rates for one RG10 output (sprint 23 Oct).

FR-61: the research profile, with every rule of the smell mapping also enabled, runs on
each modified module inside its variant copy (ResearchProfile.lint_in_tree, the one
isolated Pylint process; nothing is imported or run, NFR-26). Messages map to smell types
through the mapping by message ID, and only messages on added lines count. Per output
the counter returns:

* one detection per message (symbol, message ID, line, message, on-added-line flag);
* a symbol count for every symbol with at least one message on an added line;
* per smell type: the count, the declared opportunities, the target flag (the type is
  the output's own smell) and the rate = count / declared opportunities.

Rates exist for insertable, evaluable outputs only. A type with 0 declared opportunities
has no rate, with the reason recorded. A lint failure leaves counts null with reason
"lint failure". TDEA computes no observed-opportunity denominator, and RG10 lint results
are not cached.

CL-18 is open: FR-61 lints "each modified module" but rates only insertable, evaluable
outputs. Provisional reading (sprint 23 Oct): every output with a modified module is
linted and its detections and counts are stored; only the rate is withheld, with the
reason, for an output that is not insertable or not evaluable. An output without a
modified module (missing output, extraction none or unterminated) has no lint, so its
counts are null with that reason. Every output gets one type row per smell type, so no
output is dropped.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from tdea.analyzer.research.profile import OK, LintResult, ResearchProfile
from tdea.analyzer.research.rg10.bundle import LoadedBundle, OutputSpec
from tdea.analyzer.research.rg10.execution import (MISSING_OUTPUT, OK as EVALUABLE,
                                                   OutputEvaluation)
from tdea.analyzer.research.rg10.prepare import prepare_module

LINT_FAILURE = "lint failure"
NO_OPPORTUNITIES = "no declared opportunities"
NOT_INSERTABLE = "not insertable"
NO_MODULE = "no modified module"


@dataclass(frozen=True)
class Detection:
    """One Pylint message on a modified module (FR-61)."""

    symbol: str
    message_id: str
    line: int
    message: str
    on_added_line: bool


@dataclass(frozen=True)
class TypeCount:
    """One smell type's count and rate for an output (FR-61)."""

    smell_type: str
    count: int | None
    declared_opportunities: int
    is_target: bool
    rate: float | None
    exclusion_reason: str | None


@dataclass(frozen=True)
class OutputCounts:
    """Everything FR-61 records for one output."""

    gen_id: str
    detections: tuple[Detection, ...]
    symbol_counts: tuple[tuple[str, int], ...]  # sorted by symbol
    type_counts: tuple[TypeCount, ...]  # sorted by smell type


class SmellCounter:
    """Lints modified modules in their variant copies and counts mapped smells (FR-61)."""

    def __init__(self, profile: ResearchProfile,
                 smell_mapping: Sequence[tuple[str, tuple[str, ...]]]) -> None:
        """Use ``profile`` for linting and the checked (type, rules) mapping."""
        self._profile = profile
        self._mapping = tuple(smell_mapping)
        self._enable = tuple(sorted({rule for _type, rules in self._mapping for rule in rules}))

    def count(self, bundle: LoadedBundle, output: OutputSpec,
              evaluation: OutputEvaluation) -> OutputCounts:
        """Return the detections, symbol counts and type counts of one output."""
        task = bundle.tasks[output.task_id]
        declared = dict(task.declared_opportunities)
        if output.text is None:
            return OutputCounts(output.gen_id, (), (), self._types(output, declared, None,
                                                                   MISSING_OUTPUT))
        variant = bundle.variants[output.variant_key]
        prepared = prepare_module(bundle.modules[(variant.key, task.target_module)],
                                  task.marker, output.text)
        if prepared.text is None:
            return OutputCounts(output.gen_id, (), (), self._types(output, declared, None,
                                                                   NO_MODULE))
        result = self._profile.lint_in_tree(variant.root, task.target_module,
                                            prepared.text.encode("utf-8"), self._enable)
        if result.status != OK:
            return OutputCounts(output.gen_id, (), (), self._types(output, declared, None,
                                                                   LINT_FAILURE))
        detections = _detections(result, prepared.inserted_range)
        added = [detection for detection in detections if detection.on_added_line]
        symbols = tuple(sorted(Counter(detection.symbol for detection in added).items()))
        by_id = Counter(detection.message_id for detection in added)
        counts = {smell: sum(by_id[rule] for rule in rules) for smell, rules in self._mapping}
        rated = evaluation.match.evaluability == EVALUABLE and evaluation.match.insertable
        reason = None if rated else (NOT_INSERTABLE if evaluation.match.evaluability ==
                                     EVALUABLE else evaluation.match.evaluability)
        return OutputCounts(output.gen_id, detections, symbols,
                            self._types(output, declared, counts, reason))

    def _types(self, output: OutputSpec, declared: dict[str, int],
               counts: dict[str, int] | None, reason: str | None) -> tuple[TypeCount, ...]:
        """Return one row per smell type; a rate only when nothing excludes it."""
        rows = []
        for smell, _rules in self._mapping:
            opportunities = declared.get(smell, 0)
            count = None if counts is None else counts[smell]
            why = reason
            if why is None and opportunities == 0:
                why = NO_OPPORTUNITIES
            rate = None if why is not None or count is None else count / opportunities
            rows.append(TypeCount(smell, count, opportunities, smell == output.smell, rate, why))
        return tuple(rows)


def _detections(result: LintResult, span: tuple[int, int] | None) -> tuple[Detection, ...]:
    """Return every message as a detection, flagged when its line was added."""
    return tuple(sorted(
        (Detection(message.symbol, message.message_id, message.line, message.message,
                   span is not None and span[0] <= message.line <= span[1])
         for message in result.messages),
        key=lambda detection: (detection.line, detection.message_id, detection.message)))
