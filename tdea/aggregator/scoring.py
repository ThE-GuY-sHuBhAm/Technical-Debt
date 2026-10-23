"""Metric aggregation and debt scoring (FR-24 to FR-29, FR-47; sprint 23 Oct).

Per commit, aggregate() turns the file metrics into the repository-wide values the
charts and the score use: file count, total LOC and SLOC, mean cyclomatic complexity
over every function and method of the commit, mean Maintainability Index over its
files, smell totals by category and the mean smell density over its files.

score() then works across all analysed commits of one repository, oldest first:

* FR-24: min-max normalisation of SLOC, mean CC, mean MI, total smell count and mean
  smell density to 0.0-1.0 across the repository's commits.
* FR-25: DS = 100 x (0.30 CC_norm + 0.30 Smells_norm + 0.25 (1 - MI_norm)
  + 0.15 SLOC_growth_norm), the weights exactly as specified.
* FR-26: delta DS against the previous analysed commit; 0.0 for the first.
* FR-27: Debt Spike when delta > +5.0, Refactoring Drop when delta < -5.0, else Stable.

Open issues, followed as SRS Appendix B proposes them (not decided by the Product
Owner; each reading is one named function or constant, so a decision changes one place):

* OI-01: a metric with the same value in every commit normalises to 0.0 everywhere.
* OI-02: Smells_norm is the normalised mean smell density; SLOC_growth is the SLOC
  change from the previous analysed commit, 0 for the first, then min-max normalised.
* OI-03 (high_risk_files): files are ranked by the mean of their normalised mean CC and
  normalised mean smell density across all analysed commits; the dominant metric is the
  larger of the two; the risk class is High, Medium or Low by tercile of that mean
  among all files.
* OI-05 (trend): the sign of the least-squares slope of DS over commit sequence, with
  a dead band of TREND_DEAD_BAND DS points per commit for Stable.

Scores are rounded to ROUND_DIGITS decimal places when stored, so the same metrics give
byte-identical CSV exports on every machine (NFR-06).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from statistics import mean, pstdev

WEIGHTS = {"cc": 0.30, "smells": 0.30, "mi": 0.25, "sloc_growth": 0.15}
SPIKE_THRESHOLD = 5.0
SPIKE = "Debt Spike"
DROP = "Refactoring Drop"
STABLE = "Stable"
PATTERNS = (("Steady Growth", SPIKE, 3), ("Refactoring Event", DROP, 2),
            ("Stable Phase", STABLE, 5))
TREND_DEAD_BAND = 0.1  # OI-05 proposal: DS points per commit (provisional)
INCREASING, DECREASING = "Increasing", "Decreasing"
TOP_FILES = 10
ROUND_DIGITS = 6


@dataclass(frozen=True)
class FileSample:
    """The file metrics scoring needs: one file at one commit."""

    path: str
    sloc: int
    loc: int
    functions: tuple[int, ...]  # cyclomatic complexity of each function and method
    mean_cc: float
    mi: int
    convention: int
    refactor: int
    warning: int
    error: int
    smell_density: float

    @property
    def smells(self) -> int:
        """Return the file's counted Pylint messages."""
        return self.convention + self.refactor + self.warning + self.error


@dataclass(frozen=True)
class CommitAggregate:
    """Repository-wide values of one commit, and its score once score() has run."""

    sequence: int
    file_count: int
    total_loc: int
    total_sloc: int
    mean_cc: float
    mean_mi: float
    convention: int
    refactor: int
    warning: int
    error: int
    total_smells: int
    mean_smell_density: float
    sloc_growth: int = 0
    sloc_norm: float = 0.0
    cc_norm: float = 0.0
    mi_norm: float = 0.0
    smells_norm: float = 0.0
    density_norm: float = 0.0
    sloc_growth_norm: float = 0.0
    debt_score: float = 0.0
    debt_delta: float = 0.0
    classification: str = STABLE


@dataclass(frozen=True)
class RiskyFile:
    """One row of the high-risk table (FR-28, FR-36)."""

    path: str
    mean_cc: float
    mean_smell_density: float
    mean_mi: float
    risk_score: float
    dominant_metric: str
    risk_class: str


@dataclass(frozen=True)
class Pattern:
    """A temporal debt pattern and the commit sequence range it covers (FR-29)."""

    label: str
    first_sequence: int
    last_sequence: int
    length: int


@dataclass(frozen=True)
class Summary:
    """The statistical summary of one repository's scores (FR-47)."""

    mean_score: float
    peak_score: float
    spikes: int
    drops: int
    trend: str
    slope: float


def aggregate(sequence: int, files: Sequence[FileSample]) -> CommitAggregate:
    """Return the repository-wide values of one commit's files."""
    complexities = [value for item in files for value in item.functions]
    return CommitAggregate(
        sequence=sequence, file_count=len(files),
        total_loc=sum(item.loc for item in files),
        total_sloc=sum(item.sloc for item in files),
        mean_cc=_r(mean(complexities)) if complexities else 0.0,
        mean_mi=_r(mean(item.mi for item in files)) if files else 0.0,
        convention=sum(item.convention for item in files),
        refactor=sum(item.refactor for item in files),
        warning=sum(item.warning for item in files),
        error=sum(item.error for item in files),
        total_smells=sum(item.smells for item in files),
        mean_smell_density=_r(mean(item.smell_density for item in files)) if files else 0.0)


def normalise(values: Sequence[float]) -> list[float]:
    """Return min-max normalised values (FR-24); all 0.0 when max = min (OI-01)."""
    if not values:
        return []
    low, high = min(values), max(values)
    if high == low:
        return [0.0] * len(values)
    return [(value - low) / (high - low) for value in values]


def sloc_growth(commits: Sequence[CommitAggregate]) -> list[int]:
    """Return each commit's SLOC change from the previous one, 0 for the first (OI-02)."""
    return [0] + [later.total_sloc - earlier.total_sloc
                  for earlier, later in zip(commits, commits[1:])]


def classify(delta: float) -> str:
    """Return the FR-27 class of a debt delta."""
    if delta > SPIKE_THRESHOLD:
        return SPIKE
    if delta < -SPIKE_THRESHOLD:
        return DROP
    return STABLE


def score(commits: Sequence[CommitAggregate]) -> list[CommitAggregate]:
    """Return the commits (oldest first) with normalised values, DS, delta and class."""
    growth = sloc_growth(commits)
    columns = {
        "sloc_norm": normalise([item.total_sloc for item in commits]),
        "cc_norm": normalise([item.mean_cc for item in commits]),
        "mi_norm": normalise([item.mean_mi for item in commits]),
        "smells_norm": normalise([item.total_smells for item in commits]),
        "density_norm": normalise([item.mean_smell_density for item in commits]),
        "sloc_growth_norm": normalise(growth),
    }
    scored: list[CommitAggregate] = []
    for index, item in enumerate(commits):
        values = {name: _r(column[index]) for name, column in columns.items()}
        debt = 100 * (WEIGHTS["cc"] * values["cc_norm"]
                      + WEIGHTS["smells"] * values["density_norm"]
                      + WEIGHTS["mi"] * (1 - values["mi_norm"])
                      + WEIGHTS["sloc_growth"] * values["sloc_growth_norm"])
        debt = _r(debt)
        delta = _r(debt - scored[-1].debt_score) if scored else 0.0
        scored.append(replace(item, sloc_growth=growth[index], debt_score=debt,
                              debt_delta=delta, classification=classify(delta), **values))
    return scored


def patterns(classes: Sequence[tuple[int, str]]) -> list[Pattern]:
    """Return the FR-29 patterns in (sequence, class) pairs ordered by sequence."""
    found = []
    run_start = 0
    for index in range(1, len(classes) + 1):
        if index < len(classes) and classes[index][1] == classes[run_start][1]:
            continue
        label = classes[run_start][1]
        length = index - run_start
        for name, wanted, minimum in PATTERNS:
            if label == wanted and length >= minimum:
                found.append(Pattern(name, classes[run_start][0], classes[index - 1][0],
                                     length))
        run_start = index
    return found


def high_risk_files(samples: Iterable[FileSample], limit: int = TOP_FILES) -> list[RiskyFile]:
    """Return the highest-risk files across all analysed commits (FR-28, OI-03)."""
    by_path: dict[str, list[FileSample]] = {}
    for sample in samples:
        by_path.setdefault(sample.path, []).append(sample)
    if not by_path:
        return []
    paths = sorted(by_path)
    cc = [mean(item.mean_cc for item in by_path[path]) for path in paths]
    density = [mean(item.smell_density for item in by_path[path]) for path in paths]
    mi = [mean(item.mi for item in by_path[path]) for path in paths]
    cc_norm, density_norm = normalise(cc), normalise(density)
    risk = [(a + b) / 2 for a, b in zip(cc_norm, density_norm)]
    ordered = sorted(range(len(paths)), key=lambda index: (-risk[index], paths[index]))
    classes = _terciles(risk)
    return [RiskyFile(paths[index], _r(cc[index]), _r(density[index]), _r(mi[index]),
                      _r(risk[index]),
                      _dominant(cc_norm[index], density_norm[index]), classes[index])
            for index in ordered[:limit]]


def _dominant(cc_norm: float, density_norm: float) -> str:
    """Return the metric that contributes more to a file's risk ("none" when both are 0)."""
    if cc_norm == density_norm == 0:
        return "none"
    return "cyclomatic complexity" if cc_norm >= density_norm else "smell density"


def _terciles(values: Sequence[float]) -> list[str]:
    """Return High, Medium or Low for each value by its tercile among all values (OI-03).

    A value's share is the fraction of all values at or below it: above 2/3 is High,
    above 1/3 Medium, else Low. Ties share a class.
    """
    count = len(values)
    labels = []
    for value in values:
        share = sum(1 for other in values if other <= value) / count
        labels.append("High" if share > 2 / 3 else "Medium" if share > 1 / 3 else "Low")
    return labels


def slope(points: Sequence[tuple[int, float]]) -> float:
    """Return the least-squares slope of (sequence, score) points; 0.0 for fewer than 2."""
    if len(points) < 2:
        return 0.0
    xs = [float(x) for x, _y in points]
    ys = [y for _x, y in points]
    x_mean, y_mean = mean(xs), mean(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator == 0:
        return 0.0
    return sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denominator


def trend(value: float) -> str:
    """Return the OI-05 trend direction of a slope."""
    if value > TREND_DEAD_BAND:
        return INCREASING
    if value < -TREND_DEAD_BAND:
        return DECREASING
    return STABLE


def summary(commits: Sequence[CommitAggregate]) -> Summary:
    """Return FR-47's summary: mean and peak DS, spikes, drops and trend."""
    scores = [item.debt_score for item in commits]
    gradient = slope([(item.sequence, item.debt_score) for item in commits])
    return Summary(_r(mean(scores)) if scores else 0.0, max(scores, default=0.0),
                   sum(1 for item in commits if item.classification == SPIKE),
                   sum(1 for item in commits if item.classification == DROP),
                   trend(gradient), _r(gradient))


def statistics(values: Sequence[float]) -> tuple[float, float, float, float]:
    """Return mean, minimum, maximum and population standard deviation (FR-40 d)."""
    if not values:
        return (0.0, 0.0, 0.0, 0.0)
    return (_r(mean(values)), _r(min(values)), _r(max(values)), _r(pstdev(values)))


def _r(value: float) -> float:
    """Round a stored value to ROUND_DIGITS decimal places."""
    return round(float(value), ROUND_DIGITS)
