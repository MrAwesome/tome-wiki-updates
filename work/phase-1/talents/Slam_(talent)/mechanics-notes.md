# Potential in-game description / implementation mismatch

Source-only observation. Existing wiki text was not consulted for this note. Verify before changing source or wiki.

- In-game description vs implementation: Info says first slam hitting at least two opponents increases rampage duration by 1, but implementation does so only below max duration and if no prior Slam extension.
- Source: `game/modules/tome/data/talents/cursed/rampage.lua:147-222`
- Supporting code lines: game/modules/tome/data/talents/cursed/rampage.lua:205-210, 215-220
- Review confidence: `high`; draft, not approval.
