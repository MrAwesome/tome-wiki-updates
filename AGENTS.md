# ToME wiki talent renderer

This repository owns source-faithful `Ability_box` candidate rendering and
review. Generate candidates for human review only; never publish or rewrite wiki
pages automatically.

## Source authority

- Core target is ToME 1.7.6. The sibling `~/code/t-engine4` checkout is newer
  than that release; read core source from tag `tome-1.7.6`, commit
  `624a67329fe2ad440c5b344785a9c73fcf22ae63`.
- DLC talent text comes from local addon trees under
  `~/code/t-engine4/game/addons/`, as project decision. Record exact file hash,
  addon ID, compatibility version, and addon version.
- Tometips is an index/cross-check only. Never derive scaling endpoints from its
  rendered `info_text`; use raw `combatTalent*` / `combatStat*` source arguments.

## Renderer and review

- Talent metadata and current page wikitext live under `talents/<slug>/`.
- Renderer must fail closed on unsupported expressions. Do not invent values or
  let an LLM silently extend renderer support.
- Preserve wiki-only notes in `extras.json`, not `metadata.json`. Keep current
  wiki version fields in extras with exact presence rules: omit
  `wiki_game_version` when page has no `game_version`; omit `wiki_dlc_versions`
  for core talents; for DLC talents, use an object of listed DLC fields, or
  `null` only when no DLC version field is listed. Wiki DLC fields are
  `embers_version`, `ashes_version`, and `forbidden_version`.
- Preserve existing wiki markup, notes, page layout, and all text outside the
  `Ability_box`. Candidate page changes remain review-only and uncommitted until
  explicitly approved.
- Use LLM review source-file-by-source-file, not one agent per talent. For
  unsupported renderer cases use
  `docs/renderer-subagent-review.md` and the read-only `renderer-reviewer`
  subagent. Treat its output as a draft; verify every value against source.

See `README.md` and `docs/renderer-subagent-review.md` for workflows.
