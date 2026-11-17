# Unsupported current-page audit

## Scope and method

- Audited **551 unsupported records** from `/tmp/opencode/wiki-update-phase-1-render.jsonl` (230 source files).
- Source context came from `build_renderer_review_packet.py`; all packet source hashes passed. Core excerpts use `tome-1.7.6` at `624a67329fe2ad440c5b344785a9c73fcf22ae63`; DLC excerpts use recorded local addon sources.
- Read-only `renderer-reviewer` batches compared source `info()` with current wiki `Ability_box` descriptions. The source-verified packets retain full page/source context in `/tmp/opencode/wiki-update-unsupported-batches/`; the JSONL appendix stores one concise note per talent.
- This is **triage, not approval**: reviewer observations are draft classifications. No candidate `page` or `review.json` was changed; no renderer formulas were inferred or applied.

## Overall triage

- Behavior labels: 268 `covered`, 63 `omits_dynamic`, 200 `partial_or_divergent`, 20 `no_comparable_info`.
- Reviewer confidence: 436 high, 100 medium, 15 low. Confidence describes page/source comparison, not correctness of talent mechanics.
- `60` records fail on dynamic `%s`; draft labels split 20/20/20/0 in the same four behavior classes.
- 8 current wiki descriptions contain raw Lua-style format placeholders (`%d`, `%0.2f`, etc.); these are flagged in per-entry notes where noticed.

## Findings worth attention

### Dynamic `%s` is not one uniform omission

- Some pages omit runtime-selected values or lists: current spell/prophecy/curse state, active ward types, eligible schematic lists, and other conditional text. Some omissions are reasonable for static wiki prose; others lose useful source explanation.
- Other pages already reproduce dynamic content as static wiki text or formulas: tool descriptions, summon/poison lists, eligible spell lists, and rank-based Soul Leech timing. These still need human freshness review when content is runtime- or addon-dependent.
- Several `%s` pages have partial coverage: an optional conditional line or current-state detail is absent even though surrounding source prose appears on the page.

### High-signal mismatches / omissions (spot-checked)

- **Grim future (talent):** current page describes killing a Bleak Outcome target and dazing its allies. Target source describes consuming Bleak Outcome stacks for per-stack darkness damage and a Spellpower buff, then preventing new stacks. Strong page/source divergence; `%s` stack count is also absent.
- **Dynamic schematic lists:** Chemistry, Explosives, Therapeutics, Electricity, Mechanical, and Smith pages show generic schematic-learning rules but omit source-inserted craftable-schematic lists.
- **State-selected spell/prophecy:** Contingency, Empower, Extension, Matrix, Quicken, Grand Oration, Revelation, and Twofold Curse pages omit the current selected spell/prophecy line. Wiki pages describe the general ability, not current character state.
- **Optional trap state:** multiple trap source descriptions append “triggers immediately when placed” only when that trap is currently primed. Static wiki pages generally omit this runtime state. Separate raw damage placeholders remain on several trap pages.
- **Raw placeholders in current pages:** Ambush Trap, Decaying Grounds, Dragonsfire Trap, Freezing Trap, Purging Trap, Springrazor Trap, Deeprock Form, and Venomous Throw. These are page-quality findings, not renderer output.
- **Embedded sub-talent info:** Boulder Rock and Volcanic Rock source descriptions append Throw Boulder/Volcano descriptions; current pages mostly state only that the ability becomes available.
- **Other partial details:** Imbue Item page says body armor only while source conditionally permits more item slots; Throw Bomb page states physical damage though source type follows infusion; Ward page omits current ward choices/charges.

## Other unsupported expression families

Most non-`%s` failures are parser limits around nested formulas, clamps, cross-talent rank, helper resolution, interpolation, or runtime-dependent values. In many such records the current page already contains a human-authored scaling template or prose, so `unsupported_info` means “renderer cannot safely reproduce/verify this description,” not “wiki line is missing.” The per-entry JSONL records distinguish pages apparently covered from possible omissions/divergences; low/medium-confidence rows should be manually checked before editing.

## Per-entry appendix

Each JSONL record includes title, source path/lines, exact renderer error, behavior label, concise page/source observation, confidence, and wiki revision ID. Filter examples:

```bash
jq -c 'select(.behavior == "omits_dynamic")' work/phase-1/research/unsupported-current-page-audit.jsonl
jq -c 'select(.renderer_error == "dynamic %s formatting is unsupported")' work/phase-1/research/unsupported-current-page-audit.jsonl
```

## Error counts

| Renderer error family (overlapping) | Count |
| --- | ---: |
| Dynamic `%s` formatting | 60 |
| Unsupported `getTalentLevel` forms | 50 |
| Clamp / `math.min` / `math.max` cases | 66 |
| Unresolved source helpers | 76 |
| Interpolation expressions | 248 |
| Arithmetic / scaling expressions | 76 |
| Formatting / parser / missing-info cases | 10 |

## Limits

The audit does not certify formulas, scaling endpoints, or gameplay accuracy. `covered` means reviewer found current-page content corresponding to source prose; it does not prove every value is correct. The 551-row appendix is a triage index to focus subsequent human/source verification.
