"""FX-RG10 v2: FX-RG10 v1 plus the RE-17 and RE-18 inputs (sprint 23 Oct).

SYNTHETIC — not a research-harness bundle and not research data. v2 is v1 (make_fx_rg10,
unchanged, so the RE-14 to RE-16 tests keep their fixture) with:

* the RE-18 output (g17, label "smells"): task 1 in the T1 injected variant, whose helper
  already has a bare ``except:`` (W0702) above the marker; the output adds a bare
  ``except:`` (W0702), an ``except Exception`` (W0718) and a ``.format()`` call (C0209),
  and its record says it imported and passed every test. Task 1 declares 2 T1 and 1 T2
  opportunities, so RE-18 expects T1 = 2, T2 = 1 and rates 1.0 and 1.0.
* RE-17's other hostile outputs, each of which would act at import time if anything
  imported it: g18 connects to a local listener (port from TDEA_FX_RG10_PORT), g19
  spawns a subprocess that writes a file (path from TDEA_FX_RG10_SPAWN), g20 loops
  forever. Their records are invented harness labels (a refused connection, a passing
  import, an import time-out).
* RE-17's variant with an init-hook: the T1 injected variant carries ``pylintrc``,
  ``.pylintrc`` and ``pyproject.toml`` whose init-hook would write a file (path from
  TDEA_FX_RG10_HOOK) if Pylint ever read them.

Nothing here or in any test imports, executes or evaluates an output or a variant
(NFR-26). Usage: python make_fx_rg10_v2.py OUTPUT_DIR (builds locally; nothing is pushed).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from make_fx_rg10 import (ALL_PASS_1, BUNDLE, CASES, Case, FxRg10, build as build_v1,
                          fenced)

VERSION = "2"
PORT_VARIABLE = "TDEA_FX_RG10_PORT"
SPAWN_VARIABLE = "TDEA_FX_RG10_SPAWN"
HOOK_VARIABLE = "TDEA_FX_RG10_HOOK"
META = dict(BUNDLE, bundle_id="fx-rg10-v2")

SMELLS = fenced("def discounted(prices, rate):\n"
                '    """Return the total after a discount."""\n'
                "    try:\n"
                "        value = total(prices) * (1 - rate)\n"
                "    except:\n"
                "        value = 0\n"
                "    try:\n"
                '        print("discounted: {}".format(value))\n'
                "    except Exception:\n"
                "        value = round(value, 2)\n"
                "    return value\n")
LISTENER = fenced("import os\nimport socket\n\n"
                  f'socket.create_connection(("127.0.0.1", int(os.environ.get("{PORT_VARIABLE}",'
                  ' "9"))), timeout=1).close()\n\n\n'
                  "def discounted(prices, rate):\n"
                  '    """Return the total after a discount."""\n'
                  "    return total(prices) * (1 - rate)\n")
SPAWN = fenced("import os\nimport subprocess\nimport sys\n\n"
               'subprocess.run([sys.executable, "-c", "import os, pathlib; '
               f"pathlib.Path(os.environ.get('{SPAWN_VARIABLE}', 'fx_rg10_spawn.txt'))"
               '.write_text(\'spawned\')"], check=False)\n\n\n'
               "def discounted(prices, rate):\n"
               '    """Return the total after a discount."""\n'
               "    return total(prices) * (1 - rate)\n")
LOOP = fenced("while True:\n    pass\n\n\n"
              "def discounted(prices, rate):\n"
              '    """Return the total after a discount."""\n'
              "    return total(prices) * (1 - rate)\n")

CASES_V2 = CASES + (
    Case("smells", "task-1", "T1", "injected", SMELLS, ("ok", ALL_PASS_1)),
    Case("listener", "task-1", "T2", "placebo", LISTENER,
         ("error", "ConnectionRefusedError: [Errno 111] Connection refused")),
    Case("spawn", "task-1", "T1", "placebo", SPAWN, ("ok", ALL_PASS_1)),
    Case("loop", "task-1", "T2", "clean", LOOP,
         ("error", "TimeoutError: the import did not finish within 10 s")),
)

HOOK = (f"import os, pathlib; pathlib.Path(os.environ.get('{HOOK_VARIABLE}', "
        "'fx_rg10_hook.txt')).write_text('hook')")
INIT_HOOK_FILES = {
    "pylintrc": f"[MAIN]\ninit-hook={HOOK}\n",
    ".pylintrc": f"[MAIN]\ninit-hook={HOOK}\n",
    "pyproject.toml": f'[tool.pylint.main]\ninit-hook = "{HOOK}"\n',
}


def build(path: Path) -> FxRg10:
    """Build FX-RG10 v2 at ``path``, which must not exist, and return it."""
    return build_v1(path, CASES_V2, META, {("T1", "injected"): INIT_HOOK_FILES}, VERSION)


def main(argv: list[str] | None = None) -> int:
    """Build FX-RG10 v2 into the given directory and print each output's gen_id."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="directory to create (must not exist)")
    fixture = build(parser.parse_args(argv).output)
    for label, gen_id in fixture.gen_ids.items():
        print(f"{gen_id} {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
