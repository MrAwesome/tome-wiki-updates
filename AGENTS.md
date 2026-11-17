# ToME wiki talent renderer

This repository generates and reviews best-effort `Ability_box` description
candidates that closely match what ToME displays in game. Exact reconstruction
is not required; small discrepancies are acceptable. Generate candidates for
human review only; never publish or rewrite wiki pages automatically.

## Source authority

- Core target is ToME 1.7.6. The sibling `~/code/t-engine4` checkout is newer
  than that release; read core source from tag `tome-1.7.6`, commit
  `624a67329fe2ad440c5b344785a9c73fcf22ae63`.
- DLC talent text comes from local addon trees under
  `~/code/t-engine4/game/addons/`, as project decision. Record exact file hash,
  addon ID, compatibility version, and addon version.
- Use ToME 1.7.6 source as strongest evidence for current mechanics and numeric
  scaling. Tometips is an accepted reference for in-game wording and a useful
  guide to ambiguous formatting, `%s` fragments, and rendered descriptions.
  Account for its version when applying it to 1.7.6; do not present a Tometips
  value as an exact 1.7.6 scaling endpoint unless source supports it. A
  source-grounded best effort is preferable to leaving a clearly stale
  description untouched solely because exact reconstruction is difficult.

## Renderer and review

- Talent metadata and current page wikitext live under `talents/<slug>/`.
- Deterministic renderer must fail closed on unsupported expressions. Do not
  silently invent values or let an LLM extend renderer support. Unsupported
  cases may use reviewed LLM drafts or explicit per-talent preprocessing when
  those produce useful candidates more cheaply than generic renderer work.
- Preprocessing must be reviewable and fail closed: scope each transformation
  to talent and source provenance, match expected source context exactly, record
  intent, and test patched input and output. Prefer focused patch deltas to
  duplicated source files. Never transform upstream source in place or silently
  change mechanics. See `docs/source-preprocessing.md`.
- Preserve wiki-only notes in `extras.json`, not `metadata.json`. Keep current
  wiki version fields in extras with exact presence rules: omit
  `wiki_game_version` when page has no `game_version`; omit `wiki_dlc_versions`
  for core talents; for DLC talents, use an object of listed DLC fields, or
  `null` only when no DLC version field is listed. Wiki DLC fields are
  `embers_version`, `ashes_version`, and `forbidden_version`.
- Preserve human-added wiki notes, markup, page layout, and all text outside the
  `Ability_box`. Remove a note only when evidence shows it is inaccurate or
  obsolete (for example, it documents behavior since fixed); explain the reason
  for review. If unsure, preserve it and flag for a human. Candidate page
  changes remain review-only and uncommitted until explicitly approved.
- Use LLM review source-file-by-source-file, not one agent per talent. For
  unsupported renderer cases use
  `docs/renderer-subagent-review.md` and the read-only `renderer-reviewer`
  subagent. Treat its output as a draft; verify material mechanics against
  source where feasible and flag uncertainty rather than implying false
  precision.

See `README.md` and `docs/renderer-subagent-review.md` for workflows.
