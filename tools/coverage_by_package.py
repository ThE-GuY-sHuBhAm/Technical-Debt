"""NFR-13 report: statement coverage per tdea package, from coverage.py's JSON report.

pytest-cov reports coverage per file. NFR-13 and TC-135 judge git_handler, analyzer
and aggregator as packages, research modules included, so this script adds the file
figures up per package (the first directory under tdea/). Files directly under tdea/
are listed as "tdea".

The script only reports unless --fail-under is given. The execution plan switches the
70% gate on from Day 6, once those packages hold real code; with the gate on, a
required package with no statements also fails.

Exit status: 0 the report was printed and, with --fail-under, every required package
meets it; 1 a required package is below the threshold or has no statements; 2 the
JSON report cannot be read.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

PACKAGE = "tdea"
REQUIRED = ("git_handler", "analyzer", "aggregator")

EXIT_PASS = 0
EXIT_BELOW_THRESHOLD = 1
EXIT_CANNOT_READ = 2


@dataclass
class PackageCoverage:
    """Statement totals for one package."""

    name: str
    statements: int = 0
    covered: int = 0

    @property
    def percent(self) -> float | None:
        """Return covered statements as a percentage, or None with no statements."""
        if self.statements == 0:
            return None
        return 100.0 * self.covered / self.statements


def package_of(path: str) -> str | None:
    """Return the tdea package a reported file belongs to, or None outside tdea/."""
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if PACKAGE not in parts:
        return None
    # The last "tdea" is the package; an absolute path may also pass through a
    # checkout directory of the same name.
    start = len(parts) - parts[::-1].index(PACKAGE)
    rest = parts[start:]
    return rest[0] if len(rest) > 1 else PACKAGE


def by_package(report: Mapping[str, object]) -> dict[str, PackageCoverage]:
    """Add up coverage.py's per-file summaries per tdea package."""
    totals: dict[str, PackageCoverage] = {}
    files = report.get("files")
    if not isinstance(files, Mapping):
        raise ValueError("the report has no 'files' section")
    for path, data in files.items():
        name = package_of(path)
        if name is None:
            continue
        summary = data["summary"]
        package = totals.setdefault(name, PackageCoverage(name))
        package.statements += int(summary["num_statements"])
        package.covered += int(summary["covered_lines"])
    return totals


def main(argv: Sequence[str] | None = None) -> int:
    """Print the per-package table and apply the optional gate."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", type=Path, help="coverage JSON report (--cov-report=json)")
    parser.add_argument("--fail-under", type=float, default=None,
                        help="minimum percentage for each required package (off by default)")
    parser.add_argument("--require", nargs="+", default=list(REQUIRED),
                        help="packages the gate applies to (default: %(default)s)")
    args = parser.parse_args(argv)

    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        totals = by_package(report)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Cannot read {args.report}: {exc}", file=sys.stderr)
        return EXIT_CANNOT_READ

    for name in args.require:
        totals.setdefault(name, PackageCoverage(name))
    print("Statement coverage per package (NFR-13):")
    for name in sorted(totals):
        package = totals[name]
        label = f"{PACKAGE} (top level)" if name == PACKAGE else f"{PACKAGE}.{name}"
        shown = "no statements" if package.percent is None else f"{package.percent:5.1f}%"
        print(f"  {label:<18} {package.covered:>5}/{package.statements:<5} {shown}")

    if args.fail_under is None:
        print("Gate off: report only (the plan applies the 70% gate from Day 6).")
        return EXIT_PASS
    failing = [
        name for name in args.require
        if totals[name].percent is None or totals[name].percent < args.fail_under
    ]
    for name in failing:
        print(f"{PACKAGE}.{name} is below {args.fail_under:.1f}%.", file=sys.stderr)
    return EXIT_BELOW_THRESHOLD if failing else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
