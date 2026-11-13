"""Offline E2E safety/orchestration tests; never contact a real cluster."""
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

FILES = Path(__file__).resolve().parents[1] / "files"
sys.path.insert(0, str(FILES))
SPEC = importlib.util.spec_from_file_location("workshop_e2e", FILES / "e2e.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class E2ETests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.runner = MODULE.E2E({"aro_hcp_cluster_name": "test", "aro_hcp_cluster_rg": "test-rg"}, self.directory.name)

    def test_resource_names_are_unique_and_distinct_from_lab_names(self):
        other = MODULE.E2E({}, self.directory.name)
        self.assertNotEqual(self.runner.pool, other.pool)
        self.assertNotEqual(self.runner.pool, "kata-nodepool")
        self.assertLessEqual(len(self.runner.pool.encode()), 15)
        self.assertTrue(self.runner.namespace.startswith("qa-e2e-"))

    def test_create_records_ownership_before_api_call(self):
        doc = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": self.runner.namespace}}
        def fail(*args, **kwargs):
            self.assertEqual(self.runner.state["owned"][0]["name"], self.runner.namespace)
            raise MODULE.CheckFailure("request interrupted")
        with patch.object(self.runner, "command", side_effect=fail):
            with self.assertRaises(MODULE.CheckFailure):
                self.runner.create(doc)
        journal = json.loads((Path(self.directory.name) / "state.json").read_text())
        self.assertEqual(journal["owned"][0]["kind"], "Namespace")

    def test_preexisting_namespace_is_never_overwritten(self):
        with patch.object(self.runner, "optional", return_value={"metadata": {"name": MODULE.OSC}}), \
             patch.object(self.runner, "objects", return_value=[]), \
             patch.object(self.runner, "create") as create:
            with self.assertRaisesRegex(MODULE.CheckFailure, "Pre-existing"):
                self.runner.install_operator(MODULE.OSC, "operator", "stable")
            create.assert_not_called()

    def test_operator_waits_for_installplan_after_csv_succeeds(self):
        with patch.object(self.runner, "optional", return_value=None), \
             patch.object(self.runner, "create"), patch.object(self.runner, "wait_csv"), \
             patch.object(self.runner, "wait", return_value=True) as wait:
            self.runner.install_operator(MODULE.OSC, "operator", "stable")
        self.assertIn("completed InstallPlan", wait.call_args.args[0])

    def test_installed_kataconfig_uses_ready_node_count(self):
        self.runner.state["nodes"] = ["qa-node-a", "qa-node-b"]
        def predicate(description, function, **kwargs):
            self.assertTrue(function())
        with patch.object(self.runner, "oc") as oc, patch.object(self.runner, "create"), \
             patch.object(self.runner, "oc_json", return_value={"status": {"kataNodes": {"readyNodeCount": 2, "nodeCount": 2}}}), \
             patch.object(self.runner, "wait", side_effect=predicate):
            self.assertIn("completed", self.runner.kata_install())
        self.assertEqual(sum("--overwrite" in call.args for call in oc.call_args_list), 2)

    def test_failed_kata_install_restores_node_taints(self):
        self.runner.state["nodes"] = ["qa-node-a", "qa-node-b"]
        def predicate(description, function, **kwargs):
            function()
        with patch.object(self.runner, "oc") as oc, patch.object(self.runner, "create"), \
             patch.object(self.runner, "oc_json", return_value={"status": {"kataNodes": {"failedToInstall": ["qa-node-a"]}}}), \
             patch.object(self.runner, "wait", side_effect=predicate):
            with self.assertRaisesRegex(MODULE.CheckFailure, "failed nodes"):
                self.runner.kata_install()
        self.assertEqual(sum("--overwrite" in call.args for call in oc.call_args_list), 2)

    def test_empty_cleanup_never_deletes_resources(self):
        with patch.object(self.runner, "delete") as delete, patch.object(self.runner, "command") as command:
            self.runner.cleanup()
        delete.assert_not_called()
        command.assert_not_called()

    def test_cleanup_deletes_only_owned_namespace(self):
        self.runner.own("Namespace", self.runner.namespace)
        with patch.object(self.runner, "delete_owned") as delete, patch.object(self.runner, "optional", return_value=None):
            self.runner.cleanup()
        delete.assert_called_once_with("namespace", self.runner.namespace)

    def test_cleanup_continues_after_one_failure(self):
        self.runner.own("Namespace", self.runner.namespace)
        self.runner.own("Namespace", MODULE.AGENT)
        with patch.object(self.runner, "delete_owned", side_effect=[MODULE.CheckFailure("failed"), None]) as delete, \
             patch.object(self.runner, "optional", return_value=None):
            with self.assertRaisesRegex(MODULE.CheckFailure, "Cleanup incomplete"):
                self.runner.cleanup()
        self.assertEqual(delete.call_count, 2)

    def test_mismatched_kubernetes_owner_is_not_deleted(self):
        with patch.object(self.runner, "optional", return_value={"metadata": {"labels": {}}}), \
             patch.object(self.runner, "delete") as delete:
            with self.assertRaisesRegex(MODULE.CheckFailure, "Ownership label differs"):
                self.runner.delete_owned("namespace", MODULE.OSC)
        delete.assert_not_called()

    def test_matching_kubernetes_owner_can_be_deleted(self):
        resource = {"metadata": {"labels": {MODULE.LABEL: self.runner.run_id}}}
        with patch.object(self.runner, "optional", return_value=resource), \
             patch.object(self.runner, "delete") as delete:
            self.runner.delete_owned("namespace", self.runner.namespace)
        delete.assert_called_once_with("namespace", self.runner.namespace)

    def test_nonexistent_pool_is_not_deleted_after_rejected_create(self):
        self.runner.own("AzureNodePool", self.runner.pool)
        with patch.object(self.runner, "az", return_value=[]), patch.object(self.runner, "command") as command:
            self.runner.cleanup()
        command.assert_not_called()

    def test_pool_with_different_ownership_tag_is_not_deleted(self):
        self.runner.own("AzureNodePool", self.runner.pool)
        with patch.object(self.runner, "az", return_value=[{"name": self.runner.pool, "tags": {}}]), \
             patch.object(self.runner, "command") as command:
            with self.assertRaisesRegex(MODULE.CheckFailure, "Cleanup incomplete"):
                self.runner.cleanup()
        command.assert_not_called()

    def test_failed_prerequisite_is_skipped_not_reported_as_pass(self):
        self.runner.result("first", "First", lambda: (_ for _ in ()).throw(MODULE.CheckFailure("broken")))
        with patch.object(self.runner, "command") as command:
            result = self.runner.result("next", "Next", lambda: self.runner.command("oc"), ("first",))
        command.assert_not_called()
        self.assertEqual(result["status"], "SKIP")

    def test_cli_failures_cannot_expose_secret_stdin(self):
        with patch.object(MODULE.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "secret", "private-password")):
            result = self.runner.run_check("Secret", lambda: self.runner.command("oc", stdin="secret"))
        self.assertFalse(result["passed"])
        self.assertNotIn("private-password", json.dumps(result))
        self.assertNotIn("secret", result["message"])

    def test_new_scheduler_event_can_omit_taint_key(self):
        event = {"reason": "FailedScheduling", "message": "2 node(s) had untolerated taint(s)"}
        responses = [{"scheduling": {}}, {"spec": {}}, {"items": [event]}]
        def predicate(description, function, **kwargs):
            self.assertTrue(function())
        with patch.object(self.runner, "pod"), \
             patch.object(self.runner, "oc_json", side_effect=responses), \
             patch.object(self.runner, "wait", side_effect=predicate):
            self.assertIn("Pending", self.runner.scheduling_failure())

    def test_timeout_is_bounded(self):
        with patch.object(MODULE.time, "monotonic", side_effect=[0, 1]):
            with self.assertRaisesRegex(MODULE.CheckFailure, "Timed out"):
                self.runner.wait("test", lambda: False, timeout=1)

    def test_ready_requires_explicit_true_condition(self):
        self.assertFalse(MODULE.ready({"status": {}}))
        self.assertFalse(MODULE.ready({"status": {"conditions": [{"type": "Ready", "status": "False"}]}}))
        self.assertTrue(MODULE.ready({"status": {"conditions": [{"type": "Ready", "status": "True"}]}}))

    def test_baseline_operator_failure_does_not_block_lab_exercises(self):
        names = ("preflight", "operators", "boundary", "azure_inventory", "identity", "provision_pool",
                 "install_operator", "map_nodes", "node_setup", "acr_config", "kata_install", "runtime",
                 "scheduling_failure", "scheduling_fix", "kernels", "processes", "tenants", "sysrq", "modules",
                 "node_health", "agent_install", "agent_template", "warm_pool", "claim", "agent_security", "cleanup")
        with ExitStack() as stack:
            mocks = {name: stack.enter_context(patch.object(self.runner, name, return_value="verified")) for name in names}
            mocks["operators"].side_effect = MODULE.CheckFailure("console degraded")
            report = self.runner.run()
        self.assertFalse(report["healthy"])
        self.assertEqual(sum(r["status"] == "FAIL" for r in report["checks"]), 1)
        mocks["provision_pool"].assert_called_once()
        mocks["cleanup"].assert_called_once()
        self.assertTrue((Path(self.directory.name) / "report.json").exists())

    def test_known_console_degradation_is_skipped_and_overall_can_pass(self):
        def fake_check(name, function):
            if name == "Baseline cluster operators":
                return {"passed": False,
                        "message": "Unhealthy cluster operators: console (Degraded=True)"}
            return {"passed": True, "message": "verified"}
        with patch.object(self.runner, "run_check", side_effect=fake_check):
            report = self.runner.run()
        baseline = next(r for r in report["checks"] if r["key"] == "baseline")
        self.assertEqual(baseline["status"], "SKIP")
        self.assertIn("Known behaviour", baseline["message"])
        self.assertTrue(report["healthy"])
        saved = json.loads((Path(self.directory.name) / "report.json").read_text())
        self.assertEqual(saved, report)

    def test_other_operator_issues_still_fail(self):
        for message in (
            "Unhealthy cluster operators: authentication (Degraded=True)",
            "Unhealthy cluster operators: console (Available=False, Degraded=True)",
            "Unhealthy cluster operators: console (Degraded=True); ingress (Degraded=True)",
            "oc request failed (exit 1)",
        ):
            with self.subTest(message=message):
                def fail():
                    raise MODULE.CheckFailure(message)
                result = self.runner.result("baseline", "Baseline cluster operators", fail)
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(result["message"], message)

    def test_healthy_baseline_operators_pass(self):
        result = self.runner.result("baseline", "Baseline cluster operators", lambda: "healthy")
        self.assertEqual(result["status"], "PASS")

    def test_interruption_still_runs_cleanup(self):
        with patch.object(self.runner, "preflight", side_effect=MODULE.AbortRun()), \
             patch.object(self.runner, "cleanup", return_value="cleaned") as cleanup:
            report = self.runner.run()
        cleanup.assert_called_once()
        self.assertFalse(report["healthy"])
        self.assertTrue(any(r["key"] == "cancelled" for r in report["checks"]))


if __name__ == "__main__":
    unittest.main()
