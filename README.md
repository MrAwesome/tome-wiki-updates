# ToME wiki talent updates

Deterministic tooling for source-faithful ToME 1.7.6 talent `Ability_box`
candidates. Rendered page changes are review-only; unsupported formulas fail
closed.

- `scripts/render_candidates.py` — renders source `info()` descriptions and
  version fields to review-only JSONL.
- `scripts/test_render_candidates.py` — renderer regression tests.
- `scripts/build_renderer_review_packet.py` — verifies source hashes and builds
  compact packets for unsupported cases.
- `docs/renderer-subagent-review.md` — subagent review prompt and workflow.
- `docs/deterministic-renderer-state.md` — current coverage snapshot and
  source-backed deterministic expansion leads.
- `talents/` — mapped wiki pages and source metadata.

## Render and iterate

Render candidates to JSONL for inspection; default output is
`/tmp/opencode/tome-wiki-phase1-dry-run.jsonl`:

```bash
python3 -B scripts/render_candidates.py --output /tmp/opencode/candidates.jsonl
```

To initialize or update a review batch, explicitly name its work directory and
pass `--apply`. This writes work pages and review records under that directory;
renderer output remains JSONL:

```bash
python3 -B scripts/render_candidates.py \
  --work-dir work/phase-1 --apply \
  --output /tmp/opencode/candidates.jsonl
```

Unsupported renderer cases may use the source-file-grouped LLM workflow in
[`docs/renderer-subagent-review.md`](docs/renderer-subagent-review.md). Draft
application defaults to a plan; writes require both `--work-dir` and explicit
`--apply`, and affect only work pages/review records:

```bash
python3 -B scripts/apply_llm_drafts.py \
  /tmp/opencode/candidates.jsonl /tmp/opencode/llm-drafts/*.draft.jsonl \
  --work-dir work/phase-1 --apply
```

Baseline `talents/` pages stay unchanged throughout iteration. No workflow
automatically deletes pages, publishes wiki edits, approves LLM drafts, or
extends renderer support. Review unmatched/missing source manually; never treat
it as deletion evidence.

## Review and promote

All review data commands require `--work-dir`. `list` shows review state and
status; `show` reports one record and provenance. Select by wiki title, filename,
or talent ID:

```bash
python3 -B scripts/review.py list --work-dir work/phase-1
python3 -B scripts/review.py show --work-dir work/phase-1 "Talent title"
```

`set` changes human-owned state, workflow status, notes, blockers, and automation
settings. It previews changes and asks before writing; use `--dry-run` to inspect
only or `--yes` for noninteractive use. `batch set` applies same fields to
newline-delimited selectors in a file:

```bash
python3 -B scripts/review.py set --work-dir work/phase-1 \
  --state update --workflow-status approved "Talent title"
python3 -B scripts/review.py batch set --work-dir work/phase-1 \
  --from /tmp/opencode/titles.txt --workflow-status needs_human_review
python3 -B scripts/review.py set --work-dir work/phase-1 \
  --state mark_out_of_date \
  --notes "Source definition is absent; verify page is obsolete." "Talent title"
python3 -B scripts/review.py set --work-dir work/phase-1 \
  --deterministic-mode skip \
  --deterministic-notes "Source formula needs manual review." "Talent title"
python3 -B scripts/review.py set --work-dir work/phase-1 \
  --llm-instruction "Preserve wiki-only notes; check source conditions." "Talent title"
```

Use `instructions edit` to edit LLM instructions as a validated JSON string
array in `$VISUAL` or `$EDITOR`. Marking `mark_out_of_date` requires notes and
never deletes the page or invents wiki markup.

`pick` interactively selects titles (uses `fzf` when available; `--multi` allows
multiple selections). `validate` checks selected records, or all existing work
records when no selector is given:

```bash
python3 -B scripts/review.py pick --work-dir work/phase-1 --multi
python3 -B scripts/review.py validate --work-dir work/phase-1
```

`promote` previews approved pages for a clean target Git checkout. It checks
baseline identity and hashes; only explicit `--apply` copies approved `page`
files. It does not copy metadata or review records, commit changes, or publish
anything. Review the resulting `git diff` and handle publication separately:

```bash
python3 -B scripts/review.py promote --work-dir work/phase-1 \
  --target-root /path/to/clean/tome-wiki "Talent title"
python3 -B scripts/review.py promote --work-dir work/phase-1 \
  --target-root /path/to/clean/tome-wiki --apply "Talent title"
```

Print shell completion for `bash`, `zsh`, or `fish`, then source generated
output. Bash example:

```bash
source <(python3 -B scripts/review.py completion bash)
```

Run tests with:

```bash
python3 -B -m unittest discover -s scripts -p 'test_*.py' -v
```
