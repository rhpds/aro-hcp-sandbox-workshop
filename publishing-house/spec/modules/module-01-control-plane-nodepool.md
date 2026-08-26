# Module 1 — Where Is the Control Plane? HyperShift CRDs + NodePool Scaling

### Brief Overview

This module establishes the operational boundary of an ARO HCP cluster by examining HyperShift CRDs. Participants inspect the HostedCluster object and confirm that control plane namespaces are empty — the control plane exists but runs entirely in Red Hat's infrastructure. The NodePool object defines the customer's operational surface: everything above NodePool is Red Hat's responsibility; everything below is the customer's. Participants then exercise that boundary directly by scaling the worker NodePool and watching ARO HCP translate a declarative Kubernetes API call into an Azure VM creation — confirmed independently through the Azure CLI.

### Audience and Time

- **Target personas:** Field Engineers, Solution Architects, Consultants
- **Prerequisites for this module:** Module 0 completed; oc CLI authenticated to the lab cluster; Azure CLI authenticated to the lab resource group
- **Estimated duration:** 20 minutes

### Learning Objectives

- Verify that the HostedCluster CRD is present and that control plane namespaces contain no running pods visible to the customer
- Identify the NodePool object as the customer's primary operational surface in ARO HCP
- Verify that kubeconfig and Azure credential secrets are short-lived and operator-managed rather than static
- Scale a worker NodePool and trace the resulting Azure VM provisioning through the Kubernetes declarative API
- Analyze the relationship between a NodePool API change and its Azure infrastructure consequence

### Lab Structure

| Section | Title | Duration |
|---------|-------|----------|
| 1 | HostedCluster CRD inspection and empty control plane namespace confirmation | 8 min |
| 2 | NodePool object review and credential secret examination | 5 min |
| 3 | NodePool scale-out and Azure VM cross-reference | 7 min |

### Detailed Steps

1. Run `oc get hostedcluster -A` to list HostedCluster objects and confirm your cluster's presence.
2. Describe the HostedCluster object with `oc describe hostedcluster <cluster-name> -n <namespace>` and review its spec fields, noting the control plane endpoint and infrastructure references.
3. List all namespaces with `oc get namespaces` and identify namespaces that would be associated with control plane components (e.g., namespaces with names containing `kube-system` equivalents for the hosted control plane).
4. Attempt to list pods in the control plane namespace with `oc get pods -n <control-plane-namespace>` and observe that no control plane pods are running in the customer's view — they exist in Red Hat's infrastructure.
5. Run `oc get nodepools -A` to list all NodePool objects.
6. Describe the default worker NodePool with `oc describe nodepool <pool-name> -n <namespace>` and examine the spec, paying attention to the replicas field and the platform-specific configuration referencing Azure VM SKU.
7. Identify the operational boundary stated in the NodePool description: everything above NodePool (HostedCluster, control plane) is Red Hat's responsibility; everything at and below NodePool (worker nodes, workloads) is the customer's responsibility.
8. Locate the kubeconfig and Azure credential secrets referenced by the HostedCluster object using `oc get secrets -n <namespace>` and note their managed annotations indicating operator ownership and short-lived rotation.
9. Record the current number of replicas in the worker NodePool before scaling.
10. Scale the worker NodePool up by one replica: `oc patch nodepool <pool-name> -n <namespace> --type merge -p '{"spec":{"replicas":<current+1>}}'`
11. Watch the NodePool status update with `oc get nodepool <pool-name> -n <namespace> -w` and observe the condition transitions.
12. While the NodePool is scaling, run `az vm list --resource-group <lab-resource-group> --output table` in a second terminal and refresh periodically to observe the new VM appearing in Azure.
13. Confirm that the new worker node appears in `oc get nodes` once provisioning completes.
14. Cross-reference: note that the Kubernetes declarative API call — changing the replicas field — was the only action needed; the Azure compute API was never called directly.
15. Scale the NodePool back to the original baseline replica count: `oc patch nodepool <pool-name> -n <namespace> --type merge -p '{"spec":{"replicas":<original>}}'`
16. Confirm the NodePool returns to baseline and the additional VM is removed from the Azure resource group.

### Key Takeaways

- The HostedCluster CRD is present in the customer cluster, but no control plane pods are visible — they run in Red Hat's managed infrastructure
- The NodePool object is the customer's primary operational surface; every worker node scaling action goes through it
- kubeconfig and Azure credential secrets are short-lived and operator-managed — not static credentials held by the customer
- Scaling a NodePool demonstrates how ARO HCP translates a declarative Kubernetes API change into Azure VM provisioning without the customer ever calling the Azure compute API directly
- The clean separation between Red Hat-managed (control plane) and customer-managed (NodePool and below) defines the ARO HCP support boundary

### Infrastructure Notes

- The lab environment must have the default worker NodePool set to a baseline that allows scaling by at least one replica without hitting Azure quota limits
- Azure CLI access must be scoped to show VM list operations against the lab resource group so the cross-reference step is observable
