"""FX-S and FX-ERR: the product fixtures are as the workbook describes and always identical."""

from __future__ import annotations

from pathlib import Path

import make_fx_product
import pytest
from fixture_git import git_output

FX_S_V1_HEAD = "ae712dcaabaab1c9f1edb341baa9007b522845ad"
FX_ERR_V1_HEAD = "a1dbbca1cc3f4dc1f8e2b30adc4e345109d7ce53"


@pytest.fixture(name="fx_s", scope="module")
def _fx_s(tmp_path_factory: pytest.TempPathFactory) -> make_fx_product.BuiltFixture:
    """Build FX-S once for this module."""
    return make_fx_product.build_fx_s(tmp_path_factory.mktemp("fxs") / "fx-s")


def test_fx_s_has_12_commits_and_3_branches(fx_s: make_fx_product.BuiltFixture) -> None:
    """All 12 commits are reachable from HEAD (TC-017, TC-020); 3 branches exist (TC-018)."""
    assert git_output(fx_s.path, "rev-list", "HEAD").split() == list(reversed(fx_s.shas))
    branches = git_output(fx_s.path, "branch", "--format=%(refname:short)").split()
    assert sorted(branches) == ["develop", "feature/charts", "main"]


def test_fx_s_carries_the_commit_history_cases(fx_s: make_fx_product.BuiltFixture) -> None:
    """A 120-character message (TC-021), the TC-023 time and the TC-024 file changes."""
    messages = git_output(fx_s.path, "log", "--format=%s").splitlines()
    assert make_fx_product.LONG_MESSAGE in messages and len(make_fx_product.LONG_MESSAGE) == 120
    dates = git_output(fx_s.path, "log", "--format=%aI").split()
    assert "2026-09-10T18:30:00+05:30" in dates
    changes = git_output(fx_s.path, "show", "--name-status", "--format=", fx_s.shas[9])
    assert [line for line in changes.splitlines() if line] == \
        ["A\tfxs/a.py", "M\tfxs/b.py", "D\tfxs/c.py"]


def test_fx_s_is_identical_on_every_build(
    tmp_path: Path, fx_s: make_fx_product.BuiltFixture
) -> None:
    """A second build gives the same SHAs, and HEAD is the pinned v1 SHA."""
    assert make_fx_product.build_fx_s(tmp_path / "again").shas == fx_s.shas
    assert fx_s.shas[-1] == FX_S_V1_HEAD


def test_fx_err_holds_the_four_failing_files(tmp_path: Path) -> None:
    """Syntax error, Latin-1, empty and binary .py files, byte for byte as committed."""
    built = make_fx_product.build_fx_err(tmp_path / "fx-err")
    assert built.shas == (FX_ERR_V1_HEAD,)
    files = built.path / "fxerr"
    with pytest.raises(SyntaxError):
        compile((files / "syntax_error.py").read_text(encoding="utf-8"), "syntax_error", "exec")
    latin1 = (files / "latin1.py").read_bytes()
    with pytest.raises(UnicodeDecodeError):
        latin1.decode("utf-8")
    assert "café" in latin1.decode("latin-1")
    assert (files / "empty.py").read_bytes() == b""
    assert b"\x00" in (files / "binary.py").read_bytes()
    compile((files / "valid.py").read_text(encoding="utf-8"), "valid", "exec")
