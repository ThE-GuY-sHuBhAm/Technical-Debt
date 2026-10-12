"""Command-line entry point: ``tdea`` and its ``research`` command group (FR-48).

``tdea research validate RUN_FILE`` checks a run file without side effects and exits 0
when it is valid, or 1 (invalid input) after printing every problem in the three-part
form of FR-05 and NFR-11. The command line calls only ResearchService (Freeze section 2)
and prints errors through ApiError. The run, status and export commands, and the exit
codes that only they can return, arrive with the run command (Day 6) and FR-62; until
then this module returns 0 or 1 only, the two codes every source agrees on.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from tdea.aggregator.research_service import ResearchService
from tdea.aggregator.run_file import RunFileInvalid
from tdea.api.api_error import ApiError

EXIT_OK = 0
EXIT_INVALID_INPUT = 1  # FR-48: invalid input, including a malformed command line.


class UsageError(Exception):
    """Raised instead of argparse's own exit, so a usage error never exits with 2."""


class _Parser(argparse.ArgumentParser):
    """Argument parser whose usage errors map to exit code 1 (FR-48: invalid input)."""

    def error(self, message: str) -> None:  # type: ignore[override]
        """Raise UsageError rather than exiting with argparse's code 2."""
        raise UsageError(f"{self.prog}: error: {message}")


def build_parser() -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    """Build the top-level parser and return it with the ``research`` group parser."""
    parser = _Parser(prog="tdea", description="Technical Debt Evolution Evaluator.")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", parser_class=_Parser)
    research = commands.add_parser(
        "research",
        help="headless research engine for RG1 and RG10",
        description=(
            "Headless research engine for RG1 and RG10. Available command: validate. "
            "The run, status and export commands are not part of this version."
        ),
    )
    research_commands = research.add_subparsers(
        dest="research_command", metavar="RESEARCH_COMMAND", parser_class=_Parser)
    validate = research_commands.add_parser(
        "validate", help="check a run file without side effects",
        description="Check a research run file fully, without cloning, network access or "
                    "writing any file. Exit 0 if it is valid, 1 if it is not.")
    validate.add_argument("run_file", metavar="RUN_FILE", help="path of the YAML run file")
    return parser, research


def main(argv: Sequence[str] | None = None,
         service: ResearchService | None = None) -> int:
    """Run the ``tdea`` command line and return the process exit code."""
    parser, research = build_parser()
    try:
        args = parser.parse_args(argv)
    except UsageError as exc:
        parser.print_usage(sys.stderr)
        print(exc, file=sys.stderr)
        return EXIT_INVALID_INPUT
    except SystemExit as exc:  # --help prints and exits through argparse
        return exc.code if isinstance(exc.code, int) else EXIT_OK

    if args.command != "research":
        parser.print_help()
        return EXIT_OK
    if args.research_command == "validate":
        return _validate(args.run_file, service if service is not None else ResearchService())
    research.print_help()
    return EXIT_OK


def _validate(run_file: str, service: ResearchService) -> int:
    """Check one run file; print a summary or every problem; return 0 or 1."""
    try:
        checked = service.validate(run_file)
    except RunFileInvalid as invalid:
        for number, problem in enumerate(invalid.problems):
            if number:
                print(file=sys.stderr)
            print("\n".join(ApiError.wrap(problem).lines()), file=sys.stderr)
        return EXIT_INVALID_INPUT
    print(f"Run file is valid: an RG1 run over {len(checked.repositories)} "
          f"{_plural(len(checked.repositories), 'repository', 'repositories')} and "
          f"{checked.unit_count} {_plural(checked.unit_count, 'unit', 'units')} "
          "(one per repository and window).")
    return EXIT_OK


def _plural(count: int, one: str, many: str) -> str:
    """Return the singular or plural word for ``count``."""
    return one if count == 1 else many


if __name__ == "__main__":
    sys.exit(main())
    