"""The canonical run file and the run identifier (FR-48, FR-52, NFR-24).

Run identifier (Freeze FR-48 and glossary): the first 12 hex characters of SHA-256 over
the canonical run file without store and key_file, the profile hash, the TDEA version
and the key fingerprint; an RG10 run adds the bundle content hash under the key
"bundle_content_hash" (sprint 23 Oct), and its canonical run file is {"kind",
"smell_mapping"}, the mapping as sorted type -> rule list. The same inputs resume the
same run. SRS v2.3 also leaves out lint_cache and bundle; both are refused or absent in
an RG1 run file today, so the two texts give the same identifier.

How the parts are combined (Task 7 Day 6 decision 4): SHA-256 over the canonical JSON of
the object {"canonical_run_file", "key_fingerprint", "profile_hash", "tdea_version"}.
Canonical JSON is UTF-8 with sorted keys and no spaces, as research.db's list columns use.

The canonical run file is the run file as validate() returns it, written as data. The
identifier adds no equivalence of its own; two run files share it only when validation
gives them equal values. Exactly:

* Object: {"kind", "seeds", "w_months", "tau", "cutoff", "repositories"}; each repository
  {"url", "head", "windows"}; each window {"start", "end", "commits"}. store and key_file
  are left out (FR-48). Keys are sorted; lists keep the run file's order.
* Values: url exactly as written; head and a window's SHAs as written (validation admits
  only 40-character lowercase hex) or null; commits a list or null; seeds a mapping of
  names to integers; w_months an integer; tau a float (Python's shortest repr: 0.7);
  cutoff, start and end UTC ISO 8601 ending in Z, with microseconds only when not zero,
  or null for a cut-off left out.

Equal values from different spellings, and their authority:

* YAML layout, comments, quoting and the order of keys in a mapping: the run file is
  "canonical" (FR-48) and YAML mappings are unordered; JSON with sorted keys is Task 7
  Day 6 decision 4.
* cutoff or head left out, or written as null: the schema writes them as
  ``cutoff: null # optional UTC timestamp; default = clone time`` and
  ``head: null # optional SHA``.
* Open questions, inherited from validation (Task 7 Day 5 readings, not settled by an
  authoritative text): a window's ``commits: null`` read as "no list"; a date read as
  00:00 UTC, a timestamp without an offset read as UTC, and an offset converted, so
  ``2025-01-01``, ``2025-01-01T00:00:00Z`` and ``2025-01-01T05:30:00+05:30`` give one value.
* Not equal (Day 6 decision 4: no merging without authority): another order of
  repositories, windows or SHAs, and another URL spelling (http or https, letter case, a
  trailing slash). Such run files get different identifiers; the cost is a fresh run
  where a resume might have been possible.
* tau and seeds are never omitted, because validate refuses a missing or null value
  (Task 7 Day 5); the question of an omitted key and its default does not arise.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from tdea.aggregator.research_models import canonical_json
from tdea.aggregator.run_file import Rg10RunFile, RunFile

IDENTIFIER_LENGTH = 12


def instant_text(moment: datetime | None) -> str | None:
    """Return a UTC instant as ISO 8601 text ending in Z, or None."""
    if moment is None:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("run-file instants are timezone-aware")
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_run_file(run_file: RunFile | Rg10RunFile) -> dict[str, object]:
    """Return the run file without store and key_file (and bundle), as canonical data."""
    if isinstance(run_file, Rg10RunFile):
        return {"kind": run_file.kind,
                "smell_mapping": {smell: list(rules) for smell, rules in run_file.smell_mapping}}
    return {
        "kind": run_file.kind,
        "seeds": dict(run_file.seeds),
        "w_months": run_file.w_months,
        "tau": run_file.tau,
        "cutoff": instant_text(run_file.cutoff),
        "repositories": [
            {
                "url": repository.url,
                "head": repository.head,
                "windows": [
                    {
                        "start": instant_text(window.start),
                        "end": instant_text(window.end),
                        "commits": list(window.commits) if window.commits is not None
                        else None,
                    }
                    for window in repository.windows
                ],
            }
            for repository in run_file.repositories
        ],
    }


def canonical_text(run_file: RunFile | Rg10RunFile) -> str:
    """Return the canonical run file as the text research.db records (FR-52)."""
    return canonical_json(canonical_run_file(run_file))


def run_identifier(run_file: RunFile | Rg10RunFile, profile_hash: str, tdea_version: str,
                   key_fingerprint: str, bundle_content_hash: str | None = None) -> str:
    """Return the 12-hex run identifier of ``run_file`` under these parameters (FR-48)."""
    for name, value in (("profile_hash", profile_hash), ("tdea_version", tdea_version),
                        ("key_fingerprint", key_fingerprint)):
        if not isinstance(value, str) or not value:
            raise ValueError(f"the run identifier needs a non-empty {name}")
    parts = {
        "canonical_run_file": canonical_run_file(run_file),
        "key_fingerprint": key_fingerprint,
        "profile_hash": profile_hash,
        "tdea_version": tdea_version,
    }
    if isinstance(run_file, Rg10RunFile):
        if not bundle_content_hash:
            raise ValueError("an RG10 run identifier needs the bundle content hash")
        parts["bundle_content_hash"] = bundle_content_hash
    payload = canonical_json(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:IDENTIFIER_LENGTH]
