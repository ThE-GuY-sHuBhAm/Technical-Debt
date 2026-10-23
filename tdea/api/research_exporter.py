"""ResearchExporter: writes a record-level research export to a folder (FR-62).

The records come from the aggregator (research_export.collect); this module only formats
and writes them, so re-exporting the same runs gives byte-identical record files:

* one file per entity, ``<entity>.csv``, ``.json`` or ``.jsonl``, plus metadata.json and
  data_dictionary.csv; every file is UTF-8 without BOM with LF line endings;
* CSV uses RFC 4180 quoting (a field with a comma, quote or line break is quoted, quotes
  doubled) with LF as the record separator, a header row, empty cells for null and
  ``true``/``false`` for booleans;
* JSON is one array of objects, JSONL one object per line, keys in column order;
* numbers use one fixed, locale-free formatting: integers as digits, floats as Python's
  shortest round-trip repr (a reading of "fixed formatting": it never loses a digit and
  never depends on the machine).

metadata.json holds the run metadata, the exclusions, the key fingerprint, the export
time, the TDEA version and the SHA-256 of every record file; it is the only file that
changes between two exports of the same runs. The output folder must be empty or new,
so an export never mixes with an older one.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from tdea import __version__
from tdea.aggregator.research_export import ExportData, RecordFile, data_dictionary

FORMATS = ("csv", "json", "jsonl")
DICTIONARY_COLUMNS = ("file", "column", "type", "unit", "description", "unvalidated",
                      "exploratory")


class ExportFolderInvalid(Exception):
    """The output folder cannot take an export (invalid input, exit 1)."""

    exit_code = 1

    def __init__(self, folder: Path, cause: str) -> None:
        """Name the folder and the problem."""
        self.error_type = "Export folder unusable"
        self.cause = f"{folder}: {cause}"
        self.action = "Name a new or empty folder with --out."
        super().__init__(f"{self.error_type}: {self.cause} {self.action}")


def _utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(timezone.utc)


class ResearchExporter:
    """Writes ExportData as record files, metadata.json and data_dictionary.csv."""

    def __init__(self, clock: Callable[[], datetime] = _utc_now) -> None:
        """Use ``clock`` for the export time."""
        self._clock = clock

    def write(self, data: ExportData, folder: Path | str, fmt: str = "csv") -> list[Path]:
        """Write the export into ``folder`` and return the files written, metadata last."""
        if fmt not in FORMATS:
            raise ValueError(f"unknown export format {fmt!r}")
        folder = Path(folder).expanduser()
        if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
            raise ExportFolderInvalid(folder, "it exists and is not an empty folder.")
        folder.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        hashes: dict[str, str] = {}
        for record in data.files:
            path = folder / f"{record.entity}.{fmt}"
            hashes[path.name] = _write(path, render(record, fmt))
            written.append(path)
        dictionary = folder / "data_dictionary.csv"
        hashes[dictionary.name] = _write(dictionary, _csv(DICTIONARY_COLUMNS,
                                                          data_dictionary(data.files)))
        written.append(dictionary)
        metadata = folder / "metadata.json"
        _write(metadata, json.dumps({
            "kind": data.kind,
            "format": fmt,
            "key_fingerprint": data.key_fingerprint,
            "tdea_version": __version__,
            "export_time": self._clock().astimezone(timezone.utc).isoformat(),
            "runs": list(data.runs),
            "exclusions": list(data.exclusions),
            "record_counts": {record.entity: len(record.rows) for record in data.files},
            "files": hashes,
        }, ensure_ascii=False, indent=2, sort_keys=False) + "\n")
        written.append(metadata)
        return written


def render(record: RecordFile, fmt: str) -> str:
    """Return one record file's text in ``fmt``."""
    names = [column.name for column in record.columns]
    if fmt == "csv":
        return _csv(names, record.rows)
    objects = [dict(zip(names, row)) for row in record.rows]
    if fmt == "json":
        return json.dumps(objects, ensure_ascii=False, separators=(",", ":")) + "\n"
    return "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
                   for item in objects)


def _csv(header: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    """Return RFC 4180 CSV with LF record separators."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(header)
    writer.writerows([_csv_value(value) for value in row] for row in rows)
    return buffer.getvalue()


def _csv_value(value: object) -> str:
    """Return one CSV cell."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _write(path: Path, text: str) -> str:
    """Write UTF-8 text with LF endings (no BOM) and return its SHA-256."""
    data = text.encode("utf-8")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()
