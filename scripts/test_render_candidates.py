import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import render_candidates as renderer
from review_workflow import initialize_review, load_review, write_review


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
getDur = function(self, t) return math.floor(self:combatTalentScale(t, 5, 10)) end,
radius = 10,
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
        self.assertEqual(
            renderer.render_expression(
                "(damage + damage / 3) / 2",
                SOURCE,
                {"damage": "self:combatTalentSpellDamage(t, 15, 45)"},
            ),
            "{{ctspd|10|30}}",
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

    def test_inert_clamps_raw_levels_physical_damage_and_talent_methods(self):
        self.assertEqual(
            renderer.render_expression("math.max(1, math.floor(self:combatTalentLimit(t, 5, 1, 3)))", SOURCE, {}),
            "{{ctl|5|1|3}}",
        )
        self.assertEqual(
            renderer.render_expression("math.min(13, math.floor(self:combatTalentScale(t, 5, 9)))", SOURCE, {}),
            "{{cts|5|9}}",
        )
        with self.assertRaisesRegex(renderer.Unsupported, "changes scaling endpoints"):
            renderer.render_expression("math.max(1, self:combatTalentScale(t, 0, 4))", SOURCE, {})
        self.assertEqual(
            renderer.render_expression("math.min(4, self:getTalentLevelRaw(t))", SOURCE, {}),
            "{{gtl|1|4|raw}}",
        )
        self.assertEqual(
            renderer.render_expression("self:combatTalentPhysicalDamage(t, 15, 100)", SOURCE, {}),
            "{{ctpd|15|100}}",
        )
        self.assertEqual(
            renderer.render_expression("t:_getDur(self)", SOURCE, {}),
            "{{cts|5|10}}",
        )
        self.assertEqual(renderer.render_expression("self:getTalentRadius(t)", SOURCE, {}), "10")

    def test_optional_scale_parameters_and_leading_offset(self):
        self.assertEqual(
            renderer.render_expression('self:combatTalentScale(t, 3, 7, "log", 0, 2)', SOURCE, {}),
            "{{cts|3|7|log|0|2}}",
        )
        self.assertEqual(
            renderer.render_expression("self:combatTalentLimit(t, 6, 1.1, 2.5, false, 1.0)", SOURCE, {}),
            "{{ctl|6|1.1|2.5|false|1}}",
        )
        self.assertEqual(
            renderer.render_expression('self:combatStatScale("dex", 10, 100, 0.75)', SOURCE, {}),
            "{{css|Dex|10|100|0.75}}",
        )
        self.assertEqual(
            renderer.render_expression("1 + math.floor(self:combatTalentScale(t, 2, 6))", SOURCE, {}),
            "{{cts|3|7}}",
        )
        self.assertEqual(
            renderer.render_expression("self:combatTalentLimit(t, 30, 5, 25, true)", SOURCE, {}),
            "{{ctl|30|5|25|raw}}",
        )

    def test_generic_talent_helper_and_local_radius_do_not_collide(self):
        source = r'''radius = function(self, t) return self:combatTalentScale(t, 2, 4) end,
summonTime = function(self, t) return math.floor(self:combatTalentScale(t, 3, 7)) end,'''
        self.assertEqual(
            renderer.render_expression("t.summonTime(self, t)", source, {}),
            "{{cts|3|7}}",
        )
        self.assertEqual(
            renderer.render_expression("radius", source, {"radius": "self:getTalentRadius(t)"}),
            "{{cts|2|4}}",
        )

    def test_bare_source_helper_fields_resolve_only_when_defined(self):
        source = r'''chance = function(self, t) return self:combatTalentScale(t, 20, 50) end,'''
        self.assertEqual(renderer.render_expression("chance", source, {}), "{{cts|20|50}}")
        with self.assertRaisesRegex(renderer.Unsupported, "unsupported interpolation expression: unknown"):
            renderer.render_expression("unknown", source, {})

    def test_static_string_format_arguments_and_localized_long_strings(self):
        info = r'''info = function(self, t)
            local slot = _t"not prepared"
            return (_t[[The %s slot is ready.]]) :tformat(slot)
        end,'''
        self.assertEqual(renderer.split_info(info), "The not prepared slot is ready.")
        self.assertEqual(renderer.split_info('info = function(self,t) return (_t[[Plain text.]]) end,'), "Plain text.")

    def test_rank_color_placeholders_and_extra_tformat_args(self):
        info_block = r'''info = function(self, t)
            local _, color_normal = self:textRank(2)
            return ([[Ranks: %sNormal#LAST#; fixed %d.]]) :tformat(color_normal, 5, 99)
        end,'''
        self.assertEqual(renderer.split_info(info_block), "Ranks: Normal; fixed 5.")

    def test_symbolic_steam_formula_and_multi_return_helper(self):
        source = r'''getPower = function(self, t)
            return (60 + self:combatTalentSteamDamage(t, 15, 450)) / 7, 7, self:combatTalentLimit(t, 100, 27, 55)
        end,'''
        self.assertEqual(
            renderer.render_expression("t.getPower(self, t)", source, {}, percent=True),
            "(60% + {{ctstmd|15|450%}})/7",
        )

    def test_recursive_source_helper_fails_closed(self):
        source = r'''getStatBonus = function(self, t)
            local t = self:getTalentFromId(self.T_DEPLOY_TURRET)
            return t.getStatBonus(self, t)
        end,'''
        with self.assertRaisesRegex(renderer.Unsupported, "recursive source helper: getStatBonus"):
            renderer.render_expression("t.getStatBonus(self, t)", source, {})

    def test_changed_desc_is_reported_when_ability_box_closes_inline(self):
        before = "{{Ability box\n|desc=old text}}"
        after = "{{Ability box\n|desc=new text}}"
        _, _, before_box = renderer.extract_box(before)
        _, _, after_box = renderer.extract_box(after)
        self.assertTrue(renderer.key_parameter_changed(before_box, after_box, "desc"))

    def test_missing_desc_parameter_is_added_without_reformatting_existing_fields(self):
        box = "{{Ability box\n|name=Example\n|category=Test\n}}"
        self.assertEqual(
            renderer.set_desc(box, "Source description."),
            "{{Ability box\n|name=Example\n|category=Test\n|desc=\nSource description.\n}}",
        )

    def test_single_line_ability_box_can_receive_version_and_desc_updates(self):
        box = "{{Ability_box|name=Example|desc=Old text.}}"
        updated = renderer.set_param(box, "game_version", "1.7.6")
        self.assertIn("{{Ability_box|game_version=1.7.6|name=Example", updated)
        _, _, before_box = renderer.extract_box(box)
        _, _, ability_box = renderer.extract_box(updated)
        self.assertTrue(renderer.key_parameter_changed(before_box, ability_box, "game_version"))
        self.assertEqual(renderer.current_description(ability_box), "Old text.")
        updated = renderer.set_desc(ability_box, "Source description.")
        self.assertIn("|desc=\nSource description.\n}}", updated)

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


class WorkApplicationTests(unittest.TestCase):
    filename = 'Example_(talent)'
    baseline = b'Intro\n{{Ability_box\n|desc=Old text\n}}\nTail\n'
    candidate_page = 'Intro\n{{Ability_box\n|desc=New text\n}}\nTail\n'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.baseline_root = self.root / 'catalog'
        self.work_dir = self.root / 'work' / 'batch'
        talent_dir = self.baseline_root / 'talents' / self.filename
        talent_dir.mkdir(parents=True)
        self.metadata = {
            'filename': self.filename,
            'wiki_title': 'Example (talent)',
            'pageid': 42,
            'revid': 84,
            'source_file': 'game/modules/tome/data/talents/example.lua',
            'source_start_line': 1,
            'source_end_line': 5,
            'source_sha256': 'a' * 64,
            'talent_id': 'T_EXAMPLE',
            'source_match_status': 'matched',
            'addon_id': None,
            'addon_version': None,
        }
        (talent_dir / 'metadata.json').write_text(json.dumps(self.metadata), encoding='utf-8')
        (talent_dir / 'page').write_bytes(self.baseline)
        self.root_patch = mock.patch.object(renderer, 'ROOT', self.baseline_root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def record(self, status='candidate', after=None):
        return {
            'filename': self.filename,
            'title': self.metadata['wiki_title'],
            'pageid': self.metadata['pageid'],
            'status': status,
            'before_wikitext': self.baseline.decode(),
            'after_wikitext': self.candidate_page if after is None else after,
        }

    def paths(self):
        target = self.work_dir / 'talents' / self.filename
        return target / 'page', target / 'review.json'

    def test_work_dir_without_apply_is_dry_run(self):
        output = self.root / 'render.jsonl'
        with mock.patch.object(renderer.sys, 'argv', [
            'render_candidates.py', '--work-dir', str(self.work_dir), '--output', str(output),
        ]), mock.patch.object(renderer.subprocess, 'run') as run, mock.patch.object(
            renderer, 'build_candidate', return_value=self.record(),
        ):
            run.return_value.stdout = renderer.CORE_COMMIT + '\n'
            self.assertEqual(renderer.main(), 0)
        self.assertTrue(output.exists())
        self.assertFalse(self.work_dir.exists())

    def test_apply_requires_work_dir(self):
        with mock.patch.object(renderer.sys, 'argv', ['render_candidates.py', '--apply']):
            with self.assertRaises(SystemExit) as error:
                renderer.main()
        self.assertEqual(error.exception.code, 2)

    def test_repo_root_work_dir_is_rejected_before_any_processing(self):
        baseline_page = self.baseline_root / 'talents' / self.filename / 'page'
        original = baseline_page.read_bytes()
        with mock.patch.object(renderer.sys, 'argv', [
            'render_candidates.py', '--work-dir', str(self.baseline_root),
        ]), mock.patch.object(renderer.subprocess, 'run', side_effect=AssertionError('source lookup ran')):
            with self.assertRaises(SystemExit) as error:
                renderer.main()
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(baseline_page.read_bytes(), original)

    def test_apply_writes_complete_candidate_and_record(self):
        touched = renderer.apply_work_candidates([self.record()], self.work_dir)
        page, review_path = self.paths()
        self.assertEqual(page.read_text(encoding='utf-8'), self.candidate_page)
        review = load_review(review_path, self.metadata)
        self.assertEqual(review['render']['status'], 'candidate')
        self.assertEqual(review['render']['application_status'], 'applied')
        self.assertEqual(review['work_page']['last_managed_sha256'], hashlib.sha256(page.read_bytes()).hexdigest())
        self.assertEqual(review['workflow_status'], 'queued')
        self.assertIn(page, touched)

    def test_candidate_splice_preserves_exact_bytes_outside_ability_box(self):
        baseline = b'Header\r\n{{Ability_box\r\n|desc=Old\r\n}}\r\nFooter\r\n'
        before = baseline.decode().replace('\r\n', '\n')
        after = before.replace('|desc=Old', '|desc=New')
        result = renderer.candidate_page_bytes(baseline, before, after)
        self.assertTrue(result.startswith(b'Header\r\n'))
        self.assertTrue(result.endswith(b'\r\nFooter\r\n'))
        self.assertIn(b'|desc=New', result)

    def test_review_merge_preserves_human_and_unknown_fields(self):
        review = initialize_review(self.metadata, self.baseline, workflow_status='in_progress')
        review['state'] = 'update'
        review['notes'] = 'Human note stays.'
        review['automation']['llm']['instructions'] = ['Keep wording.']
        review['human_extension'] = {'keep': True}
        review['history'].append({
            'at': '2026-11-08T00:00:00Z', 'actor': 'human:unknown',
            'event': 'human_note_added',
        })
        _, review_path = self.paths()
        write_review(review_path, review, self.metadata)
        renderer.apply_work_candidates([self.record()], self.work_dir)
        merged = load_review(review_path, self.metadata)
        self.assertEqual(merged['state'], 'update')
        self.assertEqual(merged['workflow_status'], 'in_progress')
        self.assertEqual(merged['notes'], 'Human note stays.')
        self.assertEqual(merged['automation']['llm']['instructions'], ['Keep wording.'])
        self.assertEqual(merged['human_extension'], {'keep': True})
        self.assertEqual(merged['history'][0]['event'], 'human_note_added')

    def test_deterministic_skip_bypasses_source_parser_and_writes_no_page(self):
        review = initialize_review(self.metadata, self.baseline, workflow_status='queued')
        review['automation']['deterministic'] = {'mode': 'skip', 'notes': 'Human requested skip.'}
        _, review_path = self.paths()
        write_review(review_path, review, self.metadata)
        directory = self.baseline_root / 'talents' / self.filename
        with mock.patch.object(renderer, 'build_candidate', side_effect=AssertionError('parser called')):
            candidate = renderer.render_directory(directory, Path('/unused'), 'tag', work_dir=self.work_dir)
        self.assertEqual(candidate['status'], 'skipped_by_review')
        renderer.apply_work_candidates([candidate], self.work_dir)
        page, _ = self.paths()
        self.assertFalse(page.exists())
        self.assertEqual(load_review(review_path, self.metadata)['render']['application_status'], 'skipped_by_review')

    def test_human_out_of_date_state_and_work_page_are_never_overwritten(self):
        review = initialize_review(
            self.metadata, self.baseline, state='mark_out_of_date',
            workflow_status='needs_human_review', notes='Human decision stays.',
        )
        page, review_path = self.paths()
        page.parent.mkdir(parents=True)
        page.write_bytes(self.baseline)
        review['work_page']['last_managed_sha256'] = hashlib.sha256(self.baseline).hexdigest()
        review['work_page']['last_writer'] = 'deterministic_renderer'
        write_review(review_path, review, self.metadata)
        renderer.apply_work_candidates([self.record()], self.work_dir)
        merged = load_review(review_path, self.metadata)
        self.assertEqual(page.read_bytes(), self.baseline)
        self.assertEqual(merged['state'], 'mark_out_of_date')
        self.assertEqual(merged['notes'], 'Human decision stays.')
        self.assertEqual(merged['render']['application_status'], 'not_applied_human_state')

    def test_llm_managed_page_is_preserved_even_when_managed_hash_matches(self):
        review = initialize_review(self.metadata, self.baseline, workflow_status='queued')
        page, review_path = self.paths()
        page.parent.mkdir(parents=True)
        draft = b'LLM draft\n{{Ability_box\n|desc=Draft text\n}}\nTail\n'
        page.write_bytes(draft)
        review['work_page']['last_managed_sha256'] = hashlib.sha256(draft).hexdigest()
        review['work_page']['last_writer'] = 'llm_applier'
        write_review(review_path, review, self.metadata)

        renderer.apply_work_candidates([self.record()], self.work_dir)

        merged = load_review(review_path, self.metadata)
        self.assertEqual(page.read_bytes(), draft)
        self.assertEqual(merged['work_page']['last_writer'], 'llm_applier')
        self.assertEqual(merged['work_page']['last_managed_sha256'], hashlib.sha256(draft).hexdigest())
        self.assertEqual(merged['render']['application_status'], 'not_applied_human_state')

    def test_review_workflow_statuses_protect_existing_page(self):
        for workflow_status in ('needs_human_review', 'needs_source_review'):
            with self.subTest(workflow_status=workflow_status):
                work_dir = self.root / 'work' / workflow_status
                page = work_dir / 'talents' / self.filename / 'page'
                review_path = page.with_name('review.json')
                page.parent.mkdir(parents=True)
                page.write_bytes(self.baseline)
                review = initialize_review(
                    self.metadata, self.baseline, workflow_status=workflow_status,
                )
                review['work_page']['last_managed_sha256'] = hashlib.sha256(self.baseline).hexdigest()
                review['work_page']['last_writer'] = 'deterministic_renderer'
                write_review(review_path, review, self.metadata)

                renderer.apply_work_candidates([self.record()], work_dir)

                merged = load_review(review_path, self.metadata)
                self.assertEqual(page.read_bytes(), self.baseline)
                self.assertEqual(merged['workflow_status'], workflow_status)
                self.assertEqual(merged['render']['application_status'], 'not_applied_human_state')

    def test_invalid_output_is_preflighted_before_work_writes(self):
        output_dir = self.root / 'output-is-directory'
        output_dir.mkdir()
        with mock.patch.object(renderer.sys, 'argv', [
            'render_candidates.py', '--apply', '--work-dir', str(self.work_dir),
            '--output', str(output_dir),
        ]), mock.patch.object(renderer.subprocess, 'run') as run, mock.patch.object(
            renderer, 'build_candidate', return_value=self.record(),
        ):
            run.return_value.stdout = renderer.CORE_COMMIT + '\n'
            self.assertEqual(renderer.main(), 2)
        self.assertFalse(self.work_dir.exists())
        self.assertTrue(output_dir.is_dir())

    def test_unsupported_and_unmatched_copy_baseline_without_delete_marker(self):
        for status in ('unsupported_info', 'unmatched_source'):
            with self.subTest(status=status):
                batch = self.work_dir / status
                renderer.apply_work_candidates([self.record(status)], batch)
                page = batch / 'talents' / self.filename / 'page'
                review = load_review(page.with_name('review.json'), self.metadata)
                self.assertEqual(page.read_bytes(), self.baseline)
                self.assertNotIn(b'DELETE', page.read_bytes())
                self.assertEqual(review['state'], 'update')
                self.assertEqual(review['render']['status'], status)

    def test_rerun_is_idempotent(self):
        record = self.record()
        renderer.apply_work_candidates([record], self.work_dir)
        page, review_path = self.paths()
        page_before = page.read_bytes()
        review_before = review_path.read_bytes()
        self.assertEqual(renderer.apply_work_candidates([record], self.work_dir), [])
        self.assertEqual(page.read_bytes(), page_before)
        self.assertEqual(review_path.read_bytes(), review_before)

    def test_conflict_refuses_entire_batch_before_writes(self):
        renderer.apply_work_candidates([self.record()], self.work_dir)
        page, review_path = self.paths()
        original_review = review_path.read_bytes()
        page.write_bytes(b'Human edit\n')
        first_filename = 'First_(talent)'
        first_dir = self.baseline_root / 'talents' / first_filename
        first_dir.mkdir()
        first_meta = {**self.metadata, 'filename': first_filename, 'wiki_title': 'First (talent)', 'pageid': 43}
        (first_dir / 'metadata.json').write_text(json.dumps(first_meta), encoding='utf-8')
        (first_dir / 'page').write_bytes(self.baseline)
        first_record = {
            **self.record(), 'filename': first_filename, 'title': 'First (talent)', 'pageid': 43,
        }
        with self.assertRaisesRegex(renderer.WorkApplicationError, 'work page hash conflict'):
            renderer.apply_work_candidates([first_record, self.record()], self.work_dir)
        self.assertFalse((self.work_dir / 'talents' / first_filename).exists())
        self.assertEqual(review_path.read_bytes(), original_review)
        self.assertEqual(page.read_bytes(), b'Human edit\n')

    def test_absent_unmatched_page_is_retained_not_deleted(self):
        renderer.apply_work_candidates([self.record('unmatched_source')], self.work_dir)
        page, review_path = self.paths()
        self.assertTrue(page.exists())
        self.assertNotIn(b'DELETE', page.read_bytes())
        review = load_review(review_path, self.metadata)
        self.assertNotEqual(review['state'], 'mark_out_of_date')


if __name__ == "__main__":
    unittest.main()
