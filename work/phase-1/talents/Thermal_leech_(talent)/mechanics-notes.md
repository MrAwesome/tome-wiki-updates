# Potential in-game description / implementation mismatch

Source-only observation. Existing wiki text was not consulted for this note. Verify before changing source or wiki.

- In-game description vs implementation: Info says effect strength scales with Mindpower, but Psi return and freeze duration use talent-level scaling; only damage getter uses Mindpower scaling.
- Source: `game/modules/tome/data/talents/psionic/voracity.lua:87-145`
- Supporting code lines: game/modules/tome/data/talents/psionic/voracity.lua:104-114, game/modules/tome/data/talents/psionic/voracity.lua:137-143
- Review confidence: `high`; draft, not approval.
