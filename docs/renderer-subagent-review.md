# Reviewing unsupported renderer cases with an LLM

Use this workflow only for renderer records marked `unsupported_info`. LLM
output is a review draft, never a renderer result or wiki edit. Keep bulk
rendering deterministic and fail-closed.

## Build a review packet

The packet builder selects unsupported records, verifies source hashes, and
includes current wiki wikitext plus the exact talent definition. Core source is
read from `tome-1.7.6`; DLC source is read from local addon trees.

```bash
python3 -B scripts/render_candidates.py --title 'Example Talent (talent)' \
  --output /tmp/opencode/renderer-results.jsonl
python3 -B scripts/build_renderer_review_packet.py \
  /tmp/opencode/renderer-results.jsonl \
  --output renderer_reviews/unresolved.input.jsonl
```

Use `--title 'Talent name (talent)'` once per requested record to narrow a
packet. Trace helper/effect dependencies cited by the talent definition; do not
add unrelated full source files to the packet.

## Subagent prompt

Use `.opencode/agents/renderer-reviewer.md` from the primary session. Assign one
packet per call or a small batch; ask for the documented JSONL response and save
it separately as `renderer_reviews/<batch>.draft.jsonl` only after reviewing
the response. Agent has read-only project/source access and cannot edit files.

```text
Review each record in the assigned renderer-review JSONL packet independently.
For each, compare current Ability_box text with exact versioned source and trace
only helpers/effects needed to calculate its description.

Goal: produce the most source-faithful description possible, with minimal
wording changes. Strongly prefer existing/source wording. Do not editorialize,
add advice, explain mechanics beyond source, or invent a default character
state. Calculate exact constants and scaling endpoints from source. Put
calculations and caveats in JSON fields, never in proposed wiki prose. Do not
put raw Lua, pseudo-code, or unsupported template syntax in a description. Use
only known wiki Scaling templates when their arguments exactly represent the
calculation; otherwise preserve current wording or mark the case unresolved.

Keep assumptions outside proposed wiki text. A value depending on Cunning,
Paradox, another talent, equipment, active stance, or conditional talent must
not be replaced by one guessed number. Give a best guess only when source and
explicit assumptions support it; label it as a draft. If no unique value can be
derived, return unresolved rather than guessing.

Preserve existing links, damage-type markup, formatting, and wiki-only notes.
List any wiki-only paragraphs that proposed text would remove; do not silently
drop them. Do not change version fields, other Ability_box parameters, page
text, source code, or renderer code. Do not publish or commit.

Return exactly one JSON object per input record, with these keys:
{
  "title": string,
  "status": "draft" | "unchanged" | "unresolved",
  "proposed_desc": string | null,
  "calculations": [{"claim": string, "source_lines": [string], "derivation": string}],
  "assumptions": [string],
  "wiki_only_text_to_preserve": [string],
  "unresolved_reason": string | null
}

Keep derivations terse and source-cited. No prose outside JSONL. Do not claim
renderer support; human must verify all calculated values before changing pages
or renderer.
```

## Review discipline

- Provide the packet, this prompt, and source access; ask one reviewer to handle
  a small batch while treating each talent independently. This follows the
  project's file-by-file review preference without spawning one agent per
  talent.
- Include directly referenced shared helpers only when needed. Respect source
  provenance: core mechanics must come from the 1.7.6 tag; addon code comes from
  the specified local addon tree and its recorded hash.
- Do not use Tometips `info_text` values to derive template endpoints.
- Keep subagent output separate from page files. Verify every endpoint, unit,
  cap, condition, and preserved note against source before use.
