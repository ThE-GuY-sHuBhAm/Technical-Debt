"""The RG10 run identifier and bundle content hash (FR-48; sprint 23 Oct, provisional).

The frozen texts say an RG10 run identifier adds the bundle content hash but do not
define the hash. The provisional definition (bundle.bundle_content_hash) is pinned here:
a copy of a bundle keeps its hash, any changed, added or renamed file changes it, and
symbolic links are refused. The identifier changes with the hash and the smell mapping,
not with the store or bundle path.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from make_fx_rg10 import FxRg10

from tdea.aggregator.run_file import Rg10RunFile
from tdea.aggregator.run_identity import canonical_text, run_identifier
from tdea.analyzer.research.rg10.bundle import (DEFAULT_SMELL_MAPPING, BundleInvalid,
                                                bundle_content_hash, checked_mapping)

DEFAULT = checked_mapping(DEFAULT_SMELL_MAPPING)


def run_file(store: str = "research.db", bundle: str = "b",
             mapping: tuple = DEFAULT) -> Rg10RunFile:
    """Return an RG10 run file."""
    return Rg10RunFile("rg10", store, bundle, mapping)


def test_a_copied_bundle_keeps_its_hash(fx_rg10: FxRg10, tmp_path: Path) -> None:
    """The folder's name, place and file times do not count."""
    copy = shutil.copytree(fx_rg10.path, tmp_path / "elsewhere")
    os.utime(copy / "bundle.json", (0, 0))
    assert bundle_content_hash(copy) == bundle_content_hash(fx_rg10.path)
    assert len(bundle_content_hash(copy)) == 64


@pytest.mark.parametrize("change", ["edit", "add", "rename"])
def test_any_file_change_changes_the_hash(fx_rg10: FxRg10, tmp_path: Path,
                                          change: str) -> None:
    """A changed byte, an added file or a renamed file gives another hash."""
    copy = shutil.copytree(fx_rg10.path, tmp_path / "copy")
    output = copy / "outputs" / "g01.md"
    if change == "edit":
        output.write_bytes(output.read_bytes() + b" ")
    elif change == "add":
        (copy / "notes.txt").write_bytes(b"x")
    else:
        output.rename(copy / "outputs" / "g01b.md")
    assert bundle_content_hash(copy) != bundle_content_hash(fx_rg10.path)


def test_symbolic_links_and_missing_folders_are_refused(fx_rg10: FxRg10,
                                                        tmp_path: Path) -> None:
    """A link would make the listing depend on what it points to."""
    copy = shutil.copytree(fx_rg10.path, tmp_path / "copy")
    try:
        (copy / "link.md").symlink_to(copy / "outputs" / "g01.md")
    except OSError:
        pytest.skip("this platform cannot create symbolic links")
    with pytest.raises(BundleInvalid):
        bundle_content_hash(copy)
    with pytest.raises(BundleInvalid):
        bundle_content_hash(tmp_path / "absent")


def test_the_identifier_follows_the_hash_and_mapping_not_the_paths() -> None:
    """Store and bundle path are left out; the content hash and mapping are in."""
    first = run_identifier(run_file(), "p", "0.1.0", "k", "a" * 64)
    assert run_identifier(run_file("other.db", "elsewhere"), "p", "0.1.0", "k",
                          "a" * 64) == first
    assert run_identifier(run_file(), "p", "0.1.0", "k", "b" * 64) != first
    other_mapping = checked_mapping({"T1": ["W0702"], "T2": ["C0209"]})
    assert run_identifier(run_file(mapping=other_mapping), "p", "0.1.0", "k",
                          "a" * 64) != first
    assert canonical_text(run_file()) == (
        '{"kind":"rg10","smell_mapping":{"T1":["W0718","W0702"],"T2":["C0209"]}}')
    with pytest.raises(ValueError):
        run_identifier(run_file(), "p", "0.1.0", "k")
