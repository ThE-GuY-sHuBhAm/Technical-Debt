"""Workbook v1.2 metric and scoring cases, run against their expected values (23 Oct).

Each test is named after its case in the STATIC ANALYSIS (TC-034 to TC-051) and DEBT
SCORING (TC-068 to TC-085) sheets and asserts the workbook's EXPECTED RESULT as written.
Inputs the workbook describes but does not ship (calc.py, smells_*.py, a 4-file
repository) are built here from the case's own description; the module docstring of
each builder says how. Radon and Pylint are the pinned versions; the "radon CLI" and
"pylint JSON" oracles of TC-034, TC-041 and TC-043 to TC-046 run those tools directly.

The workbook names FX-M and FX-RISK but ships neither, and states only "50 commits" and
"15 files, 3 of them deliberately complex and smelly". TC-069, TC-073, TC-080 and TC-082
run on repositories built here to exactly that description (fx_m_files, fx_risk_files),
not on an official fixture. Not executed here: TC-052 and TC-053 (FX-L, measured in the
browser) and TC-054 (FX-100, progress values received by the frontend).
TC-048 is not run as written: its "empty __init__.py" contradicts TC-051, see
test_tc048_sloc_zero_file_has_density_zero. The workbook's ACTUAL RESULT and STATUS
columns are not changed by these tests.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

import make_fx_product
import pytest
from product_support import FX_ERR_URL, FX_S_URL, local_service, analysed
from repo_builder import RepoBuilder

from tdea.aggregator import results
from tdea.aggregator.analysis_service import AnalysisService
from tdea.aggregator.scoring import (DROP, SPIKE, STABLE, classify, debt_deltas, debt_score,
                                     normalise, patterns)
from tdea.analyzer.product_metrics import (FileMetrics, PylintCounter, RadonResult,
                                           SmellCounts, measure)

CATEGORIES = ("convention", "refactor", "warning", "error")


def calc_py() -> str:
    """Return calc.py as TC-034 describes it: 50 lines, 8 comment lines, 7 blank lines."""
    lines = ["# calc.py: small arithmetic helpers.", "# Used by the TC-034 fixture.",
             "import math", "", ""]
    for number in range(5):  # 6 code lines, 1 comment line and 1 blank line each
        lines += [f"def step_{number}(value):", "    total = value + 1",
                  f"    total = total + {number}", "    total = math.floor(total * 2)",
                  "    total = abs(total)", "    return total", f"# end of step_{number}", ""]
    lines += ["# Module-level results.", *(f"RESULT_{n} = step_{n}({n})" for n in range(4))]
    return "\n".join(lines) + "\n"


def pylint_json(folder: Path, name: str) -> dict[str, int]:
    """Return the per-category message counts of `pylint --output-format=json`."""
    completed = subprocess.run(
        [sys.executable, "-m", "pylint", "--output-format=json", "--persistent=n", name],
        cwd=folder, capture_output=True, text=True, check=False)
    counts = dict.fromkeys(CATEGORIES, 0)
    for message in json.loads(completed.stdout or "[]"):
        if message["type"] in counts:
            counts[message["type"]] += 1
    return counts


def tdea_counts(folder: Path, name: str) -> dict[str, int]:
    """Return TDEA's per-category counts for one file (FR-14)."""
    found = PylintCounter().count(folder, [name])[name]
    return {category: getattr(found, category) for category in CATEGORIES}


def radon_cli(command: str, path: Path) -> dict:
    """Return `radon <command> -j` output for one file."""
    completed = subprocess.run([sys.executable, "-m", "radon", command, "-j", str(path)],
                               capture_output=True, text=True, check=True)
    return json.loads(completed.stdout)[str(path)]


# STATIC ANALYSIS -------------------------------------------------------------------

def test_tc034_loc_metrics_match_the_case_and_radon_cli(tmp_path: Path) -> None:
    """TC-034: stored loc 50, sloc 35, comments 8, blank 7; identical to `radon raw`."""
    builder = RepoBuilder(tmp_path / "calc")
    builder.commit("Add calc.py", {"calc.py": calc_py()})
    service = local_service(tmp_path / "ws", {"tdea-fixtures/calc": builder.path})
    submission = analysed(service, "https://github.com/tdea-fixtures/calc")
    ((_commit, row),) = service.store.files(submission.repository_id, path="calc.py")
    assert (row.loc, row.sloc, row.comments, row.blank) == (50, 35, 8, 7)
    cli = radon_cli("raw", builder.path / "calc.py")
    assert (cli["loc"], cli["sloc"], cli["comments"], cli["blank"]) == (50, 35, 8, 7)


@pytest.fixture(name="fx_s", scope="module")
def _fx_s(tmp_path_factory: pytest.TempPathFactory) -> make_fx_product.BuiltFixture:
    """Build FX-S once for this module."""
    return make_fx_product.build_fx_s(tmp_path_factory.mktemp("fx") / "fx-s")


def test_tc035_only_python_files_get_metric_rows(tmp_path: Path) -> None:
    """TC-035: README.md, setup.cfg and main.py; only main.py has metric rows."""
    builder = RepoBuilder(tmp_path / "mixed")
    builder.commit("Add files", {"README.md": "# Demo\n", "setup.cfg": "[metadata]\nname = d\n",
                                 "main.py": '"""Main."""\n\nVALUE = 1\n'})
    service = local_service(tmp_path / "ws", {"tdea-fixtures/mixed": builder.path})
    submission = analysed(service, "https://github.com/tdea-fixtures/mixed")
    files = results.commit_files(service.store, submission.repository_id, builder.head())
    assert [item["path"] for item in files["files"]] == ["main.py"]


def test_tc036_loc_is_recorded_per_commit(tmp_path: Path) -> None:
    """TC-036: util.py of 20 lines at #3 and 45 lines at #4 gives two separate rows."""
    builder = RepoBuilder(tmp_path / "grow")
    builder.commit("One", {"a.py": "A = 1\n"})
    builder.commit("Two", {"b.py": "B = 2\n"})
    util = [f"V{number} = {number}" for number in range(45)]
    builder.commit("Three", {"util.py": "\n".join(util[:20]) + "\n"})
    builder.commit("Four", {"util.py": "\n".join(util) + "\n"})
    service = local_service(tmp_path / "ws", {"tdea-fixtures/grow": builder.path})
    submission = analysed(service, "https://github.com/tdea-fixtures/grow")
    rows = service.store.metric(submission.repository_id, "loc", None, "util.py")
    assert [(sequence, value) for sequence, _sha, _path, value in rows] == [(3, 20), (4, 45)]


def test_tc037_simple_function_is_cc_1_grade_a() -> None:
    """TC-037: def greet(name): return f"Hi {name}" gives cc = 1, grade = A."""
    (function,) = measure(b'def greet(name): return f"Hi {name}"\n').functions
    assert (function.complexity, function.rank) == (1, "A")


def branches(name: str, count: int) -> str:
    """Return a function with ``count`` if statements, so its CC is count + 1."""
    body = "".join(f"    if value == {number}:\n        return {number}\n"
                   for number in range(count))
    return f"def {name}(value):\n{body}    return -1\n"


def test_tc038_grade_boundary_between_a_and_b() -> None:
    """TC-038: CC 5 is grade A and CC 6 is grade B."""
    result = measure((branches("f1", 4) + "\n\n" + branches("f2", 5)).encode())
    assert [(item.name, item.complexity, item.rank) for item in result.functions] == [
        ("f1", 5, "A"), ("f2", 6, "B")]


def test_tc039_very_complex_function_is_grade_f() -> None:
    """TC-039: a function with CC = 42 gets grade F."""
    (function,) = measure(branches("huge", 41).encode()).functions
    assert (function.complexity, function.rank) == (42, "F")


def test_tc040_each_method_is_analysed_separately() -> None:
    """TC-040: class Cart with add(), remove(), total() gives three CC rows."""
    source = ("class Cart:\n    def add(self, item):\n        self.items.append(item)\n\n"
              "    def remove(self, item):\n        if item in self.items:\n"
              "            self.items.remove(item)\n\n"
              "    def total(self):\n        return sum(self.items) if self.items else 0\n")
    rows = [(item.name, item.complexity, item.rank) for item in measure(source.encode()).functions]
    assert rows == [("Cart.add", 1, "A"), ("Cart.remove", 2, "A"), ("Cart.total", 2, "A")]


def test_tc041_mi_is_an_integer_0_to_100_equal_to_rounded_radon_cli(fx_s, tmp_path: Path
                                                                    ) -> None:
    """TC-041: FX-S, 5 files; MI integers 0-100 equal to rounded `radon mi -s` values."""
    service = local_service(tmp_path / "ws", {"tdea-fixtures/fx-s": fx_s.path})
    submission = analysed(service, FX_S_URL)
    head = results.commit_files(service.store, submission.repository_id, fx_s.shas[-1])
    stored = {item["path"]: item["mi"] for item in head["files"]}
    checked = sorted(path for path in stored if (fx_s.path / path).stat().st_size)[:5]
    assert len(checked) == 5
    for path in checked:
        value = stored[path]
        assert isinstance(value, int) and 0 <= value <= 100
        assert value == round(radon_cli("mi", fx_s.path / path)["mi"]), path


def test_tc042_trivial_file_has_mi_100() -> None:
    """TC-042: const.py containing only x = 1 has mi = 100."""
    assert measure(b"x = 1\n").mi == 100


SMELL_FILES = {
    # TC-043: no module docstring and 2 badly named variables (expected 3 C messages).
    "smells_c.py": ('def compute(value):\n    """Return a value."""\n'
                    "    BadOne = value + 1\n    BadTwo = BadOne * 2\n    return BadTwo\n"),
    # TC-044: one function with 14 branches (too-many-branches).
    "smells_r.py": ('"""Branches."""\n\n\n' + "def choose(value):\n"
                    + '    """Return a code."""\n'
                    + "".join(f"    if value == {number}:\n        print({number})\n"
                              for number in range(14))
                    + "    return value\n"),
    # TC-045: an unused import and an unused variable.
    "smells_w.py": ('"""Unused things."""\n\nimport os\n\n\n'
                    'def run():\n    """Do nothing useful."""\n    unused = 1\n'),
    # TC-046: two undefined names.
    "smells_e.py": ('"""Undefined names."""\n\n\n'
                    'def run():\n    """Use names that do not exist."""\n'
                    "    return first_missing + second_missing\n"),
}


@pytest.mark.parametrize("name, category, expected", [
    ("smells_c.py", "convention", 3), ("smells_r.py", "refactor", None),
    ("smells_w.py", "warning", 2), ("smells_e.py", "error", 2)])
def test_tc043_to_tc046_smell_counts_equal_pylint_json(tmp_path: Path, name: str,
                                                       category: str, expected: int | None
                                                       ) -> None:
    """TC-043 C = 3, TC-044 R >= 1, TC-045 W = 2, TC-046 E = 2; each equals pylint JSON."""
    (tmp_path / name).write_text(SMELL_FILES[name], encoding="utf-8")
    ours, oracle = tdea_counts(tmp_path, name), pylint_json(tmp_path, name)
    assert ours[category] == oracle[category]
    if expected is None:
        assert ours[category] >= 1
    else:
        assert ours[category] == expected


def test_tc043_to_tc046_stored_counts_equal_pylint_json(tmp_path: Path) -> None:
    """TC-043 to TC-046 end to end: the stored counts of the analysed commit equal pylint JSON."""
    builder = RepoBuilder(tmp_path / "smells")
    builder.commit("Add smelly files", SMELL_FILES)
    service = local_service(tmp_path / "ws", {"tdea-fixtures/smells": builder.path})
    submission = analysed(service, "https://github.com/tdea-fixtures/smells")
    stored = {row.path: row for _commit, row in service.store.files(submission.repository_id)}
    assert sorted(stored) == sorted(SMELL_FILES)
    for name in SMELL_FILES:
        oracle = pylint_json(builder.path, name)
        assert {category: getattr(stored[name], category) for category in CATEGORIES} == oracle
    assert (stored["smells_c.py"].convention, stored["smells_w.py"].warning,
            stored["smells_e.py"].error) == (3, 2, 2)
    assert stored["smells_r.py"].refactor >= 1


def test_tc047_smell_density_is_issues_over_sloc() -> None:
    """TC-047: 12 issues and 80 SLOC give smell_density = 0.15."""
    radon = RadonResult(loc=80, sloc=80, comments=0, blank=0, functions=(), mi=60)
    assert FileMetrics("f.py", radon, SmellCounts(3, 3, 3, 3)).smell_density == 0.15


def test_tc048_sloc_zero_file_has_density_zero() -> None:
    """TC-048 (partly): a file with SLOC = 0 gets density 0.0 without a ZeroDivisionError.

    NOT the case as written. TC-048 names an *empty* __init__.py, while TC-051 (and FR-16)
    expect an empty (0-byte) .py file to be logged and skipped, which is what TDEA does.
    Here a non-empty __init__.py with SLOC 0 (a comment only) is used; the conflict is
    recorded as an open decision.
    """
    metrics = FileMetrics("pkg/__init__.py", measure(b"# package marker\n"), SmellCounts(1))
    assert metrics.radon.sloc == 0 and metrics.smell_density == 0.0


def test_tc049_to_tc051_failing_files_are_logged_and_skipped(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """TC-049 SyntaxError, TC-050 UnicodeDecodeError, TC-051 empty and binary .py files."""
    fx_err = make_fx_product.build_fx_err(tmp_path / "fx-err")
    service = local_service(tmp_path / "ws", {"tdea-fixtures/fx-err": fx_err.path})
    with caplog.at_level(logging.WARNING):
        submission = analysed(service, FX_ERR_URL)
    assert service.store.repository(submission.repository_id).status == "Complete"
    sha = fx_err.shas[0]
    logged = {(record.args[0], record.args[1], record.args[2]) for record in caplog.records
              if record.getMessage().startswith("File-level analysis failure")}
    assert ("fxerr/syntax_error.py", sha, "SyntaxError") in logged  # TC-049
    assert ("fxerr/latin1.py", sha, "UnicodeDecodeError") in logged  # TC-050
    assert {path for path, _sha, _kind in logged} >= {"fxerr/empty.py", "fxerr/binary.py"}
    files = results.commit_files(service.store, submission.repository_id, sha)["files"]
    assert [item["path"] for item in files] == ["fxerr/valid.py"]  # the others analysed


# DEBT SCORING ---------------------------------------------------------------------

def test_tc068_min_max_normalisation() -> None:
    """TC-068: SLOC [100, 150, 200] normalise to [0.0, 0.5, 1.0]."""
    assert normalise([100, 150, 200]) == [0.0, 0.5, 1.0]


def fx_m_files(number: int) -> dict[str, str]:
    """Return the files of commit ``number`` (1-50) of the FX-M stand-in.

    One module changes in every commit; its function count, branching and unused imports
    follow fixed cycles, so SLOC, complexity, MI and smells all vary over the 50 commits.
    """
    imports = "".join(f"import {name}\n" for name in ("os", "re", "json")[:number % 4])
    functions = []
    for index in range(1 + number % 7):
        branches = "".join(f"    if value == {level}:\n        return {level}\n"
                           for level in range(number % 5 + index % 3))
        functions.append(f"def f{index}(value):\n{branches}    return value\n")
    return {"fxm/core.py": imports + "\n\n" + "\n\n".join(functions)}


@pytest.fixture(name="fx_m", scope="module")
def _fx_m(tmp_path_factory: pytest.TempPathFactory) -> tuple[AnalysisService, int]:
    """Build and analyse the FX-M stand-in (50 commits) once for this module."""
    root = tmp_path_factory.mktemp("fxm")
    builder = RepoBuilder(root / "fx-m")
    builder.commit("Start", {"fxm/__init__.py": '"""FX-M stand-in."""\n', **fx_m_files(1)})
    for number in range(2, 51):
        builder.commit(f"Change {number}", fx_m_files(number))
    service = local_service(root / "ws", {"tdea-fixtures/fx-m": builder.path})
    return service, analysed(service, "https://github.com/tdea-fixtures/fx-m").repository_id


def test_tc069_all_five_metrics_are_normalised_for_every_commit(fx_m) -> None:
    """TC-069: SLOC, CC, MI, smells and density lie in 0.0-1.0 with min 0.0 and max 1.0."""
    service, repository_id = fx_m
    rows = [aggregate for _commit, aggregate in service.store.commits(repository_id)]
    assert len(rows) == 50
    for column in ("sloc_norm", "cc_norm", "mi_norm", "smells_norm", "density_norm"):
        values = [getattr(row, column) for row in rows]
        assert all(0.0 <= value <= 1.0 for value in values), column
        assert (min(values), max(values)) == (0.0, 1.0), column


def test_tc073_every_stored_debt_score_is_within_0_and_100(fx_m) -> None:
    """TC-073: 0 <= debt_score <= 100 for every row, and no NULL values."""
    service, repository_id = fx_m
    scores = [aggregate.debt_score for _commit, aggregate in service.store.commits(repository_id)]
    assert len(scores) == 50
    assert all(score is not None and 0.0 <= score <= 100.0 for score in scores)


def test_tc070_constant_metric_normalises_without_error() -> None:
    """TC-070: MI [80, 80, 80] gives all 0.0 (the workbook asks to confirm; OI-01)."""
    assert normalise([80, 80, 80]) == [0.0, 0.0, 0.0]


def test_tc071_weights() -> None:
    """TC-071: CC 0.5, Smells 0.4, MI 0.7, growth 0.2 give DS = 37.5."""
    assert debt_score(0.5, 0.4, 0.7, 0.2) == 37.5


def test_tc072_extremes() -> None:
    """TC-072: best case DS = 0.0, worst case DS = 100.0."""
    assert debt_score(0, 0, 1, 0) == 0.0
    assert debt_score(1, 1, 0, 1) == 100.0


def test_tc074_first_commit_delta_is_zero(fx_s, tmp_path: Path) -> None:
    """TC-074: FX-S, debt_delta = 0.0 at commit sequence 1."""
    service = local_service(tmp_path / "ws", {"tdea-fixtures/fx-s": fx_s.path})
    submission = analysed(service, FX_S_URL)
    assert results.series(service.store, submission.repository_id)[0]["debt_delta"] == 0.0


def test_tc075_signed_deltas() -> None:
    """TC-075: DS [42.0, 48.5, 40.0] give deltas [0.0, +6.5, -8.5]."""
    assert debt_deltas([42.0, 48.5, 40.0]) == [0.0, 6.5, -8.5]


@pytest.mark.parametrize("case, delta, label", [
    ("TC-076", 6.5, SPIKE), ("TC-077", -8.5, DROP), ("TC-078", 5.0, STABLE),
    ("TC-078", -5.0, STABLE), ("TC-079", 5.01, SPIKE), ("TC-079", -5.01, DROP)])
def test_tc076_to_tc079_classification(case: str, delta: float, label: str) -> None:
    """TC-076 to TC-079: Spike above +5.0, Drop below -5.0, Stable at exactly +-5.0."""
    assert classify(delta) == label, case
    assert label in ("Debt Spike", "Refactoring Drop", "Stable")  # the workbook's names


def test_tc081_fewer_than_ten_files(tmp_path: Path) -> None:
    """TC-081: a repository with 4 .py files lists all 4, with no placeholder rows."""
    builder = RepoBuilder(tmp_path / "four")
    builder.commit("Four files", {f"m{number}.py": "".join(
        f"def f{index}(value):\n    return value + {index}\n\n\n" for index in range(number + 1))
        for number in range(4)})
    service = local_service(tmp_path / "ws", {"tdea-fixtures/four": builder.path})
    submission = analysed(service, "https://github.com/tdea-fixtures/four")
    rows = results.high_risk(service.store, submission.repository_id)
    assert sorted(row["path"] for row in rows) == ["m0.py", "m1.py", "m2.py", "m3.py"]
    assert all(row["path"] and row["risk_class"] for row in rows)


COMPLEX_FILES = ("risk/parser.py", "risk/rules.py", "risk/engine.py")


def fx_risk_files() -> dict[str, str]:
    """Return the FX-RISK stand-in: 12 simple, clean modules and 3 complex, smelly ones."""
    files = {f"risk/simple_{index:02d}.py": f'"""Simple module {index}."""\n\n\n'
             f'def value_{index}():\n    """Return {index}."""\n    return {index}\n'
             for index in range(12)}
    for rank, path in enumerate(COMPLEX_FILES):
        branches = "".join(f"    if X == {level}:\n        Y = {level}\n"
                           for level in range(12 + 4 * rank))
        files[path] = f"import os\nimport sys\n\n\ndef Check(X):\n{branches}    return Y\n"
    return files


def test_tc080_tc082_complex_files_rank_first_with_their_dominant_metric(
        tmp_path: Path) -> None:
    """TC-080: 10 rows, the 3 complex files at ranks 1-3. TC-082: dominant metric shown."""
    builder = RepoBuilder(tmp_path / "fx-risk")
    builder.commit("Fifteen files", fx_risk_files())
    service = local_service(tmp_path / "ws", {"tdea-fixtures/fx-risk": builder.path})
    repository_id = analysed(service, "https://github.com/tdea-fixtures/fx-risk").repository_id
    rows = results.high_risk(service.store, repository_id)
    assert len(rows) == 10
    assert {row["path"] for row in rows[:3]} == set(COMPLEX_FILES)
    files = [item for _commit, item in service.store.files(repository_id)]
    assert len(files) == 15
    cc = normalise([item.mean_cc for item in files])
    density = normalise([item.smell_density for item in files])
    by_path = {item.path: (a, b) for item, a, b in zip(files, cc, density)}
    for row in rows:
        cc_norm, density_norm = by_path[row["path"]]
        expected = ("None" if cc_norm == density_norm == 0
                    else "Complexity" if cc_norm >= density_norm else "Smell density")
        assert row["dominant_metric"] == expected, row["path"]
    assert {row["dominant_metric"] for row in rows[:3]} <= {"Complexity", "Smell density"}


def labels(*classes: str) -> list[tuple[str, int, int]]:
    """Return the patterns found in a class sequence, as (label, first, last)."""
    return [(item.label, item.first_sequence, item.last_sequence)
            for item in patterns(list(enumerate(classes, start=1)))]


def test_tc083_steady_growth_needs_three_spikes() -> None:
    """TC-083: Spike x3 is Steady Growth; Spike, Spike, Stable has no pattern."""
    assert labels(SPIKE, SPIKE, SPIKE) == [("Steady Growth", 1, 3)]
    assert labels(SPIKE, SPIKE, STABLE) == []


def test_tc084_two_drops_are_a_refactoring_event() -> None:
    """TC-084: Stable, Drop, Drop, Stable labels commits 2-3 Refactoring Event."""
    assert labels(STABLE, DROP, DROP, STABLE) == [("Refactoring Event", 2, 3)]


def test_tc085_stable_phase_needs_five_stable_commits() -> None:
    """TC-085: 5 x Stable is a Stable Phase; 4 x Stable then Spike is not."""
    assert labels(*[STABLE] * 5) == [("Stable Phase", 1, 5)]
    assert labels(*[STABLE] * 4, SPIKE) == []
