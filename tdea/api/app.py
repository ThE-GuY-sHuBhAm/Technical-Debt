"""The Flask application: REST API and the dashboard's static files (Modules 1, 6-8).

create_app() builds the application around one AnalysisService (aggregator), which owns
tdea.db and the background analyses. The routes (routes.py) answer in JSON, CSV or PDF;
the dashboard (dashboard/, a React and Chart.js single-page application) is served from
its build, tdea/api/dashboard, which ships with the package (CON-06), and reaches the
API over HTTP only (NFR-14).

Errors: every failure the API reports has the FR-05 shape
``{"error": {"type", "cause", "action"}}`` with an HTTP status (NFR-07, NFR-11); an
unexpected exception is logged by type only and answered with a generic three-part
message, never raw exception text. The token (FR-04) arrives in the JSON body or the
``X-GitHub-Token`` header, never in a URL, so it cannot appear in an access log
(NFR-19).

Run locally with ``tdea serve`` (cli.py) or ``flask --app tdea.api.app run``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException

from tdea.aggregator.analysis_service import AnalysisService
from tdea.aggregator.product_store import DEFAULT_PRODUCT_STORE, ProductStore
from tdea.api.api_error import ApiError
from tdea.api.routes import api

LOGGER = logging.getLogger(__name__)
DASHBOARD_DIST = Path(__file__).resolve().parent / "dashboard"


def create_app(service: AnalysisService | None = None, *,
               store_path: Path | str = DEFAULT_PRODUCT_STORE,
               dashboard_dist: Path | str = DASHBOARD_DIST) -> Flask:
    """Return the TDEA web application, using ``service`` or a new one over ``store_path``."""
    app = Flask(__name__, static_folder=None)
    app.config["TDEA_SERVICE"] = (service if service is not None
                                  else AnalysisService(ProductStore(store_path)))
    app.config["TDEA_DASHBOARD"] = Path(dashboard_dist)
    app.json.sort_keys = False
    app.register_blueprint(api)

    @app.errorhandler(ApiError)
    def _api_error(error: ApiError):
        return jsonify(error_body(error)), error.status or 400

    @app.errorhandler(HTTPException)
    def _http_error(error: HTTPException):
        status = error.code or 500
        if status == 404:
            body = ApiError("Not found", "There is no such page or API endpoint.",
                            "Check the address, or go back to the TDEA home page.", status)
        elif status == 405:
            body = ApiError("Method not allowed", "This endpoint does not accept that method.",
                            "Use the method the API documentation names.", status)
        else:
            body = ApiError("Request not accepted", "The request could not be processed.",
                            "Check the request and try again.", status)
        return jsonify(error_body(body)), status

    @app.errorhandler(Exception)
    def _unexpected(error: Exception):
        LOGGER.error("Unhandled API error: %s", type(error).__name__)
        body = ApiError("Internal error", "An unexpected problem stopped the request.",
                        "Try again; if it keeps failing, restart TDEA and report it.", 500)
        return jsonify(error_body(body)), 500

    @app.get("/")
    @app.get("/<path:path>")
    def _dashboard(path: str = "index.html"):
        folder = app.config["TDEA_DASHBOARD"]
        if path.startswith("api/"):
            raise ApiError("Not found", "There is no such API endpoint.",
                           "Check the address against the API documentation.", 404)
        if not (folder / "index.html").is_file():
            raise ApiError("Dashboard not built", "The dashboard build (index.html) is missing.",
                           "Run 'npm install' and 'npm run build' in the dashboard folder, "
                           "then reload this page.", 503)
        if (folder / path).is_file():
            return send_from_directory(folder, path)
        return send_from_directory(folder, "index.html")  # client-side routes

    return app


def error_body(error: ApiError) -> dict[str, dict[str, str]]:
    """Return the FR-05 JSON body of an error."""
    return {"error": {"type": error.error_type, "cause": error.cause, "action": error.action}}
