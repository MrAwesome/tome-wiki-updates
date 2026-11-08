#!/usr/bin/env python3
"""Apply reviewed LLM descriptions to explicitly selected work pages.

Draft application is review-only. Writes require both ``--work-dir`` and
``--apply``; baseline ``talents/`` pages are read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import render_candidates
import review_workflow

CHECKME_PREFIX = "XXX CHECKME XXX "
VALID_STATUSES = {"draft", "unchanged", "unresolved"}
SAFE_WORKFLOW_STATUSES = {"untriaged", "queued", "in_progress", "needs_human_review"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_render_rows(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise SystemExit(f"{path}:{line_number}: renderer record must be an object")
        title = record.get("title")
        if not isinstance(title, str) or not title:
            raise SystemExit(f"{path}:{line_number}: renderer record without title")
        if title in rows:
            raise SystemExit(f"{path}:{line_number}: duplicate renderer record for {title}")
        rows[title] = record
    return rows


def load_drafts(paths: list[Path]) -> dict[str, dict[str, Any]]:
    drafts: dict[str, dict[str, Any]] = {}
    for path in paths:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                draft = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(draft, dict):
                raise SystemExit(f"{path}:{line_number}: draft record must be an object")
            title = draft.get("title")
            status = draft.get("status")
            if not isinstance(title, str) or not title:
                raise SystemExit(f"{path}:{line_number}: draft record without title")
            if status not in VALID_STATUSES:
                raise SystemExit(f"{path}:{line_number}: {title} has invalid status {status!r}")
            if title in drafts:
                raise SystemExit(f"{path}:{line_number}: duplicate draft for {title}")
            if status == "draft" and (
                not isinstance(draft.get("proposed_desc"), str)
                or not draft["proposed_desc"].strip()
            ):
                raise SystemExit(f"{path}:{line_number}: {title} is draft with no proposed_desc")
            checkme = draft.get("checkme")
            if checkme is not None and not isinstance(checkme, str):
                raise SystemExit(f"{path}:{line_number}: {title} has non-string checkme")
            drafts[title] = draft
    return drafts


def replace_description(page: str, description: str) -> str:
    start, end, box = render_candidates.extract_box(page)
    return page[:start] + render_candidates.set_desc(box, description) + page[end:]


def _safe_work_dir(work_dir: Path, baseline_dir: Path) -> None:
    work_talents = (work_dir / "talents").resolve()
    baseline = baseline_dir.resolve()
    if work_talents == baseline or baseline in work_talents.parents:
        raise SystemExit(f"work directory resolves under baseline talents/: {work_talents}")


def _safe_work_target(path: Path, work_dir: Path, baseline_dir: Path) -> None:
    target = path.resolve()
    work_root = work_dir.resolve()
    baseline = baseline_dir.resolve()
    if target != work_root and work_root not in target.parents:
        raise SystemExit(f"work target escapes --work-dir: {path}")
    if target == baseline or baseline in target.parents:
        raise SystemExit(f"work target resolves under baseline talents/: {path}")


def _load_page_plan(
    *,
    title: str,
    row: dict[str, Any],
    draft: dict[str, Any],
    baseline_dir: Path,
    work_dir: Path,
    apply: bool,
) -> dict[str, Any]:
    filename = row.get("filename")
    if not isinstance(filename, str) or not filename:
        raise SystemExit(f"{title}: renderer record has invalid filename")
    record_path = review_workflow.review_path(work_dir, filename)
    baseline_page = baseline_dir / filename / "page"
    metadata_path = baseline_dir / filename / "metadata.json"
    try:
        baseline_bytes = baseline_page.read_bytes()
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{title}: cannot read baseline page/metadata: {exc}") from exc
    if not isinstance(metadata, dict) or metadata.get("filename") != filename or metadata.get("wiki_title") != title:
        raise SystemExit(f"{title}: renderer identity does not match baseline metadata")
    before = row.get("before_wikitext")
    if not isinstance(before, str) or before.encode("utf-8") != baseline_bytes:
        raise SystemExit(f"{title}: renderer baseline differs from baseline page")

    page_path = work_dir / "talents" / filename / "page"
    _safe_work_target(page_path, work_dir, baseline_dir)
    _safe_work_target(record_path, work_dir, baseline_dir)
    if record_path.exists():
        review = review_workflow.load_review(record_path, metadata)
        if review["baseline"]["page_sha256"] != sha256(baseline_bytes):
            raise SystemExit(f"{title}: review baseline hash differs from baseline page")
    else:
        if page_path.exists():
            raise SystemExit(f"{title}: work page exists without review.json; refusing untracked page")
        review = review_workflow.initialize_review(metadata, baseline_bytes)

    if page_path.exists():
        current_bytes = page_path.read_bytes()
        managed_hash = review["work_page"]["last_managed_sha256"]
        if managed_hash is None or sha256(current_bytes) != managed_hash:
            raise SystemExit(f"{title}: work page hash conflict; current page is not last managed page")
    else:
        current_bytes = baseline_bytes
        if review["work_page"]["last_managed_sha256"] is not None:
            raise SystemExit(f"{title}: tracked work page is missing")

    if review["state"] != "update":
        raise SystemExit(f"{title}: state is {review['state']!r}; refusing LLM draft application")
    if review["automation"]["llm"]["mode"] != "allow":
        raise SystemExit(f"{title}: LLM automation is disabled")
    if review["workflow_status"] not in SAFE_WORKFLOW_STATUSES:
        raise SystemExit(
            f"{title}: workflow_status is {review['workflow_status']!r}; refusing LLM draft application"
        )

    try:
        current = current_bytes.decode("utf-8")
        description = draft["proposed_desc"].strip()
        if draft.get("checkme"):
            description = CHECKME_PREFIX + description
        updated = replace_description(current, description)
        updated_bytes = updated.encode("utf-8")
    except (UnicodeDecodeError, ValueError, render_candidates.Unsupported) as exc:
        raise SystemExit(f"{title}: cannot update Ability_box description: {exc}") from exc

    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    updated_review = review_workflow.merge_owned_fields(
        review,
        "human",
        {"workflow_status": "needs_human_review"},
    )
    updated_review["work_page"] = {
        **updated_review["work_page"],
        "last_managed_sha256": sha256(updated_bytes),
        "last_writer": "llm_draft_applier",
    }
    updated_review["llm_draft"] = {
        "applied_at": now,
        "draft_status": draft["status"],
        "checkme": draft.get("checkme"),
        "draft_record_sha256": sha256(
            json.dumps(draft, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ),
        "proposed_desc_sha256": sha256(description.encode("utf-8")),
        "source_sha256": metadata.get("source_sha256"),
    }
    event = {
        "at": now,
        "actor": "llm_draft_applier",
        "event": "llm_draft_applied",
        "reason": "Applied reviewed description draft; human review remains required.",
        "commit": None,
    }
    updated_review = review_workflow.append_history(updated_review, [event])
    review_workflow.validate_review(updated_review, metadata)
    return {
        "title": title,
        "action": "draft",
        "path": str(page_path),
        "record_path": str(record_path),
        "checkme": bool(draft.get("checkme")),
        "changed": current_bytes != updated_bytes,
        "page_bytes": updated_bytes,
        "review": updated_review,
        "metadata": metadata,
        "apply": apply,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="renderer output JSONL")
    parser.add_argument("drafts", type=Path, nargs="*", help="draft JSONL files")
    parser.add_argument("--work-dir", type=Path, required=True, help="explicit batch work root")
    parser.add_argument(
        "--baseline-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "talents",
        help="read-only baseline talent directory (default: repository talents/)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write planned work pages and review records; default only reports planned changes",
    )
    parser.add_argument("--report", type=Path, help="optional JSON report (written only with --apply)")
    args = parser.parse_args()

    if args.report and not args.apply:
        parser.error("--report writes a file and therefore requires --apply")
    _safe_work_dir(args.work_dir, args.baseline_dir)
    if args.report:
        report_path = args.report.resolve()
        baseline = args.baseline_dir.resolve()
        if report_path == baseline or baseline in report_path.parents:
            parser.error("--report must not write under baseline talents/")

    rows = load_render_rows(args.input)
    drafts = load_drafts(args.drafts)
    unknown = sorted(set(drafts) - set(rows))
    if unknown:
        raise SystemExit("draft titles missing from renderer output: " + ", ".join(unknown))

    plans: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    counts = {"draft": 0, "checkme": 0, "unchanged": 0, "unresolved": 0, "unmatched_source": 0}

    for title, row in rows.items():
        status = row.get("status")
        draft = drafts.get(title)
        if status == "unmatched_source":
            if draft is not None:
                raise SystemExit(f"{title} is unmatched_source, not unsupported_info; refusing draft")
            page_path = args.work_dir / "talents" / str(row.get("filename", "")) / "page"
            actions.append(
                {
                    "title": title,
                    "action": "unmatched_source_requires_human_review",
                    "path": str(page_path),
                    "changed": False,
                }
            )
            counts["unmatched_source"] += 1
            continue
        if draft is None:
            continue
        if status != "unsupported_info":
            raise SystemExit(f"{title} is {status}, not unsupported_info; refusing draft")
        if draft["status"] in {"unchanged", "unresolved"}:
            actions.append({"title": title, "action": draft["status"], "changed": False})
            counts[draft["status"]] += 1
            continue
        plan = _load_page_plan(
            title=title,
            row=row,
            draft=draft,
            baseline_dir=args.baseline_dir,
            work_dir=args.work_dir,
            apply=args.apply,
        )
        plans.append(plan)
        actions.append({key: value for key, value in plan.items() if key not in {"page_bytes", "review", "metadata", "apply"}})
        counts["draft"] += 1
        if draft.get("checkme"):
            counts["checkme"] += 1

    if args.apply:
        for plan in plans:
            page_path = Path(plan["path"])
            page_path.parent.mkdir(parents=True, exist_ok=True)
            page_path.write_bytes(plan["page_bytes"])
            review_workflow.write_review(plan["record_path"], plan["review"], plan["metadata"])
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(actions, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    mode = "applied" if args.apply else "planned (use --apply to write)"
    print(f"{mode}: " + ", ".join(f"{key}={value}" for key, value in counts.items() if value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
