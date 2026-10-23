# Workbook v1.2: metric and scoring cases, automated results

Branch `product-implementation` (first recorded on `sprint-implementation`), 23 Oct 2026. Sheets STATIC ANALYSIS (TC-034 to TC-054)
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
calc.py and smells_*.py are built in the test file from the case's own description. FX-M
and FX-RISK are built by `tests/fixtures/make_fx_product.py` (version 1, 23 Oct) to the
workbook's fixture table; whether those builds are accepted as the official fixtures is
the Product Owner's decision.

| Input | Built as |
| --- | --- |
| calc.py (TC-034) | 50 lines: 2 header comments, an import, 2 blanks, 5 functions of 6 code lines each followed by 1 comment and 1 blank, a comment, and 4 module-level assignments |
| smells_c/r/w/e.py (TC-043 to TC-046) | As the INPUT column describes: no module docstring and 2 bad names; 14 branches; an unused import and an unused variable; 2 undefined names |
| FX-M (TC-069, TC-073) | `build_fx_m`: 50 commits; four graded modules and a fixed 7-commit cycle of stable (docstring-only), spike (one module raised to level 10) and drop (back to level 2) changes; commit 26 adds a fifth module. The analysis classifies its commits as Spike, Drop and Stable |
| FX-RISK (TC-080, TC-082) | `build_fx_risk`: 15 files, 12 small documented modules and 3 modules with 12, 16 and 20 branches, unused imports and bad names |
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
| TC-052 | Progress page within 2 s (FX-L) | Backend and browser | Passed: POST answers 202 in under 2 s; in Chrome the progress page shows within 2 s of the click (see `workbook-browser-cases.md`) |
| TC-053 | Pages navigable during a run (FX-L) | Backend and browser | Passed: other endpoints answer 200 during the run; in Chrome, Home, an overview and a commit history load during the FX-L run, which completes |
| TC-054 | ≥ 10 integer, non-decreasing progress values ending at 100 (FX-100) | Backend and browser | Passed: the values the engine emits, and the values the frontend received and the bar showed in Chrome |
| TC-068 | [100, 150, 200] → [0.0, 0.5, 1.0] | Component | Passed |
| TC-069 | Five normalised metrics in 0.0–1.0 with min 0, max 1 | End to end (generated FX-M) | Passed |
| TC-070 | Constant MI → all 0.0, no error | Component | Passed (expected value is the OI-01 proposal, not yet approved) |
| TC-071 | Weights give DS 37.5 | Component | Passed |
| TC-072 | Extremes give 0.0 and 100.0 | Component | Passed |
| TC-073 | Every stored DS in 0–100, no NULL | End to end (generated FX-M) | Passed |
| TC-074 | First commit's delta is 0.0 (FX-S) | End to end | Passed |
| TC-075 | [42.0, 48.5, 40.0] → [0.0, +6.5, −8.5] | Component | Passed |
| TC-076 | +6.5 → Debt Spike | Component | Passed |
| TC-077 | −8.5 → Refactoring Drop | Component | Passed |
| TC-078 | ±5.0 → Stable | Component | Passed |
| TC-079 | ±5.01 → Spike / Drop | Component | Passed |
| TC-080 | 10 rows; the 3 complex files at ranks 1–3 | End to end (generated FX-RISK) | Passed (ranking rule is the OI-03 proposal) |
| TC-081 | 4 files: all 4 listed, no placeholder rows | End to end | Passed |
| TC-082 | Dominant metric shown and consistent with the values | End to end (generated FX-RISK) | Passed (labels changed to the workbook's 'Complexity' / 'Smell density'; OI-03) |
| TC-083 | 3 Spikes = Steady Growth; Spike, Spike, Stable = none | Component | Passed |
| TC-084 | Stable, Drop, Drop, Stable: commits 2–3 Refactoring Event | Component | Passed |
| TC-085 | 5 Stable = Stable Phase; 4 Stable then Spike = none | Component | Passed |

Summary: 38 of 39 cases pass as automated tests (13 of them on the end-to-end path
with stored rows, 4 more on the generated FX-M and FX-RISK, and TC-052 to TC-054 in the
browser as well). TC-048 is partly executed because of the conflict below.

## Other cases unlocked by the generated fixtures (23 Oct)

`tests/product/test_workbook_fixture_cases.py`, end to end through `AnalysisService`
with only the FR-03 check stubbed:

| Case | Fixture | Result |
| --- | --- | --- |
| TC-025 | FX-100: all 100 commits analysed | Passed |
| TC-026 | FX-101, default N = 2: #1, #3 … #101 (51) | Passed |
| TC-027 | FX-102, N = 4: #1, #5 … #101 and HEAD #102 (27) | Passed |
| TC-028 | FX-101, N = 1: all 101 | Passed |
| TC-033 | FX-M, Cancel at 30 %: temporary clone removed (also in Chrome, `workbook-browser-cases.md`) | **Partly passed**: the case also expects the status 'Cancelled', but FR-30 allows only Pending, Running, Complete and Error; TDEA shows Error with "Analysis cancelled" |
| TC-067 | FX-M, Cancel at 30 %: audit entry with processed < requested | Passed after a fix: the cancelled run's audit line had no "commits requested"; it now records the selected and the analysed commits |
| TC-122 | FX-UP 'Increasing', FX-DOWN 'Decreasing' (compared) | Passed (trend rule is the OI-05 proposal) |
| TC-126 | FX-XL (500 commits), N = 3, ≤ 900 s | Runs only with TDEA_SLOW_TESTS=1; see the evidence of the run |

FX-L and FX-XL have exactly 200 and 500 commits. FX-EMPTY and FX-PRIV must be real GitHub
repositories and do not exist yet, so TC-010 and TC-011 remain not executed.

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
