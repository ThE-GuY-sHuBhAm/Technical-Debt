"""Workbook v1.2 browser cases of the Home / Input view: TC-001 to TC-009, TC-012, TC-014,
TC-016 and TC-029 (FR-01 to FR-05, FR-08, NFR-11).

The FR-03 answers come from the stubbed transport (conftest.FakeGitHub). TC-010 (FX-PRIV)
and TC-011 (FX-EMPTY) need real GitHub repositories and TC-013 a real token, so they are
not run here.
"""

from __future__ import annotations

import pytest
from browser_support import INVALID_TOKEN, Dashboard

ANALYSE = 'main.content form button[type="submit"]'


def submit(dashboard: Dashboard, address: str, token: str = "", sampling: str = "2") -> None:
    """Fill in the Home form and click 'Analyse'."""
    dashboard.open("/", "#url")
    dashboard.type_into("#url", address)
    if token:
        dashboard.type_into("#token", token)
    dashboard.type_into("#sampling", sampling)
    dashboard.find(ANALYSE).click()


def error_panel(dashboard: Dashboard) -> str:
    """Return the text of the form's error panel once it is shown."""
    return dashboard.text("main.content section.panel .error")


def left_home(dashboard: Dashboard) -> str:
    """Wait until the submission leaves Home; return the new path."""
    return dashboard.until(lambda: dashboard.path() if dashboard.path() != "/" else None,
                           message="the submission did not leave Home")


def test_tc001_url_field_is_labelled_and_focused(dashboard: Dashboard) -> None:
    """TC-001: a field labelled 'GitHub Repository URL' has the focus on page load."""
    dashboard.open("/", "#url")
    assert dashboard.text('label[for="url"]') == "GitHub Repository URL"
    assert dashboard.driver.switch_to.active_element.get_attribute("id") == "url"


@pytest.mark.parametrize("address", ["https://github.com/psf/requests",
                                     "https://github.com/psf/requests/"])
def test_tc002_tc003_valid_url_goes_on_to_the_accessibility_check(dashboard: Dashboard,
                                                                  address: str) -> None:
    """TC-002, TC-003: no validation error; FR-03 is asked about psf/requests next."""
    submit(dashboard, address)
    path = left_home(dashboard)
    assert path.startswith(("/progress?repo=", "/overview?repo="))
    assert not dashboard.all("main.content .error")
    assert dashboard.tdea.github.keys()[-1] == "psf/requests"


@pytest.mark.parametrize("case, address, expected", [
    ("TC-004", "https://gitlab.com/gitlab-org/gitlab", "not a github.com address"),
    ("TC-005", "github.com/psf/requests", "does not start with https://github.com/"),
    ("TC-006", "https://github.com/psf", "https://github.com/<owner>/<repo>"),
    ("TC-007", "https://github.com/psf/requests/tree/main",
     "nothing after the repository name"),
])
def test_tc004_to_tc007_bad_urls_are_rejected_at_once(dashboard: Dashboard, case: str,
                                                      address: str, expected: str) -> None:
    """TC-004 to TC-007: a format error, with no GitHub request and no clone."""
    checked, cloned = len(dashboard.tdea.github.requests), len(dashboard.tdea.sources.clones)
    submit(dashboard, address)
    text = error_panel(dashboard)
    assert "Invalid repository URL" in text and expected in text, case
    assert dashboard.path() == "/"
    assert len(dashboard.tdea.github.requests) == checked
    assert len(dashboard.tdea.sources.clones) == cloned


def test_tc008_analyse_stays_disabled_without_a_url(dashboard: Dashboard) -> None:
    """TC-008: with the URL field empty (or blanks only), 'Analyse' cannot be clicked."""
    dashboard.open("/", "#url")
    button = dashboard.find(ANALYSE)
    assert button.text == "Analyse" and not button.is_enabled()
    dashboard.type_into("#url", "   ")
    assert not button.is_enabled()


def test_tc009_missing_repository_is_not_found_and_not_cloned(dashboard: Dashboard) -> None:
    """TC-009: GitHub answers 404; a structured 'Repository not found' error, no clone."""
    cloned = len(dashboard.tdea.sources.clones)
    submit(dashboard, "https://github.com/tdea-test-user/does-not-exist-9999")
    text = error_panel(dashboard)
    assert "Repository not found" in text
    assert dashboard.tdea.github.keys()[-1] == "tdea-test-user/does-not-exist-9999"
    assert len(dashboard.tdea.sources.clones) == cloned


def test_tc012_token_field_is_masked_optional_and_not_needed(dashboard: Dashboard) -> None:
    """TC-012: the PAT field is a password field marked optional; the analysis starts."""
    dashboard.open("/", "#token")
    assert dashboard.find("#token").get_attribute("type") == "password"
    assert "(optional)" in dashboard.text('label[for="token"]')
    submit(dashboard, url_for("no-token"))
    assert left_home(dashboard).startswith("/progress?repo=")
    assert dashboard.tdea.github.requests[-1] == ("tdea-fixtures/no-token", None)


def test_tc014_bad_token_fails_without_echoing_it(dashboard: Dashboard) -> None:
    """TC-014: a structured 'Authentication failed' error; the token is not shown.

    The repository has not been analysed before: a cached repository is answered from
    tdea.db without the FR-03 check (FR-22), so no token is sent for it.
    """
    submit(dashboard, url_for("bad-token"), token=INVALID_TOKEN)
    text = error_panel(dashboard)
    assert "Authentication failed" in text and INVALID_TOKEN not in text
    assert INVALID_TOKEN not in dashboard.driver.execute_script(
        "return document.body.innerText;")  # the page's text, not the input's own value
    assert dashboard.find("#token").get_attribute("value") == INVALID_TOKEN  # still typed


def test_tc016_errors_have_type_cause_and_action_and_no_traceback(
        dashboard: Dashboard) -> None:
    """TC-016: error type, probable cause and next step; no traceback or raw exception."""
    submit(dashboard, "https://gitlab.com/gitlab-org/gitlab")
    panel = dashboard.find("main.content section.panel .error")
    assert panel.find_element("css selector", "strong").text == "Invalid repository URL"
    cause, action = [item.text for item in panel.find_elements("css selector", "p")]
    assert cause == "The URL is not a github.com address."
    assert action.startswith("Enter the repository address")
    for raw in ("Traceback", "Exception", "Error:", 'File "'):
        assert raw not in dashboard.driver.page_source


def test_tc029_n_0_is_rejected_and_nothing_starts(dashboard: Dashboard) -> None:
    """TC-029: N = 0 is refused with 'N must be 1 or more'; no analysis starts."""
    checked, cloned = len(dashboard.tdea.github.requests), len(dashboard.tdea.sources.clones)
    submit(dashboard, url_for("fx-s"), sampling="0")
    assert "N must be 1 or more" in error_panel(dashboard)
    assert dashboard.path() == "/"
    assert len(dashboard.tdea.github.requests) == checked
    assert len(dashboard.tdea.sources.clones) == cloned


def url_for(name: str) -> str:
    """Return the github.com URL of fixture repository ``name``."""
    return f"https://github.com/tdea-fixtures/{name}"
