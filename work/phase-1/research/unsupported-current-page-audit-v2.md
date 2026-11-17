# Unsupported description audit v2

## Scope and method

- Re-audited **551 unsupported renderer entries** against version-correct source `info()` excerpts, current wiki `Ability_box` text, each talent’s `tometips-1.7.4-1.0`, and the wiki `game_version` field.
- Core source authority: ToME 1.7.6 tag/commit `624a67329fe2ad440c5b344785a9c73fcf22ae63`; DLC source authority: local addon files and recorded provenance/hash in each JSONL row.
- Tometips used only as an inclusion/omission and wording cross-check. Bracketed values were not used as renderer endpoints.
- Missing or non-1.7.6 wiki version treated only as staleness signal. Current page version counts: 100 listed 1.7.6; 211 listed older/other version; 240 absent.
- Results are LLM triage recommendations, not approved text or renderer changes. No candidate `page` or `review.json` changed. Mechanics sidecars were filtered to source in-game-description versus implementation discrepancies; they do not compare existing wiki text.

## Summary

| Dimension | Draft counts |
| --- | --- |
| Source-text decision | include 180; ignore runtime fragment 173; omit where both Tometips/wiki omit 23; preserve current 8; human review 167 |
| Wiki comparison | matches source 275; matches Tometips omission 11; omits source fragment 88; diverges 148; raw placeholder 24; no comparable info 5 |
| Tometips comparison | matches source 121; omits source fragment 39; dynamic/staticized/runtime content 374; diverges 15; no comparable info 2 |
| Suggested renderer route | generic rule 225; talent-specific rule 118; LLM candidate 13; preserve current 95; manual review 100 |
| Review confidence | high 363; medium 181; low 7 |

## What version field changes

- **100** pages explicitly list `game_version=1.7.6`; **211** list another version; **240** list no version. Older/unversioned pages deserve stronger stale-page suspicion, but audit decisions still compare text to current source and Tometips rather than declaring pages stale from metadata alone.
- Version and wiki revision ID are retained per row so later edits can prioritize stale pages without losing source/page provenance.

## `%s` cases and Tometips exclusions

- All **60 `%s` failures** were checked against Tometips and existing wiki text. Decision split: include_source_text 18, ignore_dynamic_fragment 18, omit_tometips_and_wiki_excluded 12, needs_human_review 12.
- Runtime-selected spell/prophecy, current curse/ward/tool choices, and character-state values generally should not be frozen as wiki constants. Tometips sometimes prints `None`/current-state values; those are not static source prose.
- Other `%s` insertions carry useful static text or lists despite runtime construction. Preserve source clauses where stable; e.g. schematic inventories, trap effects, nested Throw Boulder/Volcanic Rock info, and talent-specific conditions require separate scrutiny rather than blanket omission.
- Trap pages illustrate cross-check limits: Tometips often staticizes scaling using assumed talent/mastery/stat values; existing wiki may contain raw `%d` placeholders. Neither warrants copying those endpoints. Render deterministic source formulas only from raw source expressions.
- Dynamic `%s` renderer routes: preserve_current 27, manual_review 14, talent_special_case 11, generic_renderer_rule 6, llm_generated_candidate 2.

## Other unsupported expressions and renderer direction

- Recommendations favor generic renderer rules when the unsupported construct is repeated and bounded by explicit source arguments; talent-specific rules for custom helper semantics or embedded sub-talent text; LLM candidates for assembled descriptions whose stable prose can be separated from dynamic state; manual review when source helper/state semantics or comparison evidence remain unclear.
- Suggested routes by broad renderer-error family (draft):
| Error family | Generic rule | Talent-specific | LLM candidate | Preserve current | Manual review |
| --- | ---: | ---: | ---: | ---: | ---: |
| %s formatting | 6 | 11 | 2 | 27 | 14 |
| arithmetic/scaling | 23 | 8 | 1 | 5 | 14 |
| clamp/math.min/max | 36 | 10 | 1 | 5 | 9 |
| format/parser/other | 2 | 3 | 0 | 3 | 2 |
| interpolation | 98 | 58 | 9 | 38 | 39 |
| other | 0 | 1 | 0 | 0 | 0 |
| talent-level forms | 25 | 16 | 0 | 4 | 5 |
| unresolved helper | 35 | 11 | 0 | 13 | 17 |

### Tooling opportunities

1. Add deterministic support for repeated bounded expression families first: safe `math.min`/`math.max` clamps, nested talent-level forms, arithmetic composition, and well-scoped interpolation expressions. Add tests from source patterns; keep unknown calls fail-closed.
2. Add explicit per-talent/helper mappings for cases where descriptions depend on other talents, custom damage helpers, lists, or nested talent info. Require source path/hash and tests for each mapping.
3. Build source-file-grouped LLM drafting packets with source excerpt, wiki version, current page, Tometips text, and explicit instructions to separate stable prose from runtime/current-character output. LLM output remains draft-only; deterministic renderer support changes separately.
4. Define static-wiki policy for runtime values: omit current selections/state, but do not omit static explanatory clauses merely because Lua builds them through `%s`.

## In-game description versus implementation

- The initial side observations were rechecked with packets containing source excerpts only; existing wiki text was excluded. **10 of 124** candidates showed a clear mismatch between in-game `info()` text and implementation and remain in `mechanics-notes.md` under their talent work dirs. The other 114 were removed because they compared wiki text with code or source evidence was unclear.
- These 10 are scoped to the out-of-scope side observation requested earlier. They are not candidate page text or approval to change wiki descriptions.

## Per-entry data

`unsupported-current-page-audit-v2.jsonl` holds one row per title: source provenance/hash/line span, renderer error, wiki version and revision, Tometips hash and comparison, page comparison, text decision, renderer route/opportunity, confidence, and optional source-info-versus-code note. Mechanics sidecars do not assess existing wiki text.

```bash
jq -c 'select(.text_decision == "needs_human_review")' work/phase-1/research/unsupported-current-page-audit-v2.jsonl
jq -c 'select(.renderer_error == "dynamic %s formatting is unsupported")' work/phase-1/research/unsupported-current-page-audit-v2.jsonl
jq -c 'select(.wiki_version_signal != "1.7.6")' work/phase-1/research/unsupported-current-page-audit-v2.jsonl
```

## Limits

Behavior labels, text decisions, renderer routes, and confidence are reviewer triage, not verified implementation instructions. `ignore_dynamic_fragment` means omit dynamic state/value from static wiki text, not discard surrounding stable prose. `preserve_current` means no clear text change recommended, not that page is current or mechanics-correct.
