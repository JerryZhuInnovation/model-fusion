#!/usr/bin/env python3
"""Unit tests for model-fusion scripts (no network)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fuse  # noqa: E402
import probe  # noqa: E402


class TestProfileSlug(unittest.TestCase):
    def test_load_profile_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["MODEL_FUSION_HOME"] = td
            for bad in ["../etc", "Default", "has space", "", "a/b"]:
                with self.assertRaises(ValueError):
                    fuse.load_profile(bad)

    def test_load_profile_rejects_uppercase_space(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["MODEL_FUSION_HOME"] = td
            with self.assertRaises(ValueError):
                fuse.load_profile("Coding Review")
            with self.assertRaises(ValueError):
                fuse.load_profile("FOO")


class TestTemperature(unittest.TestCase):
    def test_default_rotation_three(self):
        self.assertEqual(
            fuse.temp_sequence({"mode": "default"}, 3),
            [0.4, 0.7, 1.0],
        )

    def test_fixed(self):
        self.assertEqual(
            fuse.temp_sequence({"mode": "fixed", "fixed": 0.5}, 3),
            [0.5, 0.5, 0.5],
        )

    def test_custom_cycle(self):
        self.assertEqual(
            fuse.temp_sequence({"mode": "custom", "sequence": [0.1, 0.9]}, 3),
            [0.1, 0.9, 0.1],
        )

    def test_default_wraps(self):
        self.assertEqual(
            fuse.temp_sequence({"mode": "default"}, 5),
            [0.4, 0.7, 1.0, 0.4, 0.7],
        )


class TestJudgeBlock(unittest.TestCase):
    def test_b_mode_separates_supplement(self):
        workers = [
            {"model_id": "a", "role": "primary"},
            {"model_id": "b", "role": "supplement"},
        ]
        candidates = [
            {"model_id": "a", "ok": True, "content": "MAIN IDEA"},
            {"model_id": "b", "ok": True, "content": "SIDE NOTE"},
        ]
        text = fuse.build_judge_task("B", "do X", candidates, workers)
        self.assertIn("主方案候选", text)
        self.assertIn("MAIN IDEA", text)
        self.assertIn("补充视角", text)
        self.assertIn("SIDE NOTE", text)
        # primary block should appear before supplement section's sample content order
        self.assertLess(text.index("MAIN IDEA"), text.index("SIDE NOTE"))

    def test_a_mode_single_pool(self):
        workers = [
            {"model_id": "a", "role": "primary"},
            {"model_id": "b", "role": "primary"},
        ]
        candidates = [
            {"model_id": "a", "ok": True, "content": "AAA"},
            {"model_id": "b", "ok": True, "content": "BBB"},
        ]
        text = fuse.build_judge_task("A", "do Y", candidates, workers)
        self.assertIn("全部候选", text)
        self.assertNotIn("主方案候选", text)


class TestProbeNoKey(unittest.TestCase):
    def test_probe_skips_without_secrets(self):
        saved = {}
        for k in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENROUTER_API_KEY",
            "DEEPSEEK_API_KEY",
            "MOONSHOT_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
        ):
            saved[k] = os.environ.pop(k, None)
        try:
            raw = probe.probe(do_ping=False, timeout_ms=1000, skip_ping=True)
            text = json.dumps(raw)
            self.assertNotIn("sk-", text)
            self.assertNotIn("Bearer ", text)
            # all missing keys → roles empty
            for c in raw["candidates"]:
                if c["id"].startswith("hint:"):
                    continue
                self.assertFalse(c["ping_ok"])
                self.assertEqual(c["roles"], [])
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


class TestApiFormat(unittest.TestCase):
    def test_anthropic_is_worker_when_key_present_no_ping(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-not-real"
        try:
            raw = probe.probe(do_ping=False, timeout_ms=1000, skip_ping=True)
            item = next(c for c in raw["candidates"] if c["id"] == "anthropic")
            self.assertEqual(item["api_format"], "anthropic")
            self.assertIn("worker", item["roles"])
            self.assertTrue(item["ping_ok"])
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_resolve_worker_endpoint_returns_format(self):
        w = {
            "model_id": "a",
            "api_key_env": "X",
            "base_url": "https://api.anthropic.com/v1",
            "model": "claude",
            "api_format": "anthropic",
        }
        os.environ["X"] = "k"
        try:
            key, base, model, fmt = fuse.resolve_worker_endpoint(w)
            self.assertEqual(fmt, "anthropic")
            self.assertEqual(base, "https://api.anthropic.com/v1")
        finally:
            os.environ.pop("X", None)

    def test_split_system(self):
        sys_t, rest = fuse._split_system(
            [
                {"role": "system", "content": "S1"},
                {"role": "user", "content": "U"},
                {"role": "assistant", "content": "A"},
                {"role": "system", "content": "S2"},
            ]
        )
        self.assertEqual(sys_t, "S1\n\nS2")
        self.assertEqual([m["role"] for m in rest], ["user", "assistant"])

    def test_extract_anthropic_content(self):
        raw = {
            "content": [
                {"type": "text", "text": "Hello "},
                {"type": "text", "text": "World"},
            ]
        }
        self.assertEqual(fuse._extract_content(raw, "anthropic"), "Hello World")

    def test_extract_openai_content(self):
        raw = {"choices": [{"message": {"content": "OK"}}]}
        self.assertEqual(fuse._extract_content(raw, "openai"), "OK")


class TestFuseMissingProfile(unittest.TestCase):
    def test_missing_profile_error(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["MODEL_FUSION_HOME"] = td
            result = fuse.run_fusion("default", "hello", None, max_workers=1)
            self.assertFalse(result["ok"])
            self.assertIn("not found", result["error"])
            for key in ("candidates", "workers_used", "strategy"):
                self.assertIn(key, result)


class TestSingleSuccessSkipsJudge(unittest.TestCase):
    def test_single_success_uses_candidate_and_writes_run(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["MODEL_FUSION_HOME"] = td
            os.environ["FAKE_API_KEY"] = "x"
            profile = {
                "version": 1,
                "strategy": "A",
                "judge": {
                    "model_id": "j",
                    "api_key_env": "FAKE_API_KEY",
                    "base_url": "http://127.0.0.1:9",
                    "model": "j",
                },
                "workers": [
                    {
                        "model_id": "w1",
                        "api_key_env": "FAKE_API_KEY",
                        "base_url": "http://127.0.0.1:9",
                        "model": "m",
                        "samples": 1,
                        "role": "primary",
                    }
                ],
                "temperature": {"mode": "default"},
                "strategy_options": {
                    "on_partial_failure": "continue_with_available",
                    "min_success": 2,
                    "timeout_s": 1,
                    "hard_only": True,
                },
            }
            (Path(td) / "default.json").write_text(
                json.dumps(profile), encoding="utf-8"
            )

            def fake_complete(*a, **k):
                return {
                    "ok": True,
                    "content": "ONLY_ANSWER",
                    "error": None,
                    "latency_ms": 1,
                }

            with mock.patch.object(fuse, "complete_once", side_effect=fake_complete):
                result = fuse.run_fusion("default", "task", None, max_workers=1)

            self.assertTrue(result["ok"])
            self.assertTrue(result["degraded"])
            self.assertEqual(result["final"], "ONLY_ANSWER")
            self.assertEqual(result.get("note"), "single_success_skipped_judge")
            self.assertTrue(result.get("run_path"))
            self.assertTrue(Path(result["run_path"]).is_file())


class TestAllFailWritesRun(unittest.TestCase):
    def test_all_worker_fail_still_writes_run(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["MODEL_FUSION_HOME"] = td
            os.environ["FAKE_API_KEY"] = "x"
            profile = {
                "version": 1,
                "strategy": "A",
                "judge": {
                    "model_id": "j",
                    "api_key_env": "FAKE_API_KEY",
                    "base_url": "http://127.0.0.1:9",
                    "model": "j",
                },
                "workers": [
                    {
                        "model_id": "w1",
                        "api_key_env": "FAKE_API_KEY",
                        "base_url": "http://127.0.0.1:9",
                        "model": "m",
                        "samples": 2,
                        "role": "primary",
                    }
                ],
                "temperature": {"mode": "default"},
                "strategy_options": {
                    "on_partial_failure": "continue_with_available",
                    "min_success": 2,
                    "timeout_s": 1,
                    "hard_only": True,
                },
            }
            (Path(td) / "default.json").write_text(
                json.dumps(profile), encoding="utf-8"
            )

            def fake_complete(*a, **k):
                return {
                    "ok": False,
                    "content": None,
                    "error": "boom",
                    "latency_ms": 1,
                }

            with mock.patch.object(fuse, "complete_once", side_effect=fake_complete):
                result = fuse.run_fusion("default", "task", None, max_workers=2)

            self.assertFalse(result["ok"])
            self.assertIn("no successful", result["error"])
            self.assertTrue(result.get("run_path"))
            self.assertTrue(Path(result["run_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
