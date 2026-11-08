#!/usr/bin/env python3
"""Human-facing review workflow CLI. Never publishes wiki changes."""

from __future__ import annotations

import argparse
import copy
import difflib
import getpass
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import review_workflow as workflow


REPO_ROOT = Path(__file__).resolve().parent.parent
STATES = sorted(workflow.STATES)
STATUSES = sorted(workflow.WORKFLOW_STATUSES)
PROMOTABLE_STATES = {"update", "keep", "mark_out_of_date"}
RENDERER_REFERENCE_RE = re.compile(r"\brender(?:er|ing)?\b|\brender[- ]engine\b", re.IGNORECASE)


class CLIError(ValueError):
    """Expected user-facing CLI failure."""


@dataclass(frozen=True)
class Talent:
    filename: str
    wiki_title: str
    pageid: int
    talent_id: str | None
    canonical_talent_id: str | None
    metadata: dict[str, Any]
    metadata_path: Path

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple(
            value
            for value in (
                self.wiki_title,
                self.filename,
                self.talent_id,
                self.canonical_talent_id,
            )
            if value
        )


def normalize_name(value: str) -> str:
    """Fold case and normalize whitespace/underscores, preserving punctuation."""
    return re.sub(r"[ _]+", " ", value).strip().casefold()


def load_catalog(catalog_root: str | os.PathLike[str] = REPO_ROOT) -> list[Talent]:
    root = Path(catalog_root)
    talents_root = root / "talents"
    entries: list[Talent] = []
    if not talents_root.is_dir():
        raise CLIError(f"talent catalog not found: {talents_root}")
    for metadata_path in sorted(talents_root.glob("*/metadata.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            identity = workflow.identity_from_metadata(metadata)
        except (OSError, UnicodeError, json.JSONDecodeError, workflow.ReviewRecordError) as exc:
            raise CLIError(f"invalid metadata {metadata_path}: {exc}") from exc
        if metadata_path.parent.name != identity["filename"]:
            raise CLIError(f"metadata filename does not match directory: {metadata_path}")
        for key in ("talent_id", "canonical_talent_id"):
            if metadata.get(key) is not None and not isinstance(metadata[key], str):
                raise CLIError(f"metadata {key} must be a string: {metadata_path}")
        entries.append(
            Talent(
                filename=identity["filename"],
                wiki_title=identity["wiki_title"],
                pageid=identity["pageid"],
                talent_id=metadata.get("talent_id"),
                canonical_talent_id=metadata.get("canonical_talent_id"),
                metadata=metadata,
                metadata_path=metadata_path,
            )
        )
    return entries


def _describe(entry: Talent) -> str:
    ids = ", ".join(x for x in (entry.canonical_talent_id, entry.talent_id) if x)
    return f"{entry.wiki_title} [{entry.filename}]" + (f" ({ids})" if ids else "")


def resolve_selector(selector: str, catalog: Iterable[Talent]) -> Talent:
    entries = list(catalog)
    exact = {entry.filename: entry for entry in entries if selector in entry.aliases}
    if len(exact) == 1:
        return next(iter(exact.values()))
    if len(exact) > 1:
        options = "\n  ".join(_describe(entry) for entry in sorted(exact.values(), key=lambda e: e.filename))
        raise CLIError(f"ambiguous selector {selector!r}; use canonical ID or pick one:\n  {options}")
    key = normalize_name(selector)
    matches = [entry for entry in entries if any(normalize_name(a) == key for a in entry.aliases)]
    # Alias collisions within one talent count once; across talents fail closed.
    unique = {entry.filename: entry for entry in matches}
    if len(unique) == 1:
        return next(iter(unique.values()))
    if len(unique) > 1:
        options = "\n  ".join(_describe(entry) for entry in sorted(unique.values(), key=lambda e: e.filename))
        raise CLIError(f"ambiguous selector {selector!r}; use canonical ID or pick one:\n  {options}")
    choices = sorted(
        ((difflib.SequenceMatcher(None, key, normalize_name(alias)).ratio(), entry)
         for entry in entries for alias in entry.aliases),
        key=lambda pair: pair[0], reverse=True,
    )
    suggestions: list[Talent] = []
    for score, entry in choices:
        if score < 0.55:
            break
        if entry not in suggestions:
            suggestions.append(entry)
        if len(suggestions) == 5:
            break
    hint = "" if not suggestions else "\nSuggestions (not selected):\n  " + "\n  ".join(map(_describe, suggestions))
    raise CLIError(f"unknown selector {selector!r}{hint}")


def _work_record(work_dir: Path, entry: Talent, *, create: bool = False) -> tuple[dict[str, Any], Path, bool]:
    path = workflow.review_path(work_dir, entry.filename)
    if path.exists():
        try:
            return workflow.load_review(path, entry.metadata), path, False
        except workflow.ReviewRecordError as exc:
            raise CLIError(str(exc)) from exc
    if not create:
        raise CLIError(f"review record missing: {path}")
    baseline_path = entry.metadata_path.parent / "page"
    try:
        baseline = baseline_path.read_bytes()
        record = workflow.initialize_review(entry.metadata, baseline)
    except (OSError, workflow.ReviewRecordError) as exc:
        raise CLIError(f"cannot initialize {entry.wiki_title}: {exc}") from exc
    return record, path, True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _actor() -> str:
    try:
        return f"human:{getpass.getuser() or 'unknown'}"
    except Exception:
        return "human:unknown"


def _parse_block_reason(value: str) -> dict[str, str]:
    code, separator, reason = value.partition(":")
    if not separator or not code.strip() or not reason.strip():
        raise CLIError("--block-reason must be CODE:REASON")
    return {"code": code.strip(), "reason": reason.strip()}


def _validate_llm_instructions(value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CLIError("LLM instructions must be a JSON array of strings")
    for item in value:
        if not item.strip():
            raise CLIError("LLM instructions must not contain empty strings")
        if RENDERER_REFERENCE_RE.search(item):
            raise CLIError("LLM instructions cannot mention renderer capabilities")
    return value


def _automation_updates(record: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    automation = copy.deepcopy(record["automation"])
    deterministic = automation["deterministic"]
    llm = automation["llm"]
    if args.deterministic_mode is not None:
        deterministic["mode"] = args.deterministic_mode
    if args.deterministic_notes is not None:
        deterministic["notes"] = args.deterministic_notes
    if args.llm_mode is not None:
        llm["mode"] = args.llm_mode
    if args.llm_instruction is not None:
        llm["instructions"] = _validate_llm_instructions(args.llm_instruction)
    elif args.clear_llm_instructions:
        llm["instructions"] = []

    old_automation = record["automation"]
    events: list[dict[str, Any]] = []
    for key, event_name in (
        ("deterministic", "deterministic_automation_changed"),
        ("llm", "llm_automation_changed"),
    ):
        if old_automation[key] != automation[key]:
            events.append({
                "at": _now(), "actor": _actor(), "event": event_name,
                "from": copy.deepcopy(old_automation[key]), "to": copy.deepcopy(automation[key]),
            })
    return ({"automation": automation}, events) if events else ({}, [])


def _updates_for_record(record: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    updates: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    fields: list[tuple[str, Any]] = []
    if args.state is not None:
        fields.append(("state", args.state))
    if args.workflow_status is not None:
        fields.append(("workflow_status", args.workflow_status))
    if args.notes is not None:
        fields.append(("notes", args.notes))
    for field, value in fields:
        if record[field] == value:
            continue
        updates[field] = value
        event = {
            "at": _now(), "actor": _actor(),
            "event": f"{field}_changed", "from": record[field], "to": value,
        }
        if field == "notes" and value:
            event["reason"] = value
        events.append(event)
    if args.block_reason is not None:
        blocker = _parse_block_reason(args.block_reason)
        if record["blocked_by"] != [blocker]:
            updates["blocked_by"] = [blocker]
            events.append({"at": _now(), "actor": _actor(), "event": "blocker_changed", "reason": blocker["reason"]})
    automation_updates, automation_events = _automation_updates(record, args)
    updates.update(automation_updates)
    events.extend(automation_events)
    if not updates:
        return {}, []
    return updates, events


def _apply_human_updates(
    entries: list[Talent],
    work: Path,
    updates_for_record: Callable[[dict[str, Any]], tuple[dict[str, Any], list[dict[str, Any]]]],
    *,
    dry_run: bool,
    yes: bool,
    preview_title: str = "Review update preview",
) -> int:
    plan: list[tuple[Talent, Path, dict[str, Any], dict[str, Any], bool]] = []
    for entry in entries:
        record, path, new = _work_record(work, entry, create=True)
        updates, events = updates_for_record(record)
        if new:
            events.insert(0, {
                "at": _now(), "actor": _actor(), "event": "review_initialized",
                "to": {"state": record["state"], "workflow_status": record["workflow_status"]},
            })
        candidate = workflow.merge_owned_fields(record, "human", updates, events) if (updates or events) else record
        workflow.validate_review(candidate, entry.metadata)
        plan.append((entry, path, record, candidate, new))
    print(f"{preview_title} ({len(plan)} talent(s)):")
    changes = 0
    for entry, path, before, after, new in plan:
        changed = before != after
        changes += int(changed or new)
        print(f"  {_describe(entry)}: {before['state']} / {before['workflow_status']} -> {after['state']} / {after['workflow_status']}")
        if before["notes"] != after["notes"]:
            print(f"    notes: {after['notes']!r}")
        if before["automation"] != after["automation"]:
            print(f"    automation: {json.dumps(after['automation'], ensure_ascii=False, sort_keys=True)}")
        print(f"    {'create' if new else 'update' if changed else 'unchanged'} {path}")
    if dry_run or changes == 0:
        return 0
    if not _confirm(f"Apply {changes} review record change(s)?", yes=yes):
        print("Cancelled.", file=sys.stderr)
        return 1
    for entry, path, original, candidate, new in plan:
        if candidate != original or new:
            workflow.write_review(path, candidate, entry.metadata)
    print(f"Updated {changes} review record(s).")
    return 0


def _confirm(prompt: str, *, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        raise CLIError("noninteractive mutation requires --yes")
    try:
        answer = input(f"{prompt} [y/N] ").strip().casefold()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _read_names_file(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise CLIError(f"cannot read selector file {path}: {exc}") from exc
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def _list(args: argparse.Namespace) -> int:
    root = Path(args.catalog_root)
    work = Path(args.work_dir)
    catalog = load_catalog(root)
    shown = 0
    for entry in catalog:
        path = workflow.review_path(work, entry.filename)
        if path.exists():
            record, _, _ = _work_record(work, entry)
            state, status = record["state"], record["workflow_status"]
        else:
            state, status = "—", "no review"
        if args.state and state != args.state:
            continue
        if args.workflow_status and status != args.workflow_status:
            continue
        if args.names_only:
            print(entry.wiki_title)
        else:
            print(f"{_describe(entry)}\t{state}\t{status}")
        shown += 1
    return 0


def _show(args: argparse.Namespace) -> int:
    catalog = load_catalog(args.catalog_root)
    entry = resolve_selector(args.selector, catalog)
    record, path, _ = _work_record(Path(args.work_dir), entry)
    print(_describe(entry))
    print(f"review: {path}")
    print(f"talent_id: {entry.talent_id or '—'}")
    print(f"canonical_talent_id: {entry.canonical_talent_id or '—'}")
    print(f"pageid: {entry.pageid}")
    print(f"baseline: revid={record['baseline']['revid']} sha256={record['baseline']['page_sha256']}")
    print(f"state: {record['state']}\nworkflow_status: {record['workflow_status']}\nnotes: {record['notes']}")
    print(f"automation: {json.dumps(record['automation'], ensure_ascii=False, sort_keys=True)}")
    print(f"render: {json.dumps(record['render'], ensure_ascii=False, sort_keys=True)}")
    print(f"blocked_by: {json.dumps(record['blocked_by'], ensure_ascii=False)}")
    recent = record["history"][-5:]
    print("recent_history:")
    for event in recent:
        print(f"  {event['at']} {event['actor']} {event['event']}" + (f": {event.get('reason')}" if event.get("reason") else ""))
    return 0


def _set(args: argparse.Namespace, selectors: list[str]) -> int:
    if not any((
        args.state is not None, args.workflow_status is not None, args.notes is not None,
        args.block_reason is not None, args.deterministic_mode is not None,
        args.deterministic_notes is not None, args.llm_mode is not None,
        args.llm_instruction is not None, args.clear_llm_instructions,
    )):
        raise CLIError("set requires at least one review or automation option")
    catalog = load_catalog(args.catalog_root)
    resolved: list[Talent] = []
    seen: set[str] = set()
    for selector in selectors:
        entry = resolve_selector(selector, catalog)
        if entry.filename not in seen:
            resolved.append(entry)
            seen.add(entry.filename)
    if not resolved:
        raise CLIError("no selectors provided")

    return _apply_human_updates(
        resolved, Path(args.work_dir), lambda record: _updates_for_record(record, args),
        dry_run=args.dry_run, yes=args.yes,
    )


def _edit_instruction_file(current: list[str]) -> list[str]:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        raise CLIError("instructions edit requires $VISUAL or $EDITOR")
    try:
        command = shlex.split(editor)
    except ValueError as exc:
        raise CLIError(f"invalid $VISUAL/$EDITOR command: {exc}") from exc
    if not command:
        raise CLIError("$VISUAL/$EDITOR is empty")
    with tempfile.TemporaryDirectory(prefix="review-instructions-") as directory:
        path = Path(directory) / "instructions.json"
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            result = subprocess.run([*command, str(path)], check=False)
        except OSError as exc:
            raise CLIError(f"cannot run editor {command[0]!r}: {exc}") from exc
        if result.returncode:
            raise CLIError(f"editor exited with status {result.returncode}")
        try:
            edited = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CLIError(f"edited instructions must be valid JSON: {exc}") from exc
    return _validate_llm_instructions(edited)


def _instructions_edit(args: argparse.Namespace) -> int:
    entry = resolve_selector(args.selector, load_catalog(args.catalog_root))
    work = Path(args.work_dir)
    record, _, _ = _work_record(work, entry, create=True)
    instructions = _edit_instruction_file(record["automation"]["llm"]["instructions"])

    def updates_for_record(current: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        automation = copy.deepcopy(current["automation"])
        llm = automation["llm"]
        validated = _validate_llm_instructions(instructions)
        if llm["instructions"] == validated:
            return {}, []
        old = copy.deepcopy(llm["instructions"])
        llm["instructions"] = validated
        return {"automation": automation}, [{
            "at": _now(), "actor": _actor(), "event": "llm_automation_changed",
            "from": old, "to": copy.deepcopy(validated),
        }]

    return _apply_human_updates(
        [entry], work, updates_for_record, dry_run=args.dry_run, yes=args.yes,
        preview_title="LLM instruction update preview",
    )


def _pick(args: argparse.Namespace) -> int:
    catalog = load_catalog(args.catalog_root)
    work = Path(args.work_dir)
    candidates: list[tuple[Talent, str, str]] = []
    for entry in catalog:
        path = workflow.review_path(work, entry.filename)
        if path.exists():
            record, _, _ = _work_record(work, entry)
            state, status = record["state"], record["workflow_status"]
        else:
            state, status = "—", "no review"
        if args.state and state != args.state and status != args.state:
            continue
        candidates.append((entry, state, status))
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise CLIError("pick requires an interactive terminal")
    labels = [f"{_describe(entry)}\t{state}\t{status}" for entry, state, status in candidates]
    fzf = shutil.which("fzf")
    if fzf:
        proc = subprocess.run([fzf, "--multi" if args.multi else "--no-multi"], input="\n".join(labels), text=True, stdout=subprocess.PIPE)
        if proc.returncode not in (0, 1):
            raise CLIError(f"fzf failed with status {proc.returncode}")
        selected = proc.stdout.splitlines()
    else:
        print("Select talent(s):")
        for index, label in enumerate(labels, 1):
            print(f"{index}. {label}")
        raw = input("Number" + ("s" if args.multi else "") + " (blank cancels): ").strip()
        if not raw:
            return 1
        tokens = re.split(r"[\s,]+", raw)
        if not args.multi and len(tokens) != 1:
            raise CLIError("select one number; use --multi for multiple")
        try:
            indices = [int(token) - 1 for token in tokens]
            if any(index < 0 or index >= len(labels) for index in indices):
                raise IndexError
            selected = [labels[index] for index in indices]
        except (ValueError, IndexError) as exc:
            raise CLIError("invalid picker selection") from exc
    for label in selected:
        print(label.split("\t", 1)[0])
    return 0


def _validate(args: argparse.Namespace) -> int:
    catalog = load_catalog(args.catalog_root)
    work = Path(args.work_dir)
    entries = (
        [resolve_selector(selector, catalog) for selector in args.selectors]
        if args.selectors
        else [entry for entry in catalog if workflow.review_path(work, entry.filename).exists()]
    )
    failures = 0
    for entry in entries:
        try:
            record, path, _ = _work_record(work, entry)
            workflow.validate_review(record, entry.metadata)
            print(f"OK {path}")
        except CLIError as exc:
            failures += 1
            print(f"INVALID {entry.wiki_title}: {exc}", file=sys.stderr)
    print(f"Validated {len(entries) - failures}/{len(entries)} review record(s).")
    return 1 if failures else 0


def _git_clean(target: Path) -> None:
    try:
        subprocess.run(["git", "-C", str(target), "rev-parse", "--show-toplevel"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        result = subprocess.run(["git", "-C", str(target), "status", "--porcelain", "--untracked-files=all"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CLIError(f"target must be a Git checkout: {target}") from exc
    if result.stdout.strip():
        raise CLIError(f"target checkout is not clean:\n{result.stdout.rstrip()}")


def _atomic_copy_bytes(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False) as stream:
            temp_name = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, destination)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def promote_plan(work_dir: Path, target_root: Path, catalog_root: Path, selectors: list[str] | None = None) -> list[tuple[Talent, Path, bytes]]:
    catalog = load_catalog(catalog_root)
    selected = [resolve_selector(item, catalog) for item in selectors] if selectors else None
    selected_filenames = {entry.filename for entry in selected} if selected is not None else None
    plan: list[tuple[Talent, Path, bytes]] = []
    for entry in catalog:
        if selected_filenames is not None and entry.filename not in selected_filenames:
            continue
        review_file = workflow.review_path(work_dir, entry.filename)
        if not review_file.exists():
            if selected_filenames is not None:
                raise CLIError(f"review record missing: {review_file}")
            continue
        record, _, _ = _work_record(work_dir, entry)
        if record["workflow_status"] != "approved":
            if selected_filenames is not None:
                raise CLIError(f"{entry.wiki_title} is not approved (status {record['workflow_status']})")
            continue
        if record["state"] not in PROMOTABLE_STATES:
            raise CLIError(f"{entry.wiki_title} has non-promotable state {record['state']!r}")
        if record["state"] == "mark_out_of_date" and not record["notes"].strip():
            raise CLIError(f"{entry.wiki_title}: mark_out_of_date requires notes")
        baseline_path = target_root / "talents" / entry.filename / "page"
        work_page = workflow.review_path(work_dir, entry.filename).parent / "page"
        try:
            baseline_bytes = baseline_path.read_bytes()
            page_bytes = work_page.read_bytes()
        except OSError as exc:
            raise CLIError(f"missing target baseline or work page for {entry.wiki_title}: {exc}") from exc
        actual_hash = hashlib.sha256(baseline_bytes).hexdigest()
        if actual_hash != record["baseline"]["page_sha256"]:
            raise CLIError(f"target baseline hash mismatch for {entry.wiki_title}: {baseline_path}")
        target_meta_path = target_root / "talents" / entry.filename / "metadata.json"
        try:
            target_meta = json.loads(target_meta_path.read_text(encoding="utf-8"))
            target_identity = workflow.identity_from_metadata(target_meta)
        except (OSError, UnicodeError, json.JSONDecodeError, workflow.ReviewRecordError) as exc:
            raise CLIError(f"invalid target metadata {target_meta_path}: {exc}") from exc
        if target_identity != workflow.identity_from_metadata(entry.metadata):
            raise CLIError(f"target identity mismatch for {entry.wiki_title}: {target_meta_path}")
        if target_meta.get("revid") != record["baseline"]["revid"]:
            raise CLIError(f"target baseline revision mismatch for {entry.wiki_title}: {target_meta_path}")
        plan.append((entry, baseline_path, page_bytes))
    if selected_filenames is not None and not plan:
        raise CLIError("no approved pages selected for promotion")
    return plan


def _promote(args: argparse.Namespace) -> int:
    target = Path(args.target_root).resolve()
    if not target.is_dir():
        raise CLIError(f"target checkout not found: {target}")
    _git_clean(target)
    plan = promote_plan(Path(args.work_dir), target, Path(args.catalog_root), args.selectors or None)
    print(f"Promotion preview ({len(plan)} page file(s); no publication):")
    for _, destination, _ in plan:
        print(f"  {destination}")
    if not args.apply or args.dry_run:
        if args.apply and args.dry_run:
            raise CLIError("--apply and --dry-run cannot be combined")
        return 0
    # --apply is explicit confirmation for this page-only export operation.
    # Recheck cleanliness immediately before first write.
    _git_clean(target)
    for _, destination, data in plan:
        _atomic_copy_bytes(destination, data)
    print(f"Copied {len(plan)} approved page file(s). Review with git diff; nothing published.")
    return 0


def _completion(args: argparse.Namespace) -> int:
    script = shlex.quote(str(Path(sys.argv[0]).resolve()))
    if args.shell == "bash":
        print(f"_tome_review_complete() {{\n  local cur=\"${{COMP_WORDS[COMP_CWORD]}}\"\n  mapfile -t COMPREPLY < <(python3 {script} _complete \"$cur\")\n  compopt -o filenames\n}}\ncomplete -F _tome_review_complete review.py")
    elif args.shell == "zsh":
        print("# Source this file to enable review.py completion\n_review.py() {\n  local -a words\n  words=(\"${(@f)$(python3 " + script + " _complete \"$PREFIX\")}\")\n  compadd -f -a words\n}\ncompdef _review.py review.py")
    else:
        print(f"complete -c review.py -f -a '(python3 {script} _complete (commandline -ct))'")
    return 0


def _internal_complete(args: argparse.Namespace) -> int:
    catalog = load_catalog(args.catalog_root)
    values = [*STATES, *STATUSES, *(alias for entry in catalog for alias in entry.aliases)]
    prefix = args.prefix.casefold()
    for value in sorted(set(values), key=str.casefold):
        if value.casefold().startswith(prefix):
            print(value)
    return 0


def _add_work_dir(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--work-dir", required=True, help="work batch root")
    parser.add_argument("--catalog-root", default=str(REPO_ROOT), help=argparse.SUPPRESS)


def _add_set_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state", choices=STATES)
    parser.add_argument("--workflow-status", choices=STATUSES)
    parser.add_argument("--notes")
    parser.add_argument("--block-reason", metavar="CODE:REASON")
    parser.add_argument("--deterministic-mode", choices=("run", "skip"))
    parser.add_argument("--deterministic-notes", help="reason for deterministic skip, or other renderer note")
    parser.add_argument("--llm-mode", choices=("allow", "skip"))
    instructions = parser.add_mutually_exclusive_group()
    instructions.add_argument(
        "--llm-instruction", action="append",
        help="replace LLM instruction list; repeat once per structured instruction string",
    )
    instructions.add_argument("--clear-llm-instructions", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review talent page candidates; never publish.")
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="list catalog talents and review status")
    _add_work_dir(listing)
    listing.add_argument("--state", choices=STATES)
    listing.add_argument("--workflow-status", choices=STATUSES)
    listing.add_argument("--names-only", action="store_true", help=argparse.SUPPRESS)
    listing.set_defaults(handler=_list)

    show = sub.add_parser("show", help="show review record and provenance")
    _add_work_dir(show)
    show.add_argument("selector")
    show.set_defaults(handler=_show)

    setter = sub.add_parser("set", help="update human-owned review fields")
    _add_work_dir(setter)
    _add_set_options(setter)
    setter.add_argument("selectors", nargs="*")
    setter.set_defaults(handler=lambda args: _set(args, args.selectors))

    batch = sub.add_parser("batch", help="batch review operations")
    batch_sub = batch.add_subparsers(dest="batch_command", required=True)
    batch_set = batch_sub.add_parser("set", help="set fields for newline-delimited selectors")
    _add_work_dir(batch_set)
    _add_set_options(batch_set)
    batch_set.add_argument("--from", dest="names_file", required=True)
    batch_set.set_defaults(handler=lambda args: _set(args, _read_names_file(Path(args.names_file))))

    instructions = sub.add_parser("instructions", help="edit page-scoped LLM instructions")
    instructions_sub = instructions.add_subparsers(dest="instructions_command", required=True)
    instructions_edit = instructions_sub.add_parser(
        "edit", help="edit JSON array of instruction strings using $VISUAL or $EDITOR",
    )
    _add_work_dir(instructions_edit)
    instructions_edit.add_argument("--dry-run", action="store_true")
    instructions_edit.add_argument("--yes", action="store_true")
    instructions_edit.add_argument("selector")
    instructions_edit.set_defaults(handler=_instructions_edit)

    picker = sub.add_parser("pick", help="select talents with fzf or numbered fallback")
    _add_work_dir(picker)
    picker.add_argument("--state", help="filter by state or workflow status")
    picker.add_argument("--multi", action="store_true")
    picker.set_defaults(handler=_pick)

    validate = sub.add_parser("validate", help="validate selected or all review records")
    _add_work_dir(validate)
    validate.add_argument("selectors", nargs="*")
    validate.set_defaults(handler=_validate)

    promote = sub.add_parser("promote", help="preview/copy approved pages to clean checkout")
    _add_work_dir(promote)
    promote.add_argument("--target-root", required=True)
    promote.add_argument("--apply", action="store_true", help="copy page files; no publication")
    promote.add_argument("--dry-run", action="store_true")
    promote.add_argument("selectors", nargs="*")
    promote.set_defaults(handler=_promote)

    completion = sub.add_parser("completion", help="print shell completion")
    completion.add_argument("shell", choices=("bash", "zsh", "fish"))
    completion.add_argument("--catalog-root", default=str(REPO_ROOT), help=argparse.SUPPRESS)
    completion.set_defaults(handler=_completion)

    internal = sub.add_parser("_complete", help="internal shell completion provider")
    internal.add_argument("--catalog-root", default=str(REPO_ROOT), help=argparse.SUPPRESS)
    internal.add_argument("prefix", nargs="?", default="")
    internal.set_defaults(handler=_internal_complete)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (CLIError, workflow.ReviewRecordError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
