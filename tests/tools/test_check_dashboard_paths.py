"""Tests for the dashboard guard in tools/check_dashboard_paths.py.

The git tests build small repositories in a temporary directory. Commits pass their
identity and switch signing off on the command line, so the machine's git
configuration cannot change the result.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import check_dashboard_paths as guard

_GIT = ["git", "-c", "user.name=TDEA Test", "-c", "user.email=test@example.invalid",
        "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]


def _git(repo: Path, *args: str) -> str:
    """Run git in ``repo`` and return its output, failing the test on an error."""
    completed = subprocess.run([*_GIT, *args], cwd=repo, capture_output=True, text=True,
                               check=True)
    return completed.stdout


def _commit(repo: Path, subject: str, changes: dict[str, str | None]) -> None:
    """Write (or, for None, delete) each path, then commit everything."""
    for name, text in changes.items():
        path = repo / name
        if text is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", subject)


@pytest.fixture(name="repo")
def _repo(tmp_path: Path) -> Path:
    """Return a repository whose first commit holds a product module and a dashboard file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "product: initial layout", {
        "tdea/api/cli.py": "# cli\n",
        "dashboard/src/App.jsx": "// app\n",
    })
    return repo


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tdea/aggregator/research_job.py", True),
        ("tests/research/test_re01.py", True),
        ("tdea/analyzer/research/rg1/tracking.py", True),
        ("fixtures/rg10/bundle.json", True),
        ("tdea/api/cli.py", False),
        ("tdea/aggregator/lock.py", False),
        ("tests/product/test_rg2_like_name.py", False),
    ],
)
def test_is_research_path(path: str, expected: bool) -> None:
    """Research paths are named by a research, rg1 or rg10 component."""
    assert guard.is_research_path(path) is expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("dashboard/src/App.jsx", True),
        ("dashboard/package.json", True),
        ("dashboard", False),
        ("tests/dashboard/test_ui.py", False),
        ("tdea/api/dashboard_routes.py", False),
    ],
)
def test_is_dashboard_path(path: str, expected: bool) -> None:
    """Only paths under the top-level dashboard directory count."""
    assert guard.is_dashboard_path(path) is expected


@pytest.mark.parametrize(
    ("subject", "is_research"),
    [
        ("research: add the RG1 tracker", True),
        ("research(rg10): load bundles", True),
        ("Research!: breaking change", True),
        ("researching a bug in the dashboard", False),
        ("product: fix the chart legend", False),
    ],
)
def test_research_subject(subject: str, is_research: bool) -> None:
    """The subject prefix marks a research commit."""
    commit = guard.Commit("0" * 40, subject, ("tdea/api/cli.py",))
    assert (guard.research_reason(commit) is not None) is is_research


def test_product_commit_may_change_dashboard(repo: Path) -> None:
    """Product work on the dashboard passes."""
    _commit(repo, "product: new chart", {"dashboard/src/Chart.jsx": "// chart\n"})
    assert guard.main(["--repo", str(repo)]) == guard.EXIT_PASS


def test_research_subject_changing_dashboard_fails(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A research: commit that edits a dashboard file is reported with the file."""
    _commit(repo, "research: show run status", {"dashboard/src/App.jsx": "// changed\n"})
    assert guard.main(["--repo", str(repo)]) == guard.EXIT_VIOLATION
    out = capsys.readouterr().out
    assert "research: show run status" in out
    assert "dashboard/src/App.jsx" in out


def test_research_path_changing_dashboard_fails(repo: Path) -> None:
    """A commit without the prefix that touches research code and the dashboard fails."""
    _commit(repo, "add the research job and a status badge", {
        "tdea/aggregator/research_job.py": "# job\n",
        "dashboard/src/Badge.jsx": "// badge\n",
    })
    assert guard.main(["--repo", str(repo)]) == guard.EXIT_VIOLATION


def test_research_commit_moving_a_dashboard_file_fails(repo: Path) -> None:
    """Moving a file out of dashboard/ is a change under it."""
    _commit(repo, "research: reuse the app shell", {
        "dashboard/src/App.jsx": None,
        "tdea/research_shell/App.jsx": "// app\n",
    })
    assert guard.main(["--repo", str(repo)]) == guard.EXIT_VIOLATION


def test_research_commit_elsewhere_passes(repo: Path) -> None:
    """Research work that leaves dashboard/ alone passes."""
    _commit(repo, "research: add the tracker", {"tdea/analyzer/research/tracking.py": "# t\n"})
    assert guard.main(["--repo", str(repo)]) == guard.EXIT_PASS


def test_merge_commits_are_skipped(repo: Path) -> None:
    """A merge bringing in product dashboard work passes; its parents are checked alone."""
    _git(repo, "checkout", "-q", "-b", "product")
    _commit(repo, "product: dashboard filter", {"dashboard/src/Filter.jsx": "// filter\n"})
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "research: add the tracker", {"tdea/analyzer/research/tracking.py": "# t\n"})
    _git(repo, "merge", "-q", "--no-ff", "-m", "research: merge product work", "product")
    assert guard.main(["--repo", str(repo)]) == guard.EXIT_PASS


def test_shallow_clone_cannot_be_checked(repo: Path, tmp_path: Path) -> None:
    """A shallow clone would hide history, so the guard refuses it."""
    _commit(repo, "product: second commit", {"tdea/api/cli.py": "# cli 2\n"})
    shallow = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth", "1", repo.resolve().as_uri(), str(shallow))
    assert guard.main(["--repo", str(shallow)]) == guard.EXIT_CANNOT_READ


def test_not_a_repository_cannot_be_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory without git history exits 2."""
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))  # never search upwards
    assert guard.main(["--repo", str(plain)]) == guard.EXIT_CANNOT_READ
