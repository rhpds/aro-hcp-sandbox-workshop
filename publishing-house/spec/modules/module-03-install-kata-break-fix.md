# Module 3 — Install Kata, Break It, Fix It

### Brief Overview

This module completes the Sandboxed Containers installation by applying KataConfig and walks participants through the full MachineConfigPool update cycle. A deliberate misconfiguration exercise reproduces the most common Sandboxed Containers admission failure seen in the field: deploying a Kata pod without the required SCC configuration. Participants trace the Forbidden error using pod events and `oc adm policy who-can`, then fix it by granting the Kata SCC to the service account. The module closes with a production-readiness discussion about least-privilege SCC assignment.

### Audience and Time

- **Target personas:** Field Engineers, Solution Architects, Consultants
- **Prerequisites for this module:** Module 2 completed; Sandboxed Containers Operator CSV in Succeeded phase; Kata NodePool nodes in Ready state
- **Estimated duration:** 20 minutes

### Learning Objectives

- Configure KataConfig with a `kataConfigPoolSelector` that correctly matches the Kata NodePool node label from Module 2
- Verify the MachineConfigPool update cycle as nodes cordon, drain, reboot, and return to Ready state
- Verify Kata installation at two layers: the kata RuntimeClass in the cluster and the containerd runtime registry on the node
- Troubleshoot a Kata pod admission failure caused by missing SCC configuration using pod events and `oc adm policy who-can`
- Demonstrate fixing the admission failure by granting the Kata SCC to the service account

### Lab Structure

| Section | Title | Duration |
|---------|-------|----------|
| 1 | KataConfig application and kataConfigPoolSelector review | 5 min |
| 2 | MachineConfigPool update cycle observation | 7 min |
| 3 | Two-layer verification: RuntimeClass and containerd registry | 3 min |
| 4 | Break it: Forbidden admission error and trace | 3 min |
| 5 | Fix it: SCC grant and production discussion | 2 min |

### Detailed Steps

1. Review the KataConfig manifest provided in the lab materials, focusing on the `kataConfigPoolSelector` field.
2. Confirm that the label selector in `kataConfigPoolSelector` exactly matches the node label applied to the Kata NodePool in Module 2 (e.g., `node-role.kubernetes.io/kata: ""`). A mismatch will cause installation to silently skip the Kata nodes.
3. Apply the KataConfig manifest: `oc apply -f kataconfig.yaml`
4. Watch the MachineConfigPool associated with the Kata nodes: `oc get machineconfigpool kata -w` and observe the status transitions as the update begins.
5. In a second terminal, watch the Kata nodes with `oc get nodes -l node-role.kubernetes.io/kata -w` and observe each node transition through: SchedulingDisabled (cordon), then drain, then NotReady (reboot), then Ready.
6. Wait for all Kata nodes to return to Ready state and the MachineConfigPool to show `Updated: True`.
7. Verify the kata RuntimeClass has been created in the cluster: `oc get runtimeclass kata` and confirm it is present with the `kata` handler.
8. Open a debug shell on a Kata node: `oc debug node/<kata-node-name>`
9. Inside the debug shell, run `chroot /host crictl info | grep -A5 kata` (or equivalent containerd inspection command) to confirm that the kata handler is registered in containerd's runtime registry.
10. Exit the debug shell.
11. Attempt to deploy a Kata pod without any SCC configuration using the test manifest: `oc apply -f kata-pod-noscc.yaml`
12. Observe that the pod does not start. Check pod events: `oc describe pod <pod-name>` and locate the `Forbidden` admission error in the events section.
13. Run `oc adm policy who-can use scc kata-scc` to identify which service accounts currently have permission to use the Kata SCC — confirm the default service account is not listed.
14. Grant the Kata SCC to the service account: `oc adm policy add-scc-to-user kata-scc -z <service-account> -n <namespace>`
15. Delete and re-apply the kata pod manifest: `oc delete pod <pod-name>` then `oc apply -f kata-pod-noscc.yaml`
16. Confirm the pod reaches Running state: `oc get pod <pod-name>`.
17. Discuss whether granting the Kata SCC to the `default` service account is appropriate for production versus creating a dedicated service account with least-privilege SCC assignment.

### Key Takeaways

- The `kataConfigPoolSelector` in KataConfig must exactly match the node label on the Kata NodePool; a label mismatch causes silent installation failure with no error — nodes are simply skipped
- The MachineConfigPool update cycle is observable in real time: nodes cordon, drain, reboot, and return Ready as Kata binaries are installed via MachineConfig
- Kata installation must be verified at two layers: the `kata` RuntimeClass present in the cluster, and the kata handler registered in containerd's runtime registry on the node
- The most common Sandboxed Containers admission failure in the field is a `Forbidden` error caused by missing SCC configuration — reproducible and traceable with `oc describe` and `oc adm policy who-can`
- Granting the Kata SCC to the `default` service account is expedient but not production-appropriate; dedicated least-privilege service accounts are the recommended pattern

### Infrastructure Notes

- The MachineConfigPool update cycle (cordon, drain, reboot, Ready) takes several minutes per node; allocate enough time in the lab schedule for all Kata nodes to cycle before moving to step 7
- The node debug shell in step 8 requires `oc debug node` access; ensure participant RBAC grants this permission in the lab environment
