# Workbook v1.2: metric and scoring cases, automated results

Branch `sprint-implementation`, 23 Oct 2026. Sheets STATIC ANALYSIS (TC-034 to TC-054)
and DEBT SCORING (TC-068 to TC-085) of `TestCases_TechnicalDebtEvaluator_v1.2.xlsx`.
Tests: `tests/product/test_workbook_cases.py`, each named after its case, asserting the
EXPECTED RESULT as written. Run on Python 3.11 (Linux) with the pinned radon and Pylint.
The CI matrix runs the same file on Linux, macOS and Windows with Python 3.10 to 3.12.

This is automated developer evidence. The workbook's ACTUAL RESULT and STATUS columns
were **not** filled in or changed. A formal execution record of these cases still has
to be made by whoever owns the workbook.

## Levels

* **End to end**: a git repository is built, analysed through `AnalysisService` (clone,
  checkout, radon, Pylint, scoring, store) and the stored rows are read back. Only the
  FR-03 GitHub check is stubbed (see `tests/fixtures/product_support.py`).
* **Component**: the analyzer or scoring function is called directly with the case's
  input.
* **Oracle**: where the case says to compare with `radon` or `pylint --output-format=json`,
  the test runs that CLI on the same file.

## Inputs built by the tests

The workbook describes calc.py, smells_*.py, FX-M and FX-RISK but does not ship them.
They are built in the test file from the case's own description. They are not official
fixtures; a reviewer may supply official ones, and the tests should then use those
instead.

| Input | Built as |
| --- | --- |
| calc.py (TC-034) | 50 lines: 2 header comments, an import, 2 blanks, 5 functions of 6 code lines each followed by 1 comment and 1 blank, a comment, and 4 module-level assignments |
| smells_c/r/w/e.py (TC-043 to TC-046) | As the INPUT column describes: no module docstring and 2 bad names; 14 branches; an unused import and an unused variable; 2 undefined names |
| FX-M stand-in (TC-069, TC-073) | 50 commits; one module changes in every commit, with function count, branching and unused imports on fixed cycles, so all five metrics vary |
| FX-RISK stand-in (TC-080, TC-082) | 15 files: 12 small documented modules and 3 modules with 12, 16 and 20 branches, unused imports and bad names |
| FX-S, FX-ERR | Existing fixtures in `tests/fixtures/make_fx_product.py` |

## Results (23 Oct 2026, local run: 36 tests, 36 passed)

| Case | What it checks | Level | Result |
| --- | --- | --- | --- |
| TC-034 | loc 50, sloc 35, comments 8, blank 7; equal to `radon raw` | End to end and oracle | Passed |
| TC-035 | Only .py files get metric rows | End to end | Passed |
| TC-036 | util.py 20 lines at #3, 45 at #4: two rows | End to end | Passed |
| TC-037 | One-line function: CC 1, grade A | Component | Passed |
| TC-038 | CC 5 is grade A, CC 6 is grade B | Component | Passed |
| TC-039 | CC 42 is grade F | Component | Passed |
| TC-040 | Cart's 3 methods give 3 CC rows | Component | Passed |
| TC-041 | FX-S, 5 files: MI integers 0 to 100, equal to rounded `radon mi` | End to end and oracle | Passed |
| TC-042 | `x = 1` has MI 100 | Component | Passed |
| TC-043 | Convention count = pylint JSON = 3 | End to end, component and oracle | Passed |
| TC-044 | Refactor count ≥ 1 and = pylint JSON | End to end, component and oracle | Passed |
| TC-045 | Warning count = pylint JSON = 2 | End to end, component and oracle | Passed |
| TC-046 | Error count = pylint JSON = 2 | End to end, component and oracle | Passed |
| TC-047 | 12 issues / 80 SLOC = 0.15 | Component | Passed |
| TC-048 | SLOC 0 gives density 0.0, no ZeroDivisionError | Component | **Partly executed**: see the conflict below |
| TC-049 | Syntax error logged with path, hash, `SyntaxError`; analysis completes | End to end | Passed |
| TC-050 | Latin-1 file logged with `UnicodeDecodeError`; others analysed | End to end | Passed |
| TC-051 | Empty and binary .py files logged and skipped; status Complete | End to end | Passed |
| TC-052 | Progress page within 2 s (FX-L) | Browser | **Not executed**: FX-L does not exist and the case is measured in the UI |
| TC-053 | Pages navigable during a run (FX-L) | Browser | **Not executed**: same reason |
| TC-054 | ≥ 10 integer, non-decreasing progress values ending at 100 (FX-100) | Frontend | **Not executed**: FX-100 does not exist and the case records values received by the frontend |
| TC-068 | [100, 150, 200] → [0.0, 0.5, 1.0] | Component | Passed |
| TC-069 | Five normalised metrics in 0.0–1.0 with min 0, max 1 | End to end (FX-M stand-in) | Passed |
| TC-070 | Constant MI → all 0.0, no error | Component | Passed (expected value is the OI-01 proposal, not yet approved) |
| TC-071 | Weights give DS 37.5 | Component | Passed |
| TC-072 | Extremes give 0.0 and 100.0 | Component | Passed |
| TC-073 | Every stored DS in 0–100, no NULL | End to end (FX-M stand-in) | Passed |
| TC-074 | First commit's delta is 0.0 (FX-S) | End to end | Passed |
| TC-075 | [42.0, 48.5, 40.0] → [0.0, +6.5, −8.5] | Component | Passed |
| TC-076 | +6.5 → Debt Spike | Component | Passed |
| TC-077 | −8.5 → Refactoring Drop | Component | Passed |
| TC-078 | ±5.0 → Stable | Component | Passed |
| TC-079 | ±5.01 → Spike / Drop | Component | Passed |
| TC-080 | 10 rows; the 3 complex files at ranks 1–3 | End to end (FX-RISK stand-in) | Passed (ranking rule is the OI-03 proposal) |
| TC-081 | 4 files: all 4 listed, no placeholder rows | End to end | Passed |
| TC-082 | Dominant metric shown and consistent with the values | End to end (FX-RISK stand-in) | Passed (labels changed to the workbook's 'Complexity' / 'Smell density'; OI-03) |
| TC-083 | 3 Spikes = Steady Growth; Spike, Spike, Stable = none | Component | Passed |
| TC-084 | Stable, Drop, Drop, Stable: commits 2–3 Refactoring Event | Component | Passed |
| TC-085 | 5 Stable = Stable Phase; 4 Stable then Spike = none | Component | Passed |

Summary: 35 of 39 cases pass as automated tests (13 of them on the end-to-end path
with stored rows, 4 more on stand-in fixtures built to the case description). TC-048
is partly executed because of the conflict below. TC-052, TC-053 and TC-054 are not
executed.

## Change made while validating

`scoring._dominant` returned "cc" / "smell_density". TC-082 names the labels
'Complexity' and 'Smell density', so it now returns those (and "None" when both
normalised values are 0). `tests/product/test_scoring.py` was updated to match. No
threshold or weight changed.

## Conflict found: TC-048 and TC-051

TC-048 expects an **empty** `__init__.py` to be analysed with smell density 0.0. TC-051
and FR-16 expect an empty (0-byte) .py file to be logged and skipped. TDEA follows
TC-051 and FR-16. The density rule itself (SLOC 0 gives 0.0, no ZeroDivisionError) is
tested on a non-empty `__init__.py` with SLOC 0. In a real repository this matters: the
pallets/itsdangerous smoke run recorded 56 file errors over 69 analysed commits, from an
empty `__init__.py`. The
decision is listed in `docs/verification/open-decisions.md`.
