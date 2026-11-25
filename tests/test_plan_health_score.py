#!/usr/bin/env python3
"""Tests for plan_health_score.py — stdlib only, no dependencies."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(ROOT, "plan_health_score.py")
EXAMPLES = os.path.join(ROOT, "examples")


def run(*args):
    proc = subprocess.run([sys.executable, TOOL, *args],
                          capture_output=True, text=True)
    return proc


def load(name):
    with open(os.path.join(EXAMPLES, name), encoding="utf-8") as f:
        return json.load(f)


def full_args(plan="plan.json"):
    return [os.path.join(EXAMPLES, plan), "--budget", "5",
            "--decisions", os.path.join(EXAMPLES, "decisions.jsonl"),
            "--findings", os.path.join(EXAMPLES, "findings.json"),
            "--evidence", os.path.join(EXAMPLES, "evidence.json"),
            "--json"]


class TestHealthScore(unittest.TestCase):
    def test_healthy_plan_scores_100(self):
        proc = run(*full_args())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["score"], 100.0)
        self.assertEqual(result["band"], "HEALTHY")
        self.assertEqual(result["rules_version"], "1.0.0")

    def test_unhealthy_plan_is_unhealthy(self):
        proc = run(os.path.join(EXAMPLES, "plan-unhealthy.json"),
                   "--budget", "5",
                   "--decisions", os.path.join(EXAMPLES, "decisions-unhealthy.jsonl"),
                   "--findings", os.path.join(EXAMPLES, "findings-unhealthy.json"),
                   "--evidence", os.path.join(EXAMPLES, "evidence-unhealthy.json"),
                   "--json")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertLess(result["score"], 60)
        self.assertEqual(result["band"], "UNHEALTHY")
        by_id = {c["id"]: c for c in result["components"]}
        self.assertEqual(by_id["policy"]["status"], "FAIL")
        self.assertEqual(by_id["cost"]["status"], "UNKNOWN")

    def test_unknown_cost_never_passes_silently(self):
        # Budget given but one step unpriced: cost is UNKNOWN, score < 100.
        proc = run(os.path.join(EXAMPLES, "plan-unhealthy.json"),
                   "--budget", "5", "--json")
        result = json.loads(proc.stdout)
        by_id = {c["id"]: c for c in result["components"]}
        self.assertEqual(by_id["cost"]["status"], "UNKNOWN")
        self.assertIsNone(by_id["cost"]["value"])
        self.assertLess(result["score"], 100)

    def test_missing_inputs_are_unknown(self):
        # Plan only, no decisions/findings/evidence/budget: three UNKNOWNs.
        proc = run(os.path.join(EXAMPLES, "plan.json"), "--json")
        result = json.loads(proc.stdout)
        by_id = {c["id"]: c for c in result["components"]}
        for cid in ("cost", "decision", "policy", "evidence"):
            self.assertEqual(by_id[cid]["status"], "UNKNOWN", cid)
        self.assertLess(result["score"], 100)

    def test_partial_gating_scores_partially(self):
        plan = load("plan.json")
        plan["steps"].append({"id": "s5", "action": "Publish the summary",
                             "why": "Share it", "failure_points": ["wrong site"],
                             "reversible": False, "cost": 0.1})
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "plan.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(plan, f)
            proc = run(path, "--json")
        result = json.loads(proc.stdout)
        by_id = {c["id"]: c for c in result["components"]}
        self.assertEqual(by_id["gating"]["value"], 0.5)

    def test_over_budget_cost_scores_zero(self):
        proc = run(os.path.join(EXAMPLES, "plan.json"),
                   "--budget", "0.5", "--json")
        result = json.loads(proc.stdout)
        by_id = {c["id"]: c for c in result["components"]}
        self.assertEqual(by_id["cost"]["value"], 0.0)

    def test_slightly_over_budget_scores_half(self):
        proc = run(os.path.join(EXAMPLES, "plan.json"),
                   "--budget", "1.7", "--json")
        result = json.loads(proc.stdout)
        by_id = {c["id"]: c for c in result["components"]}
        self.assertEqual(by_id["cost"]["value"], 0.5)

    def test_waived_high_finding_does_not_fail_policy(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "findings.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"findings": [{"id": "SEC-01", "severity": "high",
                                         "waived": True}]}, f)
            proc = run(os.path.join(EXAMPLES, "plan.json"),
                       "--findings", path, "--json")
        result = json.loads(proc.stdout)
        by_id = {c["id"]: c for c in result["components"]}
        self.assertEqual(by_id["policy"]["value"], 1.0)

    def test_custom_rules_change_score_and_version_shows(self):
        rules = load("rules.json")
        rules["version"] = "2.0.0-test"
        rules["components"][0]["weight"] = 100
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "rules.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rules, f)
            proc = run(*full_args(), "--rules", path)
        result = json.loads(proc.stdout)
        self.assertEqual(result["rules_version"], "2.0.0-test")
        self.assertNotEqual(result["score"], 100.0)

    def test_bad_json_exits_3(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bad.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not json")
            proc = run(path)
        self.assertEqual(proc.returncode, 3)

    def test_missing_plan_file_exits_3(self):
        proc = run(os.path.join(EXAMPLES, "no-such-plan.json"))
        self.assertEqual(proc.returncode, 3)


if __name__ == "__main__":
    unittest.main()
