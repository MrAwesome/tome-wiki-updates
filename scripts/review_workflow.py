"""Shared review.json validation, initialization, merge, and atomic I/O API.

Public API:

* ``review_path(work_dir, filename) -> Path`` resolves to
  ``work_dir/talents/<filename>/review.json``; ``work_dir`` is one batch root.
* ``identity_from_metadata(metadata) -> dict`` extracts canonical talent identity.
* ``initialize_review(metadata, baseline_bytes, ...) -> dict`` creates a valid
  version-1 record. Baseline hash covers exact bytes; metadata supplies revid.
* ``validate_review(record, metadata=None) -> None`` raises ``ReviewRecordError``
  for invalid records, unsupported versions, or metadata identity mismatch.
* ``load_review(path, metadata=None) -> dict`` loads UTF-8 JSON and validates it.
* ``write_review(path, record, metadata=None) -> None`` validates and atomically
  writes deterministic UTF-8 JSON (sorted keys, two-space indent, final newline).
* ``append_history(record, events) -> dict`` returns a copy with events appended.
* ``merge_owned_fields(record, owner, updates, history_events=()) -> dict``
  merges only fields owned by ``human`` or ``renderer`` and appends history.
  Renderer owns ``render`` only; human owns state, status, notes, automation,
  and blockers. All unrelated and unknown fields survive.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "review.schema.json"
STATES = {"update", "keep", "ignore", "defer", "mark_out_of_date"}
WORKFLOW_STATUSES = {
    "untriaged",
    "queued",
    "in_progress",
    "needs_source_review",
    "needs_human_review",
    "blocked",
    "approved",
    "rejected",
    "finalized",
}
RENDER_STATUSES = {"candidate", "no_change", "unsupported_info", "unmatched_source"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
HUMAN_FIELDS = {"state", "workflow_status", "notes", "automation", "blocked_by"}
OWNER_FIELDS = {"human": HUMAN_FIELDS, "renderer": {"render"}}


class ReviewRecordError(ValueError):
    """Review record violates supported version or workflow contract."""


def review_path(work_dir: str | os.PathLike[str], filename: str) -> Path:
    """Return review.json path beneath batch root; reject unsafe talent names."""
    if not isinstance(filename, str) or not filename or filename in {".", ".."}:
        raise ReviewRecordError("filename must be a nonempty talent directory name")
    if Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise ReviewRecordError(f"filename is not a single path component: {filename!r}")
    return Path(work_dir) / "talents" / filename / "review.json"


def identity_from_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Extract record identity from canonical talent metadata.json mapping."""
    if not isinstance(metadata, Mapping):
        raise ReviewRecordError("metadata must be an object")
    try:
        identity = {
            "kind": "talent",
            "filename": metadata["filename"],
            "wiki_title": metadata["wiki_title"],
            "pageid": metadata["pageid"],
        }
    except KeyError as exc:
        raise ReviewRecordError(f"metadata missing identity field {exc.args[0]!r}") from exc
    if not isinstance(identity["filename"], str) or not identity["filename"]:
        raise ReviewRecordError("metadata filename must be nonempty string")
    if not isinstance(identity["wiki_title"], str) or not identity["wiki_title"]:
        raise ReviewRecordError("metadata wiki_title must be nonempty string")
    if type(identity["pageid"]) is not int or identity["pageid"] < 1:
        raise ReviewRecordError("metadata pageid must be positive integer")
    return identity


def initialize_review(
    metadata: Mapping[str, Any],
    baseline_bytes: bytes,
    *,
    state: str = "update",
    workflow_status: str = "in_progress",
    notes: str = "",
    deterministic_mode: str = "run",
    deterministic_notes: str = "",
    llm_mode: str = "allow",
    llm_instructions: Sequence[str] = (),
) -> dict[str, Any]:
    """Build initial record using metadata identity/revid and exact baseline bytes."""
    if not isinstance(baseline_bytes, bytes):
        raise ReviewRecordError("baseline_bytes must be bytes")
    try:
        revid = metadata["revid"]
    except (KeyError, TypeError) as exc:
        raise ReviewRecordError("metadata missing baseline revid") from exc
    record = {
        "schema_version": SCHEMA_VERSION,
        "identity": identity_from_metadata(metadata),
        "baseline": {
            "revid": revid,
            "page_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
        },
        "state": state,
        "workflow_status": workflow_status,
        "notes": notes,
        "automation": {
            "deterministic": {"mode": deterministic_mode, "notes": deterministic_notes},
            "llm": {"mode": llm_mode, "instructions": list(llm_instructions)},
        },
        "blocked_by": [],
        "render": {},
        "work_page": {"last_managed_sha256": None},
        "history": [],
    }
    validate_review(record, metadata)
    return record


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_datetime(value: Any, where: str) -> None:
    if not isinstance(value, str):
        raise ReviewRecordError(f"{where} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewRecordError(f"{where} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ReviewRecordError(f"{where} must include timezone")


def _validate_sha(value: Any, where: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ReviewRecordError(f"{where} must be lowercase SHA-256 hex")


def validate_review(record: Mapping[str, Any], metadata: Mapping[str, Any] | None = None) -> None:
    """Validate known schema-v1 fields and semantics; unknown fields are allowed."""
    if not isinstance(record, Mapping):
        raise ReviewRecordError("review record must be an object")
    if record.get("schema_version") != SCHEMA_VERSION or type(record.get("schema_version")) is not int:
        raise ReviewRecordError(f"unsupported schema_version: {record.get('schema_version')!r}")
    required = {
        "identity", "baseline", "state", "workflow_status", "notes", "automation",
        "blocked_by", "render", "work_page", "history",
    }
    missing = required - record.keys()
    if missing:
        raise ReviewRecordError(f"missing required fields: {', '.join(sorted(missing))}")

    identity = record["identity"]
    if not isinstance(identity, Mapping):
        raise ReviewRecordError("identity must be an object")
    for key in ("kind", "filename", "wiki_title", "pageid"):
        if key not in identity:
            raise ReviewRecordError(f"identity missing {key}")
    if identity["kind"] != "talent" or not _is_nonempty_string(identity["filename"]) or not _is_nonempty_string(identity["wiki_title"]):
        raise ReviewRecordError("identity kind/filename/wiki_title invalid")
    if type(identity["pageid"]) is not int or identity["pageid"] < 1:
        raise ReviewRecordError("identity pageid must be positive integer")
    if metadata is not None:
        expected_identity = identity_from_metadata(metadata)
        if any(identity.get(key) != value for key, value in expected_identity.items()):
            raise ReviewRecordError("review identity does not match metadata.json")

    baseline = record["baseline"]
    if not isinstance(baseline, Mapping) or type(baseline.get("revid")) is not int or baseline["revid"] < 1:
        raise ReviewRecordError("baseline revid must be positive integer")
    _validate_sha(baseline.get("page_sha256"), "baseline.page_sha256")
    if metadata is not None and baseline["revid"] != metadata.get("revid"):
        raise ReviewRecordError("baseline revid does not match metadata.json")

    if not isinstance(record["state"], str) or record["state"] not in STATES:
        raise ReviewRecordError(f"invalid state: {record['state']!r}")
    if not isinstance(record["workflow_status"], str) or record["workflow_status"] not in WORKFLOW_STATUSES:
        raise ReviewRecordError(f"invalid workflow_status: {record['workflow_status']!r}")
    if not isinstance(record["notes"], str):
        raise ReviewRecordError("notes must be a string")
    if record["state"] == "mark_out_of_date" and not _is_nonempty_string(record["notes"]):
        raise ReviewRecordError("state mark_out_of_date requires nonempty notes")

    automation = record["automation"]
    if not isinstance(automation, Mapping):
        raise ReviewRecordError("automation must be an object")
    deterministic = automation.get("deterministic")
    llm = automation.get("llm")
    if not isinstance(deterministic, Mapping) or not isinstance(deterministic.get("mode"), str) or deterministic["mode"] not in {"run", "skip"}:
        raise ReviewRecordError("automation.deterministic.mode must be run or skip")
    if not isinstance(deterministic.get("notes"), str):
        raise ReviewRecordError("automation.deterministic.notes must be a string")
    if deterministic["mode"] == "skip" and not _is_nonempty_string(deterministic["notes"]):
        raise ReviewRecordError("deterministic skip requires nonempty notes")
    if not isinstance(llm, Mapping) or not isinstance(llm.get("mode"), str) or llm["mode"] not in {"allow", "skip"}:
        raise ReviewRecordError("automation.llm.mode must be allow or skip")
    if not isinstance(llm.get("instructions"), list) or any(not isinstance(item, str) for item in llm["instructions"]):
        raise ReviewRecordError("automation.llm.instructions must be an array of strings")

    blockers = record["blocked_by"]
    if not isinstance(blockers, list):
        raise ReviewRecordError("blocked_by must be an array")
    for index, blocker in enumerate(blockers):
        if not isinstance(blocker, Mapping) or not _is_nonempty_string(blocker.get("code")) or not _is_nonempty_string(blocker.get("reason")):
            raise ReviewRecordError(f"blocked_by[{index}] requires nonempty code and reason")
        if "source" in blocker and not isinstance(blocker["source"], str):
            raise ReviewRecordError(f"blocked_by[{index}].source must be a string")
    if record["workflow_status"] == "blocked" and not blockers:
        raise ReviewRecordError("workflow_status blocked requires blocker entries")

    render = record["render"]
    if not isinstance(render, Mapping):
        raise ReviewRecordError("render must be an object")
    if "status" in render or "updated_at" in render:
        if not isinstance(render.get("status"), str) or render["status"] not in RENDER_STATUSES:
            raise ReviewRecordError("render.status is invalid")
        _validate_datetime(render.get("updated_at"), "render.updated_at")
    for key in ("source_sha256", "candidate_page_sha256"):
        if key in render:
            _validate_sha(render[key], f"render.{key}", nullable=True)

    work_page = record["work_page"]
    if not isinstance(work_page, Mapping) or "last_managed_sha256" not in work_page:
        raise ReviewRecordError("work_page.last_managed_sha256 is required")
    _validate_sha(work_page["last_managed_sha256"], "work_page.last_managed_sha256", nullable=True)
    if "last_writer" in work_page and not isinstance(work_page["last_writer"], str):
        raise ReviewRecordError("work_page.last_writer must be a string")

    history = record["history"]
    if not isinstance(history, list):
        raise ReviewRecordError("history must be an array")
    for index, event in enumerate(history):
        if not isinstance(event, Mapping):
            raise ReviewRecordError(f"history[{index}] must be an object")
        for key in ("at", "actor", "event"):
            if key not in event:
                raise ReviewRecordError(f"history[{index}] missing {key}")
        _validate_datetime(event["at"], f"history[{index}].at")
        if not _is_nonempty_string(event["actor"]) or not _is_nonempty_string(event["event"]):
            raise ReviewRecordError(f"history[{index}] actor and event must be nonempty strings")
        if "reason" in event and not isinstance(event["reason"], str):
            raise ReviewRecordError(f"history[{index}].reason must be a string")
        if "commit" in event and event["commit"] is not None and not isinstance(event["commit"], str):
            raise ReviewRecordError(f"history[{index}].commit must be a string or null")


def load_review(path: str | os.PathLike[str], metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Read UTF-8 review JSON and validate version, fields, and optional identity."""
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as stream:
            record = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewRecordError(f"cannot load {path}: {exc}") from exc
    validate_review(record, metadata)
    return record


def write_review(
    path: str | os.PathLike[str],
    record: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
) -> None:
    """Atomically write validated JSON via same-directory temp and os.replace."""
    validate_review(record, metadata)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temp_name = stream.name
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name is not None and os.path.exists(temp_name):
            os.unlink(temp_name)


def append_history(record: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return deep-copied record with supplied history events appended in order."""
    result = copy.deepcopy(dict(record))
    if not isinstance(result.get("history"), list):
        raise ReviewRecordError("history must be an array before appending")
    for event in events:
        if not isinstance(event, Mapping):
            raise ReviewRecordError("history events must be objects")
        result["history"].append(copy.deepcopy(dict(event)))
    validate_review(result)
    return result


def merge_owned_fields(
    record: Mapping[str, Any],
    owner: str,
    updates: Mapping[str, Any],
    history_events: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Merge approved owner fields only, preserving unknown fields and history."""
    if owner not in OWNER_FIELDS:
        raise ReviewRecordError(f"unsupported field owner: {owner!r}")
    if not isinstance(updates, Mapping):
        raise ReviewRecordError("updates must be an object")
    allowed = OWNER_FIELDS[owner]
    invalid = set(updates) - allowed
    if invalid:
        raise ReviewRecordError(f"{owner} cannot update fields: {', '.join(sorted(invalid))}")
    result = copy.deepcopy(dict(record))
    for key, value in updates.items():
        if owner == "renderer" and key == "render":
            if not isinstance(value, Mapping):
                raise ReviewRecordError("renderer render update must be an object")
            current = result.get("render", {})
            if not isinstance(current, Mapping):
                raise ReviewRecordError("existing render field must be an object")
            result["render"] = {**copy.deepcopy(dict(current)), **copy.deepcopy(dict(value))}
        else:
            result[key] = copy.deepcopy(value)
    if history_events:
        result = append_history(result, history_events)
    validate_review(result)
    return result
