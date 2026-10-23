# TDEA: Technical Debt Evolution Evaluator

TDEA measures how technical debt changes over the commit history of a public GitHub
Python repository. It has two parts in one installation:

* a **web product** (FR-01 to FR-47): submit a repository URL, and TDEA clones it,
  measures every Python file at each analysed commit with radon and Pylint, scores a
  Debt Score per commit and shows the results in a browser dashboard, with CSV and PDF
  exports and a two-repository comparison;
* a **headless research engine** (FR-48 to FR-62) for the RG1 and RG10 studies,
  operated with `tdea research`.

TDEA reads repository code only as text (radon) or through an isolated Pylint process.
It never imports or runs the code it analyses, and the research engine never executes
model-generated code (NFR-26).

## Requirements

* Python 3.10, 3.11 or 3.12. Research runs need Python 3.11 in a virtual environment
  (NFR-24).
* The `git` command on the PATH (GitPython calls it to clone and check out commits).
  Most systems already have it. Note: SRS v2.3, section 2.4, does not list Git as a
  prerequisite; this is recorded as an open point, not changed here.
* Internet access for the GitHub check and the clone; the analysis itself runs offline.

Node.js is **not** needed to run TDEA: the dashboard's production build is committed in
`tdea/api/dashboard` and installed with the package (CON-06).

## Install

```bash
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install .            # or: python -m pip install -e ".[dev]" for development
tdea --help
```

## Run a product analysis in the browser

```bash
tdea serve                         # http://127.0.0.1:5000
```

Open http://127.0.0.1:5000, paste a public repository URL such as
`https://github.com/psf/requests`, and press **Analyse**. A Personal Access Token is
optional; it raises GitHub's rate limit, is used for that request only and is never
stored. The progress bar shows each commit as it is analysed; **Cancel** stops the
analysis after the current commit.

Repositories with more than 100 commits are sampled: every N-th commit (default N = 2)
plus the latest one. Results are stored in `~/.tdea/tdea.db` (change it with
`tdea serve --store PATH`), so a repository already analysed opens at once.

The dashboard sections are Repository Overview, Metric Trends (five charts), High-Risk
Files, Commit History (with a per-file drill-down for any commit), Export (CSV and PDF)
and Research Comparison.

`tdea serve --host` and `--port` change the address. The server is Flask's built-in
server, meant for one user on the same computer.

### REST API

The dashboard uses the API below; it can also be called directly. Every error has the
body `{"error": {"type", "cause", "action"}}`.

| Method and path | Purpose |
| --- | --- |
| `POST /api/analyses` `{url, token?, sampling_n?, refresh?}` | Start an analysis (202) or answer from the cache (200) |
| `POST /api/comparisons` `{url_1, url_2, token?, sampling_n?}` | Analyse two repositories in sequence |
| `GET /api/repositories`, `GET /api/repositories/<id>` | Overview and live progress |
| `POST /api/repositories/<id>/cancel` | Stop the running analysis |
| `GET /api/repositories/<id>/commits[/<sha>]` | Per-commit series; one commit's files |
| `GET /api/repositories/<id>/high-risk`, `/patterns` | Top-ten files; debt patterns |
| `GET /api/repositories/<id>/metrics?metric=&sha=&path=` | One stored metric, filtered |
| `GET /api/repositories/<id>/export.csv`, `/report.pdf` | Downloads |
| `GET /api/comparison?repo_1=&repo_2=`, `/api/comparison.csv?…` | Comparison data and CSV |

## Research engine

```bash
tdea research validate RUN_FILE    # check a run file without side effects
tdea research run RUN_FILE         # start or resume a run
tdea research status RUN_FILE      # list runs and their units
tdea research export --store STORE --out DIR [--format csv|json|jsonl] RUN_ID...  # FR-62
```

Run files, exit codes and stores are specified in the SRS (section 4.5) and the Spec
Freeze. Real RG1 runs are refused until the versioned smell lists exist (CL-03).

## Develop

```bash
python -m pip install -e ".[dev]"
python -m pytest                                    # all tests
python tools/check_pylint_scores.py --min-score 8.0 # NFR-12 gate
python -m pydocstyle tdea tests tools               # NFR-15
python tools/check_dashboard_paths.py               # research commits leave the dashboard alone
```

The dashboard's sources are in `dashboard/` (React 18, Chart.js 4, Vite 5):

```bash
cd dashboard
npm ci
npm run dev      # development server on http://127.0.0.1:5173, /api proxied to tdea serve
npm run build    # writes tdea/api/dashboard; commit the result
```

## Status (23 October 2026)

This is work in progress on the `sprint-implementation` branch. The product and the
research engine are implemented and pass the automated tests on fixture repositories;
none of that is acceptance evidence on its own. Scoring rules that the SRS leaves open
(OI-01 to OI-06) follow the SRS Appendix B proposals and are marked as provisional in
the code. The browser (Selenium) acceptance cases in the test workbook are **not
executed**. Taking Selenium automation out of scope is proposed as change request CR-01,
which is pending approval; until it is approved those cases remain in scope and open.

The FR-03 GitHub check is tested against mocked answers everywhere except the CI job
"live GitHub API (FR-03)", which runs `tests/product/test_github_live.py` against the
real api.github.com. To run those tests yourself on a machine that can reach GitHub:

```bash
TDEA_LIVE_GITHUB=1 python -m pytest tests/product/test_github_live.py -v
```
