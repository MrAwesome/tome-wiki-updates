# Potential in-game description / implementation mismatch

Source-only observation. Existing wiki text was not consulted for this note. Verify before changing source or wiki.

- In-game description vs implementation: Info says every time you inflict damage, you deal extra darkness damage; implementation skips the proc when that damage kills the target.
- Source: `game/addons/tome-orcs/tome-orcs/data/talents/spells/undead-drake.lua:20-60`
- Supporting code lines: game/addons/tome-orcs/tome-orcs/data/talents/spells/undead-drake.lua:41-47, game/addons/tome-orcs/tome-orcs/data/talents/spells/undead-drake.lua:55-58
- Review confidence: `high`; draft, not approval.
