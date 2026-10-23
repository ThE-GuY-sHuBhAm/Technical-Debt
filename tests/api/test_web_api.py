"""The REST API with the Flask test client (FR-01 to FR-05, FR-21, FR-28 to FR-47; 23 Oct).

FX-S and FX-ERR are analysed once for the module through the real AnalysisService with
the FR-03 check stubbed (product_support); every request then goes through Flask. The
dashboard itself is a separate React build and is not exercised here.
"""

from __future__ import annotations

import csv
import io
import time
from pathlib import Path

import make_fx_product
import pytest
from flask.testing import FlaskClient
from product_support import FX_ERR_URL, FX_S_URL, StubClient, local_service

from tdea.api.app import create_app
from tdea.git_handler.errors import ErrorCode, TdeaError

CSV_HEADER = ["commit_hash", "commit_sequence", "timestamp", "author", "SLOC", "mean_CC",
              "mean_MI", "total_smells", "smell_density", "debt_score", "debt_delta",
              "debt_classification"]


@pytest.fixture(name="client", scope="module")
def _client(tmp_path_factory: pytest.TempPathFactory) -> FlaskClient:
    """Return a test client whose store holds FX-S (id 1) and FX-ERR (id 2)."""
    root = tmp_path_factory.mktemp("web")
    fx_s = make_fx_product.build_fx_s(root / "fx-s")
    fx_err = make_fx_product.build_fx_err(root / "fx-err")
    service = local_service(root / "ws", {"tdea-fixtures/fx-s": fx_s.path,
                                          "tdea-fixtures/fx-err": fx_err.path})
    dist = root / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>TDEA</title>", encoding="utf-8")
    client = create_app(service, dashboard_dist=dist).test_client()
    for url in (FX_S_URL, FX_ERR_URL):
        answer = client.post("/api/analyses", json={"url": url})
        assert answer.status_code == 202, answer.json
        assert service.wait(answer.json["repository_id"], 300)
    return client


def assert_error(response, status: int) -> dict:
    """Check the FR-05 error shape and return its parts."""
    assert response.status_code == status, response.json
    error = response.get_json()["error"]
    assert set(error) == {"type", "cause", "action"} and all(error.values())
    assert "Traceback" not in str(error)
    return error


def test_cached_submission_returns_200_with_the_overview(client: FlaskClient) -> None:
    """FR-22, FR-30: a second submission is answered from the cache."""
    answer = client.post("/api/analyses", json={"url": FX_S_URL + "/"})
    assert answer.status_code == 200 and answer.json["cached"] is True
    for key in ("name", "owner", "url", "commits_analysed", "status", "last_completed_at"):
        assert answer.json[key] is not None
    assert answer.json["status"] == "Complete" and answer.json["commits_analysed"] == 12


@pytest.mark.parametrize("body, status", [
    ({"url": "https://gitlab.com/a/b"}, 400), ({"url": ""}, 400), ({}, 400),
    ({"url": "https://github.com/a/b/tree/main"}, 400),
    ({"url": FX_S_URL, "sampling_n": 0}, 400), ({"url": FX_S_URL, "sampling_n": "x"}, 400),
    ({"url": FX_S_URL, "token": 5}, 400),
])
def test_invalid_input_gets_a_structured_400(client: FlaskClient, body: dict,
                                             status: int) -> None:
    """FR-02, FR-05, NFR-07: bad input is refused with three parts."""
    assert_error(client.post("/api/analyses", json=body), status)


def test_non_json_bodies_and_unknown_routes(client: FlaskClient) -> None:
    """NFR-07: no unhandled exception for malformed requests."""
    assert_error(client.post("/api/analyses", data="url=x"), 400)
    assert_error(client.get("/api/repositories/999"), 404)
    assert_error(client.get("/api/nothing"), 404)
    assert_error(client.delete("/api/analyses"), 405)
    assert_error(client.get("/api/repositories/1/commits/zzzzzzz"), 404)
    assert_error(client.post("/api/repositories/1/cancel"), 409)  # nothing runs
    assert_error(client.post("/api/repositories/999/cancel"), 404)


def test_an_inaccessible_repository_gets_422(tmp_path: Path) -> None:
    """FR-03, NFR-07: private, missing and empty repositories are 422 with three parts."""
    for code in (ErrorCode.NOT_FOUND, ErrorCode.PRIVATE_REPOSITORY,
                 ErrorCode.EMPTY_REPOSITORY):
        stub = StubClient(failure=TdeaError(code, "Not analysable", "Because.", "Do this."))
        app = create_app(local_service(tmp_path / code.value, {}, stub))
        assert_error(app.test_client().post("/api/analyses",
                                            json={"url": "https://github.com/a/b"}), 422)


def test_the_series_has_what_every_chart_needs(client: FlaskClient) -> None:
    """FR-31 to FR-35, NFR-10: one point per commit with tooltip fields."""
    points = client.get("/api/repositories/1/commits").json
    assert [point["sequence"] for point in points] == list(range(1, 13))
    for key in ("sha", "author", "timestamp", "sloc", "mean_cc", "mean_mi", "convention",
                "refactor", "warning", "error", "debt_score", "classification"):
        assert key in points[0]


def test_commit_drill_down(client: FlaskClient) -> None:
    """FR-37: path, LOC, SLOC, CC with rank, MI and Pylint counts per file."""
    sha = client.get("/api/repositories/1/commits").json[-1]["sha"]
    detail = client.get(f"/api/repositories/1/commits/{sha[:10]}").json
    assert detail["sha"] == sha and detail["files"]
    for key in ("path", "loc", "sloc", "mean_cc", "cc_rank", "mi", "convention", "refactor",
                "warning", "error"):
        assert key in detail["files"][0]


def test_high_risk_patterns_and_parameterised_metrics(client: FlaskClient) -> None:
    """FR-21, FR-28, FR-29: parameterised by repository, hash, path and metric type."""
    risky = client.get("/api/repositories/1/high-risk").json
    assert 0 < len(risky) <= 10 and risky[0]["risk_class"] in ("High", "Medium", "Low")
    assert isinstance(client.get("/api/repositories/1/patterns").json, list)
    scores = client.get("/api/repositories/1/metrics?metric=debt_score").json
    assert len(scores) == 12 and scores[0]["path"] is None
    sha = scores[-1]["sha"]
    one = client.get(f"/api/repositories/1/metrics?metric=sloc&sha={sha}"
                     "&path=fxs/geometry.py").json
    assert len(one) == 1 and one[0]["path"] == "fxs/geometry.py"
    assert_error(client.get("/api/repositories/1/metrics?metric=drop table"), 400)


def test_csv_export_columns_and_timeliness(client: FlaskClient) -> None:
    """FR-41 to FR-43: the listed columns, from the cache, well within 5 s."""
    started = time.monotonic()
    answer = client.get("/api/repositories/1/export.csv")
    assert time.monotonic() - started < 5
    assert answer.status_code == 200 and "attachment" in answer.headers["Content-Disposition"]
    table = list(csv.reader(io.StringIO(answer.data.decode("utf-8"), newline="")))
    assert table[0] == CSV_HEADER and len(table) == 13
    assert b"\r" not in answer.data
    assert client.get("/api/repositories/1/export.csv").data == answer.data  # NFR-06


def test_pdf_report_within_10_seconds(client: FlaskClient) -> None:
    """FR-40, FR-42, FR-43: a PDF from the cache within 10 s."""
    started = time.monotonic()
    answer = client.get("/api/repositories/1/report.pdf")
    assert time.monotonic() - started < 10
    assert answer.status_code == 200 and answer.data.startswith(b"%PDF")
    assert answer.mimetype == "application/pdf"


def test_comparison_overlay_summary_and_csv(client: FlaskClient) -> None:
    """FR-45 to FR-47; OI-06 proposal: blank cells beyond the shorter repository."""
    body = client.get("/api/comparison?repo_1=1&repo_2=2").json
    first, second = body["repositories"]
    assert len(first["timeline"]) == 12 and len(second["timeline"]) == 1
    for key in ("mean_score", "peak_score", "spikes", "drops", "trend"):
        assert key in first["summary"]
    for key in ("sha", "author", "timestamp", "debt_score", "classification"):
        assert key in first["timeline"][0]  # NFR-10 tooltip fields
    table = list(csv.reader(io.StringIO(
        client.get("/api/comparison.csv?repo_1=1&repo_2=2").data.decode(), newline="")))
    assert table[0] == ["commit_sequence", "repo_1_debt_score", "repo_1_delta",
                        "repo_1_classification", "repo_2_debt_score", "repo_2_delta",
                        "repo_2_classification"]
    assert len(table) == 13 and table[2][4:] == ["", "", ""]
    assert_error(client.get("/api/comparison?repo_1=1"), 400)


def test_dashboard_is_served_for_client_routes(client: FlaskClient) -> None:
    """The SPA's index is served at / and for its client-side routes."""
    for path in ("/", "/trends", "/compare"):
        answer = client.get(path)
        assert answer.status_code == 200 and b"TDEA" in answer.data


def test_endpoints_answer_within_2_seconds(client: FlaskClient) -> None:
    """NFR-03 (test-client timing, single user): 95th percentile of 20 requests <= 2 s."""
    for path in ("/api/repositories", "/api/repositories/1", "/api/repositories/1/commits",
                 "/api/repositories/1/high-risk", "/api/comparison?repo_1=1&repo_2=2"):
        timings = []
        for _ in range(20):
            started = time.monotonic()
            assert client.get(path).status_code == 200
            timings.append(time.monotonic() - started)
        assert sorted(timings)[18] <= 2.0, path
