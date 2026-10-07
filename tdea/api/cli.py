"""Command-line entry point: ``tdea`` and its ``research`` command group (FR-48).

Task 1 provides the console script and the empty ``research`` group only. The four
research subcommands (validate, run, status, export) and their frozen exit codes
arrive with FR-48. Until then nothing here returns a research outcome code.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

EXIT_OK = 0
EXIT_INVALID_INPUT = 1  # FR-48: a malformed command line is invalid input.


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
            "Headless research engine for RG1 and RG10. Its commands "
            "(validate, run, status, export) are added with FR-48."
        ),
    )
    return parser, research


def main(argv: Sequence[str] | None = None) -> int:
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

    if args.command == "research":
        research.print_help()
    else:
        parser.print_help()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
