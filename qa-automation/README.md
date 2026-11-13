# Workshop QA

## Healthcheck

`healthcheck.yml` checks the **starting environment**, not the state after the
participant completes the workshop. It uses only Ansible built-ins and Python's
standard library. The Showroom runner connects to the bastion over SSH and runs
15 independent probes concurrently as the configured bastion user, without sudo.
The target runtime is under 60 seconds; external requests have 10-second timeouts.

Checks cover:

- Bastion tools (`oc`, `az`, `jq`, `helm`, `python3`, and `az aro hcp`).
- Existing Azure login, resource groups, cluster region/provisioning, and NodePools.
- Bastion kubeconfig target, API readiness, and administrative permissions.
- At least two Ready, uncordoned workers without resource pressure.
- All cluster operators Available, non-Degraded, and not Progressing.
- Red Hat operator catalog and the workshop's required package channels.
- Console HTTPS access and cluster OIDC discovery.
- Actual learner Keycloak authentication and API authorization, separately from
  the bastion's administrative kubeconfig.
- Registry HTTPS reachability and presence of private ACR pull-secret credentials.
  This does not prove that image pulls or ACR authentication succeed.

The check **does not require** the Kata NodePool, OSC installation, KataConfig,
RuntimeClass, or Agent Sandbox resources. Participants create them during the lab.
It does not repair configuration, install software, create workloads, or run
escape simulations. The learner authorization probe submits a non-persistent
SelfSubjectAccessReview; other cluster and Azure probes only read existing state.

### Showroom variables

The runner injects these user-data fields as Ansible extra variables:

```text
aro_hcp_cluster_location
aro_hcp_cluster_managed_rg
aro_hcp_cluster_name
aro_hcp_cluster_rg
bastion_public_hostname
bastion_ssh_user_name
bastion_ssh_password
keycloak_url
openshift_admin_user
openshift_admin_password
openshift_api_url
openshift_console_url
```

Optional variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `bastion_ssh_port` | `22` | Bastion SSH port |
| `azure_subscription` | unset | Verify the bastion's active subscription ID/name |
| `healthcheck_minimum_workers` | `2` | Minimum initial worker count |
| `healthcheck_request_timeout` | `10` | Per-command/HTTPS timeout in seconds |
| `healthcheck_keycloak_realm` | `openshift` | Provisioned learner realm |
| `healthcheck_keycloak_client_id` | `openshift-console` | Provisioned confidential OIDC client |

The learner login probe reads the existing `console-oauth-config` secret in
`openshift-console` using the bastion kubeconfig. The client must allow direct
access grants, as configured by `aro_hcp_keycloak_auth`. Tokens and passwords are
not written to files by the probes, and raw subprocess/HTTP error bodies are
never printed. Ansible suppresses tasks containing credentials with `no_log`.
SSH accepts new bastion host keys but rejects changed known keys. HTTPS uses
normal certificate verification.

### Run and test

Through Showroom:

```text
https://<showroom-host>/stream/qa/healthcheck
```

After pushing changes to the branch deployed by Showroom, delete **only the
Showroom pod** so its Deployment recreates it and the init container clones the
updated repository. This briefly interrupts the Showroom UI and terminals.
Wait for the replacement pod to be Ready before triggering the endpoint.

For a manual run, supply a protected user-data file (do not commit it):

```bash
ansible-playbook qa-automation/healthcheck.yml -e @/secure/path/user_data.yml
```

The final summary groups all results together: one line per check containing
PASS/FAIL, its name, and a short detail, followed by overall PASS/FAIL and counts.
Unhealthy checks do not fail playbook execution: Showroom's "Completed
successfully" means the checks finished, not that the environment is healthy.
Read the overall readiness summary to determine environment health. Missing
required variables, SSH failures, or an invalid probe report still fail execution.
Degraded operators remain FAIL results, not ignored warnings. A healthy console
HTTP endpoint alone does not establish cluster operator health.

Offline tests and syntax validation:

```bash
python3 -m unittest discover -s qa-automation/tests -v
ansible-playbook --syntax-check qa-automation/healthcheck.yml
```

## End-to-end workflow

`e2e.yml` executes the installation, troubleshooting, isolation and Agent Sandbox
exercises on a dedicated per-student lab. Trigger it with:

```text
https://<showroom-host>/stream/qa/e2e
```

It uses the same required Showroom variables as the healthcheck and the existing
bastion Azure session/kubeconfig. Budget 30–60 minutes and Azure quota for **two
additional Standard_D4s_v5 workers**. The Ansible task polls asynchronously every
10 seconds while the bastion runs the workflow. Do not restart Showroom or launch
a second test while a run is active. A bastion lock rejects concurrent E2E runs.

Each run uses unique `qa-e2e-*` namespaces and a `qa-kata-*` NodePool/KataConfig.
It refuses an existing KataConfig rather than changing participant state. A
pre-existing matching operator can be reused, but is never patched or deleted.
The test-installed OSC controller receives the ACR credential mount described in
the content. Pre-existing cluster-operator degradation is reported independently
and does not prevent the installation exercises from running. If the only issue
is `console (Degraded=True)`, the E2E baseline check reports SKIP with a
"Known behaviour" description instead of FAIL. Other operator issues still fail,
and the standalone healthcheck remains strict.

The final report gives one PASS/FAIL/SKIP line per stage and totals. Failed
prerequisites produce explicit SKIP results, not false passes. The known-behaviour
skip does not fail the overall E2E result; failed prerequisites still do. Environment or
exercise failures do not fail Ansible execution; an execution/reporting failure
still does. Read the summary, not just Showroom's completion banner.

### Safety and cleanup

- Only the non-destructive sysrq help command (`h`) is permitted. No reboot,
  crash, kernel-module insertion, host filesystem writes or exploits are run.
- Privileged and hostPID pods are confined to the run's workload namespace.
  Host debug pods run only read-only inspection commands on test-owned nodes.
- Workers are checked for resource pressure and unexpected boot-ID changes.
- A `finally` block attempts cleanup after success, failure or handled SIGTERM.
  It removes run-created workloads, KataConfig, operator namespaces and NodePool.
- Cluster-scoped operator API definitions (CRDs and RuntimeClasses) are retained
  deliberately, rather than risking deletion of APIs used by concurrent learners.
  Thus cleanup does not restore a byte-for-byte pristine cluster.
- Run-owned state and sanitized reports remain in a private
  `/tmp/workshop-e2e-*` directory on the bastion. `state.json` records intended
  resource ownership **before** each create request. No credentials are stored in
  these reports. Preserve this directory if cleanup fails.
- SIGKILL, host loss or an expired administrative kubeconfig can prevent cleanup.
  Inspect `state.json`, the named namespace/KataConfig, and the Azure NodePool
  before manually removing only that run's resources or retrying.

Optional wait settings: `e2e_nodepool_timeout` (1200 seconds),
`e2e_operator_timeout` (600 seconds), and `e2e_kata_timeout` (900 seconds).
Each API/CLI request is separately bounded. The overall Ansible async limit is
three hours so normal waits and cleanup have time to finish.

The infrastructure inventory check maps Azure VMs to Kubernetes workers; it does
not claim that arbitrary disk or load-balancer names prove the absence of etcd
or API-server infrastructure. Kernel version equality is allowed. Cross-tenant
runc visibility is tested only with explicitly privileged hostPID pods.
