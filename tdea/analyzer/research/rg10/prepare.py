"""prepare_module and tree_sha256: the module the harness executes, built as TDEA builds it.

FR-59 and SRS 4.5.3. The research harness imports both functions from the installed TDEA
package, so the harness and TDEA build and hash exactly the same module and tree; both
share PREPARE_MODULE_VERSION, which the bundle and every execution record must carry.

prepare_module(module_text, marker, output_text) is pure (no I/O, clock or randomness).
It takes the first complete fenced code block tagged python, py or python3 from the
model output. Its status is "none" when there is no such block, "multiple" when more
such blocks follow (the first is used), and "unterminated" when the first one has no
closing fence. With "inserted" or "multiple", the block is inserted after the line that
holds the task marker, each non-blank line indented like the marker line (D-16). With
"none" or "unterminated" there is no modified module (D-17). Inputs are read with LF line
endings, and the module text it returns is LF-only; its SHA-256 is taken over that text
as UTF-8.

How fences are recognised (not spelled out by the frozen texts; recorded in the Task 5
report): CommonMark fenced code blocks at document level, opened by 3 or more backticks
or tildes indented at most 3 spaces; the tag is the first word of the info string and must
equal python, py or python3 exactly; a block closes at a fence of the same character that
is at least as long. Fences nested in Markdown lists or block quotes are not parsed as
such. The block's own indentation is kept as written; whitespace-only lines become empty.

tree_sha256(variant_root, target_module, module_text, hidden_tests_dir, task_id) only reads
files. It is SHA-256 over the sorted UTF-8 lines "path TAB file-SHA-256", each ending in
LF, with POSIX paths relative to the variant root. The modified module is hashed as UTF-8
with LF endings, in place of the variant's file at ``target_module``; every other file is
hashed as its bytes; the task's hidden tests appear under .hidden_tests/<task_id>/.
Symbolic links are refused.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath

PREPARE_MODULE_VERSION = "1"
PYTHON_TAGS = frozenset({"python", "py", "python3"})
HIDDEN_TESTS = ".hidden_tests"

_OPENING = re.compile(r"^(?P<indent> {0,3})(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_CLOSING = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})[ \t]*$")
_INDENT = re.compile(r"^[ \t]*")


class ExtractionStatus(Enum):
    """The FR-59 extraction statuses."""

    NONE = "none"
    INSERTED = "inserted"
    MULTIPLE = "multiple"
    UNTERMINATED = "unterminated"


@dataclass(frozen=True)
class PreparedModule:
    """prepare_module's result: status, modified module text and inserted line range."""

    status: ExtractionStatus
    text: str | None
    inserted_range: tuple[int, int] | None  # 1-based, inclusive
    n_added_lines: int
    version: str = PREPARE_MODULE_VERSION

    @property
    def module_sha256(self) -> str | None:
        """Return the SHA-256 of the modified module, or None when there is none."""
        return None if self.text is None else module_sha256(self.text)


@dataclass(frozen=True)
class _Block:
    """One fenced code block of the output."""

    tag: str
    lines: tuple[str, ...]
    closed: bool


def to_lf(text: str) -> str:
    """Return ``text`` with CRLF and lone CR line endings turned into LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def module_sha256(text: str) -> str:
    """Return the SHA-256 of module text as UTF-8 with LF line endings."""
    return hashlib.sha256(to_lf(text).encode("utf-8")).hexdigest()


def prepare_module(module_text: str, marker: str, output_text: str) -> PreparedModule:
    """Build the modified module from a variant module, its task marker and an output."""
    module_lines = to_lf(module_text).split("\n")
    if not marker or "\n" in to_lf(marker) or to_lf(module_text).count(marker) != 1:
        raise ValueError("the task marker must be one line occurring exactly once")
    blocks = [block for block in _blocks(to_lf(output_text).split("\n"))
              if block.tag in PYTHON_TAGS]
    if not blocks:
        return PreparedModule(ExtractionStatus.NONE, None, None, 0)
    first = blocks[0]
    if not first.closed:
        return PreparedModule(ExtractionStatus.UNTERMINATED, None, None, 0)
    status = ExtractionStatus.MULTIPLE if len(blocks) > 1 else ExtractionStatus.INSERTED
    marker_at = next(index for index, line in enumerate(module_lines) if marker in line)
    indent = _INDENT.match(module_lines[marker_at]).group(0)
    inserted = [indent + line if line.strip() else "" for line in first.lines]
    text = "\n".join(module_lines[:marker_at + 1] + inserted + module_lines[marker_at + 1:])
    span = (marker_at + 2, marker_at + 1 + len(inserted)) if inserted else None
    return PreparedModule(status, text, span, len(inserted))


def _blocks(lines: list[str]) -> Iterator[_Block]:
    """Yield the document-level fenced code blocks of Markdown ``lines``."""
    index = 0
    while index < len(lines):
        opening = _OPENING.match(lines[index])
        index += 1
        if opening is None:
            continue
        fence, info = opening.group("fence"), opening.group("info").strip()
        if fence[0] == "`" and "`" in info:
            continue  # not a fence in CommonMark
        width = len(opening.group("indent"))
        content: list[str] = []
        closed = False
        while index < len(lines):
            closing = _CLOSING.match(lines[index])
            index += 1
            if (closing and closing.group("fence")[0] == fence[0]
                    and len(closing.group("fence")) >= len(fence)):
                closed = True
                break
            content.append(_dedent(lines[index - 1], width))
        yield _Block(info.split()[0] if info else "", tuple(content), closed)


def _dedent(line: str, width: int) -> str:
    """Remove up to ``width`` leading spaces, as CommonMark does inside an indented fence."""
    stripped = len(line) - len(line.lstrip(" "))
    return line[min(stripped, width):]


def tree_sha256(variant_root: Path | str, target_module: str, module_text: str,
                hidden_tests_dir: Path | str, task_id: str) -> str:
    """Return the SHA-256 of the executed tree: variant, modified module and hidden tests."""
    target = PurePosixPath(target_module)
    if target.is_absolute() or ".." in target.parts or not task_id or "/" in task_id \
            or "\\" in task_id:
        raise ValueError("target_module must be relative and task_id a plain name")
    entries: dict[str, str] = {}
    for relative, path in _files(Path(variant_root)):
        if relative.split("/")[0] == HIDDEN_TESTS:
            raise ValueError(f"the variant itself must not contain {HIDDEN_TESTS}/")
        entries[relative] = _file_sha256(path)
    entries[target.as_posix()] = module_sha256(module_text)
    for relative, path in _files(Path(hidden_tests_dir)):
        entries[f"{HIDDEN_TESTS}/{task_id}/{relative}"] = _file_sha256(path)
    listing = "".join(f"{line}\n" for line in sorted(
        f"{relative}\t{digest}" for relative, digest in entries.items()))
    return hashlib.sha256(listing.encode("utf-8")).hexdigest()


def _files(root: Path) -> Iterator[tuple[str, Path]]:
    """Yield (POSIX path relative to ``root``, path) for every file under ``root``."""
    if not root.is_dir():
        raise ValueError(f"{root} is not a directory")
    for directory, subdirectories, names in os.walk(root):
        for name in subdirectories + names:
            if os.path.islink(os.path.join(directory, name)):
                raise ValueError("symbolic links are not supported in an executed tree")
        for name in names:
            path = Path(directory, name)
            yield path.relative_to(root).as_posix(), path


def _file_sha256(path: Path) -> str:
    """Return the SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()

