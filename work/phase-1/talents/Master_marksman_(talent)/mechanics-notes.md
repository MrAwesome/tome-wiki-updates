# Potential in-game description / implementation mismatch

Source-only observation. Existing wiki text was not consulted for this note. Verify before changing source or wiki.

- In-game description vs implementation: Info claims weapon-damage and physical-power bonuses, but implementation applies only reload bonus; getDamage is not applied.
- Source: `game/modules/tome/data/talents/techniques/marksmanship.lua:26-54`
- Supporting code lines: game/modules/tome/data/talents/techniques/marksmanship.lua:32-39, game/modules/tome/data/talents/techniques/marksmanship.lua:49-52
- Review confidence: `high`; draft, not approval.
