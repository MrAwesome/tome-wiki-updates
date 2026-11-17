# Potential in-game description / implementation mismatch

Source-only observation. Existing wiki text was not consulted for this note. Verify before changing source or wiki.

- In-game description vs implementation: Info says effect strength scales with Mindpower, but Psi return and daze chance use talent-level scaling; only damage getter uses Mindpower scaling.
- Source: `game/modules/tome/data/talents/psionic/voracity.lua:147-214`
- Supporting code lines: game/modules/tome/data/talents/psionic/voracity.lua:164-175, game/modules/tome/data/talents/psionic/voracity.lua:206-212
- Review confidence: `high`; draft, not approval.
