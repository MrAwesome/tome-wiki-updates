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
- `talents/` — mapped wiki pages and source metadata.

Run renderer tests with:

```bash
python3 -B -m unittest discover -s scripts -p 'test_*.py' -v
```
