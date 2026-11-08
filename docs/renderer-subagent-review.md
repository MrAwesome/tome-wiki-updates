# Reviewing unsupported renderer cases with an LLM

Use this workflow only for renderer records marked `unsupported_info`. LLM
output is a review draft, never a renderer result or wiki edit. Keep bulk
rendering deterministic and fail-closed. Work pages live under an explicit
`work/<batch>/` directory; baseline `talents/` pages stay read-only.

Default to the deterministic renderer. Reach for an LLM only when the renderer
cannot represent the source expression, and treat every returned value as
unverified.

## Build review batches

Render first, then split the unsupported records into batches grouped by source
file. Each packet keeps the exact version-correct talent excerpt plus the full
current wiki page for context.

```bash
python3 -B scripts/render_candidates.py --output /tmp/opencode/renderer-results.jsonl
python3 -B scripts/build_llm_review_batches.py \
  /tmp/opencode/renderer-results.jsonl \
  --output-dir /tmp/opencode/llm-batches
```

`--max-records` and `--max-excerpt-lines` bound each batch. Batches are written
as `batch-NN.jsonl` plus an `index.json` mapping batch to records and source
files. Verify source hashes before dispatching: the builder fails on mismatch.

## Subagent prompt

Use `.opencode/agents/renderer-reviewer.md` and run one batch per call. The agent
can read project/source files and write only inside `/tmp/opencode/llm-drafts/`.

```text
Review `/tmp/opencode/llm-batches/batch-NN.jsonl`. Each record holds the current
wiki page and the exact version-correct talent source. Group your reasoning by
source file and trace only the helpers needed to explain the description.

The packet excerpt is authoritative context: do not re-read those source ranges
and do not read the packet more than twice. Work from the packet text.

For each record, write a compact JSON object:
{
  "title": string,
  "status": "draft" | "unchanged" | "unresolved",
  "proposed_desc": string | null,
  "checkme": string | null
}

`checkme` is a one-sentence reason only when you are highly confident the
current page contains a material error; otherwise null. Put no calculations,
assumptions, or notes in the JSONL and no prose outside it.

Write the complete JSONL to `/tmp/opencode/llm-drafts/batch-NN.draft.jsonl`,
validating before you write. Reply afterwards with only the record count and the
status split. Do not echo drafts back into chat.

Preserve existing links, damage-type markup, formatting, and wiki-only notes.
Prefer minimal wording changes and source wording. Never put raw Lua,
pseudo-code, or unsupported template syntax in a description. Do not change
version fields, other `Ability_box` parameters, or text outside the box.
```

## Apply drafts

```bash
python3 -B scripts/apply_llm_drafts.py \
  /tmp/opencode/renderer-results.jsonl \
  /tmp/opencode/llm-drafts/*.draft.jsonl \
  --work-dir work/phase-1
```

The script refuses titles that are not `unsupported_info`, unknown or duplicate
drafts, baseline hash mismatches, and work pages that differ from their recorded
managed hash. It prints planned changes only by default. Writes require both
`--work-dir` and explicit `--apply`; for example:

```bash
python3 -B scripts/apply_llm_drafts.py \
  /tmp/opencode/renderer-results.jsonl \
  /tmp/opencode/llm-drafts/*.draft.jsonl \
  --work-dir work/phase-1 \
  --apply \
  --report /tmp/opencode/llm-drafts/report.json
```

Draft application edits only `Ability_box` description in the work page,
preserves surrounding page text, records draft provenance and managed hash in
`review.json`, and leaves page in `needs_human_review`. It preserves human state,
notes, and instructions; it never approves a page or changes renderer support.
`checkme` reason remains visible as `XXX CHECKME XXX ` prefix for human review.

Unmatched or missing source is not deletion evidence. Keep page byte-identical
and review render condition. A human may mark page `mark_out_of_date` with
supporting notes after checking source/release; page remains in work area until
human edits and approves final text. No `DELETE` marker or automatic page-text
change is used for this case.

## Review discipline

- Keep drafts out of the primary session context. Read the report and counts,
  not the full JSONL, and never paste draft text into chat.
- One batch per call, grouped by source file. Do not spawn one agent per talent.
- LLM output may not extend renderer capabilities. Unsupported expressions stay
  unsupported until deterministic renderer changes are separately reviewed.
- Include directly referenced shared helpers only when needed. Respect source
  provenance: core mechanics must come from the 1.7.6 tag; addon code comes from
  the specified local addon tree and its recorded hash.
- Do not use Tometips `info_text` values to derive template endpoints.
- Keep subagent output separate from page files. Verify every endpoint, unit,
  cap, condition, and preserved note against source before use.
