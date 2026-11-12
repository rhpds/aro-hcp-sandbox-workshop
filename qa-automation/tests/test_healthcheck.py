"""Offline regression tests: python3 -m unittest discover -s qa-automation/tests."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import urllib.error

SPEC = importlib.util.spec_from_file_location(
    "healthcheck", Path(__file__).resolve().parents[1] / "files" / "healthcheck.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def node(name="worker-1", **overrides):
    value = {
        "metadata": {"name": name, "labels": {"node-role.kubernetes.io/worker": ""}},
        "spec": {},
        "status": {"conditions": [
            {"type": "Ready", "status": "True"},
            *[{"type": kind, "status": "False"}
              for kind in ("MemoryPressure", "DiskPressure", "PIDPressure")],
        ]},
    }
    value.update(overrides)
    return value


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.check = MODULE.Healthcheck({
            "aro_hcp_cluster_rg": "test-rg", "aro_hcp_cluster_name": "test-cluster",
            "openshift_api_url": "https://api.example.test:443",
        })

    def test_baseline_workers_need_no_kata_labels_or_runtime(self):
        with patch.object(self.check, "oc_json", return_value={"items": [node(), node("worker-2")]}):
            self.assertIn("2 workers", self.check.workers())

    def test_missing_workers_fail(self):
        with patch.object(self.check, "oc_json", return_value={"items": []}):
            with self.assertRaisesRegex(MODULE.CheckFailure, "baseline"):
                self.check.workers()

    def test_pressure_and_cordons_fail(self):
        for unhealthy in (
            node(spec={"unschedulable": True}),
            node(status={"conditions": [{"type": "Ready", "status": "False"}]}),
            node(status={"conditions": [{"type": "Ready", "status": "True"},
                                         {"type": "MemoryPressure", "status": "True"}]}),
        ):
            with self.subTest(node=unhealthy):
                with patch.object(self.check, "oc_json", return_value={"items": [unhealthy, node("worker-2")]}):
                    with self.assertRaises(MODULE.CheckFailure):
                        self.check.workers()

    def test_degraded_console_is_not_silently_ignored(self):
        operator = {"metadata": {"name": "console"}, "status": {"conditions": [
            {"type": "Available", "status": "True"},
            {"type": "Progressing", "status": "False"},
            {"type": "Degraded", "status": "True"},
        ]}}
        with patch.object(self.check, "oc_json", return_value={"items": [operator]}):
            result = self.check.run_check("Cluster operators", self.check.operators)
        self.assertFalse(result["passed"])
        self.assertIn("console (Degraded=True)", result["message"])

    def test_default_nodepool_alone_is_sufficient(self):
        with patch.object(self.check, "az", return_value=[{
            "name": "default", "properties": {"provisioningState": "Succeeded"},
        }]):
            self.assertIn("Kata pool is not required", self.check.nodepools())

    def test_failed_nodepool_fails(self):
        with patch.object(self.check, "az", return_value=[{
            "name": "default", "properties": {"provisioningState": "Failed"},
        }]):
            with self.assertRaisesRegex(MODULE.CheckFailure, "default"):
                self.check.nodepools()

    def test_required_catalog_channel_is_checked(self):
        with patch.object(self.check, "oc_json", return_value={"status": {
            "catalogSource": "redhat-operators", "channels": [{"name": "stable"}],
        }}):
            self.assertIn("stable", self.check.package("operator", "stable"))
            with self.assertRaisesRegex(MODULE.CheckFailure, "preview-0.9"):
                self.check.package("operator", "preview-0.9")

    def test_wrong_subscription_fails(self):
        self.check.config["azure_subscription"] = "expected-id"
        with patch.object(self.check, "az", return_value={"state": "Enabled", "id": "wrong-id"}):
            with self.assertRaisesRegex(MODULE.CheckFailure, "subscription"):
                self.check.azure_account()

    def test_api_url_default_https_port_is_normalized(self):
        with patch.object(self.check, "oc", side_effect=["https://api.example.test", "admin", "ok", "yes"]):
            self.assertIn("expected API", self.check.bastion_access())

    def test_wrong_cluster_fails(self):
        with patch.object(self.check, "oc", return_value="https://wrong.example.test"):
            with self.assertRaisesRegex(MODULE.CheckFailure, "different cluster"):
                self.check.bastion_access()

    def test_cli_stderr_is_not_exposed(self):
        secret = "this-is-a-secret"
        with patch.object(MODULE.subprocess, "run", return_value=subprocess.CompletedProcess(
            ["oc"], 1, stdout=secret, stderr=secret,
        )):
            result = self.check.run_check("CLI", lambda: self.check.command("oc"))
        self.assertFalse(result["passed"])
        self.assertNotIn(secret, json.dumps(result))

    def test_unexpected_exception_cannot_leak_credentials(self):
        def broken():
            raise ValueError("private-token-and-password")
        result = self.check.run_check("Login", broken)
        self.assertFalse(result["passed"])
        self.assertNotIn("private-token", json.dumps(result))

    def test_http_error_url_and_body_are_not_exposed(self):
        def unauthorized():
            raise urllib.error.HTTPError("https://secret-url", 401, "private-password", {}, None)
        result = self.check.run_check("Login", unauthorized)
        self.assertIn("HTTP 401", result["message"])
        self.assertNotIn("private-password", json.dumps(result))
        self.assertNotIn("secret-url", json.dumps(result))

    def test_timeouts_are_reported_without_command_arguments(self):
        with patch.object(MODULE.subprocess, "run", side_effect=subprocess.TimeoutExpired(
            ["oc", "secret-token"], 10,
        )):
            result = self.check.run_check("Timeout", lambda: self.check.command("oc"))
        self.assertFalse(result["passed"])
        self.assertNotIn("secret-token", json.dumps(result))

    def test_all_probes_report_even_when_one_fails(self):
        def fake_probe(name, function):
            return {"name": name, "passed": name != "Cluster operators", "message": "test"}
        with patch.object(self.check, "run_check", side_effect=fake_probe):
            report = self.check.run()
        self.assertEqual(len(report["checks"]), 15)
        self.assertFalse(report["healthy"])
        self.assertEqual(sum(c["passed"] for c in report["checks"]), 14)


if __name__ == "__main__":
    unittest.main()
