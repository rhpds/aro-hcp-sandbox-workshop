#!/usr/bin/env python3
"""Workshop E2E. Mutates only run-owned resources; configuration is stdin-only."""
import base64
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

from healthcheck import CheckFailure, Healthcheck

OSC = "openshift-sandboxed-containers-operator"
AGENT = "agent-sandbox-operator"
IMAGE = "registry.access.redhat.com/ubi9/ubi-minimal"
LABEL = "workshop.qa/run"
TOLERATION = {"key": "kata", "operator": "Equal", "value": "true", "effect": "NoSchedule"}


class AbortRun(BaseException):
    """Cancellation bypasses individual probe error handling and triggers cleanup."""


def ready(resource):
    return any(c.get("type") == "Ready" and c.get("status") == "True"
               for c in resource.get("status", {}).get("conditions", []))


class E2E(Healthcheck):
    def __init__(self, config, directory):
        super().__init__(config)
        self.timeout = 60
        self.directory = Path(directory)
        self.run_id = uuid.uuid4().hex[:10]
        self.namespace = "qa-e2e-" + self.run_id
        self.pool = "qa-kata-" + self.run_id
        self.kataconfig = "qa-kata-" + self.run_id
        self.results = []
        self.state = {"run_id": self.run_id, "namespace": self.namespace,
                      "nodepool": self.pool, "kataconfig": self.kataconfig,
                      "owned": [], "nodes": [], "baseline_nodes": {}, "initial_crds": []}
        self.persist()

    def persist(self):
        path = self.directory / "state.json"
        path.write_text(json.dumps(self.state, indent=2))
        path.chmod(0o600)

    def command(self, *args, stdin=None, timeout=None):
        try:
            result = subprocess.run(args, input=stdin, capture_output=True, text=True,
                                    timeout=timeout or self.timeout, check=False)
        except FileNotFoundError:
            raise CheckFailure("Required command missing: " + args[0]) from None
        except subprocess.TimeoutExpired:
            raise CheckFailure(args[0] + " timed out; inspect the run-owned resources") from None
        if result.returncode:
            # stdin may contain pull credentials. Never emit raw CLI output.
            raise CheckFailure(args[0] + " request failed (exit " + str(result.returncode) + ")")
        return result.stdout.strip()

    def objects(self, kind, namespace=None):
        args = ["get", kind]
        if namespace:
            args += ["-n", namespace]
        return self.oc_json(*args)["items"]

    def optional(self, kind, name, namespace=None):
        args = ["get", kind, name, "--ignore-not-found"]
        if namespace:
            args += ["-n", namespace]
        raw = self.oc(*args, "-o", "json")
        return json.loads(raw) if raw else None

    def own(self, kind, name, namespace=None):
        entry = {"kind": kind, "name": name, "namespace": namespace}
        if entry not in self.state["owned"]:
            self.state["owned"].append(entry)
            self.persist()

    def create(self, document):
        document.setdefault("metadata", {}).setdefault("labels", {})[LABEL] = self.run_id
        meta = document["metadata"]
        # Register intent before creation so an interrupted request is recoverable.
        self.own(document["kind"], meta["name"], meta.get("namespace"))
        self.command("oc", "create", "-f", "-", stdin=json.dumps(document))

    def delete(self, kind, name, namespace=None):
        args = ["delete", kind, name, "--ignore-not-found", "--wait=false"]
        if namespace:
            args += ["-n", namespace]
        self.oc(*args)

    def wait(self, description, predicate, timeout=300, interval=5):
        deadline = time.monotonic() + timeout
        while True:
            value = predicate()
            if value:
                return value
            if time.monotonic() >= deadline:
                raise CheckFailure("Timed out waiting for " + description)
            time.sleep(min(interval, max(0, deadline - time.monotonic())))

    def result(self, key, name, function, dependencies=()):
        by_key = {r["key"]: r for r in self.results}
        if any(by_key.get(d, {}).get("status") != "PASS" for d in dependencies):
            result = {"key": key, "name": name, "status": "SKIP",
                      "message": "Blocked by failed or skipped prerequisite: " + ", ".join(dependencies)}
        else:
            value = self.run_check(name, function)
            result = {"key": key, "name": name,
                      "status": "PASS" if value["passed"] else "FAIL", "message": value["message"]}
        self.results.append(result)
        self.persist()
        (self.directory / "progress.json").write_text(json.dumps(self.results))
        return result

    def preflight(self):
        self.tools()
        self.azure_account()
        self.resource_groups()
        self.cluster()
        self.bastion_access()
        self.workers()
        self.catalog()
        self.package("sandboxed-containers-operator", "stable")
        self.package("agent-sandbox-operator", "preview-0.9")
        self.learner_login()
        nodes = self.objects("nodes")
        self.state["baseline_nodes"] = {n["metadata"]["name"]: n["status"]["nodeInfo"]["bootID"] for n in nodes}
        self.state["initial_crds"] = [c["metadata"]["name"] for c in self.objects("crd")]
        # KataConfig is cluster-scoped. Do not interfere with participant installs.
        if "kataconfigs.kataconfiguration.openshift.io" in self.state["initial_crds"]:
            if self.objects("kataconfig"):
                raise CheckFailure("A pre-existing KataConfig exists; use a fresh lab to avoid modifying participant state")
        self.persist()
        return "Azure, learner login, baseline workers, catalogs and required permissions verified"

    def boundary(self):
        for node in self.objects("nodes"):
            labels = node["metadata"].get("labels", {})
            if any("node-role.kubernetes.io/" + role in labels for role in ("master", "control-plane")):
                raise CheckFailure("Customer inventory unexpectedly includes control-plane nodes")
        for namespace in ("openshift-etcd", "openshift-kube-apiserver"):
            if self.objects("pods", namespace):
                raise CheckFailure("Unexpected visible control-plane pods in " + namespace)
        return "Worker-only customer inventory; no visible etcd or API server pods"

    def azure_inventory(self):
        resources = self.az("resource", "list", "--resource-group", self.config["aro_hcp_cluster_rg"])
        vms = self.az("vm", "list", "--resource-group", self.config["aro_hcp_cluster_managed_rg"])
        if not resources or not vms:
            raise CheckFailure("Expected customer resources and managed worker VMs were not found")
        node_ids = {n["spec"].get("providerID", "").removeprefix("azure://").lower() for n in self.objects("nodes")}
        if any(vm["id"].lower() not in node_ids for vm in vms):
            raise CheckFailure("Managed resource group contains a VM not mapped to a customer worker")
        # Avoid fragile naming-based assertions about every disk/load balancer.
        self.az("disk", "list", "--resource-group", self.config["aro_hcp_cluster_managed_rg"])
        self.az("network", "lb", "list", "--resource-group", self.config["aro_hcp_cluster_managed_rg"])
        return "Azure VMs map to customer workers; disks and load balancers are queryable (absence proof is limited to VM mapping)"

    def identity(self):
        identities = self.az("resource", "list", "--resource-group", self.config["aro_hcp_cluster_rg"],
                             "--resource-type", "Microsoft.ManagedIdentity/userAssignedIdentities")
        if not identities:
            raise CheckFailure("No per-component managed identities were found")
        self.oidc()
        return "Managed identities and the cluster OIDC issuer are present"

    def provision_pool(self):
        pools = self.az("aro", "hcp", "cluster", "nodepool", "list", *self.cluster_args())
        if any(p["name"] == self.pool for p in pools):
            raise CheckFailure("Run-specific NodePool name already exists; refusing to modify it")
        version = pools[0]["properties"]["version"]["id"]
        self.own("AzureNodePool", self.pool)
        self.command("az", "aro", "hcp", "cluster", "nodepool", "create", *self.cluster_args(),
                     "--name", self.pool, "--vm-size", "Standard_D4s_v5", "--replicas", "2",
                     "--version", version, "--channel-group", "stable", "--no-wait",
                     "--only-show-errors", "--output", "none", timeout=120)
        return "Requested two Standard_D4s_v5 workers using the baseline OpenShift version"

    def install_operator(self, namespace, package, channel, all_namespaces=False):
        existing = self.optional("namespace", namespace)
        if existing:
            subscriptions = self.objects("subscription", namespace)
            if not any(s["spec"].get("name") == package and s["spec"].get("channel") == channel for s in subscriptions):
                raise CheckFailure("Pre-existing operator namespace has no matching Subscription; left unchanged: " + namespace)
            self.wait_csv(namespace, package)
            return "Reused a pre-existing healthy operator without modifying it"
        self.create({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}})
        spec = {} if all_namespaces else {"targetNamespaces": [namespace]}
        self.create({"apiVersion": "operators.coreos.com/v1", "kind": "OperatorGroup",
                     "metadata": {"name": package, "namespace": namespace}, "spec": spec})
        self.create({"apiVersion": "operators.coreos.com/v1alpha1", "kind": "Subscription",
                     "metadata": {"name": package, "namespace": namespace},
                     "spec": {"channel": channel, "installPlanApproval": "Automatic", "name": package,
                              "source": "redhat-operators", "sourceNamespace": "openshift-marketplace"}})
        self.wait_csv(namespace, package)
        plans = self.objects("installplan", namespace)
        if not any(p.get("status", {}).get("phase") == "Complete" for p in plans):
            raise CheckFailure("Operator CSV succeeded but no completed InstallPlan was found")
        return "Subscription, completed InstallPlan and Succeeded CSV verified"

    def wait_csv(self, namespace, package):
        def installed():
            subscriptions = self.objects("subscription", namespace)
            names = [s.get("status", {}).get("installedCSV") for s in subscriptions if s["spec"].get("name") == package]
            return any(c["metadata"]["name"] in names and c.get("status", {}).get("phase") == "Succeeded"
                       for c in self.objects("csv", namespace))
        self.wait(package + " CSV", installed, timeout=int(self.config.get("operator_timeout", 600)))
        self.wait(package + " controller readiness", lambda: any(
            ready(p) for p in self.objects("pods", namespace)), timeout=180)

    def map_nodes(self):
        def provisioned():
            pool = self.az("aro", "hcp", "cluster", "nodepool", "show", *self.cluster_args(), "--name", self.pool)
            state = pool.get("properties", {}).get("provisioningState")
            if state in ("Failed", "Canceled"):
                raise CheckFailure("Azure NodePool provisioning failed; inspect Azure quota and activity logs")
            return state == "Succeeded"
        self.wait("Kata NodePool provisioning", provisioned,
                  timeout=int(self.config.get("nodepool_timeout", 1200)), interval=15)
        def joined():
            nodes = [n for n in self.objects("nodes") if n["metadata"]["name"] not in self.state["baseline_nodes"]]
            if len(nodes) > 2:
                raise CheckFailure("More than two new nodes appeared; refusing ambiguous node ownership")
            if len(nodes) != 2 or not all(ready(n) for n in nodes):
                return False
            if any(n["metadata"]["labels"].get("node.kubernetes.io/instance-type") != "Standard_D4s_v5" for n in nodes):
                raise CheckFailure("New workers do not match the requested Kata VM SKU")
            return nodes
        nodes = self.wait("two Ready Kata workers", joined, timeout=300)
        vms = self.az("vm", "list", "--resource-group", self.config["aro_hcp_cluster_managed_rg"])
        vm_ids = {vm["id"].lower() for vm in vms}
        for node in nodes:
            if node["spec"].get("providerID", "").removeprefix("azure://").lower() not in vm_ids:
                raise CheckFailure("Kata worker has no matching Azure VM")
        self.state["nodes"] = [n["metadata"]["name"] for n in nodes]
        self.state["kata_boot_ids"] = {n["metadata"]["name"]: n["status"]["nodeInfo"]["bootID"] for n in nodes}
        self.persist()
        self.create({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": self.namespace}})
        self.oc("label", "namespace", self.namespace, "pod-security.kubernetes.io/enforce=privileged", "--overwrite")
        return "Two Ready Standard_D4s_v5 workers correlate with Azure VM provider IDs"

    def host(self, node, shell):
        # Debug pods are explicitly confined to the run-owned workload namespace.
        output = self.oc("debug", "node/" + node, "--to-namespace=" + self.namespace,
                         "--quiet", "--", "chroot", "/host", "sh", "-c", shell)
        return output

    def node_setup(self):
        for name in self.state["nodes"]:
            self.oc("label", "node", name, "node-role.kubernetes.io/kata=", LABEL + "=" + self.run_id)
            self.oc("adm", "taint", "node", name, "kata=true:NoSchedule")
            self.host(name, "test -c /dev/kvm && echo KVM_OK")
        return "Both run-owned nodes have the Kata label/taint and a KVM character device"

    def acr_config(self):
        owned_namespace = any(o["kind"] == "Namespace" and o["name"] == OSC for o in self.state["owned"])
        deployment = self.oc_json("get", "deployment", "controller-manager", "-n", OSC)
        containers = deployment["spec"]["template"]["spec"]["containers"]
        if not owned_namespace:
            if not any(e.get("name") == "DOCKER_CONFIG" for c in containers for e in c.get("env", [])):
                raise CheckFailure("Pre-existing OSC controller lacks DOCKER_CONFIG; refusing to patch participant resources")
            return "Reused pre-existing OSC registry configuration without modification"
        pull = self.oc_json("get", "secret", "pull-secret", "-n", "openshift-config")
        config = json.loads(base64.b64decode(pull["data"][".dockerconfigjson"]))
        auth = config.get("auths", {}).get("arohcpocpprod.azurecr.io")
        if not auth:
            raise CheckFailure("Cluster pull secret lacks ARO HCP ACR credentials")
        self.create({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "acr-docker-config", "namespace": OSC},
                     "type": "Opaque", "stringData": {"config.json": json.dumps({"auths": {"arohcpocpprod.azurecr.io": auth}})}})
        patch = {"spec": {"template": {"spec": {
            "volumes": [{"name": "acr-docker-config", "secret": {"secretName": "acr-docker-config"}}],
            "containers": [{"name": containers[0]["name"], "env": [{"name": "DOCKER_CONFIG", "value": "/tmp/docker-cfg"}],
                            "volumeMounts": [{"name": "acr-docker-config", "mountPath": "/tmp/docker-cfg", "readOnly": True}]}]}}}}
        self.command("oc", "patch", "deployment", "controller-manager", "-n", OSC,
                     "--type=strategic", "--patch-file=/dev/stdin", stdin=json.dumps(patch))
        self.wait("patched OSC controller", lambda: self.deployment_ready(OSC, "controller-manager"), timeout=180)
        return "ACR credentials mounted into run-installed OSC controller; rollout verified"

    def deployment_ready(self, namespace, name):
        obj = self.oc_json("get", "deployment", name, "-n", namespace)
        return (obj.get("status", {}).get("observedGeneration", 0) >= obj["metadata"]["generation"]
                and obj.get("status", {}).get("updatedReplicas", 0) == obj["spec"].get("replicas", 1)
                and obj.get("status", {}).get("availableReplicas", 0) == obj["spec"].get("replicas", 1))

    def kata_install(self):
        for name in self.state["nodes"]:
            self.oc("label", "node", name, "node-role.kubernetes.io/kata-oc=")
            self.oc("adm", "taint", "node", name, "kata=true:NoSchedule-")
        try:
            self.create({"apiVersion": "kataconfiguration.openshift.io/v1", "kind": "KataConfig",
                         "metadata": {"name": self.kataconfig}, "spec": {"kataConfigPoolSelector": {
                             "matchLabels": {"node-role.kubernetes.io/kata": "", LABEL: self.run_id}}}})
            def installed():
                kc = self.oc_json("get", "kataconfig", self.kataconfig)
                status = kc.get("status", {})
                details = status.get("kataNodes", {})
                if details.get("failed"):
                    raise CheckFailure("KataConfig reports failed nodes; inspect OSC installation logs")
                # Check authoritative status counters, not just an empty success list.
                completed = status.get("completed", details.get("completedNodeCount", 0))
                total = status.get("total", details.get("nodeCount", 0))
                return int(completed or 0) == 2 and int(total or 0) == 2
            self.wait("KataConfig completion on both nodes", installed,
                      timeout=int(self.config.get("kata_timeout", 900)), interval=10)
        finally:
            for name in self.state["nodes"]:
                self.oc("adm", "taint", "node", name, "kata=true:NoSchedule", "--overwrite")
        return "KataConfig completed on two selected nodes; NoSchedule taints restored"

    def runtime(self):
        resource = self.oc_json("get", "runtimeclass", "kata")
        if resource.get("handler") != "kata":
            raise CheckFailure("RuntimeClass handler is not kata")
        for name in self.state["nodes"]:
            output = self.host(name, "crictl info")
            if "containerd-shim-kata-v2" not in output and '"kata"' not in output:
                raise CheckFailure("Kata handler missing from node runtime registry")
        return "kata RuntimeClass and node runtime handlers verified on both workers"

    def pod(self, name, kata=True, privileged=False, host_pid=False, node=None, sleep="infinity", tolerate=True):
        spec = {"restartPolicy": "Never", "containers": [{"name": "workload", "image": IMAGE,
                "command": ["sleep", sleep], "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}}}]}
        if kata:
            spec["runtimeClassName"] = "kata"
        if tolerate:
            spec["tolerations"] = [TOLERATION]
        if privileged:
            spec["containers"][0]["securityContext"] = {"privileged": True}
        if host_pid:
            spec["hostPID"] = True
        if node:
            spec["nodeName"] = node
        self.create({"apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "namespace": self.namespace}, "spec": spec})

    def pod_ready(self, name):
        return self.wait(name + " Ready", lambda: ready(self.oc_json("get", "pod", name, "-n", self.namespace)), timeout=240)

    def execute(self, pod, shell):
        return self.oc("exec", pod, "-n", self.namespace, "--", "sh", "-c", shell)

    def scheduling_failure(self):
        resource = self.oc_json("get", "runtimeclass", "kata")
        if any(t.get("key") == "kata" for t in resource.get("scheduling", {}).get("tolerations", [])):
            raise CheckFailure("RuntimeClass injects a Kata toleration; the documented missing-toleration failure cannot be reproduced")
        self.pod("missing-toleration", tolerate=False)
        def expected():
            pod = self.oc_json("get", "pod", "missing-toleration", "-n", self.namespace)
            if pod.get("spec", {}).get("nodeName"):
                raise CheckFailure("Pod without toleration unexpectedly scheduled")
            events = self.oc_json("get", "events", "-n", self.namespace,
                                  "--field-selector=involvedObject.name=missing-toleration")["items"]
            return any(e.get("reason") == "FailedScheduling" and "kata" in e.get("message", "")
                       and "untolerated taint" in e.get("message", "") for e in events)
        self.wait("expected untolerated Kata taint event", expected, timeout=90)
        return "Missing toleration leaves pod Pending with the expected FailedScheduling event"

    def scheduling_fix(self):
        self.delete("pod", "missing-toleration", self.namespace)
        self.pod("kata-test")
        self.pod_ready("kata-test")
        pod = self.oc_json("get", "pod", "kata-test", "-n", self.namespace)
        if pod["spec"].get("nodeName") not in self.state["nodes"] or pod["spec"].get("runtimeClassName") != "kata":
            raise CheckFailure("Corrected pod did not use Kata on a run-owned node")
        return "Tolerated Kata pod becomes Ready on a dedicated Kata worker"

    def kernels(self):
        self.pod("runc-kernel", kata=False, node=next(iter(self.state["baseline_nodes"])))
        self.pod_ready("runc-kernel")
        host_version = self.host(self.state["nodes"][0], "uname -r").splitlines()[-1]
        runc_version = self.execute("runc-kernel", "uname -r")
        kata_version = self.execute("kata-test", "uname -r")
        if not all((host_version, runc_version, kata_version)):
            raise CheckFailure("Kernel version collection returned empty output")
        return "Collected host/runc/Kata versions; equal versions are allowed (isolation is per kernel instance)"

    def processes(self):
        self.pod("runc-probe", kata=False, privileged=True, host_pid=True, node=next(iter(self.state["baseline_nodes"])))
        self.pod("kata-probe", privileged=True, host_pid=True)
        for name in ("runc-probe", "kata-probe"):
            self.pod_ready(name)
        count = "find /proc -maxdepth 1 -type d -name '[0-9]*' | wc -l"
        runc_count = int(self.execute("runc-probe", count))
        kata_count = int(self.execute("kata-probe", count))
        runc_init = self.execute("runc-probe", "tr '\\000' ' ' < /proc/1/cmdline")
        kata_init = self.execute("kata-probe", "tr '\\000' ' ' < /proc/1/cmdline")
        if runc_count <= kata_count or "systemd" not in runc_init or "systemd" in kata_init:
            raise CheckFailure("Expected host PID visibility contrast was not observed")
        return "runc sees host systemd and " + str(runc_count) + " PIDs; Kata sees " + str(kata_count) + " guest PIDs"

    def tenants(self):
        for name, kata, sleep in (("kata-a", True, "11111"), ("kata-b", True, "22222"),
                                  ("runc-a", False, "33333"), ("runc-b", False, "44444")):
            node = self.state["nodes"][0] if kata else next(iter(self.state["baseline_nodes"]))
            self.pod(name, kata=kata, privileged=not kata, host_pid=not kata, node=node, sleep=sleep)
            self.pod_ready(name)
        kata = self.execute("kata-a", "if grep -aq '[2]2222' /proc/[0-9]*/cmdline 2>/dev/null; then echo VISIBLE; else echo ISOLATED; fi")
        runc = self.execute("runc-a", "if grep -aq '[4]4444' /proc/[0-9]*/cmdline 2>/dev/null; then echo VISIBLE; else echo ISOLATED; fi")
        if kata != "ISOLATED" or runc != "VISIBLE":
            raise CheckFailure("Cross-tenant PID inspection did not show the expected contrast")
        # Free guest VM capacity before the warm pool test.
        for name in ("kata-a", "kata-b", "runc-a", "runc-b", "runc-kernel"):
            self.delete("pod", name, self.namespace)
        return "Same-node Kata tenants are isolated; explicitly privileged hostPID runc tenants share process visibility"

    def sysrq(self):
        pod = self.oc_json("get", "pod", "kata-probe", "-n", self.namespace)
        node = pod["spec"]["nodeName"]
        before = self.host(node, "dmesg | grep -i sysrq || true")
        output = self.execute("kata-probe", "echo h > /proc/sysrq-trigger && echo GUEST_SYSRQ_OK")
        after = self.host(node, "dmesg | grep -i sysrq || true")
        if "GUEST_SYSRQ_OK" not in output or before != after:
            raise CheckFailure("Guest sysrq help was rejected or host sysrq log entries changed")
        return "Only non-destructive sysrq help executed; host sysrq logs unchanged"

    def modules(self):
        node = self.oc_json("get", "pod", "kata-probe", "-n", self.namespace)["spec"]["nodeName"]
        host = self.host(node, "cat /proc/modules")
        guest = self.execute("kata-probe", "cat /proc/modules")
        if not re.search(r"^kvm\s", host, re.M) or re.search(r"^kvm\s", guest, re.M):
            raise CheckFailure("Expected host-only KVM hypervisor module was not observed")
        return "Host KVM module is not visible in guest; no kernel modules inserted"

    def node_health(self):
        self.workers()
        expected = dict(self.state["baseline_nodes"], **self.state.get("kata_boot_ids", {}))
        nodes = self.objects("nodes")
        current_names = {node["metadata"]["name"] for node in nodes}
        if not set(self.state["baseline_nodes"]).issubset(current_names):
            raise CheckFailure("One or more pre-existing baseline workers are missing")
        for node in nodes:
            name = node["metadata"]["name"]
            if name in expected and node["status"]["nodeInfo"]["bootID"] != expected[name]:
                raise CheckFailure("Unexpected worker reboot: " + name)
        return "Workers remain Ready without pressure or unexpected reboots"

    def agent_install(self):
        message = self.install_operator(AGENT, "agent-sandbox-operator", "preview-0.9", True)
        names = {c["metadata"]["name"] for c in self.objects("crd")}
        required = {"sandboxes.agents.x-k8s.io", "sandboxtemplates.extensions.agents.x-k8s.io",
                    "sandboxwarmpools.extensions.agents.x-k8s.io", "sandboxclaims.extensions.agents.x-k8s.io"}
        if not required.issubset(names):
            raise CheckFailure("Agent Sandbox installed but one or more required CRDs are missing")
        return message + "; all four CRDs registered"

    def agent_template(self):
        for name in ("kata-test", "kata-probe", "runc-probe"):
            self.delete("pod", name, self.namespace)
        self.create({"apiVersion": "extensions.agents.x-k8s.io/v1beta1", "kind": "SandboxTemplate",
                     "metadata": {"name": "kata-executor", "namespace": self.namespace},
                     "spec": {"podTemplate": {"spec": {"runtimeClassName": "kata", "tolerations": [TOLERATION],
                     "containers": [{"name": "executor", "image": IMAGE, "command": ["sleep", "infinity"],
                                     "resources": {"requests": {"cpu": "100m", "memory": "128Mi"},
                                                   "limits": {"cpu": "500m", "memory": "512Mi"}}}]}}}})
        template = self.oc_json("get", "sandboxtemplate", "kata-executor", "-n", self.namespace)
        if template["spec"]["podTemplate"]["spec"].get("runtimeClassName") != "kata":
            raise CheckFailure("SandboxTemplate did not retain the Kata runtime")
        return "Unprivileged Kata template accepted with required toleration and resource limits"

    def warm_pool(self):
        self.create({"apiVersion": "extensions.agents.x-k8s.io/v1beta1", "kind": "SandboxWarmPool",
                     "metadata": {"name": "agent-pool", "namespace": self.namespace},
                     "spec": {"replicas": 3, "sandboxTemplateRef": {"name": "kata-executor"}}})
        self.wait("three ready Kata sandboxes", lambda: self.ready_sandbox_pods(3), timeout=300)
        return "Three pre-warmed sandboxes have Ready Kata pods on run-owned workers"

    def ready_sandbox_pods(self, count):
        sandboxes = self.objects("sandboxes", self.namespace)
        names = {s["metadata"]["name"] for s in sandboxes}
        pods = self.objects("pods", self.namespace)
        matching = [p for p in pods if any(o.get("kind") == "Sandbox" and o.get("name") in names
                                          for o in p["metadata"].get("ownerReferences", []))]
        usable = [p for p in matching if ready(p) and p["spec"].get("runtimeClassName") == "kata"
                  and p["spec"].get("nodeName") in self.state["nodes"]]
        return usable if len(usable) >= count else False

    def claim(self):
        self.create({"apiVersion": "extensions.agents.x-k8s.io/v1beta1", "kind": "SandboxClaim",
                     "metadata": {"name": "session", "namespace": self.namespace},
                     "spec": {"warmPoolRef": {"name": "agent-pool"}, "lifecycle": {"ttlSecondsAfterFinished": 1800}}})
        claim = self.wait("SandboxClaim Ready", lambda: self.ready_claim(), timeout=120)
        name = claim.get("status", {}).get("sandbox", {}).get("name")
        if not name:
            raise CheckFailure("Ready SandboxClaim did not publish status.sandbox.name")
        sandbox = self.oc_json("get", "sandbox", name, "-n", self.namespace)
        pods = self.wait("claimed sandbox pod and warm-pool replenishment", lambda: self.ready_sandbox_pods(4), timeout=300)
        backing = [p for p in pods if any(o.get("uid") == sandbox["metadata"]["uid"] for o in p["metadata"].get("ownerReferences", []))]
        if len(backing) != 1:
            raise CheckFailure("Could not uniquely resolve the claimed sandbox's pod by owner reference")
        self.state["agent_pod"] = backing[0]["metadata"]["name"]
        self.persist()
        return "Claim allocated a Ready Kata sandbox; three spare Ready instances replenished"

    def ready_claim(self):
        resource = self.oc_json("get", "sandboxclaim", "session", "-n", self.namespace)
        return resource if ready(resource) else False

    def agent_security(self):
        pod = self.state["agent_pod"]
        sysrq = self.execute(pod, "if (echo h > /proc/sysrq-trigger) 2>/dev/null; then echo WRITABLE; else echo BLOCKED; fi")
        module = self.execute(pod, "if grep -q '^kvm ' /proc/modules; then echo VISIBLE; else echo ISOLATED; fi")
        filesystem = self.execute(pod, "if test -e /host; then echo VISIBLE; else echo ABSENT; fi")
        if (sysrq, module, filesystem) != ("BLOCKED", "ISOLATED", "ABSENT"):
            raise CheckFailure("Default agent sandbox did not meet the expected sysrq/module/host-mount restrictions")
        self.node_health()
        return "Unprivileged sysrq blocked, host KVM module hidden, /host absent; nodes healthy"

    def cleanup(self):
        errors = []
        owned = self.state["owned"]
        def attempt(description, function):
            try:
                function()
            except Exception:
                errors.append(description)
        # Delete workloads before uninstalling their controllers.
        if any(o["kind"] == "Namespace" and o["name"] == self.namespace for o in owned):
            attempt("workload namespace deletion", lambda: self.delete("namespace", self.namespace))
            attempt("workload namespace termination", lambda: self.wait("workload namespace deletion",
                    lambda: self.optional("namespace", self.namespace) is None, timeout=300))
        if any(o["kind"] == "KataConfig" for o in owned):
            # The uninstall DaemonSet also needs access to the tainted QA nodes.
            for node in self.state["nodes"]:
                attempt("temporary Kata taint removal", lambda n=node: self.oc("adm", "taint", "node", n, "kata:NoSchedule-"))
            attempt("KataConfig deletion", lambda: self.delete("kataconfig", self.kataconfig))
            attempt("KataConfig uninstall", lambda: self.wait("KataConfig uninstall",
                    lambda: self.optional("kataconfig", self.kataconfig) is None, timeout=600))
        for namespace in (AGENT, OSC):
            if any(o["kind"] == "Namespace" and o["name"] == namespace for o in owned):
                attempt(namespace + " deletion", lambda ns=namespace: self.delete("namespace", ns))
                attempt(namespace + " termination", lambda ns=namespace: self.wait(ns + " deletion",
                        lambda: self.optional("namespace", ns) is None, timeout=300))
        if any(o["kind"] == "AzureNodePool" for o in owned):
            def remove_pool():
                self.command("az", "aro", "hcp", "cluster", "nodepool", "delete", *self.cluster_args(),
                             "--name", self.pool, "--yes", "--no-wait", "--only-show-errors", timeout=120)
                self.wait("Azure NodePool deletion", lambda: not any(
                    p["name"] == self.pool for p in self.az("aro", "hcp", "cluster", "nodepool", "list", *self.cluster_args())),
                    timeout=900, interval=15)
                self.wait("run-owned worker removal", lambda: not any(
                    n["metadata"]["name"] in self.state["nodes"] for n in self.objects("nodes")), timeout=300)
            attempt("Azure NodePool cleanup", remove_pool)
        # CRDs and RuntimeClasses are cluster-scoped operator APIs. Retain these
        # rather than risk removing resources installed concurrently by a learner.
        # No pre-existing namespace, operator, label or NodePool is deleted.
        self.state["cleanup_errors"] = errors
        self.persist()
        if errors:
            raise CheckFailure("Cleanup incomplete: " + "; ".join(errors) + "; inspect state.json in the run directory")
        return "Run-created workloads, KataConfig, operator namespaces and Azure NodePool removed; shared API definitions retained"

    def run(self):
        stages = [
            ("preflight", "Environment pre-flight", self.preflight, ()),
            ("baseline", "Baseline cluster operators", self.operators, ("preflight",)),
            ("boundary", "Managed control plane boundary", self.boundary, ("preflight",)),
            ("azure", "Azure infrastructure inspection", self.azure_inventory, ("preflight",)),
            ("identity", "Managed identity and OIDC", self.identity, ("preflight",)),
            ("pool", "Kata NodePool provisioning request", self.provision_pool, ("preflight",)),
            ("osc", "Sandboxed Containers Operator", lambda: self.install_operator(OSC, "sandboxed-containers-operator", "stable"), ("preflight",)),
            ("mapping", "Azure-to-OpenShift node mapping", self.map_nodes, ("pool",)),
            ("nodes", "Kata labels, taints and KVM", self.node_setup, ("mapping",)),
            ("acr", "ARO HCP registry configuration", self.acr_config, ("osc",)),
            ("kata", "KataConfig runtime installation", self.kata_install, ("nodes", "acr")),
            ("runtime", "Runtime registration", self.runtime, ("kata",)),
            ("broken", "Missing-toleration failure", self.scheduling_failure, ("runtime",)),
            ("fixed", "Scheduling recovery", self.scheduling_fix, ("runtime",)),
            ("kernels", "Kernel version comparison", self.kernels, ("fixed",)),
            ("processes", "Process namespace isolation", self.processes, ("fixed",)),
            ("tenants", "Cross-tenant process isolation", self.tenants, ("fixed",)),
            ("sysrq", "Safe sysrq containment", self.sysrq, ("processes",)),
            ("modules", "Kernel module isolation", self.modules, ("processes",)),
            ("health", "Post-simulation node health", self.node_health, ("mapping",)),
            ("agent", "Agent Sandbox Operator", self.agent_install, ("preflight",)),
            ("template", "Kata SandboxTemplate", self.agent_template, ("agent", "runtime")),
            ("warm", "Three-instance SandboxWarmPool", self.warm_pool, ("template",)),
            ("claim", "SandboxClaim and replenishment", self.claim, ("warm",)),
            ("security", "Agent Sandbox security", self.agent_security, ("claim",)),
        ]
        try:
            for args in stages:
                self.result(*args)
        except AbortRun:
            self.results.append({"key": "cancelled", "name": "Run cancellation", "status": "FAIL",
                                 "message": "E2E interrupted; remaining stages were not executed"})
        finally:
            self.result("cleanup", "Cleanup of run-owned resources", self.cleanup)
            self.result("final", "Final baseline worker health", self.node_health, ("preflight",))
        report = {"run_id": self.run_id, "directory": str(self.directory), "checks": self.results,
                  "healthy": all(r["status"] == "PASS" for r in self.results)}
        (self.directory / "report.json").write_text(json.dumps(report, indent=2))
        return report


def main():
    config = json.load(sys.stdin)
    directory = Path(sys.argv[1])
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Serialize runs on this bastion for this cluster. Never run a second test
    # against a cluster while the first owns the installation lifecycle.
    safe_cluster = re.sub(r"[^a-zA-Z0-9_-]", "_", config["aro_hcp_cluster_name"])
    with open("/tmp/workshop-e2e-" + safe_cluster + ".lock", "w") as lock:
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"healthy": False, "checks": [{"name": "Concurrent run guard", "status": "FAIL",
                              "message": "Another E2E run is already active on this bastion"}]}))
            return 1
        def interrupted(signum, frame):
            # Ignore subsequent cancellation signals while finally cleans up.
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            raise AbortRun()
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        runner = E2E(config, directory)
        report = runner.run()
        print(json.dumps(report))
        return 0 if report["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
