# Talent-specific source preprocessing

Per-talent preprocessing is an optional way to make a difficult description
renderable without expanding the generic renderer for a one-off source pattern.
It is a proposed workflow; do not assume tooling support exists until the
pipeline implements and tests it. It must not change upstream game source or
silently alter talent mechanics.

## When to use it

Prefer a reusable renderer feature when one well-defined expression occurs
across many talents and has clear semantics. Prefer focused preprocessing when
a talent has a small, identifiable source fragment that can safely be rewritten
or omitted. Prefer a reviewed description draft when a transformation would be
larger or less clear than writing the best-effort text directly. Full renderer
coverage is not a goal by itself.

Preprocessing may normalize syntax the renderer cannot parse or remove a
runtime-only fragment that should not become a fixed wiki value. Preserve useful
static prose around that fragment. Do not invent mechanics or turn uncertain
values into exact-looking claims.

## Patch requirements

- Prefer a small patch/delta over a duplicate full source file. Generate patched
  input from the authoritative source; never edit the checkout in place.
- Scope patch to talent/source identity and record source path, source hash,
  addon identity/version where applicable, and patch rationale.
- Match exact expected context and expected occurrence count. Fail closed if
  source hash or context differs; never apply a broad, unanchored regex.
- Make each replacement/removal explicit and reviewable. Do not disguise a
  mechanics change as syntax cleanup.
- Test both patched input and expected rendered output. Include a failure test
  for changed or missing source context.
- Retain original source and patch provenance alongside candidate so reviewer
  can inspect what renderer saw and what text was omitted or changed.
- Treat output as a candidate only. Preserve human-added wiki notes unless
  evidence shows they are inaccurate or obsolete; flag uncertain notes instead
  of deleting them.

If a patch cannot satisfy these conditions simply, use a reviewed best-effort
description draft instead.
