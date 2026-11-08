import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import review_workflow as workflow


METADATA = {
    "filename": "Aim_(talent)",
    "wiki_title": "Aim (talent)",
    "pageid": 1360,
    "revid": 13778,
}
BASELINE = b"baseline page\r\nexact bytes\n"


class ReviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.record = workflow.initialize_review(METADATA, BASELINE)

    def test_initialization_uses_identity_revision_and_exact_baseline_hash(self):
        self.assertEqual(
            self.record["identity"],
            {
                "kind": "talent",
                "filename": "Aim_(talent)",
                "wiki_title": "Aim (talent)",
                "pageid": 1360,
            },
        )
        self.assertEqual(self.record["baseline"]["revid"], METADATA["revid"])
        self.assertEqual(self.record["baseline"]["page_sha256"], hashlib.sha256(BASELINE).hexdigest())
        self.assertIsNone(self.record["work_page"]["last_managed_sha256"])

    def test_path_is_canonical_under_batch_root(self):
        self.assertEqual(
            workflow.review_path("work/batch-1", "Aim_(talent)"),
            Path("work/batch-1/talents/Aim_(talent)/review.json"),
        )
        with self.assertRaises(workflow.ReviewRecordError):
            workflow.review_path("work/batch-1", "../escape")

    def test_validation_rejects_unsupported_version_and_identity_mismatch(self):
        unsupported = copy.deepcopy(self.record)
        unsupported["schema_version"] = 2
        with self.assertRaisesRegex(workflow.ReviewRecordError, "unsupported schema_version"):
            workflow.validate_review(unsupported)
        with self.assertRaisesRegex(workflow.ReviewRecordError, "identity does not match"):
            workflow.validate_review(self.record, {**METADATA, "pageid": 99})

    def test_conditional_rules_fail_closed(self):
        out_of_date = copy.deepcopy(self.record)
        out_of_date["state"] = "mark_out_of_date"
        with self.assertRaisesRegex(workflow.ReviewRecordError, "requires nonempty notes"):
            workflow.validate_review(out_of_date)

        blocked = copy.deepcopy(self.record)
        blocked["workflow_status"] = "blocked"
        with self.assertRaisesRegex(workflow.ReviewRecordError, "requires blocker entries"):
            workflow.validate_review(blocked)

        skipped = copy.deepcopy(self.record)
        skipped["automation"]["deterministic"] = {"mode": "skip", "notes": "  "}
        with self.assertRaisesRegex(workflow.ReviewRecordError, "skip requires nonempty notes"):
            workflow.validate_review(skipped)

    def test_round_trip_is_sorted_utf8_json_with_trailing_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = workflow.review_path(tmp, METADATA["filename"])
            self.record["human_extra"] = "café"
            workflow.write_review(path, self.record, METADATA)
            raw = path.read_bytes()
            self.assertTrue(raw.endswith(b"\n"))
            self.assertIn("café".encode("utf-8"), raw)
            self.assertEqual(workflow.load_review(path, METADATA), self.record)
            self.assertEqual(raw, (json.dumps(self.record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    def test_renderer_merge_preserves_human_and_unknown_fields_and_appends_history(self):
        original = copy.deepcopy(self.record)
        original.update({"human_extension": {"keep": True}})
        original["automation"]["human_extension"] = "preserve"
        original["render"]["renderer_extension"] = {"preserve": True}
        original["history"].append({
            "at": "2026-11-08T00:00:00Z",
            "actor": "human:test",
            "event": "triaged",
            "custom": "keep",
        })
        renderer_event = {
            "at": "2026-11-08T01:00:00Z",
            "actor": "deterministic_renderer",
            "event": "rendered",
        }
        merged = workflow.merge_owned_fields(
            original,
            "renderer",
            {
                "render": {
                    "status": "candidate",
                    "updated_at": "2026-11-08T01:00:00Z",
                    "source_sha256": "a" * 64,
                    "candidate_page_sha256": "b" * 64,
                }
            },
            [renderer_event],
        )
        self.assertEqual(merged["state"], original["state"])
        self.assertEqual(merged["workflow_status"], original["workflow_status"])
        self.assertEqual(merged["notes"], original["notes"])
        self.assertEqual(merged["automation"], original["automation"])
        self.assertEqual(merged["blocked_by"], original["blocked_by"])
        self.assertEqual(merged["human_extension"], original["human_extension"])
        self.assertEqual(merged["render"]["renderer_extension"], {"preserve": True})
        self.assertEqual(merged["history"], original["history"] + [renderer_event])
        self.assertNotIn("status", original["render"])

    def test_owner_cannot_change_fields_outside_ownership(self):
        with self.assertRaisesRegex(workflow.ReviewRecordError, "renderer cannot update fields"):
            workflow.merge_owned_fields(self.record, "renderer", {"state": "keep"})
        with self.assertRaisesRegex(workflow.ReviewRecordError, "human cannot update fields"):
            workflow.merge_owned_fields(self.record, "human", {"render": {}})

    def test_schema_is_checked_in_and_declares_open_objects(self):
        schema = json.loads(workflow.SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
        self.assertTrue(schema["additionalProperties"])
        self.assertIn("mark_out_of_date", schema["properties"]["state"]["enum"])


if __name__ == "__main__":
    unittest.main()
