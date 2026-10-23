"""Command line: ``python -m rg10_harness assemble`` (run with harness/ on PYTHONPATH).

Example::

    docker build -t tdea-rg10-sandbox harness/docker
    PYTHONPATH=harness python -m rg10_harness assemble --materials M --generation G
        --out bundles/rg10-repilot-01 --bundle-id rg10-repilot-01 --image tdea-rg10-sandbox

(the second command on one line).

Exit status: 0 every output with a module was executed and recorded; 4 the bundle was
written but some outputs got no record because the sandbox gave no valid reply (run again
for a new bundle); 1 the inputs or the sandbox could not be used, and nothing was written.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from rg10_harness.assemble import AssemblyError, assemble
from rg10_harness.sandbox import DockerSandbox, Limits, SandboxRefused
from tdea.analyzer.research.rg10.bundle import BundleInvalid


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser."""
    parser = argparse.ArgumentParser(prog="python -m rg10_harness",
                                     description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("assemble", help="prepare, execute and assemble one bundle")
    run.add_argument("--materials", type=Path, required=True,
                     help="folder with variants/, tests/, tasks.jsonl and manifest.jsonl")
    run.add_argument("--generation", type=Path, required=True,
                     help="folder with outputs/ and generation.jsonl")
    run.add_argument("--out", type=Path, required=True, help="new bundle folder")
    run.add_argument("--bundle-id", required=True)
    run.add_argument("--image", required=True, help="sandbox image (Python 3.11, pytest)")
    defaults = Limits()
    run.add_argument("--import-timeout", type=int, default=defaults.import_timeout_s)
    run.add_argument("--test-timeout", type=int, default=defaults.test_timeout_s)
    run.add_argument("--cpus", type=float, default=defaults.cpu_limit)
    run.add_argument("--memory-mb", type=int, default=defaults.memory_limit_mb)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and return the exit status."""
    args = build_parser().parse_args(argv)
    limits = Limits(import_timeout_s=args.import_timeout, test_timeout_s=args.test_timeout,
                    cpu_limit=args.cpus, memory_limit_mb=args.memory_mb)
    try:
        report = assemble(args.materials, args.generation, args.out,
                          DockerSandbox(args.image, limits), args.bundle_id)
    except (AssemblyError, SandboxRefused, BundleInvalid) as error:
        print(f"Bundle not assembled: {error}", file=sys.stderr)
        return 1
    print(f"Bundle {report.bundle}: {len(report.recorded)} execution records; "
          f"{len(report.missing_output)} missing outputs; {len(report.no_module)} outputs "
          f"without a module; {len(report.not_parsing)} modules that do not parse; "
          f"{len(report.no_reply)} without a sandbox reply.")
    for gen_id in report.no_reply:
        print(f"No execution record (no valid sandbox reply): {gen_id}", file=sys.stderr)
    return 4 if report.no_reply else 0


if __name__ == "__main__":
    sys.exit(main())
