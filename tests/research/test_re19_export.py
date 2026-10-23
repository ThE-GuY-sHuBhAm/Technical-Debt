"""RE-19, RG1 part, through ``tdea research export`` (FR-62, NFR-27, RM-07; sprint 23 Oct).

BLOCKED as an acceptance check: RE-19 exports a run of FX-RG1, and every real run is
refused while CL-03 is incomplete. The runs exported here use the SYNTHETIC — not CL-03
doubles of run_support over FX-RG1 v2 and FX-PAIRS: mechanics evidence only. The RG10
part of RE-19 needs RG10 runs (sprint task S-04).

Covered: equal record counts in CSV, JSON and JSONL; metadata.json and data_dictionary.csv
present with the SHA-256 of every record file; the FR-62 RG1 fields (c1 SHA, paths and
blobs before and after c1, the line before c1, hunks); no raw identity anywhere and no
name-key column (CL-14, provisional reading); unvalidated and exploratory flags; a
message with commas, quotes and a newline in one CSV cell; mixed key fingerprints refused
with exit 3; a second export byte-identical except metadata.json.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from pathlib import Path

import make_fx_rg1
import pytest
from run_setup import Built
from run_support import four_units, rows

from tdea.aggregator.research_export import ENTITIES, ExportData, RecordFile, describe
from tdea.api.cli import EXIT_INVALID_INPUT, EXIT_OK, EXIT_REFUSED, main
from tdea.api.research_exporter import ExportFolderInvalid, ResearchExporter, render

IDENTITIES = (make_fx_rg1.ASHA, make_fx_rg1.RAVI, make_fx_rg1.JANE, make_fx_rg1.AI_COMMITTER,
              make_fx_rg1.AI_AUTHOR, make_fx_rg1.DEPENDENCY_BOT)
RAW_VALUES = [part for identity in IDENTITIES
              for part in (identity.name, identity.email, identity.email.split("@")[0])]


@pytest.fixture(name="exported_run", scope="module")
def _exported_run(built: Built, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    """Run the RE-01 shape once and return the store and the run identifier."""
    workspace = built.workspace(tmp_path_factory.mktemp("re19"))
    assert main(["research", "run", str(workspace.run_file(four_units()))],
                service=workspace.service()) == EXIT_OK
    (run_id,), = rows(workspace.store, "SELECT run_id FROM run")
    return workspace.store, run_id


def export(store: Path, out: Path, *run_ids: str, fmt: str = "csv") -> int:
    """Run the export command."""
    return main(["research", "export", "--store", str(store), "--out", str(out),
                 "--format", fmt, *run_ids])


def read_csv(path: Path) -> list[dict[str, str]]:
    """Read one exported CSV file."""
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_formats_hold_equal_counts_and_metadata(exported_run: tuple[Path, str],
                                                tmp_path: Path) -> None:
    """Step 1-2: CSV, JSON and JSONL hold the same records; metadata hashes every file."""
    store, run_id = exported_run
    counts = {}
    for fmt in ("csv", "json", "jsonl"):
        out = tmp_path / fmt
        assert export(store, out, run_id, fmt=fmt) == EXIT_OK
        metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
        assert metadata["kind"] == "rg1" and metadata["format"] == fmt
        assert sorted(metadata["files"]) == sorted(
            [f"{entity}.{fmt}" for entity in ENTITIES] + ["data_dictionary.csv"])
        for name, digest in metadata["files"].items():
            assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest
        if fmt == "csv":
            found = {entity: len(read_csv(out / f"{entity}.csv")) for entity in ENTITIES}
        elif fmt == "json":
            found = {entity: len(json.loads((out / f"{entity}.json").read_text("utf-8")))
                     for entity in ENTITIES}
        else:
            found = {entity: len((out / f"{entity}.jsonl").read_text("utf-8").splitlines())
                     for entity in ENTITIES}
        assert found == metadata["record_counts"]
        counts[fmt] = found
    assert counts["csv"] == counts["json"] == counts["jsonl"]
    assert counts["csv"]["debt_item"] > 0 and counts["csv"]["commit"] > 0
    assert counts["csv"]["output"] == 0  # an RG1 export has empty RG10 files


def test_rg1_fields_flags_and_no_volatile_fields(exported_run: tuple[Path, str],
                                                 tmp_path: Path) -> None:
    """FR-62 RG1 fields, unvalidated and exploratory flags; volatile fields in metadata."""
    store, run_id = exported_run
    assert export(store, tmp_path / "out", run_id) == EXIT_OK
    out = tmp_path / "out"
    items = read_csv(out / "debt_item.csv")
    for column in ("c1_sha", "introducing_sha", "path_before_c1", "blob_before_c1",
                   "path_after_c1", "blob_after_c1", "line_before_c1"):
        assert column in items[0]
    assert any(item["c1_sha"] for item in items)
    assert "hunks" in read_csv(out / "file_change.csv")[0]
    modes = read_csv(out / "item_mode.csv")
    assert modes and all(mode["unvalidated"] == "true" for mode in modes)
    assert all(row["unvalidated"] == "true" for row in read_csv(out / "unit_summary.csv"))
    dictionary = {(row["file"], row["column"]): row
                  for row in read_csv(out / "data_dictionary.csv")}
    assert dictionary[("item_mode", "mode")]["unvalidated"] == "true"
    assert dictionary[("unit_summary", "local_fix_share")]["unvalidated"] == "true"
    assert dictionary[("covariate", "account_tenure_days")]["exploratory"] == "true"
    assert all(row["description"] for row in dictionary.values())
    assert "started_at" not in read_csv(out / "run.csv")[0]
    assert "clone_time" not in read_csv(out / "repository.csv")[0]
    assert "completed_at" not in read_csv(out / "unit.csv")[0]
    metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    (run,) = metadata["runs"]
    assert run["started_at"] and run["repositories"][0]["clone_time"]
    assert run["units"][0]["completed_at"]


def test_no_raw_identity_and_no_name_keys(exported_run: tuple[Path, str],
                                          tmp_path: Path) -> None:
    """Step 3: no raw name or e-mail in any file; name keys are not exported (CL-14)."""
    store, run_id = exported_run
    for fmt in ("csv", "json", "jsonl"):
        out = tmp_path / fmt
        assert export(store, out, run_id, fmt=fmt) == EXIT_OK
        for path in out.iterdir():
            text = path.read_text(encoding="utf-8")
            for raw in RAW_VALUES:
                assert raw not in text, (raw, path.name)
    header = read_csv(tmp_path / "csv" / "commit.csv")[0]
    assert "author_email_key" in header and "committer_email_key" in header
    assert "author_name_key" not in header and "committer_name_key" not in header


def test_message_with_commas_quotes_and_newline_stays_in_one_cell() -> None:
    """Step 4 (csv module in place of pandas): RFC 4180 quoting keeps the cell whole."""
    message = 'Fix "parser", again,\nand more'
    record = RecordFile("commit", (describe("commit", "sha", str),
                                   describe("commit", "message", str)), [("abc", message)])
    text = render(record, "csv")
    assert "\r" not in text
    parsed = list(csv.reader(io.StringIO(text, newline="")))
    assert parsed == [["sha", "message"], ["abc", message]]


def test_reexport_is_byte_identical(exported_run: tuple[Path, str], tmp_path: Path) -> None:
    """Step 6: a second export's record files are byte-identical; metadata alone differs."""
    store, run_id = exported_run
    assert export(store, tmp_path / "a", run_id) == EXIT_OK
    assert export(store, tmp_path / "b", run_id) == EXIT_OK
    for path in sorted((tmp_path / "a").iterdir()):
        if path.name != "metadata.json":
            assert path.read_bytes() == (tmp_path / "b" / path.name).read_bytes(), path.name
            assert not path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_mixed_key_fingerprints_are_refused(built: Built, exported_run: tuple[Path, str],
                                            tmp_path: Path,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    """Step 5: a second run of the same run file with another key, exported together: exit 3."""
    store, run_id = exported_run
    workspace = built.workspace(tmp_path / "other-key")
    store_copy = workspace.store
    store_copy.write_bytes(store.read_bytes())
    assert main(["research", "run", str(workspace.run_file(four_units()))],
                service=workspace.service()) == EXIT_OK
    run_ids = [row[0] for row in rows(store_copy, "SELECT run_id FROM run ORDER BY run_id")]
    assert len(run_ids) == 2 and run_id in run_ids
    assert export(store_copy, tmp_path / "mixed", *run_ids) == EXIT_REFUSED
    assert "key fingerprints" in capsys.readouterr().err
    assert not (tmp_path / "mixed").exists()


def test_unknown_run_store_and_used_folder(exported_run: tuple[Path, str], tmp_path: Path,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    """Invalid input exits 1: an unknown run, a missing store, a folder already in use."""
    store, run_id = exported_run
    assert export(store, tmp_path / "x", "000000000000") == EXIT_INVALID_INPUT
    assert export(tmp_path / "missing.db", tmp_path / "y", run_id) == EXIT_INVALID_INPUT
    used = tmp_path / "used"
    used.mkdir()
    (used / "keep.txt").write_text("x", encoding="utf-8")
    assert export(store, used, run_id) == EXIT_INVALID_INPUT
    assert "Export folder unusable" in capsys.readouterr().err


def test_unfinished_run_is_refused(exported_run: tuple[Path, str], tmp_path: Path) -> None:
    """A run whose status is not complete is refused with exit 3 (a reading)."""
    store, run_id = exported_run
    copy = tmp_path / "research.db"
    copy.write_bytes(store.read_bytes())
    with sqlite3.connect(copy) as connection:
        connection.execute("UPDATE run SET status = 'running'")
    assert export(copy, tmp_path / "out", run_id) == EXIT_REFUSED


def test_exporter_refuses_unknown_format_and_file_target(tmp_path: Path) -> None:
    """The exporter writes only the three formats, and only into a folder."""
    data = ExportData("rg1", "0" * 16, (), (), ())
    with pytest.raises(ValueError):
        ResearchExporter().write(data, tmp_path / "out", "xml")
    target = tmp_path / "file"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ExportFolderInvalid):
        ResearchExporter().write(data, target)
