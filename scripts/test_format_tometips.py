import unittest
from pathlib import Path

from format_tometips import (
    CacheTalent,
    Match,
    format_html,
    format_tooltip,
    find_matches,
    selector_keys,
    source_relative_path,
)


class FormatTometipsTests(unittest.TestCase):
    def test_tooltip_omits_mastery_but_keeps_level_and_stat_context(self):
        self.assertEqual(
            format_tooltip(
                "Values for<br>talent levels 1-5,<br>talent mastery 1.00,<br>Dexterity 100"
            ),
            "TL1-5; DEX 100",
        )

    def test_non_talent_level_context_remains_visible(self):
        self.assertEqual(
            format_tooltip("Values for<br>spellpower 10, 25, 50, 75, 100"),
            "SP 10-100",
        )
        self.assertEqual(
            format_tooltip("Values for<br>mindpower 100,<br>physical power 100"),
            "MP 100; PP 100",
        )

    def test_context_value_sequences_collapse_to_endpoint_ranges(self):
        self.assertEqual(
            format_tooltip(
                "Values for<br>Cunning 10, 25, 50, 75, 100,"
                "<br>character level 1, 10, 25, 40, 50"
            ),
            "CUN 10-100; LVL 1-50",
        )

    def test_paragraphs_variables_lists_and_emphasis(self):
        source = (
            '<p>Gain <acronym class="talent-variable" '
            'title="Values for<br>talent levels 1-5,<br>talent mastery 1.00">'
            '2%, 3%, 4%, 5%, 6%</acronym>.</p>'
            '<p>Options:</p><ul><li><span style="font-style: italic">First</span></li>'
            '<li>Second</li></ul>'
        )
        self.assertEqual(
            format_html(source),
            "Gain [2%, 3%, 4%, 5%, 6% (TL1-5)].\n\n"
            "Options:\n\n* ''First''\n* Second\n",
        )

    def test_annotation_precedes_punctuation_inside_acronym(self):
        source = (
            '<p>Gain <acronym title="Values for<br>talent levels 1-5">'
            '2, 3, 4, 5, 6%.</acronym></p>'
        )
        self.assertEqual(
            format_html(source),
            "Gain [2, 3, 4, 5, 6% (TL1-5)].\n",
        )

    def test_source_path_normalizes_core_and_addon_prefixes(self):
        self.assertEqual(
            source_relative_path("game/modules/tome/data/talents/techniques/sniper.lua"),
            "techniques/sniper.lua",
        )
        self.assertEqual(
            source_relative_path("data-cults/talents/demented/oblivion.lua"),
            "demented/oblivion.lua",
        )
        self.assertIsNone(source_relative_path(""))

    def test_selector_accepts_talents_prefixed_folder(self):
        match = Match(
            metadata_path=Path("/repo/talents/Aim_(talent)/metadata.json"),
            metadata={"wiki_title": "Aim (talent)"},
            talent=CacheTalent(Path("/cache/technique.json"), {}, None, None),
            exact_line=True,
        )
        self.assertIn("talents/aim_(talent)", selector_keys(match))

    def test_pathless_cache_source_matches_unique_talent_id_and_line(self):
        matches, _issues = find_matches()
        match = next(
            match for match in matches
            if match.metadata_path.parent.name == "Venomous_strike_(talent)"
        )
        self.assertEqual(match.talent.data["id"], "T_VENOMOUS_STRIKE")
        self.assertEqual(match.talent.source_line, 289)
        self.assertTrue(match.cache_filename_missing)

    def test_mapping_issues_include_talent_and_source_details(self):
        _matches, issues = find_matches()
        self.assertTrue(issues)
        self.assertRegex(issues[0], r"\(id=.*source=.*\)")


if __name__ == "__main__":
    unittest.main()
