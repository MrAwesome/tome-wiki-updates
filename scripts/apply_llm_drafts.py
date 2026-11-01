#!/usr/bin/env python3
"""Apply review drafts and deletion markers to talent pages.

Two inputs drive this script:

* renderer JSONL: authoritative status per page. Pages whose source definition is
  gone from the version-correct source tree are replaced with the literal
  ``DELETE`` marker.
* LLM draft JSONL: compact ``renderer-review-2`` records from a reviewer.

Drafts are review material. This script only writes when ``--apply`` is passed
and refuses to touch a page whose current text does not match the renderer
baseline.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import render_candidates

DELETE_MARKER = "DELETE"
CHECKME_PREFIX = "XXX CHECKME XXX "
DELETABLE_MATCH_STATUSES = {"not_found", "removed_or_renamed"}
VALID_STATUSES = {"draft", "unchanged", "unresolved"}


def load_render_rows(path: Path) -> dict[str, dict]:
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        rows[record["title"]] = record
    return rows


def load_drafts(paths: list[Path]) -> dict[str, dict]:
    drafts: dict[str, dict] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            draft = json.loads(line)
            title = draft.get("title")
            status = draft.get("status")
            if not isinstance(title, str) or not title:
                raise SystemExit(f"{path}: draft record without title")
            if status not in VALID_STATUSES:
                raise SystemExit(f"{path}: {title} has invalid status {status!r}")
            if title in drafts:
                raise SystemExit(f"{path}: duplicate draft for {title}")
            if status == "draft" and not draft.get("proposed_desc"):
                raise SystemExit(f"{path}: {title} is draft with no proposed_desc")
            checkme = draft.get("checkme")
            if checkme is not None and not isinstance(checkme, str):
                raise SystemExit(f"{path}: {title} has non-string checkme")
            drafts[title] = draft
    return drafts


def page_path(talents_dir: Path, record: dict) -> Path:
    return talents_dir / record["filename"] / "page"


def replace_description(page: str, description: str) -> str:
    start, end, box = render_candidates.extract_box(page)
    return page[:start] + render_candidates.set_desc(box, description) + page[end:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="renderer output JSONL")
    parser.add_argument("drafts", type=Path, nargs="*", help="draft JSONL files")
    parser.add_argument("--talents-dir", type=Path, default=Path("talents"))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write pages; without this flag the run only reports planned changes",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="optional JSON report of every planned or applied change",
    )
    args = parser.parse_args()

    rows = load_render_rows(args.input)
    drafts = load_drafts(args.drafts)

    unknown = sorted(set(drafts) - set(rows))
    if unknown:
        raise SystemExit("draft titles missing from renderer output: " + ", ".join(unknown))

    actions: list[dict] = []
    counts = {"deleted": 0, "draft": 0, "checkme": 0, "unchanged": 0, "unresolved": 0, "skipped": 0}

    for title, record in rows.items():
        path = page_path(args.talents_dir, record)
        status = record.get("status")

        if status == "unmatched_source" and record.get("source_match_status") in DELETABLE_MATCH_STATUSES:
            current = path.read_text(encoding="utf-8")
            if args.apply:
                path.write_text(DELETE_MARKER + "\n", encoding="utf-8")
            actions.append({"title": title, "action": "deleted", "path": str(path)})
            counts["deleted"] += 1
            continue

        draft = drafts.get(title)
        if draft is None:
            continue
        if status != "unsupported_info":
            raise SystemExit(f"{title} is {status}, not unsupported_info; refusing draft")
        if draft["status"] in {"unchanged", "unresolved"}:
            counts[draft["status"]] += 1
            actions.append({"title": title, "action": draft["status"], "path": str(path)})
            continue

        current = path.read_text(encoding="utf-8")
        description = draft["proposed_desc"].strip()
        if draft.get("checkme"):
            description = CHECKME_PREFIX + description
            counts["checkme"] += 1
        updated = replace_description(current, description)
        if current != record["before_wikitext"] and current != updated:
            raise SystemExit(f"{title}: page text diverged from renderer baseline")
        if args.apply and current != updated:
            path.write_text(updated, encoding="utf-8")
        actions.append(
            {
                "title": title,
                "action": "draft",
                "path": str(path),
                "checkme": bool(draft.get("checkme")),
                "changed": current != updated,
            }
        )
        counts["draft"] += 1

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(actions, indent=2) + "\n", encoding="utf-8")

    mode = "applied" if args.apply else "planned (use --apply to write)"
    print(f"{mode}: " + ", ".join(f"{key}={value}" for key, value in counts.items() if value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
