"""RE-03 (FR-50): file versions, hunks and renames, checked against git itself.

The oracle is git. Hunks must equal the line ranges of ``git diff -M -U0``, run here
separately with the fixture builders' isolated configuration, and file contents must
equal ``git show`` byte for byte. The line numbers built into the fixtures give a
second, hand-written expectation. FX-RG1 carries the frozen RE-03 case (util.py lines
10-14 edited, then renamed to helpers.py with 2 lines changed). FX-HUNKS, built below,
adds the other change kinds: a deletion, a pure rename, a rename under the 50% threshold,
binary, CRLF and Latin-1 content, a type change, and a file pair on which the Myers and
patience algorithms give different hunks.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import make_fx_rg1
import pytest
from fixture_git import FixtureRepo, Identity, isolated_env
from fx_rg1_clone import ClonedFxRg1

from tdea.git_handler.errors import ErrorCode, TdeaError
from tdea.git_handler.file_changes import ChangeType, FileChange, Hunk, parse_changes
from tdea.git_handler.history_reader import HistoryReader
from tdea.git_handler.local_repository import LocalRepository

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
DEV = Identity("Hunk Builder", "hunk.builder@example.invalid")

MYERS_BEFORE = ["return x", "x += 1", "}", "c = 3", "return x", "}", "return x", "c = 3",
                "a = 1"]
MYERS_AFTER = ["return x", "}", "c = 3", "x += 1", "return x", "}", "return x", "c = 3",
               "a = 1"]
MYERS_HUNKS = (Hunk(2, 1, 1, 0), Hunk(4, 0, 4, 1))  # git diff --diff-algorithm=myers


@dataclass(frozen=True)
class OracleSection:
    """What ``git diff -M -U0`` shows for one file pair."""

    kind: str
    old_path: str | None
    new_path: str | None
    similarity: int | None
    binary: bool
    hunks: tuple[Hunk, ...]


@dataclass(frozen=True)
class FxHunks:
    """FX-HUNKS, its clone and a reader over the clone."""

    path: Path
    shas: dict[str, str]
    reader: HistoryReader
    local: LocalRepository


def _lines(lines: list[str]) -> str:
    """Join lines into file text ending in a newline."""
    return "\n".join(lines) + "\n"


def git_bytes(repo: Path, *args: str) -> bytes:
    """Run git in ``repo`` with no system or global configuration; return raw stdout."""
    return subprocess.run(["git", *args], cwd=repo, env=isolated_env(repo),
                          capture_output=True, check=True).stdout


def oracle(repo: Path, sha: str, *options: str) -> list[OracleSection]:
    """Parse ``git diff -M -U0`` of ``sha`` against its first parent (or the empty tree)."""
    parents = git_bytes(repo, "rev-list", "--parents", "-n", "1", sha).decode().split()[1:]
    base = parents[0] if parents else EMPTY_TREE
    text = git_bytes(repo, "diff", "-M", "-U0", *options, base, sha).decode(
        "utf-8", errors="replace")
    sections: list[dict] = []
    for line in text.split("\n"):
        if line.startswith("diff --git "):
            path = line.split(" b/", 1)[1]
            sections.append({"kind": "M", "old_path": path, "new_path": path,
                             "similarity": None, "binary": False, "hunks": []})
            continue
        if sections:
            _apply(sections[-1], line)
    return [OracleSection(s["kind"], s["old_path"], s["new_path"], s["similarity"],
                          s["binary"], tuple(s["hunks"])) for s in sections]


def _apply(section: dict, line: str) -> None:
    """Record what one header or hunk line of git's patch says about its section."""
    match = HUNK_HEADER.match(line)
    if match:
        old_start, old_length, new_start, new_length = match.groups()
        section["hunks"].append(Hunk(int(old_start), int(old_length or 1),
                                     int(new_start), int(new_length or 1)))
    elif line.startswith("new file mode"):
        section["kind"], section["old_path"] = "A", None
    elif line.startswith("deleted file mode"):
        section["kind"], section["new_path"] = "D", None
    elif line.startswith("similarity index "):
        section["similarity"] = int(line.rsplit(" ", 1)[1].rstrip("%"))
    elif line.startswith("rename from "):
        section["kind"], section["old_path"] = "R", line[len("rename from "):]
    elif line.startswith("rename to "):
        section["new_path"] = line[len("rename to "):]
    elif line.startswith("Binary files "):
        section["binary"] = True


def as_oracle(change: FileChange) -> OracleSection:
    """Express a FileChange the way the oracle parses git's patch."""
    return OracleSection(change.change_type.value,
                         change.old.path if change.old else None,
                         change.new.path if change.new else None,
                         change.similarity, change.binary, change.hunks)


def _build_fx_hunks(path: Path) -> dict[str, str]:
    """Build FX-HUNKS: one commit per group of change kinds, with fixed dates."""
    repo = FixtureRepo(path)
    shas = {"root": repo.commit("Add the files", {
        "a.py": "".join(f"A{n} = {n}\n" for n in range(1, 6)),
        "gone.py": "G1 = 1\nG2 = 2\nG3 = 3\n",
        "same.py": "".join(f"S{n} = {n}\n" for n in range(1, 13)),
        "small.py": "T1 = 1\nT2 = 2\nT3 = 3\nT4 = 4\n",
        "alg.txt": _lines(MYERS_BEFORE),
        "data.bin": bytes(range(256)),
        "crlf.txt": b"one\r\ntwo\r\n",
        "latin1.py": b"# caf\xe9\nX = 1\n",
        "kind.txt": "plain\n",
    }, author=DEV, date="2026-05-04T09:00:00+00:00")}
    shas["modify_delete"] = repo.commit("Modify, append and delete", {
        "a.py": "A1 = 1\nA2 = 2\nA3 = 30\nA4 = 4\nA5 = 5\nA6 = 6\n",
        "gone.py": None,
        "alg.txt": _lines(MYERS_AFTER),
        "data.bin": bytes(range(255, -1, -1)),
        "crlf.txt": b"one\r\ntwo\r\nthree\r\n",
    }, author=DEV, date="2026-05-05T09:00:00+00:00")
    repo.git("mv", "same.py", "moved.py")
    repo.git("mv", "small.py", "tiny.py")
    shas["renames"] = repo.commit("Rename one file as is and rewrite another", {
        "tiny.py": "T1 = 1\nX2 = 20\nX3 = 30\nX4 = 40\n",
    }, author=DEV, date="2026-05-06T09:00:00+00:00")
    shas["type_change"] = _replace_with_symlink(repo, "kind.txt", "a.py")
    return shas


def _replace_with_symlink(repo: FixtureRepo, name: str, target: str) -> str:
    """Commit ``name`` as a symbolic link to ``target`` through the index (any platform)."""
    (repo.path / "link-target.tmp").write_bytes(target.encode())
    blob = repo.git("hash-object", "-w", "link-target.tmp").strip()
    (repo.path / "link-target.tmp").unlink()
    repo.git("update-index", "--cacheinfo", f"120000,{blob},{name}")
    date = "2026-05-07T09:00:00+00:00"
    repo.git("commit", "-q", "--no-verify", "-m", "Make kind.txt a link", env={
        "GIT_AUTHOR_NAME": DEV.name, "GIT_AUTHOR_EMAIL": DEV.email, "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_NAME": DEV.name, "GIT_COMMITTER_EMAIL": DEV.email,
        "GIT_COMMITTER_DATE": date,
    })
    return repo.head()


@pytest.fixture(name="fx_hunks", scope="module")
def _fx_hunks(tmp_path_factory: pytest.TempPathFactory) -> Iterator[FxHunks]:
    """Build FX-HUNKS once for this module and clone it."""
    path = tmp_path_factory.mktemp("fx") / "fx-hunks"
    shas = _build_fx_hunks(path)
    with LocalRepository(tmp_path_factory.mktemp("clones")) as local:
        local.clone(path)
        yield FxHunks(path, shas, HistoryReader(local), local)


@pytest.fixture(name="rg1_reader")
def _rg1_reader(cloned_fx_rg1: ClonedFxRg1) -> HistoryReader:
    """Return a reader over the shared FX-RG1 clone."""
    return HistoryReader(cloned_fx_rg1.local)


def _by_path(changes: tuple[FileChange, ...]) -> dict[tuple[str, str], FileChange]:
    """Index changes by (change type, the path they are about)."""
    return {(change.change_type.value, (change.new or change.old).path): change
            for change in changes}


def test_edit_hunks_equal_git_and_the_fixture(
    rg1_reader: HistoryReader, cloned_fx_rg1: ClonedFxRg1
) -> None:
    """Step 1: the util.py edit is one hunk over lines 10-14, as git diff -M shows it."""
    sha = cloned_fx_rg1.fixture.shas["util_edit"]
    changes = rg1_reader.file_changes(sha)
    assert [as_oracle(change) for change in changes] == oracle(cloned_fx_rg1.fixture.path, sha)
    (change,) = changes
    edited = sorted(make_fx_rg1.UTIL_EDITED_LINES)
    assert change.change_type is ChangeType.MODIFIED
    assert change.hunks == (Hunk(edited[0], len(edited), edited[0], len(edited)),)
    assert (change.lines_added, change.lines_removed) == (5, 5)


def test_rename_with_edits_is_one_r_record(
    rg1_reader: HistoryReader, cloned_fx_rg1: ClonedFxRg1
) -> None:
    """Step 2: util.py -> helpers.py is one R record with its 2 changed lines."""
    sha = cloned_fx_rg1.fixture.shas["rename"]
    changes = rg1_reader.file_changes(sha)
    (expected,) = oracle(cloned_fx_rg1.fixture.path, sha)
    (change,) = changes
    assert as_oracle(change) == expected
    assert change.change_type is ChangeType.RENAMED
    assert (change.old.path, change.new.path) == ("pkg/util.py", "pkg/helpers.py")
    assert change.similarity == expected.similarity and change.similarity >= 50
    assert change.hunks == tuple(Hunk(line, 1, line, 1)
                                 for line in sorted(make_fx_rg1.HELPERS_CHANGED_LINES))
    assert (change.lines_added, change.lines_removed) == (2, 2)


def test_every_fx_rg1_commit_matches_git_diff(
    rg1_reader: HistoryReader, cloned_fx_rg1: ClonedFxRg1
) -> None:
    """Every first-parent commit, root and merge included, gives git's records and hunks."""
    for sha in cloned_fx_rg1.fixture.first_parent_shas():
        changes = rg1_reader.file_changes(sha)
        assert [as_oracle(change) for change in changes] == oracle(
            cloned_fx_rg1.fixture.path, sha), sha


def test_root_and_merge_use_empty_tree_and_first_parent(
    rg1_reader: HistoryReader, cloned_fx_rg1: ClonedFxRg1
) -> None:
    """The root is diffed against the empty tree; the merge against its first parent only."""
    root = rg1_reader.file_changes(cloned_fx_rg1.commit("root"))
    assert {change.change_type for change in root} == {ChangeType.ADDED}
    assert all(change.hunks[0].old_start == 0 and change.old is None for change in root)
    merge = rg1_reader.file_changes(cloned_fx_rg1.fixture.shas["merge"])
    assert [(c.change_type, c.new.path) for c in merge] == [(ChangeType.ADDED, "pkg/feature.py")]


def test_fx_hunks_change_kinds_match_git(fx_hunks: FxHunks) -> None:
    """Additions, deletions, edits, renames, binary and type changes all equal git diff -M."""
    for label, sha in fx_hunks.shas.items():
        changes = fx_hunks.reader.file_changes(sha)
        assert [as_oracle(change) for change in changes] == oracle(fx_hunks.path, sha), label


def test_addition_modification_and_deletion(fx_hunks: FxHunks) -> None:
    """Hand-written expectations for an edit with an appended line and a deleted file."""
    changes = _by_path(fx_hunks.reader.file_changes(fx_hunks.shas["modify_delete"]))
    assert changes[("M", "a.py")].hunks == (Hunk(3, 1, 3, 1), Hunk(5, 0, 6, 1))
    deleted = changes[("D", "gone.py")]
    assert (deleted.old.path, deleted.new, deleted.hunks) == ("gone.py", None, (Hunk(1, 3, 0, 0),))
    assert changes[("M", "crlf.txt")].hunks == (Hunk(2, 0, 3, 1),)
    added = _by_path(fx_hunks.reader.file_changes(fx_hunks.shas["root"]))[("A", "a.py")]
    assert (added.old, added.hunks, added.lines_added) == (None, (Hunk(0, 0, 1, 5),), 5)


def test_renames_and_threshold(fx_hunks: FxHunks) -> None:
    """A pure rename is R at 100% with no hunks; a mostly rewritten file is D plus A."""
    changes = _by_path(fx_hunks.reader.file_changes(fx_hunks.shas["renames"]))
    moved = changes[("R", "moved.py")]
    assert (moved.old.path, moved.similarity, moved.hunks) == ("same.py", 100, ())
    assert (moved.lines_added, moved.lines_removed) == (0, 0)
    assert ("D", "small.py") in changes and ("A", "tiny.py") in changes
    assert not any(kind == "R" and path == "tiny.py" for kind, path in changes)


def test_binary_and_type_change(fx_hunks: FxHunks) -> None:
    """Binary files get no hunks; a type change is a deletion and an addition, as in git."""
    binary = _by_path(fx_hunks.reader.file_changes(fx_hunks.shas["modify_delete"]))
    data = binary[("M", "data.bin")]
    assert (data.binary, data.hunks, data.lines_added, data.lines_removed) == (
        True, (), None, None)
    kinds = [(c.change_type, (c.new or c.old).path)
             for c in fx_hunks.reader.file_changes(fx_hunks.shas["type_change"])]
    assert kinds == [(ChangeType.DELETED, "kind.txt"), (ChangeType.ADDED, "kind.txt")]


def test_myers_is_used_and_differs_from_patience(fx_hunks: FxHunks) -> None:
    """The fixed algorithm is Myers: git's patience hunks differ on this pair, ours do not."""
    sha = fx_hunks.shas["modify_delete"]
    ours = _by_path(fx_hunks.reader.file_changes(sha))[("M", "alg.txt")].hunks
    patience = {s.new_path: s.hunks
                for s in oracle(fx_hunks.path, sha, "--diff-algorithm=patience")}
    assert ours == MYERS_HUNKS
    assert patience["alg.txt"] != MYERS_HUNKS


def test_content_is_byte_identical_to_git_show(
    fx_hunks: FxHunks, cloned_fx_rg1: ClonedFxRg1
) -> None:
    """Step 3: every changed file's old and new version equals git show, byte for byte."""
    rg1_reader = HistoryReader(cloned_fx_rg1.local)
    cases = [(fx_hunks.reader, fx_hunks.path, sha) for sha in fx_hunks.shas.values()]
    cases += [(rg1_reader, cloned_fx_rg1.local.path, sha)
              for sha in cloned_fx_rg1.fixture.first_parent_shas()]
    checked = 0
    for reader, repo, sha in cases:
        for change in reader.file_changes(sha):
            if change.new is not None:
                expected = git_bytes(repo, "show", f"{sha}:{change.new.path}")
                assert reader.read_blob(sha, change.new.path) == expected
                assert reader.read_blob(change.new.blob) == expected
                checked += 1
            if change.old is not None:
                expected = git_bytes(repo, "cat-file", "blob", change.old.blob)
                assert reader.read_blob(change.old.blob) == expected
    assert checked > 20
    assert fx_hunks.reader.read_blob(fx_hunks.shas["root"], "latin1.py") == b"# caf\xe9\nX = 1\n"
    assert fx_hunks.reader.read_blob(fx_hunks.shas["root"], "crlf.txt") == b"one\r\ntwo\r\n"


def _tree_state(path: Path) -> dict[str, tuple[int, int]]:
    """Return size and modification time of every working-tree file outside .git."""
    return {file.relative_to(path).as_posix(): (file.stat().st_size, file.stat().st_mtime_ns)
            for file in path.rglob("*") if file.is_file() and ".git" not in file.parts}


def test_no_checkout_occurs(fx_hunks: FxHunks) -> None:
    """Step 4: reading versions and hunks leaves HEAD and the working tree untouched."""
    path = fx_hunks.local.path
    head_before = fx_hunks.local.repo.git.rev_parse("HEAD")
    tree_before = _tree_state(path)
    for sha in fx_hunks.shas.values():
        fx_hunks.reader.file_changes(sha)
    gone = fx_hunks.reader.read_blob(fx_hunks.shas["root"], "gone.py")
    assert gone == b"G1 = 1\nG2 = 2\nG3 = 3\n"
    assert not (path / "gone.py").exists()
    assert fx_hunks.local.repo.git.rev_parse("HEAD") == head_before
    assert fx_hunks.local.repo.git.status("--porcelain") == ""
    assert _tree_state(path) == tree_before


def test_unreadable_versions_are_structured_errors(fx_hunks: FxHunks) -> None:
    """A missing path, a directory, an unknown blob or commit gives a HISTORY_FAILED error."""
    root = fx_hunks.shas["root"]
    for call in (lambda: fx_hunks.reader.read_blob(root, "missing.py"),
                 lambda: fx_hunks.reader.read_blob("f" * 40),
                 lambda: fx_hunks.reader.file_changes("0" * 40)):
        with pytest.raises(TdeaError) as caught:
            call()
        assert caught.value.code is ErrorCode.HISTORY_FAILED


def test_rename_threshold_and_versions_for_metadata(fx_hunks: FxHunks) -> None:
    """Step 5: the 50% threshold, rename limit, algorithm and tool versions are reported."""
    settings = fx_hunks.reader.diff_settings()
    assert (settings["rename_threshold_percent"], settings["rename_limit"],
            settings["diff_algorithm"]) == (50, 1000, "myers")
    git_version = git_bytes(fx_hunks.path, "--version").decode().split()[2]
    assert git_version.startswith(str(settings["git_version"]))
    assert settings["gitpython_version"]


def _snapshot(fx_hunks: FxHunks, rg1_reader: HistoryReader, rename_sha: str) -> list:
    """Return the records of every FX-HUNKS commit and the FX-RG1 rename."""
    snapshot = [fx_hunks.reader.file_changes(sha) for sha in fx_hunks.shas.values()]
    return snapshot + [rg1_reader.file_changes(rename_sha)]


def test_hostile_system_and_global_configuration_is_ignored(
    fx_hunks: FxHunks, cloned_fx_rg1: ClonedFxRg1, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Global, system and environment config, global attributes and GIT_ variables do nothing."""
    rg1_reader = HistoryReader(cloned_fx_rg1.local)
    rename_sha = cloned_fx_rg1.fixture.shas["rename"]
    baseline = _snapshot(fx_hunks, rg1_reader, rename_sha)
    assert _snapshot(fx_hunks, rg1_reader, rename_sha) == baseline  # deterministic
    attributes = tmp_path / "attributes"
    attributes.write_text("*.py binary\n*.txt -diff\n", encoding="utf-8")
    config = tmp_path / "hostile.gitconfig"
    config.write_text(
        "[diff]\n\talgorithm = patience\n\trenames = false\n\trenameLimit = 1\n"
        "\tindentHeuristic = false\n\tcontext = 7\n"
        f"[core]\n\tattributesFile = {attributes.as_posix()}\n", encoding="utf-8")
    home = tmp_path / "home"
    (home / ".config" / "git").mkdir(parents=True)
    (home / ".gitconfig").write_text(config.read_text(encoding="utf-8"), encoding="utf-8")
    (home / ".config" / "git" / "attributes").write_text("*.py binary\n", encoding="utf-8")
    for name, value in {"GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_SYSTEM": str(config),
                        "HOME": str(home), "USERPROFILE": str(home),
                        "XDG_CONFIG_HOME": str(home / ".config"),
                        "GIT_DIFF_OPTS": "--unified=5",
                        "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.bigFileThreshold",
                        "GIT_CONFIG_VALUE_0": "1",
                        "GIT_CONFIG_PARAMETERS": "'core.bigfilethreshold'='1'",
                        "GIT_DIR": str(tmp_path / "elsewhere.git")}.items():
        monkeypatch.setenv(name, value)
    assert _snapshot(fx_hunks, rg1_reader, rename_sha) == baseline


def test_hostile_repository_configuration_is_overridden(
    fx_hunks: FxHunks, tmp_path: Path
) -> None:
    """A clone whose own config marks every file big or .py files binary gives the same.

    Only settings that git diff-tree reads are planted; porcelain settings such as
    diff.algorithm do not reach it at all.
    """
    baseline = [fx_hunks.reader.file_changes(sha) for sha in fx_hunks.shas.values()]
    attributes = tmp_path / "attributes"
    attributes.write_text("*.py binary\n*.txt -diff\n", encoding="utf-8")
    (tmp_path / "clones").mkdir()
    with LocalRepository(tmp_path / "clones") as local:
        local.clone(fx_hunks.path)
        with local.repo.config_writer() as config:
            config.set_value("core", "bigFileThreshold", "1")
            config.set_value("core", "attributesFile", attributes.as_posix())
        reader = HistoryReader(local)
        assert [reader.file_changes(sha) for sha in fx_hunks.shas.values()] == baseline


def test_crlf_working_tree_is_never_read(
    fx_hunks: FxHunks, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clone checked out with CRLF conversion and a clean index gives the same records.

    Git for Windows converts line endings on checkout by default. Git reuses a checked-out
    file instead of its blob when the index marks it clean, so the reader must not use
    the index; otherwise alg.txt would read as one 9-line rewrite.
    """
    baseline = [fx_hunks.reader.file_changes(sha) for sha in fx_hunks.shas.values()]
    config = tmp_path / "autocrlf.gitconfig"
    config.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")
    (tmp_path / "clones").mkdir()
    with LocalRepository(tmp_path / "clones") as local:
        with monkeypatch.context() as patched:
            patched.setenv("GIT_CONFIG_GLOBAL", str(config))
            local.clone(fx_hunks.path)
            for name in ("a.py", "alg.txt", "crlf.txt", "latin1.py", "moved.py", "tiny.py"):
                os.utime(local.path / name, (1_000_000_000, 1_000_000_000))
            local.repo.git.update_index("--refresh")
        assert (local.path / "alg.txt").read_bytes().count(b"\r\n") == len(MYERS_AFTER)
        reader = HistoryReader(local)
        assert [reader.file_changes(sha) for sha in fx_hunks.shas.values()] == baseline


def test_sections_that_do_not_line_up_are_refused() -> None:
    """If a patch section's blob IDs differ from its file pair, parsing fails loudly."""
    old, new, other = "1" * 40, "2" * 40, "3" * 40
    raw = f":100644 100644 {old} {new} M\0a.py\0".encode()
    good = f"diff --git a/a.py b/a.py\nindex {old}..{new} 100644\n@@ -1 +1 @@\n".encode()
    assert parse_changes(raw, good)[0].hunks == (Hunk(1, 1, 1, 1),)
    with pytest.raises(ValueError, match="line up"):
        parse_changes(raw, good.replace(new.encode(), other.encode()))
    with pytest.raises(ValueError, match="fewer"):
        parse_changes(raw + raw, good)
        