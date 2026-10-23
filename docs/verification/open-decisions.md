# Open decisions affecting the product branch

Status on 23 Oct 2026. **None of these items is approved.** Where the code needed an
answer to run, it uses a provisional reading. The reading is named in the module that
applies it, so that a decision changes one place. A provisional reading is not a
decision. The recommendations below are proposals for the owner: Shubham as project
owner, and the Research Methodology (RM) side where the SRS names it. Items marked
"FCN" would change frozen behaviour and need a Freeze Change Notice.

## Research engine

This branch carries the web product only. The research decisions (CL-01, CL-03 to CL-06,
CL-13 to CL-20, the RG1 run-file readings and the RG10 bundle content hash) concern code
that is either unchanged here from `main` or under review in PR #1, and they are recorded
with that PR. None of them is approved. In particular, the Specification Freeze and SRS
v2.3 are unsigned (CL-01), and real RG1 runs are refused until the versioned lists exist
(CL-03).

## Product scoring: open issues (SRS v2.3 Appendix B)

All six are implemented as the Appendix B "proposed resolution" and marked provisional
in `tdea/aggregator/scoring.py`, `lock.py` (OI-04) and `results.py` (OI-06).

| Item | Question | Implemented proposal | Recommended resolution |
| --- | --- | --- | --- |
| OI-01 | Normalisation when max = min | Every value 0.0 (TC-070 asks to confirm) | Approve as proposed |
| OI-02 | Smells_norm and SLOC_growth definitions | Normalised mean smell density; SLOC change from the previous commit, 0 for the first | Approve as proposed |
| OI-03 | High-risk ranking and labels | Mean of normalised mean CC and mean density; High/Medium/Low by tercile; dominant metric labelled 'Complexity' or 'Smell density' (TC-082) | Approve as proposed, with the workbook's labels |
| OI-04 | Second analysis while one runs | Rejected with a clear message | Approve as proposed |
| OI-05 | Overall trend rule | Sign of the least-squares slope, with a dead band of 0.1 DS points per commit for Stable | Approve the rule and fix the dead-band value. The SRS says only "small", so 0.1 is a value the owner must confirm |
| OI-06 | Comparison CSV with unequal lengths | One row per sequence number up to the longer repository; blank cells for the shorter one | Approve as proposed |

## Found during this work

| Item | Issue | Recommended resolution |
| --- | --- | --- |
| TC-048 vs TC-051 / FR-16 | TC-048 expects an empty `__init__.py` to be analysed (density 0.0). TC-051 and FR-16 expect empty .py files to be logged and skipped. TDEA follows FR-16. | Correct TC-048's input to a non-empty file with SLOC 0 (for example a comment-only `__init__.py`). This keeps FR-16 unchanged, but it is a test-record change and needs the workbook owner's approval. The alternative, analysing 0-byte files as SLOC 0, would change FR-16. |
| TC-033 vs FR-30 | TC-033 expects the status to read 'Cancelled' after Cancel. FR-30 allows only Pending, Running, Complete and Error, so TDEA shows Error with the error type "Analysis cancelled". | Correct TC-033's expected result to "status Error, error type 'Analysis cancelled'" (a test-record change for the workbook owner), or add a Cancelled status to FR-30 (an SRS change). TDEA follows FR-30 until one is approved. |
| Generated fixtures | The workbook names FX-M, FX-L, FX-XL, FX-100/101/102, FX-RISK, FX-UP and FX-DOWN but ships none. Version 1 of each is now built by `tests/fixtures/make_fx_product.py` to the workbook's fixture table. FX-EMPTY and FX-PRIV must be real GitHub repositories and do not exist. | The Product Owner accepts the generated builds as the official fixtures (or supplies others), and creates an empty and a private repository for FX-EMPTY and FX-PRIV. |
| TC-087 Pending | FR-30 lists Pending. A submitted analysis was stored as Running at once, so Pending never appeared. It is now Pending until its run begins. A single submission still leaves Pending within milliseconds, so TC-087's "Pending → Running → Complete" can be seen in the browser only for the second repository of a comparison pair. | Accept the change as an FR-30 fix. Workbook owner: note in TC-087 that Pending is visible only while an analysis waits for its turn. |
| TC-104 standard deviation | The PDF's statistics give the population standard deviation. TC-104 suggests checking with pandas describe(), which gives the sample value (n - 1). For FX-M's 50 commits the two differ at 2 decimals. | Workbook owner: state which value TC-104 expects. If it is the sample value, change `scoring.statistics` to `stdev` (a one-line change) and the expected failure in `test_workbook_backend_cases.py` becomes a pass. |
| TC-129 repository without Python | TC-129 expects a repository with no .py files to be refused with 400 or 422 at submission. The FR-03 check cannot see a repository's files, so TDEA accepts it (202) and ends the analysis as an Error after the clone, with a three-part message. | Accept the post-clone Error for this input (test-record change), or approve a pre-check through the GitHub languages API (an addition to FR-03, needing the SRS owner). |
| Fixture details in TC-031, TC-061, TC-062, TC-066 | The generated fixtures differ from the workbook text: FX-S deletes c.py at commit #10 (not #7) and has no `src/util.py`; FX-ERR has 4 failing files (not 3); TDEA's CC metric types are `mean_cc` and `max_cc` (not `cc`). | Workbook owner: align the case text with the accepted fixtures, or supply fixtures that match the text. |
| Pylint file set (FR-14) | Pylint's counts for a file depend on which files are linted with it. Duplicate-code is found across files, and an import of another repository module resolves only when its package is linted too. TDEA lints each commit's new file versions together and reuses a version's counts while it is unchanged (for NFR-01). The counts are deterministic, but they differ from a fresh lint of the whole tree (FX-M: 240 of 275 file rows agree; itsdangerous: 65 of 67). See `metric-validation.md`. | Product Owner: choose the reference. (a) Keep the current behaviour and document it. (b) Lint the whole tree at every analysed commit: complete cross-file messages, longer analyses (NFR-01 to be re-measured). (c) Lint each file alone, as TC-043 to TC-046 do. TDEA is unchanged until a choice is made. |
| SRS 2.4 prerequisites | Git is not listed, although GitPython needs the `git` command. | Add Git to section 2.4 in the next SRS revision (editorial). The README states it meanwhile. |

## Change request and integration evidence

| Item | Status | Next action |
| --- | --- | --- |
| CR-01 (Selenium automation out of scope) | **Proposed, pending approval.** The browser cases stay in scope. From 23 Oct, 46 of them are automated with Selenium in `tests/browser` and run in the CI job "browser (Selenium, headless Chrome)" (results in `workbook-browser-cases.md`). This is developer evidence: the workbook's ACTUAL RESULT and STATUS columns are unchanged. TC-010, TC-011 and TC-013 are not executed (they need real GitHub repositories or a real token). | The owner rejects CR-01, so that the Selenium suite becomes the formal record for these cases, or approves it, and the change record says so. Either way, the workbook owner records results from a run on the reference setup. |
| FR-03 genuine integration | The product suite and the real-repository smoke run stub the GitHub check: this sandbox's proxy blocks api.github.com. `tests/product/test_github_live.py` sends the real request and runs only when `TDEA_LIVE_GITHUB=1`. Elsewhere it is reported as skipped. | The CI job "live GitHub API (FR-03)" runs it on a GitHub-hosted runner. Its result on the PR head is the integration evidence. A manual `tdea serve` run against a public URL on a networked machine would complete TC-009 to TC-015 through the UI. |
