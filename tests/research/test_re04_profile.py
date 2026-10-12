"""RE-04 (FR-51, NFR-24, NFR-26): the pinned research profile and its enforcement.

Steps 1-2 (Python 3.12, another Pylint or astroid version: exit 3 naming the expected
versions) use injected environment facts; step 3 accepts the running interpreter's real
tool versions. Step 4 compares every message with the Pylint CLI, run separately here as
RE-04 states it: ``pylint --rcfile=<empty> --disable=<list> --output-format=json`` on one
file in an empty folder. The profile hash is checked against a SHA-256 built here from
the frozen FR-51 text, not from the implementation. CL-04: E0402 stays enabled.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import fields, replace
from pathlib import Path

import make_fx_pairs
import make_fx_product
import pytest

from tdea.analyzer.research import profile as profile_module
from tdea.analyzer.research.profile import (
    DECODE_ERROR, DISABLED_MESSAGES, FAILED, OK, SYNTAX_ERROR, TIME_OUT, EnvironmentFacts,
    EnvironmentRefused, ResearchProfile, canonical_profile, hash_profile)

# FR-51 (a), expanded by hand: C0103, C0114-C0116, C0301, C0303-C0305, C0411-C0415, E0401,
# E0611 and R0801.
FROZEN_DISABLE = ["C0103", "C0114", "C0115", "C0116", "C0301", "C0303", "C0304", "C0305",
                  "C0411", "C0412", "C0413", "C0414", "C0415", "E0401", "E0611", "R0801"]
FROZEN_PROFILE = {"interpreter": "3.11", "pylint": "4.1.1", "astroid": "4.3.3",
                  "radon": "6.0.1", "rcfile": "", "disable": FROZEN_DISABLE,
                  "enabled_extras": []}
CURRENT_PROFILE_HASH = "3b2c40b30564686587e9499fa7f9e9dda74396d36e6beeb9ad7df4b7451c65c8"
BUSY = (
    '"""A module with several research-profile messages."""\n'
    "import os\n"
    "import sys\n\n\n"
    "def handler(value, unused):\n"
    '    """Handle a value."""\n'
    "    result = 1\n"
    "    try:\n"
    '        print("{}".format(value))\n'
    "    except:\n"
    "        pass\n"
    "    try:\n"
    "        return os.getcwd()\n"
    "    except Exception:\n"
    "        return None\n"
)
ON_311 = sys.version_info[:2] == (3, 11)


def cli_messages(name: str, content: bytes, folder: Path) -> list[dict]:
    """Run the Pylint CLI exactly as RE-04 step 4 states it, on one file in ``folder``.

    Pylint's stats file goes to a PYLINTHOME beside ``folder`` so the test never writes
    to the developer's home; that setting does not change the messages.
    """
    folder.mkdir()
    (folder / name).write_bytes(content)
    rcfile = folder.parent / f"{folder.name}.empty-rc"
    rcfile.write_text("", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "pylint", f"--rcfile={rcfile}",
         f"--disable={','.join(FROZEN_DISABLE)}", "--output-format=json", name],
        cwd=folder, capture_output=True, check=False,
        env=dict(os.environ, PYLINTHOME=str(folder.parent / f"{folder.name}.pylinthome")))
    return json.loads(completed.stdout.decode("utf-8"))


class MemoryCache:
    """A LintCachePort in memory that records what was stored (test double)."""

    def __init__(self) -> None:
        """Start empty."""
        self.entries: dict[tuple[str, str, str], object] = {}

    def get(self, blob_sha: str, file_name: str, profile_hash: str):
        """Return the stored result, or None."""
        return self.entries.get((blob_sha, file_name, profile_hash))

    def put(self, blob_sha: str, file_name: str, profile_hash: str, result) -> None:
        """Store the result."""
        self.entries[(blob_sha, file_name, profile_hash)] = result


@pytest.fixture(name="profile")
def _profile() -> ResearchProfile:
    """Return the research profile with its frozen settings."""
    return ResearchProfile()


@pytest.mark.skipif(not ON_311, reason="step 3 runs on the research interpreter, Python 3.11")
def test_step3_pinned_environment_is_accepted(profile: ResearchProfile) -> None:
    """Python 3.11 with Pylint 4.1.1, astroid 4.3.3 and radon 6.0.1 is accepted."""
    facts = replace(EnvironmentFacts.current(), in_virtual_environment=True)
    assert profile.check_environment(facts) == facts
    assert (facts.pylint, facts.astroid, facts.radon) == ("4.1.1", "4.3.3", "6.0.1")
    assert not facts.outside_site_packages


@pytest.mark.parametrize("change, found, expected", [
    ({"python": (3, 12, 4)}, "Python 3.12.4", "expected 3.11.x"),
    ({"python": (3, 10, 14)}, "Python 3.10.14", "expected 3.11.x"),
    ({"pylint": "4.1.0"}, "Pylint 4.1.0", "expected 4.1.1"),
    ({"astroid": "4.3.2"}, "astroid 4.3.2", "expected 4.3.3"),
    ({"radon": "6.0.0"}, "radon 6.0.0", "expected 6.0.1"),
    ({"in_virtual_environment": False}, "not a virtual environment", "expected one"),
    ({"outside_site_packages": ("Pylint",)}, "Pylint is loaded from outside",
     "expected the installed one"),
])
def test_steps1_2_wrong_environment_is_refused_with_exit_3(
    profile: ResearchProfile, change: dict, found: str, expected: str
) -> None:
    """Another interpreter or tool version is refused with exit 3, naming what is expected."""
    good = EnvironmentFacts((3, 11, 9), True, "4.1.1", "4.3.3", "6.0.1")
    with pytest.raises(EnvironmentRefused) as caught:
        profile.check_environment(replace(good, **change))
    assert caught.value.exit_code == 3
    assert found in caught.value.cause and expected in caught.value.cause
    assert "3.11" in caught.value.action and "pylint==4.1.1" in caught.value.action


def test_every_problem_is_named_and_gitpython_is_never_checked(profile: ResearchProfile) -> None:
    """All mismatches are listed together; GitPython is not among the checked facts.

    Task 6 decision 6: GitPython is recorded, never refused. The git_handler records its
    version with the diff settings (test_re03_hunks), so the profile does not look at it.
    """
    bad = EnvironmentFacts((3, 12, 0), False, "3.3.0", "3.3.0", "5.1.0", ("radon",))
    with pytest.raises(EnvironmentRefused) as caught:
        profile.check_environment(bad)
    assert caught.value.cause.count("expected") == 6
    assert not [field.name for field in fields(EnvironmentFacts) if "git" in field.name]
    good = EnvironmentFacts((3, 11, 2), True, "4.1.1", "4.3.3", "6.0.1")
    assert "gitpython_version" not in profile.metadata(profile.check_environment(good))


def test_tools_loaded_from_outside_site_packages_are_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A Pylint that PYTHONPATH or the current folder supplied is not the one -I would run."""
    assert not EnvironmentFacts.current().outside_site_packages
    monkeypatch.setattr(profile_module.pylint, "__file__", str(tmp_path / "pylint" / "x.py"))
    assert EnvironmentFacts.current().outside_site_packages == ("Pylint",)


def test_profile_hash_is_sha256_of_the_frozen_profile(profile: ResearchProfile) -> None:
    """The hash is SHA-256 of FR-51's inputs as canonical JSON; it is deterministic."""
    text = json.dumps(FROZEN_PROFILE, sort_keys=True, separators=(",", ":"))
    assert profile.profile_hash() == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert profile.profile_hash() == ResearchProfile(time_limit=5).profile_hash()
    assert profile.profile_hash() == CURRENT_PROFILE_HASH
    assert canonical_profile() == FROZEN_PROFILE


@pytest.mark.parametrize("key, value", [
    ("interpreter", "3.12"), ("pylint", "4.1.2"), ("astroid", "4.3.4"), ("radon", "6.0.2"),
    ("rcfile", "[MAIN]\n"), ("disable", sorted(FROZEN_DISABLE + ["E0402"])),
    ("enabled_extras", ["pylint.extensions.docparams"]),
])
def test_profile_hash_changes_with_each_input(key: str, value: object) -> None:
    """Changing any FR-51 input, such as disabling E0402 at G2, gives a new hash."""
    assert hash_profile({**FROZEN_PROFILE, key: value}) != CURRENT_PROFILE_HASH


def test_e0402_stays_enabled_cl04(profile: ResearchProfile) -> None:
    """CL-04 is open until G2: E0402 is not disabled and fires on a lone relative import."""
    assert "E0402" not in DISABLED_MESSAGES
    result = profile.lint(b'"""Doc."""\nfrom .sibling import thing\n\nprint(thing)\n', "a.py")
    assert [message.message_id for message in result.messages] == ["E0402"]


@pytest.mark.parametrize("name, content", [
    ("smell_profile.py", make_fx_pairs.SMELL_PROFILE.encode()),
    ("scopes.py", make_fx_pairs.SCOPES.encode()),
    ("busy.py", BUSY.encode()),
])
def test_step4_messages_equal_the_pylint_cli(
    profile: ResearchProfile, tmp_path: Path, name: str, content: bytes
) -> None:
    """Every message, and its scope, equals the CLI's under the same options."""
    expected = cli_messages(name, content, tmp_path / "cli")
    result = profile.lint(content, name)
    assert result.status == OK and expected
    assert [(m.symbol, m.message_id, m.category, m.message, m.line, m.scope)
            for m in result.messages] == [
        (e["symbol"], e["message-id"], e["type"], e["message"], e["line"], e["obj"])
        for e in expected]


def test_disabled_ids_never_appear_despite_a_project_pylintrc(
    profile: ResearchProfile, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pylintrc enabling C0103 and C0301, in the project, PYLINTRC or HOME, has no effect."""
    hostile = "[MAIN]\nload-plugins=no_such_plugin\n[MESSAGES CONTROL]\nenable=C0103,C0301\n"
    project = tmp_path / "project"
    project.mkdir()
    for name in ("pylintrc", ".pylintrc"):
        (project / name).write_text(hostile, encoding="utf-8")
    (project / "pyproject.toml").write_text('[tool.pylint."messages control"]\n'
                                            'enable = ["C0103", "C0301"]\n', encoding="utf-8")
    monkeypatch.chdir(project)
    monkeypatch.setenv("PYLINTRC", str(project / "pylintrc"))
    monkeypatch.setenv("HOME", str(project))
    result = profile.lint(make_fx_pairs.SMELL_PROFILE.encode(), "smell_profile.py")
    assert result.status == OK
    assert [m.message_id for m in result.messages] == ["W0611"]


def test_pythonpath_is_cleared_and_the_interpreter_isolated(
    profile: ResearchProfile, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PYTHONPATH holding a fake pylint and sitecustomize is never used (python -I)."""
    planted = tmp_path / "planted"
    sentinel = tmp_path / "sentinel"
    for module in ("sitecustomize.py", "usercustomize.py", "pylint/__init__.py",
                   "pylint/__main__.py"):
        (planted / module).parent.mkdir(parents=True, exist_ok=True)
        (planted / module).write_text(
            f"open({str(sentinel)!r}, 'a', encoding='utf-8').write('{module}\\n')\n",
            encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(planted))
    monkeypatch.setenv("PYTHONSTARTUP", str(planted / "sitecustomize.py"))
    result = profile.lint(make_fx_pairs.SMELL_PROFILE.encode(), "smell_profile.py")
    assert result.status == OK and [m.message_id for m in result.messages] == ["W0611"]
    assert not sentinel.exists()


def test_command_uses_the_empty_rcfile_and_no_plugins(profile: ResearchProfile) -> None:
    """The command is python -I -m pylint with the empty rcfile, frozen list and JSON."""
    command = profile.pylint_command(Path("empty.rc"), "a.py")
    assert command == [sys.executable, "-I", "-m", "pylint", "--rcfile=empty.rc",
                       f"--disable={','.join(FROZEN_DISABLE)}", "--output-format=json",
                       "--persistent=n", "a.py"]
    assert canonical_profile()["rcfile"] == "" and canonical_profile()["enabled_extras"] == []


def test_fx_err_failing_files_are_excluded_and_counted(
    profile: ResearchProfile, tmp_path: Path
) -> None:
    """Syntax, decode and binary failures keep no messages; valid and empty files lint."""
    built = make_fx_product.build_fx_err(tmp_path / "fx-err")
    results = {path.name: profile.lint(path.read_bytes(), path.name)
               for path in sorted((built.path / "fxerr").iterdir())}
    assert {name: (r.status, r.failure_reason) for name, r in results.items()} == {
        "binary.py": (FAILED, DECODE_ERROR), "empty.py": (OK, None),
        "latin1.py": (FAILED, DECODE_ERROR), "syntax_error.py": (FAILED, SYNTAX_ERROR),
        "valid.py": (OK, None)}
    assert sum(r.status == FAILED for r in results.values()) == 3
    assert all(not r.messages for r in results.values() if r.status == FAILED)


@pytest.mark.parametrize("content, reason", [
    (b"NAME = 'caf\xe9'\n", DECODE_ERROR),  # bad byte on line 1
    (b'"""Doc."""\n\nNAME = "caf\xe9"\n', DECODE_ERROR),  # bad byte on line 3
    (b"# -*- coding: no-such-codec -*-\nVALUE = 1\n", DECODE_ERROR),
    (b"\xef\xbb\xbf# -*- coding: latin-1 -*-\nVALUE = 1\n", DECODE_ERROR),  # BOM conflict
    ("VALUE = 1\n".encode("utf-16"), DECODE_ERROR),
    (b"VALUE = 1\x00\n", SYNTAX_ERROR),  # decodes, but Python rejects the NUL
    (b"# -*- coding: latin-1 -*-\nNAME = 'caf\xe9'\n", None),  # declared, so it decodes
])
def test_decode_errors_are_decided_by_python_source_rules(
    profile: ResearchProfile, content: bytes, reason: str | None
) -> None:
    """Content that does not decode as Python source fails as a decode error, wherever."""
    result = profile.lint(content, "mod.py")
    assert result.failure_reason == reason
    assert result.status == (OK if reason is None else FAILED)


def test_cache_needs_an_accepted_environment_and_the_frozen_limit() -> None:
    """Results are cached only by a profile whose environment check passed, at 120 s."""
    cache = MemoryCache()
    with pytest.raises(RuntimeError, match="check_environment"):
        ResearchProfile().lint(b"VALUE = 1\n", "a.py", blob_sha="a" * 40, cache=cache)
    short = ResearchProfile(time_limit=5)
    short.check_environment(EnvironmentFacts((3, 11, 9), True, "4.1.1", "4.3.3", "6.0.1"))
    with pytest.raises(RuntimeError, match="120 s"):
        short.lint(b"VALUE = 1\n", "a.py", blob_sha="a" * 40, cache=cache)
    assert not cache.entries


def test_pylint_that_cannot_start_is_raised_not_stored(
    profile: ResearchProfile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failing to start Pylint is an environment problem, never the file's lint result."""
    monkeypatch.setattr(profile, "pylint_command", lambda *_args: ["/no/such/python"])
    with pytest.raises(OSError):
        profile.lint(b"VALUE = 1\n", "a.py")


def test_time_limit_fails_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file that exceeds the limit fails with reason time-out; its folder is removed."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    result = ResearchProfile(time_limit=0.01).lint(b"VALUE = 1\n", "slow.py")
    assert (result.status, result.failure_reason, result.messages) == (FAILED, TIME_OUT, ())
    assert not list(tmp_path.iterdir())


def test_temporary_folders_are_removed_and_nothing_written_elsewhere(
    profile: ResearchProfile, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After success or failure no temporary folder is left, and HOME gets no Pylint data."""
    temporary = tmp_path / "tmp"
    home = tmp_path / "home"
    temporary.mkdir()
    home.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporary))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.delenv("PYLINTHOME", raising=False)
    profile.lint(BUSY.encode(), "busy.py")
    profile.lint(b"def broken(:\n", "broken.py")
    assert not list(temporary.iterdir())
    assert not list(home.rglob("*"))


def test_results_are_deterministic_and_names_must_be_plain(profile: ResearchProfile) -> None:
    """The same content gives an equal result; a path or option-like name is refused."""
    assert profile.lint(BUSY.encode(), "busy.py") == profile.lint(BUSY.encode(), "busy.py")
    for name in ("pkg/busy.py", "pkg\\busy.py", "-busy.py", "", ".."):
        with pytest.raises(ValueError):
            profile.lint(BUSY.encode(), name)
            