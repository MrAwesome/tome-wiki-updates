#!/usr/bin/env python3
"""Format cached Tometips description HTML as wiki-like review text.

By default this script is read-only. Pass --apply to replace matching
talents/*/tometips-1.7.4-1.0 files.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "cache" / "tometips"
TOMETIPS_FILENAME = "tometips-1.7.4-1.0"

STAT_NAMES = {
    "character level": "LVL",
    "constitution": "CON",
    "cunning": "CUN",
    "dexterity": "DEX",
    "magic": "MAG",
    "mindpower": "MP",
    "paradox": "PARADOX",
    "physical power": "PP",
    "psi": "PSI",
    "shield block": "SHIELD BLOCK",
    "spellpower": "SP",
    "steampower": "STEAMPOWER",
    "strength": "STR",
    "willpower": "WIL",
}


@dataclass(frozen=True)
class CacheTalent:
    cache_file: Path
    data: dict[str, Any]
    source_path: str | None
    source_line: int | None


@dataclass(frozen=True)
class Match:
    metadata_path: Path
    metadata: dict[str, Any]
    talent: CacheTalent
    exact_line: bool
    cache_filename_missing: bool = False

    @property
    def output_path(self) -> Path:
        return self.metadata_path.parent / TOMETIPS_FILENAME


def source_relative_path(path: str | None) -> str | None:
    """Drop module/addon/version prefixes, retaining path below talents/."""
    if not path:
        return None
    normalized = path.replace("\\", "/")
    match = re.search(r"(?:^|/)talents/(.+)$", normalized)
    return match.group(1) if match else None


def iter_cache_talents(value: Any, cache_file: Path):
    if isinstance(value, dict):
        info_text = value.get("info_text")
        source_code = value.get("source_code")
        if isinstance(info_text, str):
            source_path = None
            source_line = None
            if (
                isinstance(source_code, list)
                and len(source_code) == 2
                and isinstance(source_code[0], str)
                and isinstance(source_code[1], int)
            ):
                source_path = source_relative_path(source_code[0])
                source_line = source_code[1]
            yield CacheTalent(cache_file, value, source_path, source_line)
        for child in value.values():
            yield from iter_cache_talents(child, cache_file)
    elif isinstance(value, list):
        for child in value:
            yield from iter_cache_talents(child, cache_file)


def load_cache_talents() -> list[CacheTalent]:
    talents = []
    for cache_file in sorted(CACHE_DIR.glob("*.json")):
        with cache_file.open(encoding="utf-8") as stream:
            talents.extend(iter_cache_talents(json.load(stream), cache_file))
    return talents


def find_matches() -> tuple[list[Match], list[str]]:
    cache_talents = load_cache_talents()
    by_path_line: dict[tuple[str, int], list[CacheTalent]] = {}
    by_path_id: dict[tuple[str, str], list[CacheTalent]] = {}
    by_line_id: dict[tuple[int, str], list[CacheTalent]] = {}
    for talent in cache_talents:
        if talent.source_line is None:
            continue
        talent_id = talent.data.get("id")
        if isinstance(talent_id, str):
            by_line_id.setdefault((talent.source_line, talent_id), []).append(talent)
            if talent.source_path is not None:
                by_path_id.setdefault((talent.source_path, talent_id), []).append(talent)
        if talent.source_path is not None:
            by_path_line.setdefault((talent.source_path, talent.source_line), []).append(talent)

    matches = []
    issues = []

    def issue_context(metadata_path: Path, metadata: dict[str, Any]) -> str:
        return (
            f"{metadata_path.parent.name} "
            f"(id={metadata.get('talent_id') or '—'}, "
            f"source={metadata.get('source_file') or '—'}:"
            f"{metadata.get('source_start_line') or '—'})"
        )

    for metadata_path in sorted((ROOT / "talents").glob("*/metadata.json")):
        with metadata_path.open(encoding="utf-8") as stream:
            metadata = json.load(stream)
        source_path = source_relative_path(metadata.get("source_file"))
        source_line = metadata.get("source_start_line")
        talent_id = metadata.get("talent_id")
        if source_path is None or not isinstance(source_line, int):
            issues.append(f"no source path/line: {issue_context(metadata_path, metadata)}")
            continue

        exact = by_path_line.get((source_path, source_line), [])
        if talent_id:
            exact = [talent for talent in exact if talent.data.get("id") == talent_id]
        if len(exact) == 1:
            matches.append(Match(metadata_path, metadata, exact[0], True))
            continue
        if len(exact) > 1:
            issues.append(f"ambiguous exact source match: {issue_context(metadata_path, metadata)}")
            continue

        # Tometips is 1.7.4 while core metadata points at 1.7.6. A unique
        # talent-ID + source-file match safely handles source line drift.
        fallback = by_path_id.get((source_path, talent_id), []) if talent_id else []
        if len(fallback) == 1:
            matches.append(Match(metadata_path, metadata, fallback[0], False))
        elif len(fallback) > 1:
            issues.append(f"ambiguous source-file/talent-ID match: {issue_context(metadata_path, metadata)}")
        else:
            # Some cached Tometips entries omit the source filename entirely.
            # Accept only a unique talent-ID + exact-line match in that case.
            pathless = [
                talent
                for talent in by_line_id.get((source_line, talent_id), [])
                if talent.source_path is None
            ] if talent_id else []
            if len(pathless) == 1:
                matches.append(Match(metadata_path, metadata, pathless[0], True, True))
            elif len(pathless) > 1:
                issues.append(f"ambiguous source-line/talent-ID match: {issue_context(metadata_path, metadata)}")
            else:
                issues.append(f"no cached source match: {issue_context(metadata_path, metadata)}")
    return matches, issues


def format_tooltip(title: str) -> str:
    """Render Tometips hover context as a compact, visible bracket label."""
    lines = [
        re.sub(r"\s+", " ", html.unescape(line)).strip().rstrip(",")
        for line in re.split(r"<br\s*/?>", title, flags=re.IGNORECASE)
    ]
    parts = []
    for line in lines:
        if not line or line.casefold() == "values for":
            continue
        level_match = re.fullmatch(r"(?:(.*?)\s+)?talent levels\s+(.+)", line, re.I)
        if level_match:
            prefix, levels = level_match.groups()
            label = f"{prefix.upper()} " if prefix else ""
            parts.append(f"{label}TL{levels}")
            continue
        if re.fullmatch(r"talent mastery\s+.+", line, re.I):
            continue
        context_match = re.fullmatch(r"(.+?)\s+([\d.,%+\- ]+)", line)
        if context_match:
            name, values = context_match.groups()
            label = STAT_NAMES.get(name.casefold(), name.upper())
            value_list = [value.strip() for value in values.split(",") if value.strip()]
            if len(value_list) > 1 and all(
                re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)%?", value)
                for value in value_list
            ):
                values = f"{value_list[0]}-{value_list[-1]}"
            parts.append(f"{label} {values.strip()}")
        else:
            # Keep unexpected tooltip text visible rather than silently dropping it.
            parts.append(line)
    return "; ".join(parts)


class WikiTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.acronym_titles: list[tuple[str, int]] = []
        self.emphasis: list[str] = []

    def append(self, text: str) -> None:
        self.parts.append(text)

    def block_break(self, count: int = 1) -> None:
        text = "\n" * count
        if self.parts and not self.parts[-1].endswith(text):
            self.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "p":
            self.block_break(2)
        elif tag == "ul":
            self.block_break()
        elif tag == "li":
            self.block_break()
            self.append("* ")
        elif tag == "br":
            self.block_break()
        elif tag == "acronym":
            self.acronym_titles.append((attributes.get("title") or "", len(self.parts)))
        elif tag == "span":
            style = (attributes.get("style") or "").casefold()
            marker = "''" if "font-style: italic" in style else "'''" if "font-weight: bold" in style else ""
            self.emphasis.append(marker)
            if marker:
                self.append(marker)

    def handle_endtag(self, tag: str) -> None:
        if tag == "p":
            self.block_break(2)
        elif tag == "li":
            self.block_break()
        elif tag == "ul":
            self.block_break()
        elif tag == "acronym":
            title, start = self.acronym_titles.pop() if self.acronym_titles else ("", len(self.parts))
            context = format_tooltip(title)
            if context:
                value = "".join(self.parts[start:])
                del self.parts[start:]
                punctuation = ""
                match = re.search(r"([.,;:!?]+)$", value)
                if match:
                    value, punctuation = value[: match.start()], match.group(1)
                self.append(f"[{value} ({context})]{punctuation}")
        elif tag == "span" and self.emphasis:
            marker = self.emphasis.pop()
            if marker:
                self.append(marker)

    def handle_data(self, data: str) -> None:
        self.append(data)

    def result(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip() + "\n"


def format_html(source: str) -> str:
    parser = WikiTextParser()
    parser.feed(source)
    parser.close()
    return parser.result()


class PrettyHTMLParser(HTMLParser):
    """Pretty-print cached HTML by placing block elements on separate lines."""
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []

    def boundary(self):
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"p", "ul", "li"}:
            self.boundary()
        self.parts.append(self.get_starttag_text())
        if tag in {"ul", "li"}:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")
        if tag in {"p", "ul", "li"}:
            self.boundary()

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def result(self) -> str:
        return "".join(self.parts).strip()


def pretty_source_html(source: str) -> str:
    parser = PrettyHTMLParser()
    parser.feed(source)
    parser.close()
    return parser.result()


def selector_keys(match: Match) -> set[str]:
    metadata = match.metadata
    folder = match.metadata_path.parent.name
    keys = {
        folder,
        f"talents/{folder}",
        str(metadata.get("filename", "")),
        str(metadata.get("wiki_title", "")),
        str(metadata.get("talent_id", "")),
        str(metadata.get("canonical_talent_id", "")),
    }
    return {key.casefold() for key in keys if key}


def select_matches(matches: list[Match], selectors: list[str]) -> list[Match]:
    if not selectors:
        return matches
    selected = []
    for selector in selectors:
        key = selector.casefold()
        hits = [match for match in matches if key in selector_keys(match)]
        if len(hits) != 1:
            raise ValueError(f"selector {selector!r} matched {len(hits)} talents")
        if hits[0] not in selected:
            selected.append(hits[0])
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--talent", action="append", default=[], help="select by talent folder, wiki title, or talent ID; repeatable")
    parser.add_argument("--apply", action="store_true", help="replace matching tometips files; default is read-only")
    parser.add_argument("--show-source", action="store_true", help="print cached HTML beside formatted output for selected talents")
    parser.add_argument("--show-issues", action="store_true", help="list talents with missing or ambiguous cache mappings")
    args = parser.parse_args(argv)

    matches, issues = find_matches()
    try:
        selected = select_matches(matches, args.talent)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2

    changed = 0
    missing_target = 0
    for match in selected:
        source = match.talent.data["info_text"]
        output = format_html(source)
        target = match.output_path
        if not target.is_file():
            missing_target += 1
            continue
        old_text = target.read_text(encoding="utf-8")
        if old_text != output:
            changed += 1
            if args.apply:
                target.write_text(output, encoding="utf-8")
        if args.talent:
            label = match.metadata.get("wiki_title", match.metadata_path.parent.name)
            if match.cache_filename_missing:
                line_status = "unique talent ID + exact line; cache filename missing"
            elif match.exact_line:
                line_status = "exact source file + line"
            else:
                line_status = "unique talent ID + file; source line differs"
            print(f"## {label} ({line_status})")
            if args.show_source:
                print("Source HTML:")
                print("```html")
                print(pretty_source_html(source))
                print("```")
            print("Formatted description:")
            print("```text")
            print(output, end="")
            print("```")
            if old_text == output:
                print("Already formatted.\n")
            elif args.apply:
                print(f"Updated {target.relative_to(ROOT)}.\n")
            else:
                print(f"Dry run; would update {target.relative_to(ROOT)}.\n")

    print(
        f"Matched {len(matches)} pages; selected {len(selected)}; "
        f"{'updated' if args.apply else 'would update'} {changed}; "
        f"missing target files {missing_target}; mapping issues {len(issues)}."
    )
    if issues:
        print(f"Unmatched/ambiguous metadata entries: {len(issues)} (left unchanged).")
        if args.show_issues:
            for issue in issues:
                print(f"- {issue}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
