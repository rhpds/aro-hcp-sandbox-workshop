# Module 0 — Introduction, Pre-flight & Cluster Resource Group

### Brief Overview

This opening module frames the lab's customer story: how ARO HCP's managed control plane model and OpenShift Sandboxed Containers combine into a single security-first offering for regulated industries. Participants get a concise instructor-led context-setting segment followed by a live pre-flight check using the Azure CLI. The hands-on portion confirms that the lab cluster's Azure resource group contains worker VMs but none of the master infrastructure — no master VMs, no etcd disks, no control plane load balancers. The module closes by examining what replaces that infrastructure: Managed Identity and Workload Identity bindings that satisfy FSI and government security requirements by default.

### Audience and Time

- **Target personas:** Field Engineers, Solution Architects, Consultants
- **Prerequisites for this module:** Hands-on OpenShift experience (oc CLI, namespaces, YAML manifests); basic Azure and ARO familiarity (resource groups, Azure CLI); lab environment access provisioned
- **Estimated duration:** 20 minutes

### Learning Objectives

- Verify that the ARO HCP lab cluster's Azure resource group contains worker VMs but no master VMs, etcd disks, or control plane load balancers
- Analyze the cost and operational impact of the managed control plane model using a concrete multi-cluster calculation
- Identify the Managed Identity and Workload Identity bindings that replace static credentials and satisfy FSI and government security policies

### Lab Structure

| Section | Title | Duration |
|---------|-------|----------|
| 1 | Instructor context-setting: ARO HCP, Sandboxed Containers, and the customer story | 5 min |
| 2 | Pre-flight: Azure CLI resource group inspection | 8 min |
| 3 | Impact calculation and Managed Identity examination | 7 min |

### Detailed Steps

1. Listen to the instructor's overview covering what ARO HCP is, what Sandboxed Containers is, and how the two technologies combine into a single customer story for regulated industries.
2. Open a terminal and confirm that the Azure CLI is authenticated and targeting the correct subscription for your lab environment.
3. Run `az resource list --resource-group <lab-resource-group> --output table` to list all resources in your assigned resource group.
4. Identify the worker VM entries in the output and note their names.
5. Confirm the absence of master VMs by filtering for VM resources and verifying that none have a master or control-plane naming pattern.
6. Confirm the absence of etcd disks by filtering for disk resources and verifying that no etcd-labelled disks are present.
7. Confirm the absence of control plane load balancers by filtering for load balancer resources and verifying none are associated with the API server endpoint.
8. Perform the impact calculation: given 15 clusters, note that the managed control plane model eliminates 45 master VMs (3 per cluster) from customer-managed infrastructure.
9. Locate the Managed Identity resource in the resource group output and note its name.
10. Run `az identity show --resource-group <lab-resource-group> --name <managed-identity-name>` to examine the Managed Identity configuration.
11. Review the Workload Identity bindings that grant the cluster's service accounts Azure API access without static credentials.
12. Note how the absence of static credentials makes ARO HCP compliant with FSI and government security policies by default.

### Key Takeaways

- ARO HCP clusters have no master VMs, etcd disks, or control plane load balancers in the customer's Azure resource group — these run in Red Hat's infrastructure
- The managed control plane model eliminates 3 master VMs per cluster; at scale this is a significant reduction in customer-managed infrastructure
- Managed Identity and Workload Identity bindings replace static Azure credentials, satisfying FSI and government zero-static-credential requirements by default
- The customer's operational surface begins at the worker node tier, not the control plane

### Infrastructure Notes

- Each participant requires an individual ARO HCP cluster pre-provisioned before the session
- Participants need Azure CLI access scoped to their assigned lab resource group with at least Reader permissions
- No control plane VMs should be visible in the resource group; environment provisioning must confirm this before the session starts
