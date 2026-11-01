#!/usr/bin/env python3
"""Build compact, source-verified LLM packets for unsupported renderer cases.

This script reads renderer JSONL and version-correct game source. It never edits
wiki pages or source files. Core code comes from tome-1.7.6; addon code comes
from the local addon trees.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


CORE_TAG = "tome-1.7.6"
CORE_COMMIT = "624a67329fe2ad440c5b344785a9c73fcf22ae63"
DEFAULT_TENGINE = Path.home() / "code" / "t-engine4"


def version_string(value: str) -> str | None:
    match = re.search(r"\{([0-9]+(?:\s*,\s*[0-9]+){1,2})\}", value)
    return ".".join(part.strip() for part in match.group(1).split(",")) if match else None


def read_source(root: Path, record: dict) -> tuple[str, dict]:
    relative = record["source_file"]
    if relative.startswith("game/addons/"):
        source_path = (root / relative).resolve()
        if root.resolve() not in source_path.parents:
            raise ValueError(f"source path escapes t-engine root: {relative}")
        raw = source_path.read_bytes()
        parts = Path(relative).parts
        addon_id = parts[2]
        addon_roots = (
            root / "game" / "addons" / addon_id / addon_id,
            root / "game" / "addons" / addon_id,
        )
        addon_root = next((candidate for candidate in addon_roots if (candidate / "init.lua").is_file()), None)
        if addon_root is None:
            raise FileNotFoundError(f"addon init.lua not found for {addon_id}")
        init_text = (addon_root / "init.lua").read_text(encoding="utf-8")
        compatibility = re.search(r"(?m)^\s*version\s*=\s*(\{[^}]+\})", init_text)
        addon_version = re.search(r"(?m)^\s*addon_version\s*=\s*(\{[^}]+\})", init_text)
        provenance = {
            "kind": "local_addon_tree",
            "addon_id": addon_id,
            "compatibility_game_version": version_string(compatibility.group(1)) if compatibility else None,
            "addon_version": version_string(addon_version.group(1)) if addon_version else None,
        }
    else:
        raw = subprocess.check_output(
            ["git", "-C", str(root), "show", f"{CORE_TAG}:{relative}"]
        )
        provenance = {
            "kind": "tagged_core_source",
            "tag": CORE_TAG,
            "commit": CORE_COMMIT,
        }

    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash != record["source_sha256"]:
        raise ValueError(
            f"source hash mismatch for {record['title']}: "
            f"expected {record['source_sha256']}, got {actual_hash}"
        )
    lines = raw.decode("utf-8").splitlines()
    start, end = record["source_lines"]
    excerpt = "\n".join(f"{number}: {lines[number - 1]}" for number in range(start, end + 1))
    return excerpt, provenance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="renderer output JSONL")
    parser.add_argument("--t-engine-root", type=Path, default=DEFAULT_TENGINE)
    parser.add_argument("--title", action="append", default=[], help="select title; repeatable")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("renderer_reviews/unsupported-review-packet.jsonl"),
    )
    args = parser.parse_args()

    wanted = set(args.title)
    selected = []
    for line_number, line in enumerate(args.input.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("status") != "unsupported_info":
            continue
        if wanted and record.get("title") not in wanted:
            continue
        selected.append(record)
    if wanted:
        found = {record["title"] for record in selected}
        missing = wanted - found
        if missing:
            parser.error("requested titles are not unsupported in input: " + ", ".join(sorted(missing)))
    if not selected:
        parser.error("input contains no selected unsupported_info records")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as stream:
        for record in selected:
            source_excerpt, provenance = read_source(args.t_engine_root, record)
            packet = {
                "packet_schema": "renderer-review-1",
                "title": record["title"],
                "renderer_error": record["error"],
                "source": {
                    "provenance": provenance,
                    "path": record["source_file"],
                    "lines": record["source_lines"],
                    "sha256": record["source_sha256"],
                    "talent_id": record.get("talent_id"),
                    "excerpt": source_excerpt,
                },
                "current_wiki_wikitext": record["before_wikitext"],
            }
            stream.write(json.dumps(packet, ensure_ascii=False) + "\n")
    print(f"Wrote {len(selected)} source-verified review records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
