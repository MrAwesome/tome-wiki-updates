import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import review_workflow  # noqa: E402


class ApplyDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.baseline = self.root / "baseline"
        self.work = self.root / "work" / "batch-1"
        self.render = self.root / "render.jsonl"
        self.drafts = self.root / "drafts.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def write_baseline(self, slug, title, text):
        directory = self.baseline / slug
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "page").write_bytes(text.encode("utf-8"))
        metadata = {
            "filename": slug,
            "wiki_title": title,
            "pageid": 123,
            "revid": 456,
        }
        (directory / "metadata.json").write_text(json.dumps(metadata) + "\n", encoding="utf-8")
        return metadata

    def write_work_page(self, slug, text, *, metadata, baseline_text=None):
        page = self.work / "talents" / slug / "page"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_bytes(text.encode("utf-8"))
        baseline_bytes = (baseline_text if baseline_text is not None else text).encode("utf-8")
        record = review_workflow.initialize_review(metadata, baseline_bytes)
        record["work_page"] = {
            "last_managed_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "last_writer": "deterministic_renderer",
        }
        review_workflow.write_review(review_workflow.review_path(self.work, slug), record, metadata)
        return page

    def write_render(self, *records):
        self.render.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")

    def write_drafts(self, *records):
        self.drafts.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")

    def run_script(self, *extra, drafts=True):
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPTS / "apply_llm_drafts.py"),
                str(self.render),
                *([str(self.drafts)] if drafts and self.drafts.exists() else []),
                "--baseline-dir",
                str(self.baseline),
                "--work-dir",
                str(self.work),
                *extra,
            ],
            capture_output=True,
            text=True,
            cwd=SCRIPTS,
        )

    def test_unmatched_source_page_remains_byte_identical_without_deletion_marker(self):
        slug = "Gone_(talent)"
        title = "Gone (talent)"
        before = "{{Ability_box\n|desc =\nstale text\n}}\n\n[[Category: Removed Talents]]"
        metadata = self.write_baseline(slug, title, before)
        page = self.write_work_page(slug, before, metadata=metadata)
        self.write_render(
            {
                "title": title,
                "filename": slug,
                "status": "unmatched_source",
                "source_match_status": "removed_or_renamed",
                "before_wikitext": before,
            }
        )

        result = self.run_script("--apply", drafts=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(page.read_bytes(), before.encode("utf-8"))
        self.assertNotIn("DELETE", page.read_text(encoding="utf-8"))
        self.assertIn("unmatched_source=1", result.stdout)

    def test_draft_applies_checkme_and_preserves_page_and_human_review_fields(self):
        slug = "Live_(talent)"
        title = "Live (talent)"
        before = "Lead text.\n\n{{Ability_box\n|name = Live\n|desc =\nold desc\n}}\n\nTrailing note.\n"
        metadata = self.write_baseline(slug, title, before)
        page = self.write_work_page(slug, before, metadata=metadata)
        record_path = review_workflow.review_path(self.work, slug)
        record = review_workflow.load_review(record_path, metadata)
        record["notes"] = "Human note stays."
        record["automation"]["llm"]["instructions"] = ["Preserve terminology."]
        review_workflow.write_review(record_path, record, metadata)
        self.write_render(
            {
                "title": title,
                "filename": slug,
                "status": "unsupported_info",
                "before_wikitext": before,
            }
        )
        self.write_drafts(
            {
                "title": title,
                "status": "draft",
                "proposed_desc": "New {{cts|1|5}} desc.",
                "checkme": "Old desc omits the cap.",
            }
        )

        result = self.run_script("--apply")

        self.assertEqual(result.returncode, 0, result.stderr)
        updated = page.read_text(encoding="utf-8")
        self.assertIn("XXX CHECKME XXX New {{cts|1|5}} desc.", updated)
        self.assertNotIn("old desc", updated)
        self.assertTrue(updated.startswith("Lead text."))
        self.assertTrue(updated.rstrip().endswith("Trailing note."))
        review = review_workflow.load_review(record_path, metadata)
        self.assertEqual(review["state"], "update")
        self.assertEqual(review["workflow_status"], "needs_human_review")
        self.assertEqual(review["notes"], "Human note stays.")
        self.assertEqual(review["automation"]["llm"]["instructions"], ["Preserve terminology."])
        self.assertEqual(review["work_page"]["last_managed_sha256"], hashlib.sha256(page.read_bytes()).hexdigest())
        self.assertEqual(review["work_page"]["last_writer"], "llm_draft_applier")
        self.assertEqual(review["llm_draft"]["checkme"], "Old desc omits the cap.")
        self.assertEqual(review["history"][-1]["event"], "llm_draft_applied")

    def test_plan_does_not_write_and_apply_requires_explicit_flag(self):
        slug = "Plan_(talent)"
        title = "Plan (talent)"
        before = "{{Ability_box\n|desc =\nold\n}}\n"
        metadata = self.write_baseline(slug, title, before)
        page = self.write_work_page(slug, before, metadata=metadata)
        record_path = review_workflow.review_path(self.work, slug)
        original_review = record_path.read_bytes()
        self.write_render({"title": title, "filename": slug, "status": "unsupported_info", "before_wikitext": before})
        self.write_drafts({"title": title, "status": "draft", "proposed_desc": "new", "checkme": None})

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(page.read_text(encoding="utf-8"), before)
        self.assertEqual(record_path.read_bytes(), original_review)

    def test_apply_without_work_dir_is_rejected(self):
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPTS / "apply_llm_drafts.py"),
                str(self.render),
                "--apply",
            ],
            capture_output=True,
            text=True,
            cwd=SCRIPTS,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--work-dir", result.stderr)

    def test_diverged_work_page_is_rejected_without_writing(self):
        slug = "Drift_(talent)"
        title = "Drift (talent)"
        before = "{{Ability_box\n|desc =\nold desc\n}}\n"
        metadata = self.write_baseline(slug, title, before)
        page = self.write_work_page(slug, before, metadata=metadata)
        page.write_text(before.replace("old desc", "hand edited"), encoding="utf-8")
        self.write_render({"title": title, "filename": slug, "status": "unsupported_info", "before_wikitext": before})
        self.write_drafts({"title": title, "status": "draft", "proposed_desc": "New desc.", "checkme": None})

        result = self.run_script("--apply")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("work page hash conflict", result.stderr)
        self.assertIn("hand edited", page.read_text(encoding="utf-8"))

    def test_changed_baseline_is_rejected(self):
        slug = "Stale_(talent)"
        title = "Stale (talent)"
        before = "{{Ability_box\n|desc =\nold\n}}\n"
        metadata = self.write_baseline(slug, title, before)
        page = self.write_work_page(slug, before, metadata=metadata)
        self.write_render({"title": title, "filename": slug, "status": "unsupported_info", "before_wikitext": before})
        self.write_drafts({"title": title, "status": "draft", "proposed_desc": "New desc.", "checkme": None})
        (self.baseline / slug / "page").write_text(before + "new baseline tail\n", encoding="utf-8")

        result = self.run_script("--apply")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("renderer baseline differs", result.stderr)
        self.assertEqual(page.read_text(encoding="utf-8"), before)

    def test_unknown_and_duplicate_drafts_are_rejected(self):
        self.write_render({"title": "Known", "filename": "Known_(talent)", "status": "unsupported_info"})
        self.write_drafts({"title": "Unknown", "status": "draft", "proposed_desc": "text"})
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing from renderer output", result.stderr)

        self.write_drafts(
            {"title": "Known", "status": "unresolved"},
            {"title": "Known", "status": "unresolved"},
        )
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate draft", result.stderr)


if __name__ == "__main__":
    unittest.main()
