"""FR-24 to FR-29 and FR-47 scoring against hand-computed values (sprint 23 Oct).

Where the SRS leaves a rule open (OI-01, OI-02, OI-03, OI-05), the tests pin the
Appendix B proposal the code follows; they are marked "proposal" in their docstrings.
"""

from __future__ import annotations

import pytest

from tdea.aggregator.scoring import (DECREASING, DROP, INCREASING, SPIKE, STABLE,
                                     CommitAggregate, FileSample, aggregate, classify,
                                     high_risk_files, normalise, patterns, score, slope,
                                     statistics, summary, trend)


def commit(sequence: int, sloc: int, cc: float, mi: float, density: float,
           smells: int = 0) -> CommitAggregate:
    """Return an unscored commit aggregate."""
    return CommitAggregate(sequence, 1, sloc, sloc, cc, mi, smells, 0, 0, 0, smells, density)


def sample(path: str, cc: float, density: float, mi: int = 80,
           functions: tuple[int, ...] = ()) -> FileSample:
    """Return one file sample."""
    return FileSample(path, 10, 12, functions, cc, mi, 1, 0, 0, 0, density)


def test_aggregate_means_over_functions_and_files() -> None:
    """Mean CC over all functions of the commit; mean MI and density over its files."""
    files = [FileSample("a.py", 10, 12, (1, 3), 2.0, 80, 1, 2, 0, 0, 0.3),
             FileSample("b.py", 30, 40, (5,), 5.0, 60, 0, 0, 1, 1, 0.0667)]
    result = aggregate(4, files)
    assert (result.total_sloc, result.total_loc, result.file_count) == (40, 52, 2)
    assert result.mean_cc == 3.0 and result.mean_mi == 70.0
    assert (result.convention, result.refactor, result.warning, result.error) == (1, 2, 1, 1)
    assert result.total_smells == 5 and result.mean_smell_density == pytest.approx(0.18335)
    assert aggregate(1, []).mean_cc == 0.0


def test_min_max_normalisation_and_constant_metric() -> None:
    """FR-24; OI-01 proposal: a constant metric normalises to 0.0."""
    assert normalise([2, 4, 6]) == [0.0, 0.5, 1.0]
    assert normalise([3, 3, 3]) == [0.0, 0.0, 0.0]
    assert normalise([]) == []


def test_debt_score_delta_and_class_by_hand() -> None:
    """FR-25 to FR-27 with OI-02's proposal (density for smells, SLOC growth)."""
    scored = score([commit(1, 100, 1.0, 90.0, 0.10),
                    commit(2, 150, 3.0, 70.0, 0.30),
                    commit(3, 150, 2.0, 80.0, 0.20)])
    # Commit 1: CC 0, smells 0, MI_norm 1 (1 - 1 = 0), growth 0 of [0, 50, 0] -> DS 0.
    # Commit 2: CC 1, smells 1, MI_norm 0, growth 1 -> 100 x (0.30 + 0.30 + 0.25 + 0.15).
    # Commit 3: CC 0.5, smells 0.5, MI_norm 0.5, growth 0 -> 100 x (0.15 + 0.15 + 0.125).
    assert [item.debt_score for item in scored] == [0.0, 100.0, 42.5]
    assert [item.debt_delta for item in scored] == [0.0, 100.0, -57.5]
    assert [item.classification for item in scored] == [STABLE, SPIKE, DROP]
    assert [item.sloc_growth for item in scored] == [0, 50, 0]
    assert scored[1].sloc_norm == 1.0 and scored[2].smells_norm == 0.0


@pytest.mark.parametrize("delta, label", [(5.0, STABLE), (5.000001, SPIKE), (-5.0, STABLE),
                                          (-5.01, DROP), (0.0, STABLE)])
def test_classification_thresholds(delta: float, label: str) -> None:
    """FR-27: spike above +5.0, drop below -5.0, stable within."""
    assert classify(delta) == label


def test_patterns_need_their_run_lengths() -> None:
    """FR-29: 3 spikes, 2 drops, 5 stable commits in a row."""
    classes = [SPIKE, SPIKE, SPIKE, STABLE, DROP, DROP, STABLE, STABLE, STABLE, STABLE,
               STABLE, SPIKE, SPIKE]
    found = patterns(list(enumerate(classes, start=1)))
    assert [(item.label, item.first_sequence, item.last_sequence) for item in found] == [
        ("Steady Growth", 1, 3), ("Refactoring Event", 5, 6), ("Stable Phase", 7, 11)]
    assert not patterns([])


def test_high_risk_ranking_dominant_metric_and_terciles() -> None:
    """FR-28; OI-03 proposal: mean of normalised CC and density, terciles."""
    samples = [sample("a.py", 10.0, 0.0), sample("a.py", 6.0, 0.0),   # mean CC 8
               sample("b.py", 2.0, 0.5), sample("c.py", 2.0, 0.0),
               sample("d.py", 5.0, 0.25)]
    rows = high_risk_files(samples)
    # CC means 8, 2, 2, 5 -> norm 1, 0, 0, 0.5; density 0, .5, 0, .25 -> 0, 1, 0, .5.
    assert [(row.path, row.risk_score) for row in rows] == [
        ("a.py", 0.5), ("b.py", 0.5), ("d.py", 0.5), ("c.py", 0.0)]
    assert [row.dominant_metric for row in rows] == [
        "cyclomatic complexity", "smell density", "cyclomatic complexity", "none"]
    assert [row.risk_class for row in rows] == ["High", "High", "High", "Low"]
    spread = high_risk_files([sample("x.py", 1.0, 0.0), sample("y.py", 2.0, 0.0),
                              sample("z.py", 3.0, 0.0)])
    assert [row.risk_class for row in spread] == ["High", "Medium", "Low"]
    assert len(high_risk_files([sample(f"f{n}.py", n, 0.0) for n in range(15)])) == 10


def test_trend_and_summary() -> None:
    """FR-47; OI-05 proposal: slope sign with a dead band of 0.1 per commit."""
    assert slope([(1, 0.0), (2, 1.0), (3, 2.0)]) == 1.0
    assert (trend(0.2), trend(-0.2), trend(0.05)) == (INCREASING, DECREASING, STABLE)
    scored = score([commit(1, 100, 1.0, 90.0, 0.10), commit(2, 150, 3.0, 70.0, 0.30),
                    commit(3, 150, 2.0, 80.0, 0.20)])
    result = summary(scored)
    assert (result.mean_score, result.peak_score, result.spikes, result.drops) == (
        pytest.approx(47.5), 100.0, 1, 1)
    assert result.trend == INCREASING and result.slope == 21.25


def test_statistics_for_the_report() -> None:
    """FR-40 (d): mean, minimum, maximum and population standard deviation."""
    assert statistics([1.0, 2.0, 3.0]) == (2.0, 1.0, 3.0, pytest.approx(0.816497, abs=1e-6))
    assert statistics([]) == (0.0, 0.0, 0.0, 0.0)
