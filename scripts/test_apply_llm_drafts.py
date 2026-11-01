import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


class ApplyDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.talents = self.root / "talents"
        self.render = self.root / "render.jsonl"
        self.drafts = self.root / "drafts.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def write_page(self, slug, text):
        page = self.talents / slug / "page"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(text, encoding="utf-8")
        return page

    def run_script(self, *extra):
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPTS / "apply_llm_drafts.py"),
                str(self.render),
                *([str(self.drafts)] if self.drafts.exists() else []),
                "--talents-dir",
                str(self.talents),
                *extra,
            ],
            capture_output=True,
            text=True,
            cwd=SCRIPTS,
        )

    def test_removed_source_page_becomes_delete_marker(self):
        slug = "Gone_(talent)"
        self.write_page(slug, "{{Ability_box\n|desc =\nstale text\n}}\n\n[[Category: Removed Talents]]")
        self.render.write_text(
            json.dumps(
                {
                    "title": "Gone (talent)",
                    "filename": slug,
                    "status": "unmatched_source",
                    "source_match_status": "removed_or_renamed",
                    "before_wikitext": "ignored",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        result = self.run_script("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.talents / slug / "page").read_text(encoding="utf-8"), "DELETE\n")

    def test_draft_applies_checkme_prefix_and_keeps_page_text(self):
        slug = "Live_(talent)"
        before = "Lead text.\n\n{{Ability_box\n|name = Live\n|desc =\nold desc\n}}\n\nTrailing note.\n"
        self.write_page(slug, before)
        self.render.write_text(
            json.dumps(
                {
                    "title": "Live (talent)",
                    "filename": slug,
                    "status": "unsupported_info",
                    "before_wikitext": before,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.drafts.write_text(
            json.dumps(
                {
                    "title": "Live (talent)",
                    "status": "draft",
                    "proposed_desc": "New {{cts|1|5}} desc.",
                    "checkme": "Old desc omits the cap.",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        result = self.run_script("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        page = (self.talents / slug / "page").read_text(encoding="utf-8")
        self.assertIn("XXX CHECKME XXX New {{cts|1|5}} desc.", page)
        self.assertNotIn("old desc", page)
        self.assertTrue(page.startswith("Lead text."))
        self.assertTrue(page.rstrip().endswith("Trailing note."))

    def test_diverged_page_is_rejected_without_writing(self):
        slug = "Drift_(talent)"
        before = "{{Ability_box\n|desc =\nold desc\n}}\n"
        self.write_page(slug, before.replace("old desc", "hand edited"))
        self.render.write_text(
            json.dumps(
                {
                    "title": "Drift (talent)",
                    "filename": slug,
                    "status": "unsupported_info",
                    "before_wikitext": before,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.drafts.write_text(
            json.dumps(
                {
                    "title": "Drift (talent)",
                    "status": "draft",
                    "proposed_desc": "New desc.",
                    "checkme": None,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        result = self.run_script("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("diverged", result.stderr)
        self.assertIn("hand edited", (self.talents / slug / "page").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
