"""Command-line entry point: ``tdea`` and its ``research`` command group (FR-48).

Commands (SRS 4.5.1):

* ``tdea research validate RUN_FILE`` checks a run file without side effects: exit 0 when
  it is valid, 1 (invalid input) after printing every problem.
* ``tdea research run RUN_FILE [--ask-token]`` starts the run, or resumes the incomplete
  run with the same identifier. It prints the run identifier first and one line per
  unit; starting a completed run again does nothing and exits 0. The optional PAT is
  read from TDEA_GITHUB_TOKEN, or with --ask-token from a prompt that does not echo; it
  goes to the accessibility check only and is never printed or stored (FR-04, NFR-19).
* ``tdea research status RUN_FILE`` lists every run in the run file's research store and
  its units, without writing anything.
* ``tdea research export --store DB --out DIR [--format csv|json|jsonl] RUN_ID...``
  writes the record files of complete runs, metadata.json and data_dictionary.csv
  (FR-62): exit 1 for an unknown run, store or unusable folder, 3 for runs of different
  kinds or key fingerprints, or a run that has not completed.

Exit codes are the ones every source agrees on (Task 7 Day 6 decision 3): 0 complete or
nothing to do; 2 an RG10 evaluation complete with exclusions (FR-59; sprint 23 Oct);
1 invalid input (a run file that fails validation, a malformed command
line, SHAs outside their window or absent from the history); 3 refused (the NFR-24
environment, CL-03 incomplete (decision 1), the single-analysis lock held or unusable,
a changed history). Every other failure has no agreed exit code. It is printed in the
three-part form followed by a note naming its category and why its code is unresolved:
CL-15 (SRS v2.3 gives exit 4 to "a unit stopped on a clone, network or disk error"; the
Specification Freeze defines 0 to 3 only), CL-19 (a failed accessibility check at run time)
or no definition at all. Until the owner decides, the process ends with status 1 as a
placeholder; the note says so, and status 1 then does not mean invalid input. No code
outside 0 to 4 is used. Every error is printed as
error type, cause and action through ApiError (FR-05, NFR-11); the command line calls only
ResearchService (Freeze section 2).
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Sequence
from datetime import datetime, time

from tdea.aggregator.research_job import PlannedUnit, RunObserver, UnitCounts
from tdea.aggregator.research_service import ResearchRunError, ResearchService, StoreStatus
from tdea.aggregator.rg10_job import Rg10Result
from tdea.aggregator.run_file import Rg10RunFile, RunFileInvalid
from tdea.api.api_error import ApiError
from tdea.api.research_exporter import FORMATS, ExportFolderInvalid, ResearchExporter

EXIT_OK = 0
EXIT_INVALID_INPUT = 1  # FR-48: invalid input, including a malformed command line.
EXIT_EXCLUSIONS = 2  # FR-48: complete with exclusions (RG10 unevaluable outputs, FR-59).
EXIT_REFUSED = 3  # FR-48: refused.
EXIT_UNDECIDED = 1  # placeholder while the code is unresolved; not a claim of invalid input
TOKEN_VARIABLE = "TDEA_GITHUB_TOKEN"
PLACEHOLDER_NOTE = ("Exit status: 1 is a placeholder until this exit code is decided; here it "
                    "does not mean that the input is invalid.")


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
            "Headless research engine for RG1 and RG10. Available commands: validate, run, "
            "status and export."
        ),
    )
    research_commands = research.add_subparsers(
        dest="research_command", metavar="RESEARCH_COMMAND", parser_class=_Parser)
    validate = research_commands.add_parser(
        "validate", help="check a run file without side effects",
        description="Check a research run file fully, without cloning, network access or "
                    "writing any file. Exit 0 if it is valid, 1 if it is not.")
    validate.add_argument("run_file", metavar="RUN_FILE", help="path of the YAML run file")
    run = research_commands.add_parser(
        "run", help="start a run, or resume the incomplete run with the same identifier",
        description="Start the research run a run file defines, or resume it. The run "
                    f"identifier is printed first. A PAT is read from {TOKEN_VARIABLE}, or "
                    "from a prompt with --ask-token; it is never stored.")
    run.add_argument("run_file", metavar="RUN_FILE", help="path of the YAML run file")
    run.add_argument("--ask-token", action="store_true",
                     help="prompt for a GitHub Personal Access Token (input is hidden)")
    status = research_commands.add_parser(
        "status", help="list the runs in the run file's research store and their units",
        description="List every run in the run file's research store with its units, "
                    "without writing anything.")
    status.add_argument("run_file", metavar="RUN_FILE", help="path of the YAML run file")
    export = research_commands.add_parser(
        "export", help="export the records of complete runs (FR-62)",
        description="Write every record of one or more complete runs of the same kind and "
                    "key as CSV, JSON or JSONL, with metadata.json and data_dictionary.csv. "
                    "Record files hold identity keys only.")
    export.add_argument("run_ids", metavar="RUN_ID", nargs="+", help="run identifiers")
    export.add_argument("--store", required=True, help="path of the research.db file")
    export.add_argument("--out", required=True, help="new or empty output folder")
    export.add_argument("--format", choices=FORMATS, default="csv", dest="fmt",
                        help="record file format (default csv)")
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
    service = service if service is not None else ResearchService()
    try:
        if args.research_command == "validate":
            return _validate(args.run_file, service)
        if args.research_command == "run":
            return _run(args.run_file, service, _token(args.ask_token))
        if args.research_command == "status":
            return _status(args.run_file, service)
        if args.research_command == "export":
            return _export(args, service)
    except ExportFolderInvalid as error:
        print("\n".join(ApiError.wrap(error).lines()), file=sys.stderr)
        return error.exit_code
    except RunFileInvalid as invalid:
        _print_problems(invalid)
        return EXIT_INVALID_INPUT
    except ResearchRunError as error:
        print("\n".join(ApiError.wrap(error).lines()), file=sys.stderr)
        if error.exit_code is None:
            print(unresolved_note(error.category, error.open_item), file=sys.stderr)
            print(PLACEHOLDER_NOTE, file=sys.stderr)
            return EXIT_UNDECIDED
        return error.exit_code
    research.print_help()
    return EXIT_OK


def unresolved_note(category: str | None, open_item: str | None) -> str:
    """Return the line naming a failure's category and why its exit code is unresolved."""
    kind = category or "failure"
    if open_item == "CL-15":
        return (f"Exit code unresolved for this {kind} (CL-15 open): SRS v2.3 gives exit 4 to "
                "a unit stopped on a clone, network or disk error, but the Specification "
                "Freeze defines exit codes 0 to 3 only.")
    if open_item == "CL-19":
        return (f"Exit code unresolved for this {kind} at run time (CL-19 open): no source "
                "defines its exit code.")
    return f"Exit code unresolved for this {kind}: no source defines its exit code."


def _token(ask: bool) -> str | None:
    """Return the PAT from a hidden prompt or TDEA_GITHUB_TOKEN, or None (FR-04)."""
    token = getpass.getpass("GitHub Personal Access Token (input hidden): ") if ask \
        else os.environ.get(TOKEN_VARIABLE, "")
    return token.strip() or None


def _print_problems(invalid: RunFileInvalid) -> None:
    """Print every run-file problem as a three-part block, blocks separated by a blank line."""
    for number, problem in enumerate(invalid.problems):
        if number:
            print(file=sys.stderr)
        print("\n".join(ApiError.wrap(problem).lines()), file=sys.stderr)


def _validate(run_file: str, service: ResearchService) -> int:
    """Check one run file; print a summary or raise RunFileInvalid."""
    checked = service.validate(run_file)
    if isinstance(checked, Rg10RunFile):
        types = ", ".join(f"{smell} = {' + '.join(rules)}" for smell, rules in
                          checked.smell_mapping)
        print(f"Run file is valid: an RG10 evaluation of the bundle {checked.bundle} with "
              f"the smell mapping {types}. The bundle itself is checked when the run "
              "starts.")
        return EXIT_OK
    print(f"Run file is valid: an RG1 run over {len(checked.repositories)} "
          f"{_plural(len(checked.repositories), 'repository', 'repositories')} and "
          f"{checked.unit_count} {_plural(checked.unit_count, 'unit', 'units')} "
          "(one per repository and window).")
    return EXIT_OK


def _run(run_file: str, service: ResearchService, token: str | None) -> int:
    """Start or resume one run, printing progress; return 0, or 2 with exclusions."""
    result = service.run(run_file, token=token, observer=_Progress())
    if isinstance(result, Rg10Result):
        print(f"Bundle evaluated: {result.outputs} "
              f"{_plural(result.outputs, 'output', 'outputs')}, {result.unevaluable} "
              f"unevaluable (listed in the store and in exports).", flush=True)
        if result.with_exclusions and not result.already_complete:
            return EXIT_EXCLUSIONS
    return EXIT_OK


def _status(run_file: str, service: ResearchService) -> int:
    """Print every run of the run file's store and its units."""
    print("\n".join(status_lines(service.status(run_file))))
    return EXIT_OK


def _export(args: argparse.Namespace, service: ResearchService) -> int:
    """Export the named runs and print where the files went."""
    data = service.export(args.store, list(args.run_ids))
    written = ResearchExporter().write(data, args.out, args.fmt)
    rows = sum(len(record.rows) for record in data.files)
    print(f"Exported {len(data.runs)} {_plural(len(data.runs), 'run', 'runs')} "
          f"({data.kind}, key {data.key_fingerprint}): {rows} records in "
          f"{len(data.files)} {args.fmt} files, plus data_dictionary.csv and metadata.json, "
          f"to {written[-1].parent}.")
    return EXIT_OK


class _Progress(RunObserver):
    """Prints the run identifier first, then one line per unit (FR-48)."""

    def run_identified(self, run_id: str) -> None:
        """Print the run identifier."""
        print(f"Run identifier: {run_id}", flush=True)

    def run_starting(self, run_id: str, total: int, complete: int) -> None:
        """Say whether the run starts or resumes."""
        if complete:
            print(f"Resuming run {run_id}: {complete} of {total} "
                  f"{_plural(total, 'unit', 'units')} already complete.", flush=True)
        else:
            print(f"Starting run {run_id}: {total} {_plural(total, 'unit', 'units')}.",
                  flush=True)

    def unit_skipped(self, unit: PlannedUnit, total: int) -> None:
        """Report a unit skipped because it is complete."""
        print(f"Unit {unit.number} of {total} ({unit.label}): already complete, skipped.",
              flush=True)

    def unit_completed(self, unit: PlannedUnit, total: int, counts: UnitCounts) -> None:
        """Report a committed unit and its counts."""
        print(f"Unit {unit.number} of {total} ({unit.label}): complete; "
              f"{_counts(counts)}.", flush=True)

    def run_completed(self, run_id: str, total: int, already: bool) -> None:
        """Report the end of the run."""
        if already:
            print(f"Run {run_id} is already complete; nothing to do.", flush=True)
        else:
            print(f"Run {run_id} complete: {total} of {total} "
                  f"{_plural(total, 'unit', 'units')}.", flush=True)


def status_lines(found: StoreStatus) -> list[str]:
    """Return the lines ``status`` prints for one research store."""
    lines = [f"Research store: {found.store}"]
    if not found.exists:
        return lines + ["No research store exists at this path yet, so there are no runs."]
    if not found.runs:
        return lines + ["No runs in this store."]
    for run in found.runs:
        ended = f", ended {run.ended_at}" if run.ended_at else ""
        lines.append(f"Run {run.run_id} ({run.kind}): {run.status}, started {run.started_at}"
                     f"{ended}; {run.complete_units} of {len(run.units)} "
                     f"{_plural(len(run.units), 'unit', 'units')} complete.")
        for unit in run.units:
            where = f"  {unit.repository}, {_moment(unit.window_start)} to " \
                    f"{_moment(unit.window_end)}"
            if unit.counts is None:
                lines.append(f"{where}: {unit.status}.")
            else:
                lines.append(f"{where}: {unit.status} at {unit.completed_at}; "
                             f"{_counts(unit.counts)}.")
    return lines


def _counts(counts: UnitCounts) -> str:
    """Return a unit's counts as text."""
    return (f"{counts.selected_commits} selected "
            f"{_plural(counts.selected_commits, 'commit', 'commits')}, "
            f"{counts.introductions} "
            f"{_plural(counts.introductions, 'introduction', 'introductions')}, "
            f"{counts.lint_failures} "
            f"{_plural(counts.lint_failures, 'lint failure', 'lint failures')}, "
            f"{counts.commits_after_cutoff} "
            f"{_plural(counts.commits_after_cutoff, 'commit', 'commits')} after the cut-off")


def _moment(moment: datetime) -> str:
    """Return a UTC window bound: the date alone at midnight, else the full time."""
    return moment.date().isoformat() if moment.time() == time(0) else moment.isoformat()


def _plural(count: int, one: str, many: str) -> str:
    """Return the singular or plural word for ``count``."""
    return one if count == 1 else many


if __name__ == "__main__":
    sys.exit(main())
