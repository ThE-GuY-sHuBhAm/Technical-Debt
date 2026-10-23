"""REST endpoints of the TDEA web API (FR-01 to FR-05, FR-17 to FR-47; sprint 23 Oct).

Analyses
  POST /api/analyses              {url, token?, sampling_n?, refresh?}: start one analysis,
                                  202 with its repository_id only, or return the cached
                                  one with its overview, 200 (FR-22)
  POST /api/comparisons           {url_1, url_2, token?, sampling_n?}: analyse two in
                                  sequence for the comparison view (FR-44)
Repositories and results (all read from tdea.db; nothing is recomputed, FR-42)
  GET /api/repositories                         every repository (NFR-16)
  GET /api/repositories/<id>                    overview and live progress (FR-30, FR-38)
  POST /api/repositories/<id>/cancel            stop its running analysis (SRS 4.1)
  GET /api/repositories/<id>/commits            per-commit series (FR-31 to FR-35)
  GET /api/repositories/<id>/commits/<sha>      per-file drill-down (FR-37)
  GET /api/repositories/<id>/high-risk          top ten files (FR-28, FR-36)
  GET /api/repositories/<id>/patterns           debt patterns (FR-29)
  GET /api/repositories/<id>/metrics?metric=&sha=&path=   parameterised query (FR-21)
  GET /api/repositories/<id>/export.csv         per-commit CSV (FR-41)
  GET /api/repositories/<id>/report.pdf         PDF report (FR-40)
  GET /api/comparison?repo_1=&repo_2=           overlay and summaries (FR-45, FR-47)
  GET /api/comparison.csv?repo_1=&repo_2=       comparison CSV (FR-46)

Status codes: 400 for input the API cannot use (bad URL, sampling interval, parameter),
422 for a repository that cannot be analysed (not found, private, empty, not Python,
token refused), 404 for an unknown repository or commit, 409 while another analysis
runs or the results are not ready, 429 for GitHub's rate limit, 502 when GitHub cannot
be reached. Every error body has the FR-05 shape.
"""

from __future__ import annotations

from flask import Blueprint, Response, current_app, jsonify, request

from tdea.aggregator import results
from tdea.aggregator.analysis_service import AnalysisService, SubmissionRefused
from tdea.aggregator.product_models import ERROR
from tdea.aggregator.product_store import METRICS, UnknownMetric
from tdea.api.api_error import ApiError
from tdea.api.report_builder import comparison_csv, metrics_csv, pdf_report

api = Blueprint("api", __name__, url_prefix="/api")

STATUS_BY_KIND = {
    "invalid_url": 400, "invalid_sampling": 400, "same_repository": 400,
    "not_found": 422, "private_repository": 422, "not_accessible": 422,
    "empty_repository": 422, "auth_failed": 422, "not_python": 422,
    "rate_limited": 429, "busy": 409, "network": 502, "github_error": 502,
}
TOKEN_HEADER = "X-GitHub-Token"


def _service() -> AnalysisService:
    """Return the application's analysis service."""
    return current_app.config["TDEA_SERVICE"]


def _body() -> dict:
    """Return the JSON object of the request, or raise a 400 ApiError."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Invalid request", "The request body is not a JSON object.",
                       "Send a JSON object such as {\"url\": \"https://github.com/owner/repo\"}.",
                       400)
    return data


def _token(data: dict) -> str | None:
    """Return the optional token from the body or header; never from the URL (NFR-19)."""
    token = data.get("token") or request.headers.get(TOKEN_HEADER) or None
    if token is not None and not isinstance(token, str):
        raise ApiError("Invalid token", "The Personal Access Token must be text.",
                       "Paste the token as it was issued, or leave the field empty.", 400)
    if not token:
        return None
    return token.strip() or None


def _refused(error: SubmissionRefused) -> ApiError:
    """Return the HTTP error for a refused submission, naming its field if it has one."""
    failure = ApiError(error.error_type, error.cause, error.action,
                       STATUS_BY_KIND.get(error.kind, 422))
    failure.field = error.field
    return failure


def _results(call, *args):
    """Run a results reader and turn its lookups into HTTP errors."""
    try:
        return call(_service().store, *args)
    except results.NotFound as exc:
        raise ApiError("Not found", str(exc).capitalize() + ".",
                       "Check the repository or commit and try again.", 404) from None
    except results.NotReady:
        row = _service().store.repository(args[0])
        if row is not None and row.status == ERROR and row.error_type:
            raise ApiError(row.error_type, row.error_cause or "The analysis failed.",
                           row.error_action or "Submit the repository again.", 422) from None
        raise ApiError("Results not ready", "The analysis of this repository has not "
                       "finished yet.", "Wait for the progress bar to reach 100%, then "
                       "reload the results.", 409) from None


@api.post("/analyses")
def start_analysis():
    """Start an analysis, or answer from the cache (FR-01 to FR-04, FR-17, FR-22)."""
    data = _body()
    try:
        submission = _service().submit(data.get("url"), _token(data),
                                       data.get("sampling_n", 2), bool(data.get("refresh")))
    except SubmissionRefused as exc:
        raise _refused(exc) from None
    if not submission.cached:
        # FR-17, TC-052: answer at once. The analysis thread is already running, so this
        # answer reads nothing more; the progress view asks for the overview next.
        return jsonify({"repository_id": submission.repository_id, "cached": False}), 202
    body = {"repository_id": submission.repository_id, "cached": True,
            **_overview(submission.repository_id)}
    return jsonify(body), 200


@api.post("/comparisons")
def start_comparison():
    """Analyse two repositories in sequence for the comparison view (FR-44)."""
    data = _body()
    try:
        first, second = _service().submit_pair(data.get("url_1"), data.get("url_2"),
                                               _token(data), data.get("sampling_n", 2),
                                               bool(data.get("refresh")))
    except SubmissionRefused as exc:
        raise _refused(exc) from None
    return jsonify({"repo_1": first.repository_id, "repo_2": second.repository_id,
                    "cached": [first.cached, second.cached]}), 202


@api.get("/repositories")
def list_repositories():
    """Return every repository in the store."""
    return jsonify(_results(results.repositories))


@api.get("/repositories/<int:repository_id>")
def repository(repository_id: int):
    """Return one overview card with its live progress (FR-30, FR-38)."""
    return jsonify(_overview(repository_id))


def _overview(repository_id: int) -> dict:
    """Return the overview with the live progress of a running analysis."""
    body = _results(results.overview, repository_id)
    progress = _service().progress(repository_id)
    body["progress"] = None if progress is None else vars(progress)
    return body


@api.post("/repositories/<int:repository_id>/cancel")
def cancel(repository_id: int):
    """Ask the repository's running analysis to stop (SRS 4.1, Cancel button)."""
    _results(results.overview, repository_id)
    if not _service().cancel(repository_id):
        raise ApiError("Nothing to cancel", "This repository has no analysis running.",
                       "Reload the page to see the repository's current status.", 409)
    return jsonify({"repository_id": repository_id, "cancelling": True}), 202


@api.get("/repositories/<int:repository_id>/commits")
def commits(repository_id: int):
    """Return the per-commit series for the trend charts (FR-31 to FR-35)."""
    return jsonify(_results(results.series, repository_id))


@api.get("/repositories/<int:repository_id>/commits/<sha>")
def commit(repository_id: int, sha: str):
    """Return one commit's per-file breakdown (FR-37)."""
    return jsonify(_results(results.commit_files, repository_id, sha))


@api.get("/repositories/<int:repository_id>/high-risk")
def high_risk(repository_id: int):
    """Return the ten highest-risk files (FR-28, FR-36)."""
    return jsonify(_results(results.high_risk, repository_id))


@api.get("/repositories/<int:repository_id>/patterns")
def patterns(repository_id: int):
    """Return the debt patterns found in the series (FR-29)."""
    return jsonify(_results(results.patterns, repository_id))


@api.get("/repositories/<int:repository_id>/metrics")
def metric(repository_id: int):
    """Return one metric type, filtered by commit hash and file path (FR-21)."""
    name = request.args.get("metric", "")
    _results(results.overview, repository_id)
    try:
        rows = _service().store.metric(repository_id, name, request.args.get("sha"),
                                       request.args.get("path"))
    except UnknownMetric:
        raise ApiError("Unknown metric", f"'{name[:40]}' is not a metric TDEA stores.",
                       f"Use one of: {', '.join(METRICS)}.", 400) from None
    return jsonify([{"sequence": sequence, "sha": sha, "path": path, "value": value}
                    for sequence, sha, path, value in rows])


@api.get("/repositories/<int:repository_id>/export.csv")
def export_csv(repository_id: int):
    """Return the per-commit dataset as CSV (FR-41, FR-42)."""
    rows = _results(results.csv_rows, repository_id)
    name = _results(results.overview, repository_id)["name"]
    return _download(metrics_csv(rows), "text/csv", f"tdea-{name}-metrics.csv")


@api.get("/repositories/<int:repository_id>/report.pdf")
def report_pdf(repository_id: int):
    """Return the PDF report built from the stored results (FR-40, FR-42)."""
    store = _service().store
    content = _results(lambda _store, rid: pdf_report(store, rid), repository_id)
    name = _results(results.overview, repository_id)["name"]
    return _download(content, "application/pdf", f"tdea-{name}-report.pdf")


def _pair() -> tuple[int, int]:
    """Return the two repository ids of a comparison request."""
    try:
        return int(request.args["repo_1"]), int(request.args["repo_2"])
    except (KeyError, ValueError):
        raise ApiError("Invalid comparison", "repo_1 and repo_2 must both be repository ids.",
                       "Choose two analysed repositories to compare.", 400) from None


@api.get("/comparison")
def comparison():
    """Return both Debt Score timelines and summaries (FR-45, FR-47)."""
    first, second = _pair()
    return jsonify(_results(results.comparison, first, second))


@api.get("/comparison.csv")
def comparison_export():
    """Return the comparison CSV (FR-46)."""
    first, second = _pair()
    rows = _results(results.comparison_rows, first, second)
    return _download(comparison_csv(rows), "text/csv", "tdea-comparison.csv")


def _download(content: str | bytes, mimetype: str, filename: str) -> Response:
    """Return a file download."""
    response = Response(content, mimetype=mimetype)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
