# Workbook v1.2: browser cases, automated results

Branch `product-acceptance`, 23 Oct 2026. These are the workbook's browser
(Selenium) cases, automated in `tests/browser` and run in headless Chrome. They are
**developer evidence**. The workbook's ACTUAL RESULT and STATUS columns are unchanged.
CR-01 (Selenium automation out of scope) is still only proposed, so these cases stay in
scope.

## How the cases run

- **Command:** `TDEA_BROWSER_TESTS=1 python -m pytest tests/browser`. Without the flag the
  cases are reported as skipped. The CI job "browser (Selenium, headless Chrome)" runs
  them on ubuntu-22.04.
- **What is real:**
  - the Flask application on 127.0.0.1;
  - AnalysisService and ProductEngine, with real clones, radon and Pylint;
  - tdea.db;
  - the built dashboard;
  - Chrome itself.
  The browser runs with `--no-proxy-server`. No case reaches the network.
- **FR-03 stubbed transport:** the real `GitHubClient` is used, but its HTTP call is
  answered by a stand-in for api.github.com:
  - 200 for the fixture repositories;
  - 404 for any other repository;
  - 401 for TC-014's token.
  The real request is tested by the CI job "live GitHub API (FR-03)".
- **Fixtures:**
  - Every github.com URL is cloned from a generated local fixture
    (`tests/fixtures/make_fx_product.py`), so `https://github.com/psf/requests` is FX-S here.
  - FX-S, FX-M, FX-RISK, FX-L, FX-UP, FX-DOWN and the comparison pair fx-cmp-a (10 commits,
    Sep 2026) and fx-cmp-b (15 commits, Oct 2026) are analysed once per session.
  - The cases that watch an analysis use their own repository names.
- **Reading charts and simulating input:**
  - Chart values, colours, annotations and tooltips are read from the Chart.js instance
    that `charts.jsx` leaves on each canvas.
  - Hovers and clicks are real mouse events at the drawn point's position.
  - A script installed before the page loads records each overview answer the frontend
    receives and each value the progress bar shows.
- **Timing:** times were measured on the development container, not on the reference
  machine of NFR-01 to NFR-03.

## Results (23 Oct 2026, local run: Chromium 141, 51 tests)

The first full run had 50 tests passed and 1 failed: TC-014. The test was at fault,
twice. It submitted a repository that was already in the cache, so no check, and no
token, was sent (FR-22). It also searched the page source, which holds the token typed
into the field itself. The test now uses an uncached repository, and it checks the
error message and the page's text. Re-run alone, it passed.

| Case | What is checked | Result |
| --- | --- | --- |
| TC-001 | Field labelled 'GitHub Repository URL' has the focus on load | Passed after a fix (the label read "GitHub repository URL") |
| TC-002 | psf/requests: no validation error; the FR-03 check follows | Passed (stubbed transport) |
| TC-003 | Trailing '/' accepted | Passed (stubbed transport) |
| TC-004 | GitLab URL: format error; no GitHub request and no clone | Passed |
| TC-005 | No https://: message names https://github.com/ | Passed |
| TC-006 | Owner-only URL: message names github.com/&lt;owner&gt;/&lt;repo&gt; | Passed |
| TC-007 | Branch-view URL rejected | Passed |
| TC-008 | 'Analyse' disabled while the URL field is empty | Passed |
| TC-009 | 404: 'Repository not found'; no clone | Passed (stubbed transport) |
| TC-012 | PAT field masked and marked optional; analysis starts without it | Passed (stubbed transport) |
| TC-014 | Bad PAT: 'Authentication failed'; token not echoed | Passed on re-run (stubbed transport; see the note above) |
| TC-016 | Error has type, cause and next step; no traceback | Passed |
| TC-029 | N = 0: 'N must be 1 or more'; nothing starts | Passed after a fix (the message did not contain those words) |
| TC-033 | Cancel at about 30 % (FX-M): back to Home, no clone left | **Partly passed**: the status is Error with "Analysis cancelled", not 'Cancelled' (conflict with FR-30, see `open-decisions.md`) |
| TC-052 | FX-L: progress page shown within 2 s of the click | Passed |
| TC-053 | During the FX-L run, Home, an overview and a commit history load; the run completes | Passed |
| TC-054 | FX-100: ≥ 10 integer, never-decreasing values received, ending at 100 | Passed after a fix (see TC-099) |
| TC-086 | FX-S overview card: name, owner, URL, 12 commits, Complete, last analysed | Passed |
| TC-087 | FX-M: Running then Complete; a failing repository shows Error | **Partly passed**: a single submission is Pending only until its thread starts, too short for the 1 s poll to see. Pending is seen for the second repository of a pair (TC-113) and in a service test |
| TC-088 | LOC chart: SLOC against commit sequence, 12 points | Passed |
| TC-089 | Point #6 tooltip: short hash, author, time, SLOC equal to CSV row 6 | Passed |
| TC-090 | Mean CC per commit; labelled line at CC = 5 | Passed |
| TC-091 | FX-M smells: 4 coloured series with a legend; stack = total_smells | Passed (all 50 commits) |
| TC-092 | FX-M MI: red band for MI < 25 | Passed |
| TC-093 | FX-M Debt Score: one point per commit; Spike, Drop and Stable markers differ and are named | Passed |
| TC-094 | FX-RISK high-risk table: required columns, 10 rows | Passed (Rank and Dominant metric columns are extra) |
| TC-095 | Every column: first click ascending, second descending | Passed after a fix (see below) |
| TC-096 | Clicking LOC point #6 lists every file of commit 6 with all columns | Passed |
| TC-097 | Commit 6 from the MI and Debt Score charts: same breakdown, equal to file_metrics | Passed |
| TC-098 | FX-100 bar: percent and short hash; at most 10 commits between updates | Passed |
| TC-099 | FX-S bar reaches 100 %, then the dashboard | Passed after a fix (see below) |
| TC-100 | Every section links to every other section | Passed |
| TC-101 | Commit history: 12 commits with hash, author, date, Debt Score | Passed |
| TC-102 | FX-M PDF downloads and is a complete PDF file | **Partly passed**: opening it in a standard PDF viewer is manual and not done |
| TC-106 | CSV row 1 is the FR-41 header | Passed |
| TC-111 | FX-L PDF downloaded within 10 s | Passed (development container) |
| TC-112 | FX-L CSV downloaded within 5 s | Passed (development container) |
| TC-113 | Two URLs accepted; both analyses start; the comparison opens | Passed; generated pair stands in for the experiment repositories |
| TC-115 | Invalid URL in field 2: error under field 2 only; field 1 kept | Passed after a fix (see below) |
| TC-116 | Pair chosen from history: no clone, no FR-03 request | Passed |
| TC-117 | One chart, two coloured lines, legend names both | Passed |
| TC-118 | x = commit sequence 1 to 15; A ends at 10, B at 15 | Passed |
| TC-119 | Comparison CSV row 1 is the FR-46 header | Passed |
| TC-121 | Summary panel equals mean, peak, spikes and drops recomputed from the CSV | Passed |
| TC-124 | FX-L dashboard ready within 3 s, three loads | Passed (development container) |
| TC-138 | Five repositories analysed without a restart; each dashboard shows its own | Passed |

## Not run in the browser

| Case | Why |
| --- | --- |
| TC-010, TC-011 | FX-PRIV and FX-EMPTY must be real GitHub repositories and do not exist |
| TC-013 | Needs a real Personal Access Token and the real GitHub API |
| TC-103 to TC-105, TC-107 to TC-110, TC-114, TC-120 | File content and database checks; next increment, without the browser |

## Product changes made because of these cases

| Case | Change |
| --- | --- |
| TC-001 | Home label is "GitHub Repository URL". |
| TC-029 | The N = 0 message reads "N must be 1 or more: …". |
| TC-095 | The high-risk table starts in rank order with no column selected, so the first click on any heading, Rank included, sorts ascending. Numeric columns used to start descending. |
| TC-099 | The progress view shows the completed bar at 100 % for 0.8 s before opening the results. It used to leave at once, so the bar never showed 100 %. |
| TC-115 | A refused comparison URL is reported with `field` = `url_1` or `url_2`. The view shows the error under that field (with `aria-invalid`), and both fields keep their values. |
| FR-30 (TC-087) | A submitted analysis is now Pending until its run begins, then Running. Before, it was Running at once, so Pending never appeared. Home, the overview and Cancel treat Pending like Running. An interrupted Pending run is marked as an Error on restart, as a Running one already was. |
| TC-052 | `POST /api/analyses` answers 202 with only `repository_id` and `cached` for a new analysis. It no longer reads the overview after the analysis thread has started. One Windows CI run took 2.7 s for that answer. A cached answer (200) still carries the overview. |
