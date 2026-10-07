"""FX-RG1 v1 and FX-PAIRS: the Day 2 cases are present and every build is identical.

The pinned HEAD SHAs make CI check determinism across operating systems: a commit SHA
covers every file, identity, date, message and parent, so one changed byte anywhere
changes it.
"""

from __future__ import annotations

import re
from pathlib import Path

import make_fx_pairs
import make_fx_rg1
import pytest
from fixture_git import git_output

FX_RG1_V1_HEAD = "b65545bb89af14d6c6ce67bdb9fbff451904a7da"
FX_PAIRS_V1_HEAD = "6653dffa54ede3defeeb21aa76bad7bcd2adb470"
FULL_LOG = "--format=%H %P%n%an <%ae> %aI%n%cn <%ce> %cI%n%B%n--"


def test_fx_rg1_is_identical_on_every_build(tmp_path: Path, fx_rg1: make_fx_rg1.FxRg1) -> None:
    """Two builds give the same history, and HEAD is the pinned v1 SHA."""
    again = make_fx_rg1.build(tmp_path / "again")
    assert again.shas == fx_rg1.shas
    assert git_output(again.path, "log", "--all", FULL_LOG) == \
        git_output(fx_rg1.path, "log", "--all", FULL_LOG)
    assert fx_rg1.shas["latest"] == FX_RG1_V1_HEAD


def test_fx_rg1_ignores_machine_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hostile global git config, another time zone and stray GIT_ variables change nothing."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        "[user]\n\tname = Hostile\n[init]\n\tdefaultBranch = trunk\n"
        "[core]\n\tautocrlf = true\n[commit]\n\tgpgsign = true\n", encoding="utf-8")
    for name, value in (("HOME", str(home)), ("XDG_CONFIG_HOME", str(home)),
                        ("USERPROFILE", str(home)), ("TZ", "Pacific/Kiritimati"),
                        ("GIT_AUTHOR_NAME", "Intruder"),
                        ("GIT_COMMITTER_DATE", "2001-01-01T00:00:00")):
        monkeypatch.setenv(name, value)
    assert make_fx_rg1.build(tmp_path / "hostile").shas["latest"] == FX_RG1_V1_HEAD


def test_fx_rg1_has_the_day2_cases(fx_rg1: make_fx_rg1.FxRg1) -> None:
    """Merge, +05:30 dates, trailers, AI committer and author, bot, folders, generated file."""
    shas, path = fx_rg1.shas, fx_rg1.path

    def show(label: str, fmt: str) -> str:
        return git_output(path, "show", "-s", f"--format={fmt}", shas[label]).strip()

    assert len(show("merge", "%P").split()) == 2
    assert show("trailers", "%aI").endswith("+05:30")
    trailers = show("trailers", "%B")
    assert make_fx_rg1.AI_TRAILER in trailers and "Co-authored-by: Asha Rao" in trailers
    assert show("ai_committer", "%cn|%an") == "copilot-swe-agent[bot]|Ravi Menon"
    assert show("ai_author", "%an|%cn") == "Cursor Agent|Asha Rao"
    assert show("dependency_bot", "%an") == "dependabot[bot]"
    signed = show("signed_off", "%B")
    assert "Signed-off-by: Jane Doe <jane.doe@example.invalid>" in signed
    assert "Reported by jane.doe@example.invalid" in signed
    tree = git_output(path, "ls-tree", "-r", "--name-only", shas["latest"]).split()
    for folder in ("tests/", "docs/", "examples/", "vendor/", "migrations/"):
        assert any(name.startswith(folder) for name in tree), folder
    generated = git_output(path, "show", f"{shas['latest']}:pkg/_generated_schema.py")
    assert generated.startswith("# Code generated") and "DO NOT EDIT" in generated


def test_fx_rg1_rename_with_edits_matches_re03(fx_rg1: make_fx_rg1.FxRg1) -> None:
    """util.py lines 10-14 are edited, then util.py becomes helpers.py with 2 lines changed."""
    shas, path = fx_rg1.shas, fx_rg1.path
    edit = git_output(path, "diff", "-U0", shas["dependency_bot"], shas["util_edit"])
    assert re.findall(r"^@@ .* @@", edit, flags=re.MULTILINE) == ["@@ -10,5 +10,5 @@"]
    rename = git_output(path, "diff", "-M", "--name-status", shas["util_edit"], shas["rename"])
    assert re.fullmatch(r"R\d+\tpkg/util\.py\tpkg/helpers\.py\n", rename)
    numstat = git_output(path, "diff", "-M", "--numstat", shas["util_edit"], shas["rename"])
    assert numstat.split()[:2] == ["2", "2"]


def test_fx_pairs_is_identical_on_every_build(tmp_path: Path) -> None:
    """Two builds give the same pairs, and the last commit is the pinned v1 SHA."""
    first = make_fx_pairs.build(tmp_path / "one")
    assert make_fx_pairs.build(tmp_path / "two") == first
    assert first["e_added_modified_untouched"].after_sha == FX_PAIRS_V1_HEAD


def flagged_lines(text: str) -> list[tuple[int, str]]:
    """Return (line number, text) of the lines holding the W1309 f-strings."""
    return [(number, line) for number, line in enumerate(text.splitlines(), start=1)
            if 'print(f"' in line]


def test_fx_pairs_cases_are_as_described() -> None:
    """Each pair differs from its before version exactly as its RE case says."""
    pairs = {name: (flagged_lines(before), flagged_lines(after))
             for name, (_description, before, after) in make_fx_pairs.PAIRS.items()}
    (before_a,), (after_a,) = pairs["a_moved"]
    assert after_a[0] - before_a[0] == 5 and after_a[1] == before_a[1]
    assert len(pairs["b_duplicate"][0]) == 2 and len(pairs["b_duplicate"][1]) == 1
    assert len(pairs["c_two_scopes"][1]) == 2
    (before_d,), (after_d,) = pairs["d_whitespace"]
    assert before_d[1] != after_d[1]
    assert " ".join(before_d[1].split()) == " ".join(after_d[1].split())
    before_e = [text for _number, text in pairs["e_added_modified_untouched"][0]]
    after_e = [text for _number, text in pairs["e_added_modified_untouched"][1]]
    assert before_e[0] == after_e[0] and before_e[1] != after_e[1] and len(after_e) == 3


def test_profile_and_scope_files_are_written(tmp_path: Path) -> None:
    """smell_profile.py and scopes.py are written at test time with their trigger code."""
    profile = make_fx_pairs.write_smell_profile(tmp_path).read_text(encoding="utf-8")
    assert "import os" in profile and "def BadlyNamedFunction" in profile
    assert max(len(line) for line in profile.splitlines()) > 100
    scopes = make_fx_pairs.write_scopes(tmp_path).read_text(encoding="utf-8")
    assert "class Cart" in scopes and "def total" in scopes and "def inner" in scopes
    compile(scopes, "scopes.py", "exec")
    compile(profile, "smell_profile.py", "exec")
