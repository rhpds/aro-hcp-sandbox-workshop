# Module 2 — Kata-Dedicated NodePool & Sandboxed Containers Operator

### Brief Overview

This module introduces a second NodePool dedicated to Kata workloads and installs the Sandboxed Containers Operator. Participants create a NodePool targeting the Standard_D4s_v5 Azure VM SKU — the minimum size that supports nested virtualization required for KVM-based Kata isolation. Two manifest details are examined closely: the node label that will bind KataConfig in Module 3, and the NoSchedule taint that keeps runc workloads off the Kata nodes. While the NodePool provisions, participants install the operator and study its three managed resources. The module closes with a production-readiness discussion about automatic upgrade approval.

### Audience and Time

- **Target personas:** Field Engineers, Solution Architects, Consultants
- **Prerequisites for this module:** Module 1 completed; oc CLI authenticated to the lab cluster; Azure CLI access to lab resource group
- **Estimated duration:** 20 minutes

### Learning Objectives

- Create a Kata-dedicated NodePool targeting the Standard_D4s_v5 VM SKU with a node label and NoSchedule taint
- Verify KVM availability on the newly provisioned Kata nodes
- Deploy the Sandboxed Containers Operator from the OperatorHub subscription manifest
- Analyze the three resources managed by the Sandboxed Containers Operator: KataConfig, RuntimeClass, and PeerPodConfig
- Demonstrate the operator CSV reaching the Succeeded phase

### Lab Structure

| Section | Title | Duration |
|---------|-------|----------|
| 1 | Kata NodePool manifest review and application | 7 min |
| 2 | Sandboxed Containers Operator subscription | 6 min |
| 3 | Operator managed resources review and production discussion | 7 min |

### Detailed Steps

1. Review the Kata NodePool manifest provided in the lab materials, noting the `platform.azure.vmSize: Standard_D4s_v5` field — the minimum VM SKU that supports nested virtualization for KVM-based Kata isolation.
2. Examine the `nodeLabels` section of the manifest and note the specific label key and value (e.g., `node-role.kubernetes.io/kata: ""`) — this label must match the `kataConfigPoolSelector` in Module 3 exactly or KataConfig installation will fail silently.
3. Examine the `taints` section of the manifest and note the `NoSchedule` taint — this prevents runc workloads from being scheduled onto Kata nodes and incurring unnecessary kata overhead.
4. Apply the Kata NodePool manifest: `oc apply -f kata-nodepool.yaml`
5. Watch the NodePool provision with `oc get nodepool kata-nodepool -n <namespace> -w` and note the status transitions.
6. In a second terminal, run `az vm list --resource-group <lab-resource-group> --output table` periodically to observe the Standard_D4s_v5 VMs appearing in Azure.
7. Once the Kata nodes reach Ready state, open a debug shell on one of the new nodes: `oc debug node/<kata-node-name>`
8. Inside the debug shell, run `ls /dev/kvm` to verify the KVM device is present, confirming nested virtualization is available on these nodes.
9. Exit the debug shell.
10. Review the Sandboxed Containers Operator subscription manifest, which contains three resources: a `Namespace` (e.g., `openshift-sandboxed-containers-operator`), an `OperatorGroup`, and a `Subscription`.
11. Apply the subscription manifest: `oc apply -f sandboxed-containers-subscription.yaml`
12. Watch the CSV (ClusterServiceVersion) status: `oc get csv -n openshift-sandboxed-containers-operator -w` and wait for the phase to reach `Succeeded`.
13. While waiting, review the three resource types the operator will manage once installed: `KataConfig` (installs Kata binaries onto labeled nodes via MachineConfig), `RuntimeClass` (registers the kata handler with containerd), and `PeerPodConfig` (configures environments without nested virtualization using cloud-provider VM delegation).
14. Once the CSV reaches `Succeeded`, confirm the operator pod is running: `oc get pods -n openshift-sandboxed-containers-operator`.
15. Examine the `installPlanApproval` field in the Subscription manifest and discuss whether `Automatic` approval is appropriate for production environments versus a `Manual` approval workflow that gates operator upgrades.

### Key Takeaways

- The Standard_D4s_v5 VM SKU is the minimum Azure VM size that supports nested virtualization — required for KVM-based Kata isolation
- The node label on the Kata NodePool must exactly match the `kataConfigPoolSelector` in KataConfig; a mismatch causes silent installation failure
- The NoSchedule taint on Kata nodes prevents runc workloads from incurring kata overhead unintentionally
- The Sandboxed Containers Operator manages three resources: KataConfig (binary installation), RuntimeClass (containerd registration), and PeerPodConfig (peer-pod environments)
- Automatic `installPlanApproval` is convenient but may not be appropriate for production environments that require change-controlled operator upgrades

### Infrastructure Notes

- Azure subscription quota must allow Standard_D4s_v5 VMs in the lab region for each participant
- Nested virtualization must be supported in the Azure region used for the lab; verify this during environment preparation
- Pre-stage the Sandboxed Containers Operator index image if the lab environment has limited or no internet access to OperatorHub
