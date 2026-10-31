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
getBleedMult = function(self, t) return self:combatTalentScale(t, 1.4, 2.8) end,
getEnergyDrain = function(self, t) return self:combatTalentScale(t, 40, 70) end,
getEnergyCap = function(self, t) return 4000 end,
getSpillThreshold = function(self, t) return 40 end,
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

    def test_offsets_optional_cts_power_and_level(self):
        self.assertEqual(
            renderer.render_expression("mult - 1", SOURCE, {"mult": "t.getBleedMult(self, t) * 100"}, percent=True),
            "{{cts|139|279%}}",
        )
        self.assertEqual(
            renderer.scale_value("cts", ["t", "0.885", "2.12", "0.75"], True, percentage_multiplier=100),
            "{{cts|88.5|212%|0.75}}",
        )
        self.assertEqual(renderer.render_expression("self:getTalentLevel(t)", SOURCE, {}), "{{gtl|1|5}}")
        self.assertEqual(renderer.render_expression("40", SOURCE, {}), "40")
        self.assertEqual(renderer.render_expression("t.getSpillThreshold(self, t)", SOURCE, {}, percent=True), "40%")

    def test_decimal_placeholders_inline_comments_and_dynamic_strings(self):
        info_block = SOURCE + "\n" + r'''info = function(self, t)
            return ([[Heal %0.1f%%; cap %.1f turns; fixed %d.]])
            :tformat(t.getEnergyDrain(self, t) * 0.1, t.getEnergyCap(self, t) / 1000, 5)
        end,'''
        self.assertEqual(
            renderer.split_info(info_block),
            "Heal {{cts|4|7%}}; cap 4 turns; fixed 5.",
        )
        self.assertEqual(
            renderer.render_expression("self:combatTalentLimit(t, 10, 30, 13.5) -- inline note", SOURCE, {}),
            "{{ctl|10|30|13.5}}",
        )
        dynamic_block = r'''info = function(self, t)
            return ([[Dynamic value: %s.]]) :tformat(descs)
        end,'''
        with self.assertRaisesRegex(renderer.Unsupported, r"dynamic %s"):
            renderer.split_info(dynamic_block)

    def test_unsupported_arithmetic_fails_closed(self):
        with self.assertRaises(renderer.Unsupported):
            renderer.render_expression("t.getDamage(self, t) + t.getDamage(self, t)", SOURCE, {})

    def test_wiki_only_notes_preserved_but_source_quote_not_duplicated(self):
        notes = "''First wiki note.''\n\n''Second wiki note.''"
        self.assertEqual(
            renderer.preserved_wiki_notes(notes, "Source description."),
            ["''First wiki note.''", "''Second wiki note.''"],
        )
        quote = "''All living things are linked.''"
        rendered_quote = "#{italic}#All living things are linked.#{normal}#"
        self.assertEqual(renderer.preserved_wiki_notes(quote, rendered_quote), [])

    def test_existing_wiki_links_and_damage_markup_survive_source_render(self):
        current = "reduced by {{c|Bloodstained}} and deals {{fire}} damage to {{t|Entropic Gift}}."
        rendered = "reduced by Bloodstained and deals fire damage to Entropic Gift."
        self.assertEqual(
            renderer.preserve_wiki_links(current, rendered),
            "reduced by {{c|Bloodstained}} and deals {{fire}} damage to {{t|Entropic Gift}}.",
        )

    def test_source_dash_lists_become_wiki_bullets(self):
        self.assertEqual(renderer.wiki_bullets("- Fire\n- Cold"), "*Fire\n*Cold")


if __name__ == "__main__":
    unittest.main()
