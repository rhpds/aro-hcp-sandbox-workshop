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

`e2e.yml` remains a placeholder; full installation and isolation exercises are
outside the readiness healthcheck.
