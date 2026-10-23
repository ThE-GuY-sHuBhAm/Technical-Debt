"""Metric validation: check an analysed repository's stored metrics against oracles.

For each chosen analysed commit, the commit's tree is exported from a local clone of the
same repository (``git archive``) and every stored file row is compared with:

* ``radon raw -j``: LOC, SLOC, comment and blank lines (FR-11);
* ``radon cc -j``: the complexity of each function and method, then mean and maximum
  (FR-12). The radon CLI does not list the methods of a class nested in another class;
  TDEA does. Such functions are checked to be inside a nested class (by parsing the
  file, never importing it) and are reported apart;
* ``radon mi -j -s``: the Maintainability Index, rounded half to even and clamped to
  0..100 (FR-13);
* ``pylint --output-format=json`` with Pylint's default configuration, as TDEA runs it:
  the C, R, W and E message counts, the total and the smell density (FR-14, FR-15).
  Pylint's answer for a file depends on which files are linted with it (duplicate-code
  is found across files; an import of another module of the repository resolves only
  when that module's package is linted too). So it runs twice: on all the commit's .py
  files at once ("whole tree") and on each file alone ("file alone"). TDEA lints the
  new file versions of a commit together and reuses a version's counts while it stays
  unchanged; the two results bracket that.

Then the stored repository rows are recomputed from the stored values with independent
arithmetic: aggregates from the file rows, min-max normalisation (FR-24, OI-01), the
Debt Score with the SRS weights (FR-25), the delta (FR-26) and the classification at
+/-5.0 (FR-27). The stored column smells_norm is the normalised total smell count; the
Debt Score's Smells_norm is the normalised mean smell density, stored as density_norm
(OI-02 proposal).

Nothing in the analysed repository is imported or run. Usage::

    python tools/validate_metrics.py --db tdea.db --repository owner/name
        --clone PATH [--commits 5 | --commits all] [--pylint both|tree|file]
        [--out report.json]

Exit status: 0 every compared value agrees; 1 at least one value differs (listed in
the report); 2 the inputs cannot be used.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

EXIT_AGREE, EXIT_DIFFER, EXIT_UNUSABLE = 0, 1, 2
CATEGORIES = {"convention": "convention", "refactor": "refactor", "warning": "warning",
              "error": "error"}
WEIGHTS = {"cc": 0.30, "smells": 0.30, "mi": 0.25, "sloc_growth": 0.15}  # SRS FR-25
THRESHOLD = 5.0  # FR-27
TOLERANCE = 1e-6
ROUNDING = 1e-5  # stored aggregates are rounded to 6 decimal places (scoring.ROUND_DIGITS)
BATCH = 100


class Unusable(Exception):
    """The database, repository or clone cannot be used."""


class Report:
    """Counts of compared and differing values per check, with examples of differences."""

    def __init__(self) -> None:
        """Start empty."""
        self.compared: Counter[str] = Counter()
        self.differing: Counter[str] = Counter()
        self.examples: dict[str, list[dict]] = defaultdict(list)
        self.notes: Counter[str] = Counter()

    def check(self, name: str, stored: object, expected: object, where: dict,
              tolerance: float = 0.0) -> None:
        """Record one comparison of a stored value with its oracle value."""
        self.compared[name] += 1
        if isinstance(stored, (int, float)) and isinstance(expected, (int, float)):
            same = abs(float(stored) - float(expected)) <= tolerance
        else:
            same = stored == expected
        if not same:
            self.differing[name] += 1
            if len(self.examples[name]) < 10:
                self.examples[name].append({**where, "stored": stored, "oracle": expected})

    def as_dict(self) -> dict:
        """Return the report as plain data."""
        return {"checks": {name: {"compared": self.compared[name],
                                  "differing": self.differing[name],
                                  "examples": self.examples.get(name, [])}
                           for name in sorted(self.compared)},
                "notes": dict(self.notes)}


def _run(command: Sequence[str], cwd: Path | None = None,
         env: dict[str, str] | None = None) -> str:
    """Run a command and return its standard output."""
    completed = subprocess.run(list(command), cwd=cwd, env=env, capture_output=True,
                               text=True, encoding="utf-8", check=False)
    return completed.stdout


def load(db: Path, full_name: str) -> tuple[list[dict], dict[int, list[dict]]]:
    """Return the repository's analysed commits and their file rows from tdea.db."""
    if not db.is_file():
        raise Unusable(f"no database at {db}")
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    with connection:
        found = connection.execute(
            "SELECT repository_id, status FROM repositories WHERE lower(owner || '/' || "
            "name) = lower(?)", (full_name,)).fetchone()
        if found is None or found["status"] != "Complete":
            raise Unusable(f"{full_name} has no complete analysis in {db}")
        commits = [dict(row) for row in connection.execute(
            "SELECT c.commit_id, c.sequence, c.sha, a.* FROM commits c JOIN "
            "aggregate_metrics a USING (commit_id) WHERE c.repository_id = ? "
            "ORDER BY c.sequence", (found["repository_id"],))]
        files: dict[int, list[dict]] = defaultdict(list)
        for row in connection.execute(
                "SELECT f.* FROM file_metrics f JOIN commits c USING (commit_id) "
                "WHERE c.repository_id = ?", (found["repository_id"],)):
            files[row["commit_id"]].append(dict(row))
    connection.close()
    return commits, files


def choose(commits: list[dict], wanted: str) -> list[dict]:
    """Return all commits, or ``wanted`` of them spread evenly, first and last included."""
    if wanted == "all" or int(wanted) >= len(commits):
        return commits
    count = max(1, int(wanted))
    if count == 1:
        return [commits[-1]]
    picks = sorted({round(index * (len(commits) - 1) / (count - 1)) for index in range(count)})
    return [commits[index] for index in picks]


def export_tree(clone: Path, sha: str, destination: Path) -> None:
    """Write the regular files of commit ``sha`` under ``destination``."""
    archive = subprocess.run(["git", "-C", str(clone), "archive", "--format=tar", sha],
                             capture_output=True, check=False)
    if archive.returncode != 0:
        raise Unusable(f"commit {sha} is not in the clone at {clone}")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as bundle:
        for member in bundle.getmembers():
            parts = PurePosixPath(member.name).parts
            if not member.isfile() or member.name.startswith("/") or ".." in parts:
                continue
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            target.write_bytes(source.read() if source else b"")


def radon_json(tree: Path, command: Sequence[str], paths: Sequence[str]) -> dict:
    """Return the radon CLI's JSON answer for ``paths``, keyed by POSIX path."""
    answer: dict = {}
    for start in range(0, len(paths), BATCH):
        output = _run([sys.executable, "-m", "radon", *command, "-j",
                       *paths[start:start + BATCH]], cwd=tree)
        for key, value in json.loads(output or "{}").items():
            answer[PurePosixPath(Path(key).as_posix()).as_posix()] = value
    return answer


def cli_functions(blocks: object) -> list[tuple[int, int]]:
    """Return (line, complexity) of every function, method and closure radon lists."""
    found: list[tuple[int, int]] = []
    if not isinstance(blocks, list):
        return found
    for block in blocks:
        if block.get("type") in ("function", "method"):
            found.append((block["lineno"], block["complexity"]))
            found.extend(cli_functions(block.get("closures", [])))
    return sorted(set(found))


def nested_class_lines(source: bytes) -> set[int]:
    """Return the lines of functions defined inside a class nested in another class."""
    lines: set[int] = set()

    def visit(node: ast.AST, class_depth: int) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, class_depth + 1)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if class_depth >= 2:
                    lines.add(child.lineno)
                visit(child, class_depth)
            else:
                visit(child, class_depth)

    visit(ast.parse(source), 0)
    return lines


def pylint_counts(tree: Path, paths: Sequence[str]) -> dict[str, Counter]:
    """Return C, R, W and E message counts per file from one fresh Pylint run."""
    counts: dict[str, Counter] = {path: Counter() for path in paths}
    with tempfile.TemporaryDirectory(prefix="tdea-validate-pylint-") as temporary:
        rcfile = Path(temporary) / "empty.pylintrc"
        rcfile.write_text("", encoding="utf-8")
        environment = {key: value for key, value in os.environ.items()
                       if key not in ("PYTHONPATH", "PYLINTRC")}
        environment["PYLINTHOME"] = temporary
        output = _run([sys.executable, "-I", "-m", "pylint", f"--rcfile={rcfile}",
                       "--output-format=json", "--persistent=n", *paths],
                      cwd=tree, env=environment)
    for message in json.loads(output or "[]"):
        path = Path(message["path"]).as_posix()
        if message["type"] in CATEGORIES and path in counts:
            counts[path][message["type"]] += 1
    return counts


def python_files(tree: Path) -> list[str]:
    """Return the POSIX paths of the regular .py files under ``tree``."""
    return sorted(path.relative_to(tree).as_posix() for path in tree.rglob("*.py")
                  if path.is_file() and not path.is_symlink())


def validate_commit(report: Report, tree: Path, commit: dict, rows: list[dict],
                    scopes: Sequence[str]) -> None:
    """Compare one commit's stored file rows with the radon and Pylint oracles."""
    paths = sorted(row["path"] for row in rows)
    raw = radon_json(tree, ["raw"], paths)
    complexity = radon_json(tree, ["cc"], paths)
    maintainability = radon_json(tree, ["mi", "-s"], paths)
    linted = {}
    if "tree" in scopes:
        linted["whole tree"] = pylint_counts(tree, python_files(tree))
    if "file" in scopes:
        linted["file alone"] = {path: pylint_counts(tree, [path])[path] for path in paths}
    for row in rows:
        path = row["path"]
        where = {"sequence": commit["sequence"], "sha": commit["sha"][:12], "path": path}
        for field in ("loc", "sloc", "comments", "blank"):
            report.check(f"radon raw: {field}", row[field], raw.get(path, {}).get(field),
                         where)
        mi = maintainability.get(path, {}).get("mi")
        report.check("radon mi: MI", row["mi"],
                     None if mi is None else min(100, max(0, round(mi))), where)
        stored = sorted((item["line"], item["complexity"])
                        for item in json.loads(row["functions"]))
        oracle = cli_functions(complexity.get(path))
        extra = [item for item in stored if item not in oracle]
        if extra:
            nested = nested_class_lines((tree / path).read_bytes())
            if all(line in nested for line, _value in extra):
                report.notes["functions in nested classes (not listed by radon CLI)"] += \
                    len(extra)
                oracle = sorted(oracle + extra)
        report.check("radon cc: functions (line, complexity)", stored, oracle, where)
        values = [value for _line, value in oracle]
        report.check("radon cc: max CC", row["max_cc"], max(values, default=0), where)
        report.check("radon cc: mean CC", row["mean_cc"],
                     sum(values) / len(values) if values else 0.0, where, ROUNDING)
        for scope, smells in linted.items():
            counted = smells[path]
            for field in CATEGORIES:
                report.check(f"pylint ({scope}): {field}", row[field], counted[field], where)
            total = sum(counted[field] for field in CATEGORIES)
            report.check(f"pylint ({scope}): smells", row["smells"], total, where)
        report.check("smell density = smells / SLOC", row["smell_density"],
                     row["smells"] / row["sloc"] if row["sloc"] else 0.0, where, TOLERANCE)


def normalised(values: Sequence[float]) -> list[float]:
    """Return min-max normalised values; all 0.0 when the values are constant (OI-01)."""
    low, high = min(values), max(values)
    return [0.0 if high == low else (value - low) / (high - low) for value in values]


def validate_scores(report: Report, commits: list[dict], files: dict[int, list[dict]]
                    ) -> None:
    """Recompute aggregates, normalisation, Debt Score, delta and class from stored rows."""
    for commit in commits:
        rows = files.get(commit["commit_id"], [])
        where = {"sequence": commit["sequence"], "sha": commit["sha"][:12]}
        complexities = [item["complexity"] for row in rows
                        for item in json.loads(row["functions"])]
        expected = {
            "file_count": len(rows), "total_loc": sum(row["loc"] for row in rows),
            "total_sloc": sum(row["sloc"] for row in rows),
            "convention": sum(row["convention"] for row in rows),
            "refactor": sum(row["refactor"] for row in rows),
            "warning": sum(row["warning"] for row in rows),
            "error": sum(row["error"] for row in rows),
            "total_smells": sum(row["smells"] for row in rows),
            "mean_cc": sum(complexities) / len(complexities) if complexities else 0.0,
            "mean_mi": sum(row["mi"] for row in rows) / len(rows) if rows else 0.0,
            "mean_smell_density": (sum(row["smell_density"] for row in rows) / len(rows)
                                   if rows else 0.0)}
        for field, value in expected.items():
            report.check(f"aggregate: {field}", commit[field], value, where, ROUNDING)
    growth = [0] + [later["total_sloc"] - earlier["total_sloc"]
                    for earlier, later in zip(commits, commits[1:])]
    norms = {"sloc_norm": normalised([item["total_sloc"] for item in commits]),
             "cc_norm": normalised([item["mean_cc"] for item in commits]),
             "mi_norm": normalised([item["mean_mi"] for item in commits]),
             "smells_norm": normalised([item["total_smells"] for item in commits]),
             "density_norm": normalised([item["mean_smell_density"] for item in commits]),
             "sloc_growth_norm": normalised(growth)}
    previous = None
    for index, commit in enumerate(commits):
        where = {"sequence": commit["sequence"], "sha": commit["sha"][:12]}
        report.check("score: sloc_growth", commit["sloc_growth"], growth[index], where)
        for field, values in norms.items():
            report.check(f"score: {field}", commit[field], values[index], where, ROUNDING)
        score = 100 * (WEIGHTS["cc"] * commit["cc_norm"]
                       + WEIGHTS["smells"] * commit["density_norm"]
                       + WEIGHTS["mi"] * (1 - commit["mi_norm"])
                       + WEIGHTS["sloc_growth"] * commit["sloc_growth_norm"])
        report.check("score: debt_score", commit["debt_score"], score, where, 0.01)
        delta = 0.0 if previous is None else commit["debt_score"] - previous
        report.check("score: debt_delta", commit["debt_delta"], delta, where, 0.01)
        label = ("Debt Spike" if delta > THRESHOLD else
                 "Refactoring Drop" if delta < -THRESHOLD else "Stable")
        report.check("score: classification", commit["classification"], label, where)
        previous = commit["debt_score"]


def validate(db: Path, full_name: str, clone: Path, wanted: str,
             scopes: Sequence[str] = ("tree", "file")) -> dict:
    """Run every check and return the report as plain data."""
    commits, files = load(db, full_name)
    report = Report()
    chosen = choose(commits, wanted)
    for commit in chosen:
        rows = files.get(commit["commit_id"], [])
        if not rows:
            continue
        with tempfile.TemporaryDirectory(prefix="tdea-validate-") as temporary:
            tree = Path(temporary)
            export_tree(clone, commit["sha"], tree)
            validate_commit(report, tree, commit, rows, scopes)
    validate_scores(report, commits, files)
    result = report.as_dict()
    result.update({"repository": full_name, "analysed_commits": len(commits),
                   "file_checks_on": [item["sequence"] for item in chosen],
                   "file_rows_checked": sum(len(files.get(item["commit_id"], []))
                                            for item in chosen)})
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the arguments, validate, print a summary and write the report."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--repository", required=True, help="owner/name as analysed")
    parser.add_argument("--clone", type=Path, required=True,
                        help="a local git clone of the same repository")
    parser.add_argument("--commits", default="5",
                        help="'all', or how many analysed commits get file-level checks")
    parser.add_argument("--pylint", choices=("both", "tree", "file"), default="both",
                        help="lint the whole tree, each file alone, or both")
    parser.add_argument("--out", type=Path, help="write the JSON report here")
    arguments = parser.parse_args(argv)
    try:
        scopes = ("tree", "file") if arguments.pylint == "both" else (arguments.pylint,)
        result = validate(arguments.db, arguments.repository, arguments.clone,
                          arguments.commits, scopes)
    except Unusable as exc:
        print(f"validate_metrics: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE
    if arguments.out:
        arguments.out.write_text(json.dumps(result, indent=2, default=str) + "\n",
                                 encoding="utf-8")
    differing = 0
    for name, check in result["checks"].items():
        differing += check["differing"]
        print(f"{check['compared'] - check['differing']:>7} of {check['compared']:>7} "
              f"agree  {name}")
    for note, count in result["notes"].items():
        print(f"note: {count} {note}")
    return EXIT_DIFFER if differing else EXIT_AGREE


if __name__ == "__main__":
    sys.exit(main())
