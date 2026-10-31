#!/usr/bin/env python3
"""Render source info() text into review-only Ability_box candidates.

Renderer intentionally supports a conservative subset of ToME's Lua info
functions. Unsupported expressions produce status records, never guesses.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TENGINE = Path.home() / "code" / "t-engine4"
DEFAULT_OUTPUT = Path("/tmp/opencode/tome-wiki-phase1-dry-run.jsonl")
CORE_COMMIT = "624a67329fe2ad440c5b344785a9c73fcf22ae63"


class Unsupported(Exception):
    pass


def skip_quoted(text: str, pos: int) -> int:
    quote = text[pos]
    i = pos + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
        elif text[i] == quote:
            return i + 1
        else:
            i += 1
    return len(text)


def long_bracket(text: str, pos: int):
    if pos >= len(text) or text[pos] != "[":
        return None
    i = pos + 1
    while i < len(text) and text[i] == "=":
        i += 1
    if i < len(text) and text[i] == "[":
        return i + 1, "]" + text[pos + 1 : i] + "]"
    return None


def skip_comment(text: str, pos: int):
    if not text.startswith("--", pos):
        return None
    opener = long_bracket(text, pos + 2)
    if opener:
        body, closer = opener
        end = text.find(closer, body)
        return len(text) if end < 0 else end + len(closer)
    end = text.find("\n", pos + 2)
    return len(text) if end < 0 else end + 1


def matching(text: str, open_pos: int, opening: str, closing: str) -> int:
    depth = 0
    i = open_pos
    while i < len(text):
        if text[i] in "'\"":
            i = skip_quoted(text, i)
            continue
        comment_end = skip_comment(text, i)
        if comment_end is not None:
            i = comment_end
            continue
        if text[i] == "[":
            lb = long_bracket(text, i)
            if lb:
                body, closer = lb
                end = text.find(closer, body)
                i = len(text) if end < 0 else end + len(closer)
                continue
        if text.startswith(opening, i):
            depth += 1
            i += len(opening)
            continue
        if text.startswith(closing, i):
            depth -= 1
            i += len(closing)
            if depth == 0:
                return i
            continue
        i += 1
    raise Unsupported(f"unbalanced {opening}{closing}")


def split_args(text: str) -> list[str]:
    args = []
    start = 0
    stack = []
    i = 0
    pairs = {"(": ")", "{": "}", "[": "]"}
    while i < len(text):
        if text[i] in "'\"":
            i = skip_quoted(text, i)
            continue
        comment_end = skip_comment(text, i)
        if comment_end is not None:
            i = comment_end
            continue
        if text[i] in pairs:
            stack.append(pairs[text[i]])
        elif stack and text[i] == stack[-1]:
            stack.pop()
        elif text[i] == "," and not stack:
            args.append(text[start:i].strip())
            start = i + 1
        i += 1
    tail = text[start:].strip()
    if tail:
        args.append(tail)
    return args


def has_unsupported_top_level_arithmetic(text: str) -> bool:
    pairs = {"(": ")", "{": "}", "[": "]"}
    stack = []
    i = 0
    while i < len(text):
        if text[i] in "'\"":
            i = skip_quoted(text, i)
            continue
        comment_end = skip_comment(text, i)
        if comment_end is not None:
            i = comment_end
            continue
        if text[i] == "[":
            lb = long_bracket(text, i)
            if lb:
                body, closer = lb
                end = text.find(closer, body)
                i = len(text) if end < 0 else end + len(closer)
                continue
        if text[i] in pairs:
            stack.append(pairs[text[i]])
        elif stack and text[i] == stack[-1]:
            stack.pop()
        elif not stack and text[i] in "+-":
            previous = text[:i].rstrip()[-1:] or ""
            if previous not in "(+−-*/%,":
                return True
        i += 1
    return False


def call_args(expr: str, name: str):
    match = re.search(rf"\b{re.escape(name)}\s*\(", expr)
    if not match:
        return None
    open_pos = expr.find("(", match.start())
    end = matching(expr, open_pos, "(", ")")
    return split_args(expr[open_pos + 1 : end - 1])


def exact_call(expr: str, names):
    expr = strip_outer_parens(expr)
    match = re.match(r"(?:(?:self):)?([A-Za-z_]\w*)\s*\(", expr)
    if not match or match.group(1) not in names:
        return None
    open_pos = expr.find("(", match.start())
    end = matching(expr, open_pos, "(", ")")
    if end != len(expr):
        return None
    return match.group(1), split_args(expr[open_pos + 1 : end - 1])


def strip_outer_parens(expr: str) -> str:
    expr = expr.strip()
    while expr.startswith("("):
        try:
            if matching(expr, 0, "(", ")") != len(expr):
                break
        except Unsupported:
            break
        expr = expr[1:-1].strip()
    return expr


def fmt_number(value: str, multiplier: float = 1.0) -> str:
    try:
        number = float(value.strip()) * multiplier
    except ValueError as error:
        raise Unsupported(f"non-literal scaling argument: {value}") from error
    if number.is_integer():
        return str(int(number))
    return format(number, ".10g")


def scale_value(
    template: str,
    args: list[str],
    percent: bool,
    floor: bool = False,
    ceil: bool = False,
    value_multiplier: float = 1.0,
    percentage_multiplier: float = 1.0,
    complement_constant: float | None = None,
) -> str:
    if complement_constant is not None and template != "ctl":
        raise Unsupported("complement arithmetic is only supported for ctl scaling")
    if template == "ctwd":
        if len(args) < 3 or len(args) > 3:
            raise Unsupported("weapon damage helper has extra scaling arguments")
        low, high = fmt_number(args[1], 100 * value_multiplier), fmt_number(args[2], 100 * value_multiplier)
        return f"{{{{ctwd|{low}|{high}%}}}}"

    if template in ("cts", "ctspd", "ctmd", "ctstmd"):
        if len(args) < 3:
            raise Unsupported(f"{template} lacks low/high arguments")
        low, high = args[1], args[2]
        power = None
        if template in ("ctspd", "ctmd") and len(args) >= 4:
            power_arg = args[3].lower()
            power = "PM" if "paradox" in power_arg else "Mindpower" if "mind" in power_arg else "Spellpower"
        elif len(args) > 3:
            raise Unsupported(f"unsupported extra arguments to {template}")
        if floor:
            low, high = fmt_number(low, 1), fmt_number(high, 1)
            low, high = str(int(float(low) // 1)), str(int(float(high) // 1))
        elif ceil:
            low, high = str(int(-(-float(low) // 1))), str(int(-(-float(high) // 1)))
        scale = value_multiplier * percentage_multiplier
        params = [fmt_number(low, scale), fmt_number(high, scale)]
        if power:
            params.append(power)
        if percent and template not in ("ctspd", "ctmd", "ctstmd"):
            params[-1] += "%"
        return "{{" + template + "|" + "|".join(params) + "}}"

    if template == "ctstd":
        if len(args) < 4 or len(args) > 4:
            raise Unsupported("stat damage helper has unsupported arguments")
        stat = args[1].strip(" '\"").capitalize()
        scale = value_multiplier * percentage_multiplier
        low, high = fmt_number(args[2], scale), fmt_number(args[3], scale)
        if floor:
            low, high = str(int(float(low) // 1)), str(int(float(high) // 1))
        if ceil:
            low, high = str(int(-(-float(low) // 1))), str(int(-(-float(high) // 1)))
        return f"{{{{ctstd|{stat}|{low}|{high}}}}}"

    if template == "ctl":
        if len(args) < 4 or len(args) > 4:
            raise Unsupported("talent limit helper has unsupported arguments")
        scale = value_multiplier * percentage_multiplier
        max_value, low, high = (fmt_number(x, scale) for x in args[1:4])
        if complement_constant is not None:
            max_value, low, high = (
                fmt_number(str(complement_constant - float(x))) for x in (max_value, low, high)
            )
        if floor:
            low, high = str(int(float(low) // 1)), str(int(float(high) // 1))
        if ceil:
            low, high = str(int(-(-float(low) // 1))), str(int(-(-float(high) // 1)))
        if percent:
            max_value += "%"
            high += "%"
        return f"{{{{ctl|{max_value}|{low}|{high}}}}}"

    if template == "cts":
        if len(args) < 3 or len(args) > 3:
            raise Unsupported("talent scale helper has unsupported arguments")
        scale = value_multiplier * percentage_multiplier
        low, high = fmt_number(args[1], scale), fmt_number(args[2], scale)
        if floor:
            low, high = str(int(float(low) // 1)), str(int(float(high) // 1))
        if ceil:
            low, high = str(int(-(-float(low) // 1))), str(int(-(-float(high) // 1)))
        if percent:
            high += "%"
        return f"{{{{cts|{low}|{high}}}}}"

    if template == "css":
        if len(args) < 4 or len(args) > 4:
            raise Unsupported("stat scale helper has unsupported arguments")
        stat = args[1].strip(" '\"").capitalize()
        scale = value_multiplier * percentage_multiplier
        low, high = fmt_number(args[2], scale), fmt_number(args[3], scale)
        if percent:
            high += "%"
        return f"{{{{css|{stat}|{low}|{high}}}}}"

    raise Unsupported(f"unsupported scaling template {template}")


def source_helper(block: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*=\s*function\b", block)
    if not match:
        raise Unsupported(f"source helper {name} not found")
    body_start = match.end()
    # Current supported helper definitions end at `end,` on the same or later line.
    end_match = re.search(r"\bend\s*,", block[body_start:])
    if not end_match:
        raise Unsupported(f"cannot bound helper {name}")
    return block[body_start : body_start + end_match.start()]


def render_expression(
    expr: str,
    block: str,
    locals_map: dict[str, str],
    seen=None,
    percent=False,
    value_multiplier: float = 1.0,
    percentage_multiplier: float = 1.0,
    complement_constant: float | None = None,
) -> str:
    expr = strip_outer_parens(expr)
    seen = set() if seen is None else seen
    if expr in locals_map and expr not in seen:
        return render_expression(
            locals_map[expr], block, locals_map, seen | {expr}, percent,
            value_multiplier, percentage_multiplier, complement_constant,
        )

    # Percent damage multipliers are commonly multiplied by 100 before tformat.
    mul = re.fullmatch(r"(.+?)\s*\*\s*100", expr) or re.fullmatch(r"100\s*\*\s*(.+)", expr)
    if mul:
        return render_expression(
            mul.group(1), block, locals_map, seen, percent=True,
            value_multiplier=value_multiplier,
            percentage_multiplier=percentage_multiplier * 100,
            complement_constant=complement_constant,
        )

    complement = re.fullmatch(r"(-?[\d.]+)\s*-\s*(.+)", expr)
    if complement:
        constant, base = complement.groups()
        if complement_constant is not None:
            raise Unsupported("nested complement arithmetic is unsupported")
        return render_expression(
            base, block, locals_map, seen, percent,
            value_multiplier=value_multiplier,
            percentage_multiplier=percentage_multiplier,
            complement_constant=float(constant),
        )

    # Carry literal multiplication/division through helper-based scaling. This
    # covers values such as damage / 10 and a stat-scaled value multiplied by 4.
    scalar = re.fullmatch(r"(.+?)\s*([*/])\s*(-?[\d.]+)", expr)
    scalar_left = re.fullmatch(r"(-?[\d.]+)\s*\*\s*(.+)", expr)
    if scalar or scalar_left:
        if scalar:
            base, operator, literal = scalar.groups()
            factor = float(literal) if operator == "*" else 1 / float(literal)
        else:
            literal, base = scalar_left.groups()
            factor = float(literal)
        return render_expression(
            base, block, locals_map, seen, percent,
            value_multiplier=value_multiplier * factor,
            percentage_multiplier=percentage_multiplier,
            complement_constant=complement_constant,
        )

    if has_unsupported_top_level_arithmetic(expr):
        raise Unsupported(f"unsupported arithmetic expression: {expr}")

    floor = ceil = False
    m = re.fullmatch(r"math\.(floor|ceil)\s*\((.*)\)", expr, re.S)
    if m:
        floor, ceil = m.group(1) == "floor", m.group(1) == "ceil"
        expr = strip_outer_parens(m.group(2))

    if expr.startswith("damDesc"):
        args = call_args(expr, "damDesc")
        if not args:
            raise Unsupported("cannot parse damDesc")
        # Damage type is already named in source prose; damDesc supplies numeric value.
        return render_expression(
            args[-1], block, locals_map, seen, percent, value_multiplier,
            percentage_multiplier, complement_constant,
        )

    helper_match = re.search(r"(?:\bt\.|\bself:)(get[A-Za-z0-9_]+)\s*\(", expr)
    if helper_match:
        name = helper_match.group(1)
        name = {'getTalentRadius': 'radius', 'getTalentRange': 'range'}.get(name, name)
        helper = source_helper(block, name)
        branch = re.search(
            r"if\b.*?then\s*return\s*(-?[\d.]+)\s*else\s*return\s*(-?[\d.]+)\s*end",
            helper,
            re.S,
        )
        if branch:
            return f"{{{{gtl|{fmt_number(branch.group(2))}|{fmt_number(branch.group(1))}}}}}"
        ret = re.search(r"\breturn\s+(.+)$", helper, re.S)
        if not ret:
            raise Unsupported(f"cannot resolve helper {name}")
        helper_expr = re.sub(r"\s+end\s*$", "", ret.group(1).strip())
        return render_expression(
            helper_expr, block, locals_map, seen | {name}, percent,
            value_multiplier, percentage_multiplier, complement_constant,
        )

    helper_specs = (
        ("combatTalentWeaponDamage", "ctwd"),
        ("combatTalentSpellDamage", "ctspd"),
        ("combatTalentMindDamage", "ctmd"),
        ("combatTalentSteamDamage", "ctstmd"),
        ("combatTalentStatDamage", "ctstd"),
        ("combatTalentLimit", "ctl"),
        ("combatTalentScale", "cts"),
        ("combatStatScale", "css"),
    )
    parsed_call = exact_call(expr, {name for name, _ in helper_specs})
    if parsed_call:
        helper, args = parsed_call
        template = dict(helper_specs)[helper]
        return scale_value(
            template, args, percent, floor, ceil, value_multiplier, percentage_multiplier,
            complement_constant,
        )

    raise Unsupported(f"unsupported interpolation expression: {expr}")


def split_info(block: str):
    info = re.search(r"\binfo\s*=\s*function\b", block)
    if not info:
        raise Unsupported("info function missing")
    tail = block[info.end() :]
    ret = re.search(r"\breturn\s*\(\s*\[(=*)\[(.*?)\]\1\]", tail, re.S)
    if not ret:
        raise Unsupported("info return is not a supported Lua long string")
    raw = ret.group(2)
    after = tail[ret.end() :]
    fmt = re.search(r":\s*tformat\s*\(", after)
    if not fmt:
        args = []
    else:
        open_pos = after.find("(", fmt.start())
        end = matching(after, open_pos, "(", ")")
        args = split_args(after[open_pos + 1 : end - 1])

    pre_return = tail[: ret.start()]
    locals_map = {}
    for match in re.finditer(r"(?m)^\s*local\s+([A-Za-z_]\w*)\s*=\s*([^\n]+)", pre_return):
        locals_map[match.group(1)] = match.group(2).strip().rstrip(",")

    # Convert the source's indentation-only whitespace, preserving wording and blank lines.
    source_lines = [line.strip() for line in raw.splitlines()]
    source_text = wiki_bullets("\n".join(source_lines).strip())
    tokens = list(re.finditer(r"%0\.\d+f|%d|%f|%%", source_text))
    if len(args) != sum(token.group() != "%%" for token in tokens):
        raise Unsupported(f"format argument mismatch: {len(tokens)} placeholders, {len(args)} args")

    result = []
    pos = 0
    arg_index = 0
    token_index = 0
    while token_index < len(tokens):
        token = tokens[token_index]
        result.append(source_text[pos : token.start()])
        if token.group() == "%%":
            result.append("%")
            pos = token.end()
            token_index += 1
            continue
        percent = token_index + 1 < len(tokens) and tokens[token_index + 1].group() == "%%" and tokens[token_index + 1].start() == token.end()
        rendered = render_expression(args[arg_index], block, locals_map, percent=percent)
        result.append(rendered)
        arg_index += 1
        pos = tokens[token_index + 1].end() if percent else token.end()
        token_index += 2 if percent else 1
    result.append(source_text[pos:])
    return "\n".join(line.rstrip() for line in "".join(result).splitlines()).strip()


def extract_box(page: str):
    matches = list(re.finditer(r"\{\{\s*Ability(?:_|\s)box\b", page, re.I))
    if len(matches) != 1:
        raise Unsupported(f"expected one Ability_box, found {len(matches)}")
    start = matches[0].start()
    depth = 0
    i = start
    while i < len(page) - 1:
        if page.startswith("<!--", i):
            close = page.find("-->", i + 4)
            i = len(page) if close < 0 else close + 3
            continue
        if page.startswith("{{", i):
            depth += 1
            i += 2
        elif page.startswith("}}", i):
            depth -= 1
            i += 2
            if depth == 0:
                return start, i, page[start:i]
        else:
            i += 1
    raise Unsupported("unbalanced Ability_box wikitext")


def set_param(box: str, key: str, value: str) -> str:
    pattern = re.compile(rf"(?m)^([ \t]*\|\s*{re.escape(key)}\s*=\s*)([^\r\n]*)")
    matches = list(pattern.finditer(box))
    if len(matches) > 1:
        raise Unsupported(f"multiple {key} parameters")
    if matches:
        match = matches[0]
        return box[: match.start(2)] + value + box[match.end(2) :]
    first_newline = box.find("\n")
    if first_newline < 0:
        raise Unsupported("Ability_box opener is not on its own line")
    return box[: first_newline + 1] + f"|{key}={value}\n" + box[first_newline + 1 :]


def set_desc(box: str, description: str) -> str:
    match = re.search(r"(?m)^[ \t]*\|\s*desc\s*=", box)
    if not match:
        raise Unsupported("desc parameter missing")
    value_start = match.end()
    next_param = re.search(r"(?m)^[ \t]*\|\s*[A-Za-z_][A-Za-z0-9_.-]*\s*=", box[value_start:])
    closing = box.rfind("}}")
    if closing < value_start:
        raise Unsupported("Ability_box close missing")
    end = value_start + next_param.start() if next_param else closing
    prefix = box[:value_start]
    suffix = box[end:]
    return prefix + "\n" + description + "\n" + suffix


def normalize_description(value: str) -> str:
    # Avoid reformatting an existing source-equivalent description just because
    # template aliases or blank-line layout differ.
    value = re.sub(r"\{\{([A-Za-z_][A-Za-z0-9_-]*)", lambda m: "{{" + m.group(1).lower(), value)
    return re.sub(r"\s+", " ", value).strip()


def current_description(box: str):
    match = re.search(r"(?m)^[ \t]*\|\s*desc\s*=", box)
    if not match:
        return None
    start = match.end()
    next_param = re.search(r"(?m)^[ \t]*\|\s*[A-Za-z_][A-Za-z0-9_.-]*\s*=", box[start:])
    close = box.rfind("}}")
    end = start + next_param.start() if next_param else close
    return box[start:end]


def wiki_bullets(value: str) -> str:
    """Render leading dash-list markers as MediaWiki bullets."""
    return re.sub(r"(?m)^-\s+", "*", value)


def note_content(value: str) -> str:
    """Normalize note text enough to recognize source wording with different markup."""
    value = re.sub(r"#\{/?(?:italic|normal|bold)\}#", " ", value, flags=re.I)
    value = re.sub(r"'''|''", "", value)
    value = re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\{\{(?:t|ct|c)\|([^}|]+)(?:\|[^}]*)?\}\}", r"\1", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip().casefold()


def preserved_wiki_notes(current: str | None, rendered: str) -> list[str]:
    """Keep explicit notes and italicized wiki asides absent from source info()."""
    if not current:
        return []
    rendered_text = note_content(rendered)
    notes = []
    for paragraph in re.split(r"\n\s*\n", current.strip()):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        explicit_note = re.match(r"(?is)^(?:'''|''|\s)*note\b", paragraph)
        italic_aside = paragraph.startswith("''") and paragraph.endswith("''")
        if not explicit_note and not italic_aside:
            continue
        normalized = note_content(paragraph)
        if normalized and normalized not in rendered_text:
            notes.append(paragraph)
    return notes


def append_preserved_notes(description: str, notes: list[str]) -> str:
    if not notes:
        return description
    return description.rstrip() + "\n\n" + "\n\n".join(notes)


def patch_templates_only(current: str, rendered: str):
    """Patch changed Scaling calls in place when prose and template positions agree."""
    token = re.compile(r"\{\{[^{}]*\}\}")
    current_plain = re.sub(token, "<TEMPLATE>", current)
    rendered_plain = re.sub(token, "<TEMPLATE>", rendered)
    if re.sub(r"\s+", " ", current_plain).strip() != re.sub(r"\s+", " ", rendered_plain).strip():
        return None
    current_tokens = list(token.finditer(current))
    rendered_tokens = list(token.finditer(rendered))
    if len(current_tokens) != len(rendered_tokens):
        return None
    result = current
    for old, new in reversed(list(zip(current_tokens, rendered_tokens))):
        old_text, new_text = old.group(0), new.group(0)
        if old_text.lower() != new_text.lower():
            result = result[:old.start()] + new_text + result[old.end():]
    return result


def load_source(meta: dict, tengine_root: Path, core_tag: str) -> str:
    relative = meta['source_file']
    if meta['source_provenance'] == 'tagged_core_source':
        result = subprocess.run(
            ['git', '-C', str(tengine_root), 'show', f'{core_tag}:{relative}'],
            check=True, capture_output=True,
        )
        raw = result.stdout
    elif meta['source_provenance'] == 'local_gitignored_addon_tree':
        raw = (tengine_root / relative).read_bytes()
    else:
        raise Unsupported('unknown source provenance')
    digest = hashlib.sha256(raw).hexdigest()
    if digest != meta['source_sha256']:
        raise Unsupported(f"source hash mismatch: expected {meta['source_sha256']}, got {digest}")
    lines = raw.decode('utf-8').splitlines()
    return '\n'.join(lines[meta['source_start_line'] - 1 : meta['source_end_line']])


def build_candidate(directory: Path, tengine_root: Path, core_tag: str):
    meta = json.loads((directory / 'metadata.json').read_text(encoding='utf-8'))
    before = (directory / 'page').read_text(encoding='utf-8')
    common = {
        'title': meta['wiki_title'], 'filename': meta['filename'],
        'pageid': meta['pageid'], 'revid': meta['revid'],
        'source_file': meta['source_file'],
        'source_lines': [meta['source_start_line'], meta['source_end_line']],
        'source_sha256': meta['source_sha256'], 'talent_id': meta['talent_id'],
        'source_match_status': meta['source_match_status'],
        'before_wikitext': before, 'after_wikitext': before,
    }
    if meta['source_match_status'] != 'matched':
        return {**common, 'status': 'unmatched_source', 'error': meta['source_match_status']}
    try:
        block = load_source(meta, tengine_root, core_tag)
        desc = split_info(block)
        start, end, box = extract_box(before)
        candidate_box = set_param(box, 'game_version', '1.7.6')
        if meta['addon_id'] == 'tome-orcs':
            candidate_box = set_param(candidate_box, 'embers_version', meta['addon_version'])
        elif meta['addon_id'] == 'tome-ashes-urhrok':
            candidate_box = set_param(candidate_box, 'ashes_version', meta['addon_version'])
        elif meta['addon_id'] == 'tome-cults':
            raise Unsupported('wiki addon-version parameter for tome-cults is not established')
        current_desc = current_description(candidate_box)
        notes = preserved_wiki_notes(current_desc, desc)
        desc = append_preserved_notes(desc, notes)
        patched_desc = patch_templates_only(current_desc, desc) if current_desc is not None else None
        if patched_desc is not None:
            if patched_desc != current_desc:
                desc_match = re.search(r"(?m)^[ \t]*\|\s*desc\s*=", candidate_box)
                desc_start = desc_match.end()
                next_param = re.search(r"(?m)^[ \t]*\|\s*[A-Za-z_][A-Za-z0-9_.-]*\s*=", candidate_box[desc_start:])
                close = candidate_box.rfind("}}")
                desc_end = desc_start + next_param.start() if next_param else close
                candidate_box = candidate_box[:desc_start] + patched_desc + candidate_box[desc_end:]
        elif current_desc is None or normalize_description(current_desc) != normalize_description(desc):
            candidate_box = set_desc(candidate_box, desc)
        after = before[:start] + candidate_box + before[end:]
        new_start, new_end, _ = extract_box(after)
        if before[:start] != after[:new_start] or before[end:] != after[new_end:]:
            raise Unsupported('candidate changed text outside Ability_box')
        return {
            **common, 'status': 'candidate' if after != before else 'no_change',
            'updated_fields': [key for key in ('game_version', 'embers_version', 'ashes_version', 'desc')
                               if key_parameter_changed(box, candidate_box, key)],
            'preserved_wiki_notes': [
                {'field': 'desc', 'text': note, 'review_status': 'needs_review_1.7.6'}
                for note in notes
            ],
            'before_wikitext': before, 'after_wikitext': after,
        }
    except (Unsupported, subprocess.CalledProcessError, OSError) as error:
        return {**common, 'status': 'unsupported_info', 'error': str(error)}


def key_parameter_changed(before_box: str, after_box: str, key: str) -> bool:
    if key == 'desc':
        pattern = r'(?ms)^\s*\|\s*desc\s*=.*?(?=^\s*\|\s*[A-Za-z_][\w.-]*\s*=|^\s*\}\})'
    else:
        pattern = rf'(?m)^\s*\|\s*{re.escape(key)}\s*=.*$'
    before_match = re.search(pattern, before_box)
    after_match = re.search(pattern, after_box)
    before_value = before_match.group(0) if before_match else None
    after_value = after_match.group(0) if after_match else None
    return before_value != after_value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--t-engine-root', type=Path, default=DEFAULT_TENGINE)
    parser.add_argument('--core-tag', default='tome-1.7.6')
    parser.add_argument('--title', action='append', default=[], help='wiki title; repeat to select samples')
    parser.add_argument('--limit', type=int, help='limit processed pages after filtering')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    resolved_commit = subprocess.run(
        ['git', '-C', str(args.t_engine_root), 'rev-parse', f'{args.core_tag}^{{commit}}'],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if resolved_commit != CORE_COMMIT:
        parser.error(f'{args.core_tag} resolves to {resolved_commit}, expected {CORE_COMMIT}')

    dirs = sorted((ROOT / 'talents').iterdir())
    if args.title:
        wanted = set(args.title)
        dirs = [d for d in dirs if json.loads((d / 'metadata.json').read_text(encoding='utf-8'))['wiki_title'] in wanted]
        found = {json.loads((d / 'metadata.json').read_text(encoding='utf-8'))['wiki_title'] for d in dirs}
        missing = wanted - found
        if missing:
            parser.error('unknown titles: ' + ', '.join(sorted(missing)))
    if args.limit is not None:
        dirs = dirs[:args.limit]

    records = [build_candidate(d, args.t_engine_root, args.core_tag) for d in dirs]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w', encoding='utf-8', newline='\n') as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    counts = {}
    for record in records:
        counts[record['status']] = counts.get(record['status'], 0) + 1
    print(f'Wrote {len(records)} review-only records to {args.output}: {counts}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
