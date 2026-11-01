#!/usr/bin/env python3
"""Split unsupported renderer records into source-verified LLM review batches.

Each batch groups records by source file so one reviewer can trace a file once.
The packet keeps the full current wiki page for context, plus the exact
version-correct talent excerpt. Output is review material only; nothing here
edits a wiki page.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from build_renderer_review_packet import DEFAULT_TENGINE, read_source


def build_packets(records: list[dict], root: Path) -> list[dict]:
    packets = []
    for record in records:
        excerpt, provenance = read_source(root, record)
        packets.append(
            {
                "packet_schema": "renderer-review-1",
                "title": record["title"],
                "renderer_error": record["error"],
                "source": {
                    "provenance": provenance,
                    "path": record["source_file"],
                    "lines": record["source_lines"],
                    "sha256": record["source_sha256"],
                    "talent_id": record.get("talent_id"),
                    "excerpt": excerpt,
                },
                "current_wiki_wikitext": record["before_wikitext"],
            }
        )
    return packets


def batch_packets(
    packets: list[dict], max_records: int, max_excerpt_lines: int
) -> list[list[dict]]:
    groups = collections.defaultdict(list)
    for packet in packets:
        groups[packet["source"]["path"]].append(packet)

    batches: list[list[dict]] = []
    current: list[dict] = []
    lines = 0
    for source_file in sorted(groups):
        group = groups[source_file]
        group_lines = sum(
            packet["source"]["lines"][1] - packet["source"]["lines"][0] + 1
            for packet in group
        )
        if current and (
            len(current) + len(group) > max_records or lines + group_lines > max_excerpt_lines
        ):
            batches.append(current)
            current = []
            lines = 0
        current.extend(group)
        lines += group_lines
    if current:
        batches.append(current)
    return batches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="renderer output JSONL")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--t-engine-root", type=Path, default=DEFAULT_TENGINE)
    parser.add_argument("--max-records", type=int, default=20)
    parser.add_argument("--max-excerpt-lines", type=int, default=1200)
    args = parser.parse_args()

    records = [
        record
        for line in args.input.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for record in [json.loads(line)]
        if record.get("status") == "unsupported_info"
    ]
    if not records:
        parser.error("input contains no unsupported_info records")

    packets = build_packets(records, args.t_engine_root)
    batches = batch_packets(packets, args.max_records, args.max_excerpt_lines)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    index = []
    for number, batch in enumerate(batches, 1):
        name = f"batch-{number:02}.jsonl"
        target = args.output_dir / name
        target.write_text(
            "\n".join(json.dumps(packet, ensure_ascii=False) for packet in batch) + "\n",
            encoding="utf-8",
        )
        index.append(
            {
                "packet": name,
                "records": len(batch),
                "source_files": sorted({packet["source"]["path"] for packet in batch}),
                "titles": [packet["title"] for packet in batch],
            }
        )
        print(f"{name}: {len(batch)} records, {len(index[-1]['source_files'])} source files")

    (args.output_dir / "index.json").write_text(
        json.dumps({"batches": index}, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Wrote {len(records)} records in {len(batches)} batches to {args.output_dir} "
        f"({len({packet['source']['path'] for packet in packets})} source files)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
