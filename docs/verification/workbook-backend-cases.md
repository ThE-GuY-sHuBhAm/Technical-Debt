# Workbook v1.2: backend cases, automated results

Branch `product-acceptance`, 23 Oct 2026. These are workbook cases whose checks are on
tdea.db, the REST API and the exported files, so they need no browser. They are
automated in `tests/product/test_workbook_backend_cases.py` and run in every CI test job.
They are **developer evidence**. The workbook's ACTUAL RESULT and STATUS columns are
unchanged.

## How the cases run

- Every analysis goes through the real AnalysisService, ProductEngine (clone, radon,
  Pylint) and Flask application. Only the FR-03 GitHub check is stubbed.
- The repositories are the generated fixtures of `tests/fixtures/make_fx_product.py`
  (FX-S, FX-M, FX-ERR, FX-100, fx-cmp-a and fx-cmp-b), plus small repositories built in
  the test for TC-108, TC-127 and TC-129.
- The PDF report is read with pypdf 5.1.0, now in the dev extra.
- TC-130 runs the analysis in a child process (`tests/fixtures/product_kill_target.py`)
  and kills it with SIGKILL (TerminateProcess on Windows).
- Times were measured on the development container and the CI runners, not on the
  reference machine of NFR-01 to NFR-03.

## Results (23 Oct 2026, local run: 46 passed, 1 expected failure)

| Case | What is checked | Result |
| --- | --- | --- |
| TC-015 | After an analysis with a PAT, the token is in no file under the workspace (tdea.db, audit log, clone folder) and in no log record | Passed (checked with TC-141) |
| TC-017 | The clone holds FX-S's 12 commits | Passed. TDEA has no debug mode, so the test reads the clone where analysis begins, through a wrapped analyzer |
| TC-018 | The clone's remote branches equal `git ls-remote --heads` (3 branches) | Passed, as TC-017 |
| TC-020 | 12 stored hashes equal `git log --format=%H` | Passed |
| TC-021 | The 120-character message is stored as its first 72 characters | Passed |
| TC-022 | Stored author name and email equal `git log` for every commit | Passed |
| TC-023 | 2026-09-10 18:30 +05:30 is stored as 2026-09-10T13:00:00Z | Passed |
| TC-024 | Commit #10 lists a.py added, b.py modified, c.py deleted | Passed (FX-S, commit #10) |
| TC-032 | After the analyses no clone is left in the temporary folder | Passed |
| TC-030 | At FX-S commits #2, #6 and #12, each file's LOC equals the file at that commit (`git show`) | Passed |
| TC-031 | A file deleted at a commit has rows only while it exists | Passed. FX-S deletes `fxs/c.py` at commit #10, not #7 |
| TC-055 | Four tables with FR-19's primary and foreign keys | Passed |
| TC-056 | `PRAGMA foreign_key_check` returns no rows | Passed |
| TC-057 | A second aggregate row for a commit is refused (UNIQUE) | Passed |
| TC-058, TC-109 | After a restart: the same results, CSV and PDF; no clone and no new audit entry | Passed |
| TC-059 | A query for FX-S returns FX-S commits only | Passed |
| TC-060 | File metrics for commit #6's hash are that commit's rows only | Passed |
| TC-061 | One file's metrics, one row per commit, in commit order | Passed, on `fxs/geometry.py` (FX-S has no `src/util.py`) |
| TC-062 | One metric type only | Passed. The type is `mean_cc` (or `max_cc`); `cc` is refused with a 400 whose action names `mean_cc` |
| TC-063 | Unknown commit hash: structured not found, not a server error | Passed |
| TC-064 | Resubmission answered from tdea.db in under 3 s, with no clone | Passed |
| TC-065 | A new URL after FX-S runs the full pipeline and has its own results | Passed |
| TC-066 | FX-ERR audit entry: URL, start and end, commits requested and processed, file errors | Passed. FX-ERR has 4 failing files, not 3 |
| TC-103 | FX-M PDF: metadata, Debt Score timeline (drawn), top-10 table | Passed |
| TC-104 | PDF statistics: mean, minimum, maximum equal the CSV's to 2 decimals | **Partly passed**: the standard deviation is the population value. pandas describe(), which the case suggests, gives the sample value, so that comparison fails (recorded as an expected failure; see `open-decisions.md`) |
| TC-105 | PDF patterns with commit ranges; radon, Pylint and the weights 0.30/0.30/0.25/0.15 | Passed |
| TC-107 | FX-M CSV: 50 rows; rows 1, 25 and 50 equal tdea.db | Passed |
| TC-108 | Author "Doe, Jane" stays in one CSV cell | Passed (Python csv module; pandas is not a dependency) |
| TC-110 | Export of repository 999: structured not found | Passed |
| TC-114 | A comparison stores two repositories, each with its own commits and metrics | Passed |
| TC-120 | Comparison CSV of 10 and 15 commits: 15 rows, repository 1 blank for 11 to 15 | Passed (OI-06 proposal) |
| TC-125 | Eleven GET endpoints, 20 calls each: 95th percentile within 2 s | Passed (development container) |
| TC-127 | 45 valid and 5 corrupt files: Complete, 5 logged errors, 45 files measured | Passed |
| TC-128 | FX-M analysed three times from an empty cache: equal CSV MD5 | Passed |
| TC-129, TC-133 | Nine invalid inputs: 400 or 422 with type, cause and next step; token not echoed | Passed. The FR-03 answers (not found, private, empty, bad token) come from the stub |
| TC-129, tenth input | A repository without .py files | **Partly passed**: it is accepted (202) and ends as an Error with a three-part message after the clone. The case expects 400 or 422 at submission (see `open-decisions.md`) |
| TC-130 | Process killed at 20 % or more: integrity_check ok, no partial commit rows, run marked interrupted on restart | Passed (POSIX locally; CI covers Windows and macOS) |
| TC-141 | No PAT in tdea.db, the audit log, the clone folder, the exports, the API answers or the log records | Passed |

## Not covered here

| Case | Where |
| --- | --- |
| TC-134, TC-135, TC-137, TC-144 | CI gates (Pylint, pydocstyle, coverage, pinned versions) |
| TC-136 | `tests/architecture/test_layer_imports.py` |
| TC-019, TC-123, TC-131, TC-132, TC-139, TC-140, TC-142, TC-143, TC-145 | Manual or reference-machine cases; not executed |
