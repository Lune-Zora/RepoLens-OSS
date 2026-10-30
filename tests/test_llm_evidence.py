"""Isolated HTTP mocks and synthetic CLI fixtures; never counted as model experiments."""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from replens.llm import LLMError, OpenAICompatLLM

SPEC = importlib.util.spec_from_file_location(
    "demo_evidence_runner", Path(__file__).resolve().parents[1] / "scripts" / "run_llm_demo.py")
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)


class HTTPAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {"REPLENS_LLM_TRACE_DIR": str(self.root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = OpenAICompatLLM("http://localhost/v1", "synthetic-test-secret", purpose="plan")

    def response(self, content):
        body = json.dumps({"model": "test-model", "choices": [{"message": {"content": content}}],
                           "usage": {"total_tokens": 9}}).encode()
        return contextlib.nullcontext(SimpleNamespace(read=lambda: body))

    def record(self):
        paths = list(self.root.glob("*.json"))
        self.assertEqual(len(paths), 1)
        return json.loads(paths[0].read_text(encoding="utf-8"))

    def test_records_response_usage_and_redacts_key(self):
        with patch("urllib.request.urlopen", return_value=self.response('{"answer": 1}')):
            self.assertEqual(self.client.chat_json("system", "synthetic-test-secret"), {"answer": 1})
        row = self.record()
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["usage"]["total_tokens"], 9)
        self.assertEqual(row["purpose"], "plan")
        self.assertNotIn("synthetic-test-secret", json.dumps(row))
        self.assertNotIn("Authorization", json.dumps(row))

    def test_network_error_is_a_recorded_call_attempt(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            with self.assertRaises(LLMError):
                self.client.chat_json("system", "user")
        row = self.record()
        self.assertEqual(row["status"], "error")
        self.assertIn("offline", row["error"]["message"])

    def test_invalid_json_preserves_raw_response(self):
        with patch("urllib.request.urlopen", return_value=self.response("not json")):
            with self.assertRaises(LLMError):
                self.client.chat_json("system", "user")
        self.assertEqual(self.record()["status"], "error")
        self.assertIn("not json", self.record()["response_text"])

    def test_disabled_audit_preserves_normal_client_behavior(self):
        with patch.dict(os.environ, {"REPLENS_LLM_TRACE_DIR": ""}), \
             patch("urllib.request.urlopen", return_value=self.response('{"answer": 1}')):
            self.assertEqual(self.client.chat_json("system", "user"), {"answer": 1})
        self.assertEqual(list(self.root.iterdir()), [])


class DemoContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "isolated-synthetic-project"
        self.root.mkdir()
        (self.root / "README.md").write_text("Synthetic fixture, not a model result.", encoding="utf-8")
        self.output = self.root.parent / "output"
        self.calls = []

    def fake_cli(self, label, command, env, output, commands):
        self.calls.append(label)
        self.assertEqual(env["REPLENS_REQUIRE_LLM"], "1")
        code = self.failure_code if label == self.failure_stage else 0
        commands.append({"label": label, "exit_code": code})
        if label == "run":
            report = {"fixture": "isolated synthetic CLI response", "status": "VERIFIED",
                      "plan": {"generated_by": "llm"}, "provenance": {"status": "WRITTEN"},
                      "patch_note": "LLM synthetic fixture"}
            demo._save(output / "report.json", report)
            traces = output / "model-requests"
            for purpose in ("plan", "patch"):
                demo._save(traces / (purpose + ".json"), {
                    "fixture": "isolated synthetic trace", "purpose": purpose, "status": "success",
                    "context": {"generation_round": 1}, "duration_seconds": 0.1})
            if self.missing_bundle:
                return code, "", ""
            bundle = self.root / "examples/demo_repo/.replens/provenance/synthetic-new-bundle"
            bundle.mkdir(parents=True)
            demo._save(bundle / "report.json", report)
            (bundle / "report.md").write_text("Synthetic fixture", encoding="utf-8")
            return code, "", "证据包已生成: " + str(bundle)
        replay = {"status": "PASS", "materials_status": "PASS", "replay_status": "PASS",
                  "repair_status": "VERIFIED", "validation_matches": not self.mismatch}
        demo._save(output / "replay.json", replay)
        if self.change_source:
            (self.root / "README.md").write_text("changed during verification", encoding="utf-8")
        return code, "", ""

    def execute(self, failure_stage=None, missing_bundle=False, mismatch=False, change_source=False):
        self.failure_stage = failure_stage
        self.failure_code = 7
        self.missing_bundle = missing_bundle
        self.mismatch = mismatch
        self.change_source = change_source
        with patch.object(demo, "ROOT", self.root), patch.object(demo, "_git", return_value="fixture"), \
             patch.object(demo, "_run", side_effect=self.fake_cli):
            code = demo.main(["--skip-check", "--output-dir", str(self.output)])
        summary = json.loads((self.output / "summary.json").read_text(encoding="utf-8"))
        return code, summary

    def test_success_requires_both_commands_and_complete_trace(self):
        code, summary = self.execute()
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, ["run", "verify"])
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(summary["model_calls"]["call_attempts"], 2)
        self.assertTrue((self.output / "source-snapshot.zip").is_file())

    def test_run_nonzero_is_not_success(self):
        code, summary = self.execute(failure_stage="run")
        self.assertEqual(code, 2)
        self.assertEqual(summary["status"], "FAILED")
        self.assertEqual(self.calls, ["run"])
        self.assertEqual(summary["model_calls"]["call_attempts"], 2)

    def test_missing_bundle_is_not_success(self):
        code, summary = self.execute(missing_bundle=True)
        self.assertEqual(code, 2)
        self.assertEqual(summary["status"], "FAILED")

    def test_verify_nonzero_is_not_success(self):
        code, summary = self.execute(failure_stage="verify")
        self.assertEqual(code, 2)
        self.assertEqual(summary["status"], "FAILED")

    def test_zero_exit_with_mismatched_replay_is_not_success(self):
        code, summary = self.execute(mismatch=True)
        self.assertEqual(code, 2)
        self.assertEqual(summary["status"], "FAILED")

    def test_source_change_invalidates_result(self):
        code, summary = self.execute(change_source=True)
        self.assertEqual(code, 2)
        self.assertEqual(summary["source_files_changed"], ["README.md"])

    def test_incomplete_trace_cannot_be_a_success(self):
        directory = self.root / "traces"
        directory.mkdir()
        demo._save(directory / "pending.json", {"status": "pending", "purpose": "plan"})
        with self.assertRaises(ValueError):
            demo._trace_summary(directory)


if __name__ == "__main__":
    unittest.main()
