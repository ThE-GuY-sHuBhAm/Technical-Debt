# Workbook v1.2: coverage of all 145 cases

Branch `product-acceptance`, 23 Oct 2026. One row per case of
`TestCases_TechnicalDebtEvaluator_v1.2.xlsx`, with the requirements the workbook
traces it to and where its automated result is recorded:

- **metric/scoring:** `workbook-metric-scoring-cases.md` (`tests/product/test_workbook_cases.py`,
  `tests/product/test_workbook_fixture_cases.py`);
- **browser:** `workbook-browser-cases.md` (`tests/browser`, headless Chrome);
- **backend:** `workbook-backend-cases.md` (`tests/product/test_workbook_backend_cases.py`).

This is **developer evidence**, not the workbook's execution record. The ACTUAL RESULT
and STATUS columns of the workbook are unchanged. In particular:

- "Passed" means an automated test asserting the EXPECTED RESULT passed in the local run
  of 23 Oct; CI runs the same tests on every push. It is not formal acceptance.
- Timed cases were measured on the development container and CI runners, not on the
  reference machine.
- The FX fixtures are generated (`tests/fixtures/make_fx_product.py`) and await the
  Product Owner's acceptance.
- Cases whose expected values follow OI-01 to OI-06 pass against the SRS proposals,
  which are not yet approved.
- FR-03 answers in the browser and backend cases come from a stubbed transport. The
  real request is tested by the CI job "live GitHub API (FR-03)".

## Summary

| Status | Cases |
| --- | --- |
| Passed | 123 |
| Passed (gate) | 5 |
| Automated, opt-in | 1 |
| Partly met | 7 |
| Not executed | 9 |
| **Total** | **145** |

## Cases

| Case | Workbook area | Requirements | Status | Recorded in | Note |
| --- | --- | --- | --- | --- | --- |
| TC-001 | HOME PAGE - URL FIELD | FR-01 | Passed | browser | After a fix (the label read "GitHub repository URL") |
| TC-002 | REPOSITORY URL | FR-01, FR-02 | Passed | browser | Stubbed transport |
| TC-003 | REPOSITORY URL | FR-02 | Passed | browser | Stubbed transport |
| TC-004 | REPOSITORY URL | FR-02, FR-05 | Passed | browser |  |
| TC-005 | REPOSITORY URL | FR-02 | Passed | browser |  |
| TC-006 | REPOSITORY URL | FR-02 | Passed | browser |  |
| TC-007 | REPOSITORY URL | FR-02 | Passed | browser |  |
| TC-008 | REPOSITORY URL | FR-01, FR-05 | Passed | browser |  |
| TC-009 | ACCESSIBILITY CHECK | FR-03, FR-05 | Passed | browser | Stubbed transport |
| TC-010 | ACCESSIBILITY CHECK | FR-03, FR-05 | Not executed |  | FX-PRIV and FX-EMPTY must be real GitHub repositories and do not exist |
| TC-011 | ACCESSIBILITY CHECK | FR-03, FR-05 | Not executed |  | FX-PRIV and FX-EMPTY must be real GitHub repositories and do not exist |
| TC-012 | PAT FIELD | FR-04 | Passed | browser | Stubbed transport |
| TC-013 | PAT FIELD | FR-04 | Not executed |  | Needs a real Personal Access Token and the real GitHub API |
| TC-014 | PAT FIELD | FR-04, FR-05 | Passed | browser | On re-run (stubbed transport; see the results document) |
| TC-015 | PAT FIELD | FR-04 | Passed | backend | Checked with TC-141 |
| TC-016 | ERROR MESSAGE | FR-05 | Passed | browser |  |
| TC-017 | REPOSITORY CLONE | FR-06 | Passed | backend | TDEA has no debug mode, so the test reads the clone where analysis begins, through a wrapped analyzer |
| TC-018 | REPOSITORY CLONE | FR-06 | Passed | backend | As TC-017 |
| TC-019 | REPOSITORY CLONE | FR-06, FR-10 | Passed | backend | FX-M is served by `git daemon` through a relay that drops the connection after 4 KiB of the pack; the workbook disconnects the network during a clone of psf/requests |
| TC-020 | COMMIT HISTORY - HASH | FR-07 | Passed | backend |  |
| TC-021 | COMMIT HISTORY - MESSAGE | FR-07 | Passed | backend |  |
| TC-022 | COMMIT HISTORY - AUTHOR | FR-07 | Passed | backend |  |
| TC-023 | COMMIT HISTORY - TIMESTAMP | FR-07 | Passed | backend |  |
| TC-024 | COMMIT HISTORY - CHANGED FILES | FR-07 | Passed | backend | FX-S, commit #10 |
| TC-025 | COMMIT SAMPLING | FR-08 | Passed | metric/scoring |  |
| TC-026 | COMMIT SAMPLING | FR-08 | Passed | metric/scoring |  |
| TC-027 | COMMIT SAMPLING | FR-08 | Passed | metric/scoring |  |
| TC-028 | COMMIT SAMPLING | FR-08 | Passed | metric/scoring |  |
| TC-029 | COMMIT SAMPLING | FR-08 | Passed | browser | After a fix (the message did not contain those words) |
| TC-030 | COMMIT CHECKOUT | FR-09 | Passed | backend |  |
| TC-031 | COMMIT CHECKOUT | FR-09 | Passed | backend | FX-S deletes `fxs/c.py` at commit #10, not #7 |
| TC-032 | TEMP CLEANUP | FR-10 | Passed | backend |  |
| TC-033 | TEMP CLEANUP | FR-10 | Partly met | browser, metric/scoring | the case also expects the status 'Cancelled', but FR-30 allows only Pending, Running, Complete and Error; TDEA shows Error with "Analysis cancelled" |
| TC-034 | LOC METRICS | FR-11 | Passed | metric/scoring |  |
| TC-035 | LOC METRICS | FR-11 | Passed | metric/scoring |  |
| TC-036 | LOC METRICS | FR-11 | Passed | metric/scoring |  |
| TC-037 | CYCLOMATIC COMPLEXITY | FR-12 | Passed | metric/scoring |  |
| TC-038 | CYCLOMATIC COMPLEXITY | FR-12 | Passed | metric/scoring |  |
| TC-039 | CYCLOMATIC COMPLEXITY | FR-12 | Passed | metric/scoring |  |
| TC-040 | CYCLOMATIC COMPLEXITY | FR-12 | Passed | metric/scoring |  |
| TC-041 | MAINTAINABILITY INDEX | FR-13 | Passed | metric/scoring |  |
| TC-042 | MAINTAINABILITY INDEX | FR-13 | Passed | metric/scoring |  |
| TC-043 | CODE SMELLS - CONVENTION (C) | FR-14 | Passed | metric/scoring |  |
| TC-044 | CODE SMELLS - REFACTOR (R) | FR-14 | Passed | metric/scoring |  |
| TC-045 | CODE SMELLS - WARNING (W) | FR-14 | Passed | metric/scoring |  |
| TC-046 | CODE SMELLS - ERROR (E) | FR-14 | Passed | metric/scoring |  |
| TC-047 | SMELL DENSITY | FR-15 | Passed | metric/scoring |  |
| TC-048 | SMELL DENSITY | FR-15 | Partly met | metric/scoring | TC-048 conflicts with TC-051 and FR-16 (empty file); see open-decisions.md |
| TC-049 | FAILURE ISOLATION | FR-16 | Passed | metric/scoring |  |
| TC-050 | FAILURE ISOLATION | FR-16 | Passed | metric/scoring |  |
| TC-051 | FAILURE ISOLATION | FR-16 | Passed | metric/scoring |  |
| TC-052 | BACKGROUND EXECUTION | FR-17 | Passed | browser, metric/scoring | POST answers 202 in under 2 s; in Chrome the progress page shows within 2 s of the click (see `workbook-browser-cases.md`) |
| TC-053 | BACKGROUND EXECUTION | FR-17 | Passed | browser, metric/scoring | Other endpoints answer 200 during the run; in Chrome, Home, an overview and a commit history load during the FX-L run, which completes |
| TC-054 | PROGRESS EVENTS | FR-18 | Passed | browser, metric/scoring | The values the engine emits, and the values the frontend received and the bar showed in Chrome; After a fix (see TC-099) |
| TC-055 | DATABASE SCHEMA | FR-19 | Passed | backend |  |
| TC-056 | DATABASE SCHEMA | FR-19 | Passed | backend |  |
| TC-057 | DATABASE SCHEMA | FR-19 | Passed | backend |  |
| TC-058 | SESSION PERSISTENCE | FR-20 | Passed | backend |  |
| TC-059 | QUERY - REPOSITORY ID | FR-21 | Passed | backend |  |
| TC-060 | QUERY - COMMIT HASH | FR-21 | Passed | backend |  |
| TC-061 | QUERY - FILE PATH | FR-21 | Passed | backend | On `fxs/geometry.py` (FX-S has no `src/util.py`) |
| TC-062 | QUERY - METRIC TYPE | FR-21 | Passed | backend | The type is `mean_cc` (or `max_cc`); `cc` is refused with a 400 whose action names `mean_cc` |
| TC-063 | QUERY - INVALID KEY | FR-21 | Passed | backend |  |
| TC-064 | RESULT CACHE | FR-22 | Passed | backend |  |
| TC-065 | RESULT CACHE | FR-22 | Passed | backend |  |
| TC-066 | AUDIT LOG | FR-23 | Passed | backend | FX-ERR has 4 failing files, not 3 |
| TC-067 | AUDIT LOG | FR-23 | Passed | metric/scoring | After a fix: the cancelled run's audit line had no "commits requested"; it now records the selected and the analysed commits |
| TC-068 | NORMALISATION | FR-24 | Passed | metric/scoring |  |
| TC-069 | NORMALISATION | FR-24 | Passed | metric/scoring |  |
| TC-070 | NORMALISATION | FR-24 | Passed | metric/scoring | Expected value is the OI-01 proposal, not yet approved |
| TC-071 | DEBT SCORE | FR-25 | Passed | metric/scoring |  |
| TC-072 | DEBT SCORE | FR-25 | Passed | metric/scoring |  |
| TC-073 | DEBT SCORE | FR-25 | Passed | metric/scoring |  |
| TC-074 | DEBT DELTA | FR-26 | Passed | metric/scoring |  |
| TC-075 | DEBT DELTA | FR-26 | Passed | metric/scoring |  |
| TC-076 | COMMIT CLASSIFICATION | FR-27 | Passed | metric/scoring |  |
| TC-077 | COMMIT CLASSIFICATION | FR-27 | Passed | metric/scoring |  |
| TC-078 | COMMIT CLASSIFICATION | FR-27 | Passed | metric/scoring |  |
| TC-079 | COMMIT CLASSIFICATION | FR-27 | Passed | metric/scoring |  |
| TC-080 | HIGH-RISK FILES | FR-28 | Passed | metric/scoring | Ranking rule is the OI-03 proposal |
| TC-081 | HIGH-RISK FILES | FR-28 | Passed | metric/scoring |  |
| TC-082 | HIGH-RISK FILES | FR-28 | Passed | metric/scoring | Labels changed to the workbook's 'Complexity' / 'Smell density'; OI-03 |
| TC-083 | DEBT PATTERN | FR-29 | Passed | metric/scoring |  |
| TC-084 | DEBT PATTERN | FR-29 | Passed | metric/scoring |  |
| TC-085 | DEBT PATTERN | FR-29 | Passed | metric/scoring |  |
| TC-086 | OVERVIEW CARD | FR-30 | Passed | browser |  |
| TC-087 | OVERVIEW CARD | FR-30 | Partly met | browser | a single submission is Pending only until its thread starts, too short for the 1 s poll to see. Pending is seen for the second repository of a pair (TC-113) and in a service test |
| TC-088 | LOC CHART | FR-31 | Passed | browser |  |
| TC-089 | LOC CHART | FR-31 | Passed | browser |  |
| TC-090 | COMPLEXITY CHART | FR-32 | Passed | browser |  |
| TC-091 | CODE SMELL CHART | FR-33 | Passed | browser | All 50 commits |
| TC-092 | MI CHART | FR-34 | Passed | browser |  |
| TC-093 | DEBT SCORE TIMELINE | FR-35 | Passed | browser |  |
| TC-094 | HIGH-RISK TABLE | FR-36 | Passed | browser | Rank and Dominant metric columns are extra |
| TC-095 | HIGH-RISK TABLE | FR-36 | Passed | browser | After a fix (see the results document) |
| TC-096 | COMMIT DRILL-DOWN | FR-37 | Passed | browser |  |
| TC-097 | COMMIT DRILL-DOWN | FR-37 | Passed | browser |  |
| TC-098 | PROGRESS BAR | FR-38 | Passed | browser |  |
| TC-099 | PROGRESS BAR | FR-38 | Passed | browser | After a fix (see the results document) |
| TC-100 | NAVIGATION | FR-39 | Passed | browser |  |
| TC-101 | NAVIGATION | FR-39 | Passed | browser |  |
| TC-102 | PDF REPORT | FR-40 | Partly met | browser | opening it in a standard PDF viewer is manual and not done |
| TC-103 | PDF REPORT | FR-40 | Passed | backend |  |
| TC-104 | PDF REPORT | FR-40 | Partly met | backend | the standard deviation is the population value. pandas describe(), which the case suggests, gives the sample value, so that comparison fails (recorded as an expected failure; see `open-decisions.md`) |
| TC-105 | PDF REPORT | FR-40 | Passed | backend |  |
| TC-106 | CSV EXPORT | FR-41 | Passed | browser |  |
| TC-107 | CSV EXPORT | FR-41 | Passed | backend |  |
| TC-108 | CSV EXPORT | FR-41 | Passed | backend | Python csv module; pandas is not a dependency |
| TC-109 | CACHE-SERVED EXPORT | FR-42 | Passed | backend |  |
| TC-110 | CACHE-SERVED EXPORT | FR-42 | Passed | backend |  |
| TC-111 | EXPORT TIME | FR-43 | Passed | browser | Development container |
| TC-112 | EXPORT TIME | FR-43 | Passed | browser | Development container |
| TC-113 | COMPARISON VIEW | FR-44 | Passed | browser | Generated pair stands in for the experiment repositories |
| TC-114 | COMPARISON VIEW | FR-44 | Passed | backend |  |
| TC-115 | COMPARISON VIEW | FR-44 | Passed | browser | After a fix (see the results document) |
| TC-116 | COMPARISON VIEW | FR-22, FR-44 | Passed | browser |  |
| TC-117 | OVERLAY CHART | FR-45 | Passed | browser |  |
| TC-118 | OVERLAY CHART | FR-45 | Passed | browser |  |
| TC-119 | COMPARISON CSV | FR-46 | Passed | browser |  |
| TC-120 | COMPARISON CSV | FR-46 | Passed | backend | OI-06 proposal |
| TC-121 | STATISTICAL SUMMARY | FR-47 | Passed | browser |  |
| TC-122 | STATISTICAL SUMMARY | FR-47 | Passed | metric/scoring | Trend rule is the OI-05 proposal |
| TC-123 | PERFORMANCE - ANALYSIS TIME | NFR-01 | Not executed |  | Needs the reference machine (8 GB RAM, 2.0 GHz quad-core) |
| TC-124 | PERFORMANCE - DASHBOARD LOAD | NFR-02 | Passed | browser | Development container |
| TC-125 | PERFORMANCE - API RESPONSE | NFR-03 | Passed | backend | Development container |
| TC-126 | PERFORMANCE - LARGE REPOSITORY | NFR-04 | Automated, opt-in | metric/scoring | Runs only with TDEA_SLOW_TESTS=1 (500-commit analysis) |
| TC-127 | RELIABILITY - FAULT TOLERANCE | NFR-05 | Passed | backend |  |
| TC-128 | RELIABILITY - REPRODUCIBILITY | NFR-06 | Passed | backend |  |
| TC-129 | RELIABILITY - INVALID INPUT | NFR-07 | Partly met | backend | it is accepted (202) and ends as an Error with a three-part message after the clone. The case expects 400 or 422 at submission (see `open-decisions.md`) |
| TC-130 | RELIABILITY - DATA INTEGRITY | NFR-08 | Passed | backend | POSIX locally; CI covers Windows and macOS |
| TC-131 | USABILITY - INSTALLATION | NFR-09 | Not executed |  | Needs a volunteer who has not seen TDEA |
| TC-132 | USABILITY - CHART CLARITY | NFR-10 | Not executed |  | Needs a reviewer outside the team |
| TC-133 | USABILITY - ERROR QUALITY | NFR-11 | Passed | backend | The FR-03 answers (not found, private, empty, bad token) come from the stub |
| TC-134 | MAINTAINABILITY - CODE STYLE | NFR-12 | Passed (gate) |  | CI gate: Pylint every file >= 8.0 (quality gates job) |
| TC-135 | MAINTAINABILITY - TEST COVERAGE | NFR-13 | Passed (gate) |  | CI gate: coverage per package >= 70 % (tests jobs) |
| TC-136 | MAINTAINABILITY - MODULAR DESIGN | NFR-14 | Passed (gate) |  | Automated: tests/architecture/test_layer_imports.py (dashboard reaches api over HTTP only) |
| TC-137 | MAINTAINABILITY - DOCSTRINGS | NFR-15 | Passed (gate) |  | CI gate: pydocstyle (quality gates job) |
| TC-138 | SCALABILITY - MULTI-REPOSITORY | NFR-16 | Passed | browser |  |
| TC-139 | SCALABILITY - METRIC EXTENSIBILITY | NFR-17 | Not executed |  | Needs a prototype metric change and a review of its diff |
| TC-140 | SCALABILITY - LANGUAGE ADAPTER | NFR-18 | Not executed |  | Needs the Architecture & Design Document, not yet produced (CL-11) |
| TC-141 | SECURITY - PAT NOT STORED | NFR-19 | Passed | backend |  |
| TC-142 | SECURITY - DATA CONFINEMENT | NFR-20 | Not executed |  | Needs a network capture of a real analysis |
| TC-143 | PORTABILITY - CROSS-PLATFORM | NFR-21 | Partly met |  | CI runs the suite on ubuntu-22.04, macos-15 and windows-2025; the case names macOS 13 and Windows 11, so those runs are still manual |
| TC-144 | PORTABILITY - PYTHON VERSION | NFR-22 | Passed (gate) |  | CI gate: Python 3.10 / 3.11 / 3.12 matrix |
| TC-145 | REPRODUCIBILITY - CROSS-PLATFORM | NFR-23 | Passed | backend | In CI: the TC-128 test of each of the nine cells (Ubuntu 22.04, macOS 15, Windows Server 2025; Python 3.10 to 3.12) gave 69bb095660ee1e7d6e134d1211e32a9c (run 39969120720). The job "same FX-M CSV MD5 on every OS (TC-145)" compares them on every push |
