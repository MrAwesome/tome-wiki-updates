# Deterministic talent renderer: current state

Snapshot: 2026-11-07. This is a coverage snapshot, not a requirement to support
every description deterministically; rerun the renderer for fresh counts.
Project goal is useful best-effort descriptions close to in-game text; small
discrepancies are acceptable.

## Current coverage

Target is ToME 1.7.6 core at commit
`624a67329fe2ad440c5b344785a9c73fcf22ae63`, plus DLC talent text from local
addon trees under `~/code/t-engine4/game/addons/`.

Fresh render produced 1,602 wiki-page records:

| Status | Count | Meaning |
| --- | ---: | --- |
| `candidate` | 921 | Deterministic render differs from current page. |
| `no_change` | 44 | Deterministic render matches current page. |
| `unsupported_info` | 551 | Source matched, but renderer cannot safely evaluate its description. |
| `unmatched_source` | 86 | Wiki page did not map to a unique current source definition. |

Of 921 candidates, 751 change description text and 170 are version-only. Thus
965 pages have deterministic output (921 candidates + 44 no-change); 551 are
matched but unsupported. `unmatched_source` is a mapping result, not proof a
talent was removed. In particular, `not_found` must not be treated as confirmed
deletion without positive evidence.

Regenerate snapshot with:

```bash
python3 -B scripts/render_candidates.py --output /tmp/opencode/renderer-state.jsonl
```

## Existing supported patterns

Keep regression coverage in `scripts/test_render_candidates.py`. Current support
includes talent/stat scaling templates, logarithmic scaling, supported optional
helper arguments, local source-helper resolution, safe arithmetic, selected
inert clamps, static formatted strings, inline or missing descriptions, and
recursive-helper fail-closed checks. Use version-correct source as strongest
evidence for current mechanics and exact numeric scaling. Tometips can guide
wording and interpretation of formatting or `%s` fragments; account for version
differences and avoid presenting uncertain values with false precision.

## High-value unsupported cases

The current JSONL is authoritative for exact per-talent errors. Largest exact
error groups in the 551 unsupported records include:

- 60 dynamic `%s` formatting expressions.
- 20 unsupported `getTalentLevel` argument forms, often another talent's rank.
- 17 `math.min` and 16 `math.max` clamps that the renderer cannot prove inert.
- Remaining cases include helper/stat dependencies, dynamic interpolation,
  composite arithmetic, and format/parser edge cases.

Potential deterministic investigations from prior source review (leads, not
commitments; compare cost with preprocessing and reviewed drafts first):

1. Resolve `%s` values only when the source expression is provably static,
   including local constants such as trap `instant`; preserve runtime-dependent
   strings as unsupported.
2. Support Lua multi-return expansion only in the exact context that expands
   returns, such as a final `tformat` argument. Current helper evaluation uses
   only first return value; do not globally expand helper returns.
3. Support cross-talent rank scaling only if the wiki template can express the
   referenced talent rank unambiguously. Never substitute current talent rank.
4. Keep clamps unsupported unless source and renderer semantics prove clamp
   inert or an existing template exactly represents it.
5. Trace unresolved `getStr`/`getWil`/shield helpers to their actual definitions;
   do not infer endpoints from helper names.

For each proposed renderer feature, weigh reuse and maintenance cost against
reviewed LLM drafts or talent-specific preprocessing. Do not pursue complete
renderer coverage for its own sake. For reusable patterns: trace source,
document semantics, add positive and fail-closed regression tests, then rerun
the full render. For per-talent preprocessing, follow
[`source-preprocessing.md`](source-preprocessing.md). LLM drafts and Tometips
are candidate-generation evidence, not proof of deterministic renderer
semantics.

## Source and page cautions

- Core source must come from tag `tome-1.7.6` at the commit above. DLC source
  uses the local addon files and must retain file hash, addon ID, compatibility
  version, and addon version.
- Keep candidate changes review-only. Never let an LLM silently add renderer
  support or invent precise endpoints.
- Preserve human-added notes and all page text outside the description. Remove
  a note only with evidence that it is inaccurate or obsolete; otherwise flag
  it for human review.
- A wiki page lacking a unique current source match is not automatically
  deleted; distinguish absent, renamed, ambiguous, and confirmed-removed cases.
