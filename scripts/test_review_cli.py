import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import review as cli
import review_workflow as workflow


class ReviewCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalog_root = self.root / "source"
        self.work = self.root / "work" / "batch"
        self.target = self.root / "target"
        self.metadata = {
            "filename": "Ice_beam_(talent)",
            "wiki_title": "Ice beam (talent)",
            "pageid": 41,
            "revid": 92,
            "canonical_talent_id": "ICE_BEAM",
            "talent_id": "T_ICE_BEAM",
        }
        self.baseline = b"baseline\r\n"
        self._write_talent(self.catalog_root, self.metadata, self.baseline)

    def tearDown(self):
        self.temp.cleanup()

    def _write_talent(self, root, metadata, page):
        talent_dir = root / "talents" / metadata["filename"]
        talent_dir.mkdir(parents=True, exist_ok=True)
        (talent_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        (talent_dir / "page").write_bytes(page)

    def _init_review(self, *, state="update", status="in_progress", notes=""):
        record = workflow.initialize_review(self.metadata, self.baseline, state=state, workflow_status=status, notes=notes)
        path = workflow.review_path(self.work, self.metadata["filename"])
        workflow.write_review(path, record, self.metadata)
        return path

    def _args(self, *args):
        return [args[0], "--work-dir", str(self.work), "--catalog-root", str(self.catalog_root), *args[1:]]

    def test_resolves_wiki_directory_and_canonical_id_forms(self):
        catalog = cli.load_catalog(self.catalog_root)
        for selector in (
            "Ice beam (talent)", "Ice_beam_(talent)", "ice BEAM (TALENT)",
            "ICE_BEAM", "T_ICE_BEAM",
        ):
            with self.subTest(selector=selector):
                self.assertEqual(cli.resolve_selector(selector, catalog).filename, self.metadata["filename"])

    def test_ambiguous_normalized_alias_fails_with_candidates(self):
        other = {
            **self.metadata,
            "filename": "ICE_beam_(talent)",
            "wiki_title": "ICE beam (talent)",
            "pageid": 42,
            "canonical_talent_id": "OTHER_ICE",
            "talent_id": "T_OTHER_ICE",
        }
        self._write_talent(self.catalog_root, other, b"other baseline\n")
        with self.assertRaisesRegex(cli.CLIError, "ambiguous selector"):
            cli.resolve_selector("ice beam (talent)", cli.load_catalog(self.catalog_root))

    def test_fuzzy_match_only_suggests_and_never_resolves(self):
        with self.assertRaisesRegex(cli.CLIError, r"Suggestions \(not selected\)"):
            cli.resolve_selector("Ice beem", cli.load_catalog(self.catalog_root))

    def test_batch_invalid_name_is_atomic(self):
        names = self.root / "names.txt"
        names.write_text("Ice beam (talent)\nNo such talent\n", encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = cli.main(["batch", "set", "--work-dir", str(self.work), "--catalog-root", str(self.catalog_root), "--state", "keep", "--from", str(names), "--yes"])
        self.assertEqual(result, 2)
        self.assertFalse(workflow.review_path(self.work, self.metadata["filename"]).exists())

    def test_batch_file_accepts_spaced_and_underscored_names(self):
        names = self.root / "names.txt"
        names.write_text("# selected talent\nIce beam (talent)\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = cli.main(["batch", "set", "--work-dir", str(self.work), "--catalog-root", str(self.catalog_root), "--state", "keep", "--from", str(names), "--yes"])
        self.assertEqual(result, 0)
        record = workflow.load_review(workflow.review_path(self.work, self.metadata["filename"]))
        self.assertEqual(record["state"], "keep")
        self.assertEqual(record["history"][-1]["event"], "state_changed")

    def test_dry_run_and_noninteractive_confirmation_never_write(self):
        command = self._args("set", "--state", "keep", "Ice beam (talent)")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            dry_result = cli.main([*command, "--dry-run"])
            refused_result = cli.main(command)
        self.assertEqual(dry_result, 0)
        self.assertEqual(refused_result, 2)
        self.assertFalse(workflow.review_path(self.work, self.metadata["filename"]).exists())

    def test_mark_out_of_date_requires_notes_and_records_history(self):
        self._init_review()
        work_page = workflow.review_path(self.work, self.metadata["filename"]).parent / "page"
        original_page = b"Human-edited page; do not synthesize markup.\n"
        work_page.write_bytes(original_page)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            failed = cli.main(self._args("set", "--state", "mark_out_of_date", "Ice beam (talent)", "--yes"))
        self.assertEqual(failed, 2)
        record_path = workflow.review_path(self.work, self.metadata["filename"])
        self.assertEqual(workflow.load_review(record_path)["state"], "update")

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = cli.main(self._args("set", "--state", "mark_out_of_date", "--notes", "Source absent; verify page.", "Ice_beam_(talent)", "--yes"))
        self.assertEqual(result, 0)
        record = workflow.load_review(record_path, self.metadata)
        self.assertEqual(record["state"], "mark_out_of_date")
        self.assertEqual(record["notes"], "Source absent; verify page.")
        self.assertEqual([event["event"] for event in record["history"][-2:]], ["state_changed", "notes_changed"])
        self.assertEqual(work_page.read_bytes(), original_page)

    def test_state_changes_and_blocked_transition_are_validated_and_historicized(self):
        self._init_review()
        for state in ("keep", "ignore", "defer", "update"):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = cli.main(self._args("set", "--state", state, "Ice beam (talent)", "--yes"))
            self.assertEqual(result, 0)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = cli.main(self._args("set", "--workflow-status", "blocked", "--block-reason", "source_ambiguous:Needs source confirmation", "Ice beam (talent)", "--yes"))
        self.assertEqual(result, 0)
        record = workflow.load_review(workflow.review_path(self.work, self.metadata["filename"]))
        self.assertEqual(record["workflow_status"], "blocked")
        self.assertEqual(record["blocked_by"][0]["code"], "source_ambiguous")
        self.assertEqual(len(record["history"]), 6)

    def test_automation_set_requires_skip_reason_and_records_history(self):
        path = self._init_review()
        record = workflow.load_review(path, self.metadata)
        record["automation"]["human_extension"] = {"preserve": True}
        workflow.write_review(path, record, self.metadata)

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            missing_reason = cli.main(self._args(
                "set", "--deterministic-mode", "skip", "Ice beam (talent)", "--yes",
            ))
        self.assertEqual(missing_reason, 2)
        self.assertEqual(workflow.load_review(path, self.metadata)["automation"]["deterministic"]["mode"], "run")

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = cli.main(self._args(
                "set", "--deterministic-mode", "skip", "--deterministic-notes", "Avoid unsupported expression parsing.",
                "--llm-mode", "skip", "--llm-instruction", "Preserve terminology.",
                "--llm-instruction", "Keep page wording concise.", "Ice beam (talent)", "--yes",
            ))
        self.assertEqual(result, 0)
        updated = workflow.load_review(path, self.metadata)
        self.assertEqual(updated["automation"]["deterministic"], {
            "mode": "skip", "notes": "Avoid unsupported expression parsing.",
        })
        self.assertEqual(updated["automation"]["llm"], {
            "mode": "skip", "instructions": ["Preserve terminology.", "Keep page wording concise."],
        })
        self.assertEqual(updated["automation"]["human_extension"], {"preserve": True})
        self.assertEqual(
            [event["event"] for event in updated["history"][-2:]],
            ["deterministic_automation_changed", "llm_automation_changed"],
        )
        for event in updated["history"][-2:]:
            self.assertIn("from", event)
            self.assertIn("to", event)

    def test_automation_modes_and_renderer_extension_instructions_fail_closed(self):
        path = self._init_review()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.main(self._args("set", "--llm-mode", "sometimes", "Ice beam (talent)", "--yes"))
            renderer_result = cli.main(self._args(
                "set", "--llm-instruction", "Add renderer support for custom syntax.",
                "Ice beam (talent)", "--yes",
            ))
        self.assertEqual(renderer_result, 2)
        record = workflow.load_review(path, self.metadata)
        self.assertEqual(record["automation"]["llm"]["instructions"], [])
        self.assertEqual(record["history"], [])

    def _editor_command(self, instructions):
        script = self.root / "editor.py"
        script.write_text(
            "import json, sys\n"
            f"with open(sys.argv[1], 'w', encoding='utf-8') as stream: json.dump({instructions!r}, stream)\n",
            encoding="utf-8",
        )
        return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"

    def _edit_args(self):
        return [
            "instructions", "edit", "--work-dir", str(self.work),
            "--catalog-root", str(self.catalog_root), "Ice beam (talent)", "--yes",
        ]

    def test_instructions_edit_uses_json_strings_and_appends_history(self):
        path = self._init_review()
        editor = self._editor_command(["Retain source terminology.", "Do not alter text outside the ability box."])
        with patch.dict(os.environ, {"EDITOR": editor, "VISUAL": ""}, clear=False):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = cli.main(self._edit_args())
        self.assertEqual(result, 0)
        record = workflow.load_review(path, self.metadata)
        expected = ["Retain source terminology.", "Do not alter text outside the ability box."]
        self.assertEqual(record["automation"]["llm"]["instructions"], expected)
        self.assertEqual(record["history"][-1]["event"], "llm_automation_changed")
        self.assertEqual(record["history"][-1]["from"], [])
        self.assertEqual(record["history"][-1]["to"], expected)

    def test_instructions_edit_keeps_preview_confirmation(self):
        path = self._init_review()
        editor = self._editor_command(["Review source wording only."])
        args = self._edit_args()
        args.remove("--yes")
        with patch.dict(os.environ, {"EDITOR": editor, "VISUAL": ""}, clear=False):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = cli.main(args)
        self.assertEqual(result, 2)
        record = workflow.load_review(path, self.metadata)
        self.assertEqual(record["automation"]["llm"]["instructions"], [])
        self.assertEqual(record["history"], [])

    def test_instructions_edit_rejects_non_string_and_renderer_extension(self):
        path = self._init_review()
        for invalid in (["valid", 3], ["Teach the renderer to parse new syntax."]):
            with self.subTest(invalid=invalid):
                editor = self._editor_command(invalid)
                with patch.dict(os.environ, {"EDITOR": editor, "VISUAL": ""}, clear=False):
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        result = cli.main(self._edit_args())
                self.assertEqual(result, 2)
                record = workflow.load_review(path, self.metadata)
                self.assertEqual(record["automation"]["llm"]["instructions"], [])
                self.assertEqual(record["history"], [])

    def _git_target(self, page=None):
        self._write_talent(self.target, self.metadata, self.baseline if page is None else page)
        subprocess.run(["git", "init", "-q", str(self.target)], check=True)
        subprocess.run(["git", "-C", str(self.target), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.target), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.target), "add", "talents"], check=True)
        subprocess.run(["git", "-C", str(self.target), "commit", "-qm", "baseline"], check=True)

    def test_promotion_requires_approved_and_exports_page_only(self):
        self._git_target()
        self._init_review(status="in_progress")
        work_page = workflow.review_path(self.work, self.metadata["filename"]).parent / "page"
        work_page.write_bytes(b"approved candidate\n")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = cli.main(self._args("promote", "--target-root", str(self.target), "Ice beam (talent)", "--apply"))
        self.assertEqual(result, 2)
        self.assertEqual((self.target / "talents" / self.metadata["filename"] / "page").read_bytes(), self.baseline)

        path = workflow.review_path(self.work, self.metadata["filename"])
        record = workflow.load_review(path)
        updated = workflow.merge_owned_fields(record, "human", {"workflow_status": "approved"}, [{
            "at": "2026-11-08T00:00:00Z", "actor": "human:test", "event": "workflow_status_changed",
            "from": "in_progress", "to": "approved",
        }])
        workflow.write_review(path, updated, self.metadata)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = cli.main(self._args("promote", "--target-root", str(self.target), "Ice beam (talent)"))
        self.assertEqual(result, 0)  # preview only without --apply
        self.assertEqual((self.target / "talents" / self.metadata["filename"] / "page").read_bytes(), self.baseline)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = cli.main(self._args("promote", "--target-root", str(self.target), "Ice beam (talent)", "--apply"))
        self.assertEqual(result, 0)
        self.assertEqual((self.target / "talents" / self.metadata["filename"] / "page").read_bytes(), b"approved candidate\n")
        changed = subprocess.run(["git", "-C", str(self.target), "status", "--porcelain"], check=True, stdout=subprocess.PIPE, text=True).stdout.splitlines()
        self.assertEqual(changed, [f" M talents/{self.metadata['filename']}/page"])

    def test_promotion_rejects_stale_target_baseline(self):
        self._git_target(page=b"stale baseline\n")
        self._init_review(status="approved")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = cli.main(self._args("promote", "--target-root", str(self.target), "Ice beam (talent)", "--apply"))
        self.assertEqual(result, 2)
        self.assertEqual((self.target / "talents" / self.metadata["filename"] / "page").read_bytes(), b"stale baseline\n")


if __name__ == "__main__":
    unittest.main()
