# Potential in-game description / implementation mismatch

Source-only observation. Existing wiki text was not consulted for this note. Verify before changing source or wiki.

- In-game description vs implementation: Info says life must be below 30%; code also triggers at exactly 30% after damage (it returns only when the ratio is greater than 0.3).
- Source: `game/modules/tome/data/talents/misc/races.lua:934-959`
- Supporting code lines: game/modules/tome/data/talents/misc/races.lua:946-957
- Review confidence: `high`; draft, not approval.
