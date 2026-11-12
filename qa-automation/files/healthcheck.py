#!/usr/bin/env python3
"""Read-only workshop readiness probes, executed as the learner's bastion user.

Configuration arrives on stdin. Only curated, non-secret results go to stdout.
No operators, NodePools, namespaces, or workloads are created by these checks.
"""
import base64
import concurrent.futures
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request


class CheckFailure(Exception):
    """A safe, deliberately curated diagnostic suitable for Showroom output."""


class Healthcheck:
    def __init__(self, config):
        self.config = config
        self.timeout = int(config.get("timeout", 10))

    def command(self, *args):
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError:
            raise CheckFailure("Required command is missing: " + args[0]) from None
        except subprocess.TimeoutExpired:
            raise CheckFailure(args[0] + " exceeded the request timeout") from None
        if result.returncode:
            # Never forward raw stderr: CLI errors can contain credentials.
            raise CheckFailure(
                args[0] + " request failed (exit " + str(result.returncode) + ")"
            )
        return result.stdout.strip()

    def oc(self, *args):
        return self.command("oc", "--request-timeout=" + str(self.timeout) + "s", *args)

    def oc_json(self, *args):
        return json.loads(self.oc(*args, "-o", "json"))

    def az(self, *args):
        return json.loads(self.command(
            "az", *args, "--only-show-errors", "--output", "json"
        ))

    def http(self, url, data=None, token=None, form=False):
        headers = {}
        if token:
            headers["Authorization"] = "Bearer " + token
        if data is not None:
            if form:
                data = urllib.parse.urlencode(data).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                data = json.dumps(data).encode()
                headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read()

    def tools(self):
        missing = [tool for tool in ("oc", "az", "jq", "helm", "python3")
                   if not shutil.which(tool)]
        if missing:
            raise CheckFailure("Missing bastion tools: " + ", ".join(missing))
        self.command("az", "aro", "hcp", "cluster", "show", "--help")
        return "oc, az, jq, helm, python3 and az aro hcp are available"

    def azure_account(self):
        account = self.az("account", "show")
        if account.get("state") != "Enabled":
            raise CheckFailure("The bastion Azure subscription is not Enabled")
        expected = self.config.get("azure_subscription")
        if expected and expected not in (account.get("id"), account.get("name")):
            raise CheckFailure("The active Azure subscription does not match user data")
        return "Bastion Azure session has an enabled subscription"

    def resource_groups(self):
        for key in ("aro_hcp_cluster_rg", "aro_hcp_cluster_managed_rg"):
            group = self.az("group", "show", "--name", self.config[key])
            if group.get("properties", {}).get("provisioningState") != "Succeeded":
                raise CheckFailure(key + " is not successfully provisioned")
        return "Customer and managed resource groups are accessible"

    def cluster_args(self):
        return ("--resource-group", self.config["aro_hcp_cluster_rg"],
                "--cluster-name", self.config["aro_hcp_cluster_name"])

    def cluster(self):
        cluster = self.az(
            "aro", "hcp", "cluster", "show",
            "--resource-group", self.config["aro_hcp_cluster_rg"],
            "--name", self.config["aro_hcp_cluster_name"],
        )
        if cluster.get("properties", {}).get("provisioningState") != "Succeeded":
            raise CheckFailure("ARO HCP cluster provisioningState is not Succeeded")
        if cluster.get("location", "").lower() != self.config["aro_hcp_cluster_location"].lower():
            raise CheckFailure("ARO HCP cluster location does not match user data")
        return "ARO HCP cluster is successfully provisioned in the expected region"

    def nodepools(self):
        pools = self.az("aro", "hcp", "cluster", "nodepool", "list", *self.cluster_args())
        if not pools:
            raise CheckFailure("No worker NodePools exist")
        for pool in pools:
            if pool.get("properties", {}).get("provisioningState") != "Succeeded":
                raise CheckFailure("NodePool is not Succeeded: " + pool["name"])
        return "All existing NodePools are Succeeded (Kata pool is not required yet)"

    def bastion_access(self):
        actual = self.oc("whoami", "--show-server").rstrip("/")
        expected = self.config["openshift_api_url"].rstrip("/")
        # Kubernetes commonly omits the default HTTPS port in kubeconfig.
        if actual.removesuffix(":443") != expected.removesuffix(":443"):
            raise CheckFailure("Bastion kubeconfig points to a different cluster")
        self.oc("whoami")
        self.oc("get", "--raw=/readyz")
        if self.oc("auth", "can-i", "*", "*", "--all-namespaces") != "yes":
            raise CheckFailure("Bastion kubeconfig lacks workshop cluster-admin permissions")
        return "Bastion kubeconfig targets the expected API and has workshop permissions"

    def workers(self):
        nodes = self.oc_json("get", "nodes")["items"]
        if len(nodes) < int(self.config.get("minimum_workers", 2)):
            raise CheckFailure("Fewer than the required baseline worker nodes are present")
        for node in nodes:
            name = node["metadata"]["name"]
            conditions = {c["type"]: c["status"] for c in node["status"]["conditions"]}
            if conditions.get("Ready") != "True":
                raise CheckFailure("Node is not Ready: " + name)
            for pressure in ("MemoryPressure", "DiskPressure", "PIDPressure"):
                if conditions.get(pressure) != "False":
                    raise CheckFailure(name + " has unhealthy condition " + pressure)
            if node["spec"].get("unschedulable", False):
                raise CheckFailure("Node is cordoned: " + name)
            labels = node["metadata"].get("labels", {})
            if "node-role.kubernetes.io/worker" not in labels:
                raise CheckFailure("Unexpected non-worker node in customer inventory: " + name)
        return str(len(nodes)) + " workers are Ready, uncordoned and free of pressure"

    def operators(self):
        operators = self.oc_json("get", "clusteroperators")["items"]
        if not operators:
            raise CheckFailure("No cluster operator status objects were returned")
        unhealthy = []
        for operator in operators:
            conditions = {c["type"]: c["status"] for c in operator["status"]["conditions"]}
            bad = [key + "=" + str(conditions.get(key, "Missing"))
                   for key, expected in (("Available", "True"), ("Degraded", "False"),
                                         ("Progressing", "False"))
                   if conditions.get(key) != expected]
            if bad:
                unhealthy.append(operator["metadata"]["name"] + " (" + ", ".join(bad) + ")")
        if unhealthy:
            raise CheckFailure("Unhealthy cluster operators: " + "; ".join(unhealthy))
        return "All cluster operators are available, non-degraded and stable"

    def catalog(self):
        catalog = self.oc_json("get", "catalogsource", "redhat-operators", "-n", "openshift-marketplace")
        state = catalog.get("status", {}).get("connectionState", {}).get("lastObservedState")
        if state != "READY":
            raise CheckFailure("redhat-operators catalog is not READY")
        return "redhat-operators CatalogSource is READY"

    def package(self, name, channel):
        package = self.oc_json("get", "packagemanifest", name, "-n", "openshift-marketplace")
        if package.get("status", {}).get("catalogSource") != "redhat-operators":
            raise CheckFailure(name + " is not supplied by redhat-operators")
        channels = [c["name"] for c in package.get("status", {}).get("channels", [])]
        if channel not in channels:
            raise CheckFailure(name + " does not offer the required channel " + channel)
        return name + " is available on channel " + channel

    def console(self):
        self.http(self.config["openshift_console_url"])
        return "OpenShift console endpoint responds over verified HTTPS"

    def oidc(self):
        discovery = json.loads(self.oc("get", "--raw=/.well-known/openid-configuration"))
        if not discovery.get("issuer", "").startswith("https://"):
            raise CheckFailure("Cluster OIDC discovery document has no HTTPS issuer")
        return "Cluster OIDC discovery publishes an HTTPS issuer"

    def learner_login(self):
        issuer = self.config["keycloak_url"].rstrip("/") + "/realms/" + self.config.get("keycloak_realm", "openshift")
        discovery = json.loads(self.http(issuer + "/.well-known/openid-configuration"))
        # The external-auth console client is confidential. Read its existing
        # secret without displaying it, then exercise the actual learner login.
        secret = self.oc_json("get", "secret", "console-oauth-config", "-n", "openshift-console")
        client_secret = base64.b64decode(secret["data"]["clientSecret"]).decode()
        if discovery.get("issuer") != issuer:
            raise CheckFailure("Keycloak issuer does not match the configured realm")
        tokens = json.loads(self.http(issuer + "/protocol/openid-connect/token", {
            "client_id": self.config.get("keycloak_client_id", "openshift-console"),
            "client_secret": client_secret,
            "username": self.config["openshift_admin_user"],
            "password": self.config["openshift_admin_password"],
            "grant_type": "password", "scope": "openid",
        }, form=True))
        token = tokens["id_token"]
        api = self.config["openshift_api_url"].rstrip("/")
        self.http(api + "/api/v1/nodes?limit=1", token=token)
        review = json.loads(self.http(api + "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews", {
            "apiVersion": "authorization.k8s.io/v1", "kind": "SelfSubjectAccessReview",
            "spec": {"resourceAttributes": {"verb": "*", "group": "*", "resource": "*"}},
        }, token=token))
        if not review.get("status", {}).get("allowed"):
            raise CheckFailure("Learner login works but lacks workshop cluster-admin permissions")
        return "Keycloak learner login authenticates to the API with workshop permissions"

    def registry(self):
        registries = ("registry.access.redhat.com", "quay.io", "arohcpocpprod.azurecr.io")
        for registry in registries:
            try:
                self.http("https://" + registry + "/v2/")
            except urllib.error.HTTPError as error:
                code = error.code
                error.close()
                if code != 401:
                    raise CheckFailure(registry + " registry endpoint returned HTTP " + str(code)) from None
        pull_secret = self.oc_json("get", "secret", "pull-secret", "-n", "openshift-config")
        docker_config = json.loads(base64.b64decode(pull_secret["data"][".dockerconfigjson"]))
        auth = docker_config.get("auths", {}).get("arohcpocpprod.azurecr.io", {})
        if not (auth.get("auth") or (auth.get("username") and auth.get("password"))):
            raise CheckFailure("Cluster pull secret lacks the ARO HCP ACR credentials needed in Module 03")
        return "Registries are reachable and ACR credentials exist (image pulls are tested by E2E)"

    def run_check(self, name, function):
        try:
            return {"name": name, "passed": True, "message": function()}
        except CheckFailure as error:
            message = str(error)
        except urllib.error.HTTPError as error:
            message = "HTTPS request failed (HTTP " + str(error.code) + ")"
            error.close()
        except Exception as error:
            # Do not expose exception strings, tokens, command output or config.
            message = "Probe failed (" + type(error).__name__ + "); inspect the bastion or provisioning logs"
        return {"name": name, "passed": False, "message": message}

    def run(self):
        probes = [
            ("Bastion tools", self.tools),
            ("Azure session", self.azure_account),
            ("Azure resource groups", self.resource_groups),
            ("ARO HCP cluster", self.cluster),
            ("Existing NodePools", self.nodepools),
            ("Bastion OpenShift access", self.bastion_access),
            ("Worker readiness", self.workers),
            ("Cluster operators", self.operators),
            ("Operator catalog", self.catalog),
            ("Sandboxed Containers package", lambda: self.package("sandboxed-containers-operator", "stable")),
            ("Agent Sandbox package", lambda: self.package("agent-sandbox-operator", "preview-0.9")),
            ("OpenShift console", self.console),
            ("Cluster OIDC discovery", self.oidc),
            ("Learner authentication and authorization", self.learner_login),
            ("Registry prerequisites", self.registry),
        ]
        # Bound each external call, run independent probes concurrently, and keep
        # output in a stable order. One failure must not hide other failures.
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(probes)) as pool:
            futures = [pool.submit(self.run_check, name, function) for name, function in probes]
            checks = [future.result() for future in futures]
        return {"checks": checks, "healthy": all(check["passed"] for check in checks)}


def main():
    report = Healthcheck(json.load(sys.stdin)).run()
    print(json.dumps(report))
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
