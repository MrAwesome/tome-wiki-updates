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
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from review_workflow import (
    ReviewRecordError,
    identity_from_metadata,
    initialize_review,
    load_review,
    merge_owned_fields,
    review_path,
    validate_review,
    write_review,
)


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


def strip_lua_comments(text: str) -> str:
    result = []
    i = 0
    while i < len(text):
        if text[i] in "'\"":
            end = skip_quoted(text, i)
            result.append(text[i:end])
            i = end
            continue
        comment_end = skip_comment(text, i)
        if comment_end is not None:
            result.append(" ")
            i = comment_end
            continue
        if text[i] == "[":
            lb = long_bracket(text, i)
            if lb:
                body, closer = lb
                end = text.find(closer, body)
                end = len(text) if end < 0 else end + len(closer)
                result.append(text[i:end])
                i = end
                continue
        result.append(text[i])
        i += 1
    return "".join(result).strip()


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
            args.append(strip_lua_comments(text[start:i]))
            start = i + 1
        i += 1
    tail = strip_lua_comments(text[start:])
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
    value_offset: float = 0.0,
) -> str:
    if complement_constant is not None and template != "ctl":
        raise Unsupported("complement arithmetic is only supported for ctl scaling")
    if template == "ctwd":
        if len(args) < 3 or len(args) > 3:
            raise Unsupported("weapon damage helper has extra scaling arguments")
        low = fmt_number(args[1], 100 * value_multiplier)
        high = fmt_number(args[2], 100 * value_multiplier)
        low, high = fmt_number(str(float(low) + value_offset)), fmt_number(str(float(high) + value_offset))
        return f"{{{{ctwd|{low}|{high}%}}}}"

    if template in ("cts", "ctspd", "ctmd", "ctstmd", "ctpd"):
        if len(args) < 3:
            raise Unsupported(f"{template} lacks low/high arguments")
        if template == "cts" and len(args) > 6:
            raise Unsupported("talent scale helper has too many arguments")
        if template != "cts" and len(args) > 3 and not (template in ("ctspd", "ctmd") and len(args) == 4):
            raise Unsupported(f"unsupported extra arguments to {template}")
        low, high = args[1], args[2]
        power = None
        if template in ("ctspd", "ctmd") and len(args) >= 4:
            power_arg = args[3].lower()
            power = "PM" if "paradox" in power_arg else "Mindpower" if "mind" in power_arg else "Spellpower"
        elif template in ("ctspd", "ctmd") and len(args) > 4:
            raise Unsupported(f"unsupported extra arguments to {template}")
        if floor:
            low, high = fmt_number(low, 1), fmt_number(high, 1)
            low, high = str(int(float(low) // 1)), str(int(float(high) // 1))
        elif ceil:
            low, high = str(int(-(-float(low) // 1))), str(int(-(-float(high) // 1)))
        scale = value_multiplier * percentage_multiplier
        params = [
            fmt_number(str(float(fmt_number(low, scale)) + value_offset)),
            fmt_number(str(float(fmt_number(high, scale)) + value_offset)),
        ]
        if power:
            params.append(power)
        if template == "cts" and len(args) >= 4:
            power = args[3].strip().strip("'\"")
            params.append("log" if power == "log" else fmt_number(power))
            params.extend(fmt_number(arg) for arg in args[4:])
        if percent and template == "cts":
            params[1] += "%"
        elif percent and template not in ("ctspd", "ctmd", "ctstmd", "ctpd"):
            params[-1] += "%"
        return "{{" + template + "|" + "|".join(params) + "}}"

    if template == "ctstd":
        if len(args) < 4 or len(args) > 4:
            raise Unsupported("stat damage helper has unsupported arguments")
        stat = args[1].strip(" '\"").capitalize()
        scale = value_multiplier * percentage_multiplier
        low, high = fmt_number(args[2], scale), fmt_number(args[3], scale)
        low, high = fmt_number(str(float(low) + value_offset)), fmt_number(str(float(high) + value_offset))
        if floor:
            low, high = str(int(float(low) // 1)), str(int(float(high) // 1))
        if ceil:
            low, high = str(int(-(-float(low) // 1))), str(int(-(-float(high) // 1)))
        return f"{{{{ctstd|{stat}|{low}|{high}}}}}"

    if template == "ctl":
        if len(args) < 4 or len(args) > 6:
            raise Unsupported("talent limit helper has unsupported arguments")
        scale = value_multiplier * percentage_multiplier
        max_value, low, high = (fmt_number(x, scale) for x in args[1:4])
        max_value, low, high = (
            fmt_number(str(float(x) + value_offset)) for x in (max_value, low, high)
        )
        if complement_constant is not None:
            max_value, low, high = (
                fmt_number(str(complement_constant - float(x))) for x in (max_value, low, high)
            )
        if floor:
            low, high = str(int(float(low) // 1)), str(int(float(high) // 1))
        if ceil:
            low, high = str(int(-(-float(low) // 1))), str(int(-(-float(high) // 1)))
        optional = []
        if len(args) >= 5:
            raw = args[4].strip().lower()
            if raw in ("true", "raw"):
                optional.append("raw")
            elif raw == "false":
                optional.append("false")
            elif re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
                # Lua treats every number, including 0, as truthy.
                optional.append("raw")
            else:
                raise Unsupported("talent limit raw flag is not a supported literal")
        if len(args) == 6:
            mastery = args[5].strip()
            if re.fullmatch(r"-?\d+(?:\.\d+)?", mastery):
                optional.append(fmt_number(mastery))
            elif mastery.startswith("{") and mastery.endswith("}"):
                values = split_args(mastery[1:-1])
                if not values or any(not re.fullmatch(r"-?\d+(?:\.\d+)?", value) for value in values):
                    raise Unsupported("talent limit mastery table is not numeric")
                optional.append(",".join(fmt_number(value) for value in values))
            else:
                raise Unsupported("talent limit mastery is not a supported literal")
        if percent:
            max_value += "%"
            high += "%"
        return "{{ctl|" + "|".join([max_value, low, high, *optional]) + "}}"

    if template == "cts":
        if len(args) < 3 or len(args) > 3:
            raise Unsupported("talent scale helper has unsupported arguments")
        scale = value_multiplier * percentage_multiplier
        low, high = fmt_number(args[1], scale), fmt_number(args[2], scale)
        low, high = fmt_number(str(float(low) + value_offset)), fmt_number(str(float(high) + value_offset))
        if floor:
            low, high = str(int(float(low) // 1)), str(int(float(high) // 1))
        if ceil:
            low, high = str(int(-(-float(low) // 1))), str(int(-(-float(high) // 1)))
        if percent:
            high += "%"
        return f"{{{{cts|{low}|{high}}}}}"

    if template == "css":
        if len(args) < 3 or len(args) > 6:
            raise Unsupported("stat scale helper has unsupported arguments")
        stat = args[0].strip(" '\"").capitalize()
        scale = value_multiplier * percentage_multiplier
        low, high = fmt_number(args[1], scale), fmt_number(args[2], scale)
        low, high = fmt_number(str(float(low) + value_offset)), fmt_number(str(float(high) + value_offset))
        optional = []
        if len(args) >= 4:
            power = args[3].strip().strip("'\"")
            optional.append("log" if power == "log" else fmt_number(power))
            optional.extend(fmt_number(arg) for arg in args[4:])
        if percent:
            high += "%"
        return "{{css|" + "|".join([stat, low, high, *optional]) + "}}"

    raise Unsupported(f"unsupported scaling template {template}")


def source_helper(block: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*=\s*function\b", block)
    if not match:
        field = re.search(
            rf"(?m)^\s*{re.escape(name)}\s*=\s*(-?\d+(?:\.\d+)?)\s*,?\s*(?:--[^\n]*)?$",
            block,
        )
        if field:
            return f"return {field.group(1)}"
        raise Unsupported(f"source helper {name} not found")
    body_start = match.end()
    # Current supported helper definitions end at `end,` on the same or later line.
    end_match = re.search(r"\bend\s*,", block[body_start:])
    if not end_match:
        raise Unsupported(f"cannot bound helper {name}")
    return block[body_start : body_start + end_match.start()]


def static_lua_string(expr: str, locals_map: dict[str, str], seen=None) -> str | None:
    """Resolve only literal Lua strings and local aliases to literal strings."""
    expr = strip_lua_comments(expr).strip()
    seen = set() if seen is None else seen
    if expr in locals_map:
        if expr in seen:
            return None
        return static_lua_string(locals_map[expr], locals_map, seen | {expr})
    if expr.startswith("_t"):
        expr = expr[2:].strip()
    if expr.startswith(("'", '"')):
        end = skip_quoted(expr, 0)
        if end != len(expr):
            return None
        value = expr[1:-1]
        for old, new in ((r"\\n", "\n"), (r"\\r", "\r"), (r"\\t", "\t"), (r"\\\"", '"'), (r"\\'", "'"), (r"\\\\", "\\")):
            value = value.replace(old, new)
        return value
    opener = long_bracket(expr, 0)
    if opener:
        body, closer = opener
        end = expr.find(closer, body)
        if end >= 0 and end + len(closer) == len(expr):
            return expr[body:end]
    return None


def render_expression(
    expr: str,
    block: str,
    locals_map: dict[str, str],
    seen=None,
    percent=False,
    value_multiplier: float = 1.0,
    percentage_multiplier: float = 1.0,
    complement_constant: float | None = None,
    value_offset: float = 0.0,
) -> str:
    expr = strip_outer_parens(strip_lua_comments(expr))
    seen = set() if seen is None else seen
    if expr in locals_map and expr not in seen:
        return render_expression(
            locals_map[expr], block, locals_map, seen | {expr}, percent,
            value_multiplier, percentage_multiplier, complement_constant, value_offset,
        )

    # Percent damage multipliers are commonly multiplied by 100 before tformat.
    mul = re.fullmatch(r"(.+?)\s*\*\s*100", expr) or re.fullmatch(r"100\s*\*\s*(.+)", expr)
    if mul:
        return render_expression(
            mul.group(1), block, locals_map, seen, percent=True,
            value_multiplier=value_multiplier,
            percentage_multiplier=percentage_multiplier * 100,
            complement_constant=complement_constant,
            value_offset=value_offset,
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
            value_offset=value_offset,
        )

    offset = re.fullmatch(r"(.+?)\s*([+-])\s*(-?[\d.]+)", expr)
    if offset:
        base, operator, literal = offset.groups()
        offset_value = float(literal) * (1 if operator == "+" else -1)
        return render_expression(
            base, block, locals_map, seen, percent,
            value_multiplier=value_multiplier,
            percentage_multiplier=percentage_multiplier,
            complement_constant=complement_constant,
            value_offset=value_offset + offset_value,
        )

    leading_offset = re.fullmatch(r"(-?[\d.]+)\s*\+\s*(.+)", expr, re.S)
    if leading_offset:
        literal, base = leading_offset.groups()
        return render_expression(
            base, block, locals_map, seen, percent,
            value_multiplier=value_multiplier,
            percentage_multiplier=percentage_multiplier,
            complement_constant=complement_constant,
            value_offset=value_offset + float(literal),
        )

    # Collapse repeated instances of the same value before scalar parsing so
    # operator precedence stays exact, e.g. (damage + damage/3)/2.
    repeated_sum = re.fullmatch(
        r"([A-Za-z_]\w*)\s*\+\s*\1(?:\s*/\s*(\d+(?:\.\d+)?))?", expr
    )
    reversed_repeated_sum = re.fullmatch(
        r"([A-Za-z_]\w*)\s*/\s*(\d+(?:\.\d+)?)\s*\+\s*\1", expr
    )
    if repeated_sum:
        variable, divisor = repeated_sum.groups()
        ratio = 1 + (1 / float(divisor) if divisor else 1)
        return render_expression(
            variable, block, locals_map, seen, percent,
            value_multiplier=value_multiplier * ratio,
            percentage_multiplier=percentage_multiplier,
            complement_constant=complement_constant,
            value_offset=value_offset * ratio,
        )
    if reversed_repeated_sum:
        variable, divisor = reversed_repeated_sum.groups()
        ratio = 1 + 1 / float(divisor)
        return render_expression(
            variable, block, locals_map, seen, percent,
            value_multiplier=value_multiplier * ratio,
            percentage_multiplier=percentage_multiplier,
            complement_constant=complement_constant,
            value_offset=value_offset * ratio,
        )

    # Carry literal multiplication/division through helper-based scaling. This
    # covers values such as damage / 10 and a stat-scaled value multiplied by 4.
    symbolic_steam_formula = re.fullmatch(
        r"\(?\s*(-?\d+(?:\.\d+)?)\s*\+\s*(?:self:)?combatTalentSteamDamage\s*\((.*?)\)\s*\)?\s*/\s*(\d+(?:\.\d+)?)",
        expr,
        re.S,
    )
    scalar = re.fullmatch(r"(.+?)\s*([*/])\s*(-?[\d.]+)", expr)
    scalar_left = re.fullmatch(r"(-?[\d.]+)\s*\*\s*(.+)", expr)
    if (scalar or scalar_left) and not symbolic_steam_formula:
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
            value_offset=value_offset * factor,
        )

    floor = ceil = False
    m = re.fullmatch(r"math\.(floor|ceil)\s*\((.*)\)", expr, re.S)
    if m:
        floor, ceil = m.group(1) == "floor", m.group(1) == "ceil"
        expr = strip_outer_parens(m.group(2))

    # These bounds are common around scaling helpers. Preserve only when the
    # monotone helper's full endpoint range proves bound cannot change result.
    clamp = re.fullmatch(r"math\.(min|max)\s*\((.*)\)", expr, re.S)
    if clamp:
        kind = clamp.group(1)
        args = split_args(clamp.group(2))
        if len(args) != 2:
            raise Unsupported(f"unsupported math.{kind} arguments")
        literal_index = next((i for i, arg in enumerate(args) if re.fullmatch(r"-?\d+(?:\.\d+)?", arg)), None)
        if literal_index is None:
            raise Unsupported(f"math.{kind} needs one literal bound")
        bound = float(args[literal_index])
        inner = args[1 - literal_index]
        if floor:
            inner = f"math.floor({inner})"
        elif ceil:
            inner = f"math.ceil({inner})"
        rendered = render_expression(
            inner, block, locals_map, seen, percent,
            value_multiplier=value_multiplier,
            percentage_multiplier=percentage_multiplier,
            complement_constant=complement_constant,
            value_offset=value_offset,
        )
        if kind == "min" and re.fullmatch(r"\{\{gtl\|1\|5\|raw\}\}", rendered):
            if 1 <= bound <= 5 and bound.is_integer():
                return f"{{{{gtl|1|{int(bound)}|raw}}}}"
            raise Unsupported("unsupported raw talent-level clamp")
        numeric_args = re.fullmatch(r"\{\{(?:cts|ctl|ctwd|ctspd|ctmd|ctstmd|ctpd|ctstd|css)\|([^{}]+)\}\}", rendered)
        if not numeric_args:
            raise Unsupported(f"cannot prove math.{kind} clamp is inert")
        values = []
        for value in numeric_args.group(1).split("|"):
            numeric = re.fullmatch(r"(-?\d+(?:\.\d+)?)(?:%)?", value)
            if numeric:
                values.append(float(numeric.group(1)))
        if not values or not (all(v >= bound for v in values) if kind == "max" else all(v <= bound for v in values)):
            raise Unsupported(f"math.{kind} clamp changes scaling endpoints")
        return rendered

    # Preserve affine steam-damage formulas symbolically. Mecharachnid's
    # percentage is (60 + steam damage) / 7; flattening to template endpoints
    # would lose the Spellpower-dependent scaling semantics.
    formula = symbolic_steam_formula
    if formula:
        constant, helper_args, divisor = formula.groups()
        scaling = render_expression(
            f"self:combatTalentSteamDamage({helper_args})", block, locals_map, seen,
            value_multiplier=value_multiplier,
            percentage_multiplier=percentage_multiplier,
            complement_constant=complement_constant,
            value_offset=value_offset,
        )
        if not scaling.startswith("{{ctstmd|"):
            raise Unsupported("steam damage formula did not resolve to ctstmd")
        if percent:
            scaling = scaling[:-2] + "%}}"
            constant = fmt_number(constant) + "%"
        return f"({constant} + {scaling})/{fmt_number(divisor)}"

    if has_unsupported_top_level_arithmetic(expr):
        raise Unsupported(f"unsupported arithmetic expression: {expr}")

    if re.fullmatch(r"-?\d+(?:\.\d+)?", expr):
        value = float(expr) * value_multiplier * percentage_multiplier + value_offset
        if complement_constant is not None:
            value = complement_constant - value
        rendered = fmt_number(str(value))
        return rendered + "%" if percent else rendered

    if re.fullmatch(r"-?\d+(?:\.\d+)?", expr):
        return fmt_number(str(float(expr) + value_offset))

    if expr.startswith("damDesc"):
        args = call_args(expr, "damDesc")
        if not args:
            raise Unsupported("cannot parse damDesc")
        # Damage type is already named in source prose; damDesc supplies numeric value.
        return render_expression(
            args[-1], block, locals_map, seen, percent, value_multiplier,
            percentage_multiplier, complement_constant, value_offset,
        )

    helper_match = re.search(r"(?:\bt\.|\bself:)(get[A-Za-z0-9_]+)\s*\(", expr)
    if not helper_match:
        helper_match = re.search(r"\bt:(_get[A-Za-z0-9_]+)\s*\(", expr)
    if helper_match:
        name = helper_match.group(1)
        if name in ("getTalentLevel", "getTalentLevelRaw"):
            args = call_args(expr, name) or []
            if args and args[-1].strip() == "t":
                suffix = "|raw" if name == "getTalentLevelRaw" else ""
                return f"{{{{gtl|1|5{suffix}}}}}"
            raise Unsupported("unsupported getTalentLevel arguments")
        if name.startswith("_get"):
            name = name[1:]
        name = {'getTalentRadius': 'radius', 'getTalentRange': 'range'}.get(name, name)
        helper_marker = f"helper:{name}"
        if helper_marker in seen:
            raise Unsupported(f"recursive source helper: {name}")
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
        helper_expr = re.sub(r"\s+end\s*$", "", strip_lua_comments(ret.group(1).strip()))
        # Lua uses only first result when a multi-return call is not final
        # tformat argument; helpers like getPower may return extra values.
        helper_expr = split_args(helper_expr)[0]
        return render_expression(
            helper_expr, block, locals_map, seen | {helper_marker}, percent,
            value_multiplier, percentage_multiplier, complement_constant, value_offset,
        )

    if not helper_match:
        talent_method = re.search(r"\bt\.([A-Za-z_]\w*)\s*\(", expr)
        if talent_method:
            name = talent_method.group(1)
            helper_marker = f"helper:{name}"
            if helper_marker in seen:
                raise Unsupported(f"recursive source helper: {name}")
            helper = source_helper(block, name)
            ret = re.search(r"\breturn\s+(.+)$", helper, re.S)
            if not ret:
                raise Unsupported(f"cannot resolve helper {name}")
            helper_expr = re.sub(r"\s+end\s*$", "", strip_lua_comments(ret.group(1).strip()))
            helper_expr = split_args(helper_expr)[0]
            return render_expression(
                helper_expr, block, locals_map, seen | {helper_marker}, percent,
                value_multiplier, percentage_multiplier, complement_constant, value_offset,
            )

    helper_specs = (
        ("combatTalentWeaponDamage", "ctwd"),
        ("combatTalentSpellDamage", "ctspd"),
        ("combatTalentMindDamage", "ctmd"),
        ("combatTalentSteamDamage", "ctstmd"),
        ("combatTalentPhysicalDamage", "ctpd"),
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
            complement_constant, value_offset,
        )

    if re.fullmatch(r"[A-Za-z_]\w*", expr):
        helper_marker = f"helper:{expr}"
        if helper_marker in seen:
            raise Unsupported(f"recursive source helper: {expr}")
        try:
            helper = source_helper(block, expr)
        except Unsupported as error:
            if str(error) != f"source helper {expr} not found":
                raise
        else:
            ret = re.search(r"\breturn\s+(.+)$", helper, re.S)
            if not ret:
                raise Unsupported(f"cannot resolve helper {expr}")
            helper_expr = re.sub(r"\s+end\s*$", "", strip_lua_comments(ret.group(1).strip()))
            helper_expr = split_args(helper_expr)[0]
            return render_expression(
                helper_expr, block, locals_map, seen | {helper_marker}, percent,
                value_multiplier, percentage_multiplier, complement_constant, value_offset,
            )

    raise Unsupported(f"unsupported interpolation expression: {expr}")


def split_info(block: str):
    info = re.search(r"\binfo\s*=\s*function\b", block)
    if not info:
        raise Unsupported("info function missing")
    tail = block[info.end() :]
    ret = re.search(r"\breturn\s*\(?\s*(?:_t\s*)?\[(=*)\[(.*?)\]\1\]", tail, re.S)
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
    tokens = list(re.finditer(r"%(?:0)?\.\d+f|%d|%f|%s|%%", source_text))
    value_placeholders = sum(token.group() not in ("%%", "%s") for token in tokens)
    required_placeholders = sum(token.group() != "%%" for token in tokens)
    if len(args) < required_placeholders:
        raise Unsupported(f"format argument mismatch: {value_placeholders} value placeholders, {len(args)} args")

    rank_colors = {
        name for name in re.findall(r"(?m)^\s*local\s+_,\s*(color_[A-Za-z0-9_]+)\s*=\s*self:textRank\s*\(", block)
    }
    dynamic_string_tokens = []
    string_values = {}
    has_rank_colors = False
    arg_cursor = 0
    for token in tokens:
        if token.group() == "%%":
            continue
        if token.group() == "%s":
            arg = args[arg_cursor].strip()
            if arg in rank_colors:
                string_values[arg_cursor] = ""
                has_rank_colors = True
            else:
                value = static_lua_string(arg, locals_map)
                if value is None:
                    dynamic_string_tokens.append(token)
                else:
                    string_values[arg_cursor] = value
        arg_cursor += 1
    if dynamic_string_tokens:
        raise Unsupported("dynamic %s formatting is unsupported")
    if has_rank_colors:
        source_text = source_text.replace("#LAST#", "")
        tokens = list(re.finditer(r"%(?:0)?\.\d+f|%d|%f|%s|%%", source_text))

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
        if token.group() == "%s":
            result.append(string_values.get(arg_index, ""))
            arg_index += 1
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
    inline = re.search(rf"(\|\s*{re.escape(key)}\s*=\s*)([^|\r\n}}]*)", box)
    if inline:
        return box[: inline.start(2)] + value + box[inline.end(2) :]
    first_newline = box.find("\n")
    if first_newline < 0:
        opener = re.match(r"\{\{\s*Ability(?:_|\s)box\b", box, re.I)
        if not opener:
            raise Unsupported("Ability_box opener is not recognized")
        return box[: opener.end()] + f"|{key}={value}" + box[opener.end() :]
    return box[: first_newline + 1] + f"|{key}={value}\n" + box[first_newline + 1 :]


def set_desc(box: str, description: str) -> str:
    match = re.search(r"\|\s*desc\s*=", box)
    if not match:
        if not description.strip():
            return box
        closing = box.rfind("}}")
        if closing < 0:
            raise Unsupported("Ability_box close missing")
        prefix = box[:closing]
        separator = "" if prefix.endswith("\n") else "\n"
        return prefix + separator + "|desc=\n" + description + "\n" + box[closing:]
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
    match = re.search(r"\|\s*desc\s*=", box)
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


def preserve_wiki_links(current: str | None, rendered: str) -> str:
    """Retain existing links/typed damage markup when source prose has same label."""
    if not current:
        return rendered
    markup = re.compile(
        r"\{\{(?:t|c|ct)\|[^{}]+\}\}|\{\{(?:physical|fire|lightning|darkness|acid|cold|ice|nature|temporal|blight|light|arcane|mind)\}\}|\[\[[^\[\]]+\]\]",
        re.I,
    )
    result = rendered
    for match in markup.finditer(current):
        token = match.group(0)
        if token.startswith("[["):
            target = token[2:-2]
            label = target.split("|", 1)[-1]
        elif token.startswith("{{") and "|" in token:
            label = token[2:-2].split("|", 1)[1]
        elif token.startswith("{{"):
            label = token[2:-2]
        else:
            continue
        if not label or token in result:
            continue
        plain = re.search(re.escape(label), result, re.I)
        if plain:
            result = result[:plain.start()] + token + result[plain.end():]
    return result


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
            forbidden_version = re.search(r'(?m)^\s*\|\s*forbidden_version\s*=', candidate_box)
            if forbidden_version and meta.get('addon_version'):
                candidate_box = set_param(candidate_box, 'forbidden_version', meta['addon_version'])
        current_desc = current_description(candidate_box)
        desc = preserve_wiki_links(current_desc, desc)
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
            'updated_fields': [key for key in ('game_version', 'embers_version', 'ashes_version', 'forbidden_version', 'desc')
                               if key_parameter_changed(box, candidate_box, key)],
            'preserved_wiki_notes': [
                {'field': 'desc', 'text': note, 'review_status': 'needs_review_1.7.6'}
                for note in notes
            ],
            'before_wikitext': before, 'after_wikitext': after,
        }
    except (Unsupported, subprocess.CalledProcessError, OSError) as error:
        return {**common, 'status': 'unsupported_info', 'error': str(error)}


def build_skipped_candidate(directory: Path):
    """Build a non-parsing record for a page with deterministic mode=skip."""
    meta = json.loads((directory / 'metadata.json').read_text(encoding='utf-8'))
    before = (directory / 'page').read_text(encoding='utf-8')
    return {
        'title': meta['wiki_title'], 'filename': meta['filename'],
        'pageid': meta['pageid'], 'revid': meta['revid'],
        'source_file': meta['source_file'],
        'source_lines': [meta['source_start_line'], meta['source_end_line']],
        'source_sha256': meta['source_sha256'], 'talent_id': meta['talent_id'],
        'source_match_status': meta['source_match_status'],
        'before_wikitext': before, 'after_wikitext': before,
        'status': 'skipped_by_review',
    }


class WorkApplicationError(ValueError):
    """Work application failed preflight; no work files were written."""


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_work_target(work_dir: Path, filename: str, leaf: str) -> Path:
    target = review_path(work_dir, filename).with_name(leaf)
    root = work_dir.resolve()
    resolved = target.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise WorkApplicationError(f'work path escapes --work-dir: {target}')
    if target.is_symlink():
        raise WorkApplicationError(f'refusing symlink work target: {target}')
    return target


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def validate_work_dir(work_dir: Path) -> Path:
    """Reject work roots whose talent tree could touch the baseline tree."""
    work_talents = (Path(work_dir) / 'talents').resolve(strict=False)
    baseline_talents = (ROOT / 'talents').resolve(strict=False)
    if _paths_overlap(work_talents, baseline_talents):
        raise WorkApplicationError(
            f'--work-dir talents path overlaps baseline talents: {work_talents}'
        )
    return Path(work_dir)


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='wb', dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp', delete=False,
        ) as stream:
            temp_name = stream.name
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name is not None and os.path.exists(temp_name):
            os.unlink(temp_name)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _normalized_newlines(value: str) -> str:
    return value.replace('\r\n', '\n').replace('\r', '\n')


def _jsonl_bytes(records: list[dict]) -> bytes:
    return ''.join(json.dumps(record, ensure_ascii=False) + '\n' for record in records).encode('utf-8')


def _prepare_jsonl_output(output: Path, records: list[dict], plans: list[dict], work_dir: Path) -> Path:
    """Validate destination and stage complete JSONL before work-tree mutation."""
    output = Path(output)
    output_path = output.resolve(strict=False)
    baseline_root = (ROOT / 'talents').resolve(strict=False)
    work_talents_root = (Path(work_dir) / 'talents').resolve(strict=False)
    protected_paths = {
        path.resolve(strict=False)
        for plan in plans
        for path in (plan['page_target'], plan['review_target'])
    }
    protected_paths.update({baseline_root, work_talents_root})
    if any(_paths_overlap(output_path, path) for path in protected_paths):
        raise WorkApplicationError(f'JSONL output conflicts with protected page/review path: {output}')
    if output.is_symlink():
        raise WorkApplicationError(f'refusing symlink JSONL output: {output}')

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not output.is_file():
        raise WorkApplicationError(f'JSONL output is not a regular file: {output}')

    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='wb', dir=output.parent, prefix=f'.{output.name}.', suffix='.tmp', delete=False,
        ) as stream:
            temp_name = stream.name
            stream.write(_jsonl_bytes(records))
            stream.flush()
            os.fsync(stream.fileno())
        return Path(temp_name)
    except Exception:
        if temp_name is not None and os.path.exists(temp_name):
            os.unlink(temp_name)
        raise


def candidate_page_bytes(baseline_bytes: bytes, before_wikitext: str, after_wikitext: str) -> bytes:
    """Apply rendered Ability_box while retaining exact baseline bytes outside it."""
    baseline_text = baseline_bytes.decode('utf-8')
    if _normalized_newlines(baseline_text) != _normalized_newlines(before_wikitext):
        raise WorkApplicationError('candidate baseline text does not match baseline page')
    before_start, before_end, before_box = extract_box(baseline_text)
    after_start, after_end, after_box = extract_box(after_wikitext)
    if (
        _normalized_newlines(baseline_text[:before_start])
        != _normalized_newlines(after_wikitext[:after_start])
        or _normalized_newlines(baseline_text[before_end:])
        != _normalized_newlines(after_wikitext[after_end:])
    ):
        raise WorkApplicationError('candidate changed text outside Ability_box')
    if _normalized_newlines(before_box) == _normalized_newlines(after_box):
        return baseline_bytes
    start_byte = len(baseline_text[:before_start].encode('utf-8'))
    end_byte = len(baseline_text[:before_end].encode('utf-8'))
    return baseline_bytes[:start_byte] + after_box.encode('utf-8') + baseline_bytes[end_byte:]


def _review_skip(work_dir: Path, metadata: dict) -> bool:
    path = _safe_work_target(work_dir, metadata['filename'], 'review.json')
    if not path.exists():
        return False
    review = load_review(path, metadata)
    return review['automation']['deterministic']['mode'] == 'skip'


def render_directory(directory: Path, tengine_root: Path, core_tag: str, *, work_dir=None):
    """Render one baseline directory, bypassing source parsing for human skip."""
    if work_dir is not None:
        metadata = json.loads((directory / 'metadata.json').read_text(encoding='utf-8'))
        if _review_skip(Path(work_dir), metadata):
            return build_skipped_candidate(directory)
    return build_candidate(directory, tengine_root, core_tag)


def plan_work_application(records: list[dict], work_dir: Path) -> list[dict]:
    """Validate all records/targets and prepare write operations without mutation."""
    work_dir = validate_work_dir(Path(work_dir))
    plans = []
    seen = set()
    seen_identity = set()
    protected_states = {'keep', 'ignore', 'defer', 'mark_out_of_date'}
    protected_workflows = {
        'approved', 'finalized', 'rejected', 'blocked',
        'needs_human_review', 'needs_source_review',
    }
    for candidate in records:
        filename = candidate.get('filename')
        if not isinstance(filename, str) or filename in seen:
            raise WorkApplicationError(f'missing or duplicate candidate identity: {filename!r}')
        seen.add(filename)
        review_target = _safe_work_target(work_dir, filename, 'review.json')
        page_target = _safe_work_target(work_dir, filename, 'page')
        baseline_dir = ROOT / 'talents' / filename
        metadata = json.loads((baseline_dir / 'metadata.json').read_text(encoding='utf-8'))
        canonical_identity = identity_from_metadata(metadata)
        identity = tuple(canonical_identity[key] for key in ('kind', 'filename', 'wiki_title', 'pageid'))
        if (
            identity in seen_identity
            or metadata.get('filename') != filename
            or candidate.get('pageid') != metadata.get('pageid')
            or candidate.get('title', metadata.get('wiki_title')) != metadata.get('wiki_title')
        ):
            raise WorkApplicationError(f'candidate identity does not match baseline metadata: {filename}')
        seen_identity.add(identity)
        baseline_bytes = (baseline_dir / 'page').read_bytes()
        baseline_hash = sha256(baseline_bytes)
        if review_target.exists():
            try:
                review = load_review(review_target, metadata)
            except ReviewRecordError as error:
                raise WorkApplicationError(str(error)) from error
            if review['baseline']['page_sha256'] != baseline_hash:
                raise WorkApplicationError(f'baseline page hash conflict: {filename}')
        else:
            review = initialize_review(
                metadata, baseline_bytes, state='update', workflow_status='queued',
            )

        current_bytes = page_target.read_bytes() if page_target.exists() else None
        managed_hash = review['work_page']['last_managed_sha256']
        if current_bytes is not None:
            if managed_hash is None or sha256(current_bytes) != managed_hash:
                raise WorkApplicationError(f'work page hash conflict: {page_target}')
        elif managed_hash is not None:
            raise WorkApplicationError(f'managed work page is missing: {page_target}')

        status = candidate.get('status')
        if status not in {'candidate', 'no_change', 'unsupported_info', 'unmatched_source', 'skipped_by_review'}:
            raise WorkApplicationError(f'unsupported candidate status for {filename}: {status!r}')
        skip = status == 'skipped_by_review'
        protected = (
            review['state'] in protected_states
            or review['workflow_status'] in protected_workflows
            or (
                current_bytes is not None
                and review['work_page']['last_writer'] != 'deterministic_renderer'
            )
        )
        if skip:
            rendered_bytes = None
            page_bytes = None
            application_status = 'skipped_by_review'
        elif status in {'unsupported_info', 'unmatched_source'}:
            rendered_bytes = baseline_bytes
            page_bytes = None if protected else rendered_bytes
            application_status = 'not_applied_human_state' if protected else 'baseline_preserved'
        else:
            rendered_bytes = candidate_page_bytes(
                baseline_bytes, candidate['before_wikitext'], candidate['after_wikitext'],
            )
            page_bytes = None if protected else rendered_bytes
            application_status = 'not_applied_human_state' if protected else 'applied'

        page_changed = page_bytes is not None and current_bytes != page_bytes
        desired_page_hash = sha256(page_bytes) if page_bytes is not None else None
        candidate_page_hash = sha256(rendered_bytes) if rendered_bytes is not None else None
        render = {
            'application_status': application_status,
            'source_sha256': metadata['source_sha256'],
            'candidate_page_sha256': candidate_page_hash,
        }
        render['status'] = 'no_change' if skip else status
        existing_render = review.get('render', {})
        render_changed = any(existing_render.get(key) != value for key, value in render.items())
        review_changed = not review_target.exists() or render_changed or page_changed
        if render_changed:
            render['updated_at'] = _utc_now()
        elif 'updated_at' in existing_render and not skip:
            render['updated_at'] = existing_render['updated_at']

        event = None
        if review_changed:
            event = {
                'at': _utc_now(), 'actor': 'deterministic_renderer',
                'event': 'deterministic_render_applied' if page_bytes is not None else 'deterministic_render_recorded',
                'reason': application_status,
            }
        updated_review = None
        if review_changed:
            updated_review = merge_owned_fields(
                review, 'renderer', {'render': render},
                history_events=[event] if event else (),
            )
            if page_bytes is not None:
                updated_review['work_page'] = {
                    **updated_review['work_page'],
                    'last_managed_sha256': desired_page_hash,
                    'last_writer': 'deterministic_renderer',
                }
            validate_review(updated_review, metadata)
        plans.append({
            'filename': filename, 'metadata': metadata, 'review_target': review_target,
            'page_target': page_target, 'baseline_bytes': baseline_bytes,
            'review': review, 'render': render, 'event': event,
            'updated_review': updated_review,
            'page_bytes': page_bytes, 'page_changed': page_changed,
            'review_changed': review_changed, 'desired_page_hash': desired_page_hash,
            'current_bytes': current_bytes,
        })
    return plans


def apply_work_candidates(records: list[dict], work_dir: Path) -> list[Path]:
    """Preflight entire batch, then atomically write pages and renderer-owned review data."""
    plans = plan_work_application(records, work_dir)
    return apply_work_plan(plans)


def apply_work_plan(plans: list[dict]) -> list[Path]:
    """Apply an already validated, complete work write plan."""
    touched = []
    for plan in plans:
        if plan['review_changed']:
            if plan['page_changed']:
                _atomic_write(plan['page_target'], plan['page_bytes'])
                touched.append(plan['page_target'])
            write_review(plan['review_target'], plan['updated_review'], plan['metadata'])
            touched.append(plan['review_target'])
        elif plan['page_changed']:
            # Defensive: page changes always count as a review change above.
            raise WorkApplicationError(f'internal plan error for {plan["filename"]}')
    return touched


def key_parameter_changed(before_box: str, after_box: str, key: str) -> bool:
    if key == 'desc':
        return current_description(before_box) != current_description(after_box)
    else:
        pattern = rf'(?m)^\s*\|\s*{re.escape(key)}\s*=.*$'
    before_match = re.search(pattern, before_box)
    after_match = re.search(pattern, after_box)
    if not before_match:
        before_match = re.search(rf"\|\s*{re.escape(key)}\s*=\s*([^|\r\n}}]*)", before_box)
    if not after_match:
        after_match = re.search(rf"\|\s*{re.escape(key)}\s*=\s*([^|\r\n}}]*)", after_box)
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
    parser.add_argument('--work-dir', type=Path, help='explicit work batch root')
    parser.add_argument('--apply', action='store_true', help='write candidates into --work-dir')
    args = parser.parse_args()

    if args.apply and args.work_dir is None:
        parser.error('--apply requires --work-dir')
    if args.work_dir is not None:
        try:
            validate_work_dir(args.work_dir)
        except WorkApplicationError as error:
            parser.error(str(error))

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

    records = []
    for directory in dirs:
        if args.apply:
            try:
                record = render_directory(
                    directory, args.t_engine_root, args.core_tag, work_dir=args.work_dir,
                )
            except (ReviewRecordError, WorkApplicationError) as error:
                parser.error(str(error))
        else:
            record = render_directory(directory, args.t_engine_root, args.core_tag)
        records.append(record)

    if args.apply:
        staged_output = None
        try:
            plans = plan_work_application(records, args.work_dir)
            staged_output = _prepare_jsonl_output(args.output, records, plans, args.work_dir)
            touched = apply_work_plan(plans)
            os.replace(staged_output, args.output)
            staged_output = None
        except (ReviewRecordError, WorkApplicationError, OSError, ValueError) as error:
            print(f'Work application refused: {error}', file=sys.stderr)
            return 2
        finally:
            if staged_output is not None and staged_output.exists():
                staged_output.unlink()
        print(f'Applied work records: {len(touched)} files changed under {args.work_dir}')

    if not args.apply:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('w', encoding='utf-8', newline='\n') as stream:
            stream.write(_jsonl_bytes(records).decode('utf-8'))
    counts = {}
    for record in records:
        counts[record['status']] = counts.get(record['status'], 0) + 1
    print(f'Wrote {len(records)} review-only records to {args.output}: {counts}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
