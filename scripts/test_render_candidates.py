import unittest

import render_candidates as renderer


SOURCE = r'''
getRate = function(self, t) return self:combatTalentScale(t, 0.15, 0.5) end,
getDamage = function(self, t) return self:combatTalentMindDamage(t, 10, 150) end,
getShield = function(self, t) return self:combatTalentStatDamage(t, "str", 20, 100) end,
getShieldBonus = function(self, t) return t.getShield(self, t) * 4 end,
getLimit = function(self, t) return math.floor(self:combatTalentLimit(t, 10, 100, 20)) end,
getChance = function(self, t) return self:combatTalentLimit(t, 50, 20, 35) end,
getWeapon = function(self, t) return self:combatTalentWeaponDamage(t, 0.4, 1.6) end,
'''


class RenderCandidateTests(unittest.TestCase):
    def test_percent_factor_and_weapon_damage_conversion(self):
        self.assertEqual(
            renderer.render_expression("t.getRate(self, t) * 100", SOURCE, {}),
            "{{cts|15|50%}}",
        )
        self.assertEqual(
            renderer.render_expression("t.getWeapon(self, t) * 100", SOURCE, {}),
            "{{ctwd|40|160%}}",
        )

    def test_literal_arithmetic_propagates_through_helpers(self):
        self.assertEqual(
            renderer.render_expression("t.getDamage(self, t) / 10", SOURCE, {}),
            "{{ctmd|1|15}}",
        )
        self.assertEqual(
            renderer.render_expression("t.getShieldBonus(self, t)", SOURCE, {}),
            "{{ctstd|Str|80|400}}",
        )

    def test_complement_and_percent_cap(self):
        self.assertEqual(
            renderer.render_expression("100 - t.getLimit(self, t)", SOURCE, {}, percent=True),
            "{{ctl|90%|0|80%}}",
        )
        self.assertEqual(
            renderer.render_expression("t.getChance(self, t)", SOURCE, {}, percent=True),
            "{{ctl|50%|20|35%}}",
        )

    def test_unsupported_arithmetic_fails_closed(self):
        with self.assertRaises(renderer.Unsupported):
            renderer.render_expression("t.getDamage(self, t) + 1", SOURCE, {})

    def test_wiki_only_notes_preserved_but_source_quote_not_duplicated(self):
        notes = "''First wiki note.''\n\n''Second wiki note.''"
        self.assertEqual(
            renderer.preserved_wiki_notes(notes, "Source description."),
            ["''First wiki note.''", "''Second wiki note.''"],
        )
        quote = "''All living things are linked.''"
        rendered_quote = "#{italic}#All living things are linked.#{normal}#"
        self.assertEqual(renderer.preserved_wiki_notes(quote, rendered_quote), [])

    def test_source_dash_lists_become_wiki_bullets(self):
        self.assertEqual(renderer.wiki_bullets("- Fire\n- Cold"), "*Fire\n*Cold")


if __name__ == "__main__":
    unittest.main()
