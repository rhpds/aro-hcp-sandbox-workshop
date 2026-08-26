# ARO HCP and OpenShift Sandboxed Containers: Control Plane and Kernel Isolation

<!-- This file is the design document for your lab or demo. -->
<!-- Edit directly or run /rhdp-publishing-house to have the intake skill help. -->

## Overview

This lab demonstrates two complementary technologies: ARO HCP's managed control plane model and OpenShift Sandboxed Containers' kernel-level workload isolation. Participants explore how the Hosted Control Plane model eliminates customer-managed master infrastructure and examine what replaces it — Managed Identity and Workload Identity bindings that satisfy FSI and government security requirements by default. They then install and validate Sandboxed Containers to prove that Kata workloads run in a separate guest kernel, isolated from the host and from neighboring tenants.

Participants inspect an ARO HCP cluster's Azure resource group to verify the absence of master VMs, etcd disks, and control plane load balancers; scale NodePools and trace the resulting Azure VM provisioning; install and configure the Sandboxed Containers operator; troubleshoot admission failures; and execute live host-escape attempts that are absorbed by the guest VM — confirming the isolation boundary in front of a real-world security scenario.

## Target Audience

- **Role:** Field Engineers, Solution Architects, Consultants
- **Experience level:** Advanced
- **What they already know:** Hands-on OpenShift experience — comfortable with oc CLI, namespaces, operators, and YAML manifests; basic Azure and ARO familiarity
- **What they don't know:** ARO HCP's managed control plane architecture, HyperShift CRDs and NodePool operations, Sandboxed Containers installation and troubleshooting, kernel-level isolation mechanics

## Prerequisites

- Hands-on OpenShift experience (oc CLI, namespaces, operators, YAML manifests)
- Basic Azure and ARO familiarity (resource groups, Azure CLI)
- Cannot be automatically validated — trust-based

## Learning Objectives

1. Verify ARO HCP's managed control plane model by inspecting the Azure resource group and confirming the absence of customer-managed master VMs, etcd disks, and control plane load balancers
2. Scale a worker NodePool and trace the resulting Azure VM provisioning through the Kubernetes declarative API
3. Deploy OpenShift Sandboxed Containers on a Kata-dedicated NodePool, including operator installation and KataConfig configuration
4. Troubleshoot Sandboxed Containers admission failures using pod events and `oc adm policy who-can`
5. Prove kernel-level isolation between runc and kata pods through live kernel version comparison and cross-tenant `/proc` inspection
6. Demonstrate host-escape containment by executing sysrq trigger and kernel module load attempts inside a kata workload

## Content Type

Lab (hands-on)

## Products & Technologies

- Azure Red Hat OpenShift with Hosted Control Planes (ARO HCP)
- OpenShift Sandboxed Containers
- HyperShift (upstream — HostedCluster and NodePool CRDs)
- Kata Containers (upstream runtime)
- Azure CLI

## Module Map

| Module | Title | Duration |
|--------|-------|----------|
| 0 | Introduction, Pre-flight & Cluster Resource Group | 20 min |
| 1 | Where Is the Control Plane? HyperShift CRDs + NodePool Scaling | 20 min |
| 2 | Kata-Dedicated NodePool & Sandboxed Containers Operator | 20 min |
| 3 | Install Kata, Break It, Fix It | 20 min |
| 4 | Proving Isolation: Kernel, Process, and Tenant Boundaries | 20 min |
| 5 | Build Pipeline — Host Escape Attempt | 20 min |
| — | **Total hands-on** | **2 hours** |
| — | Intro / presentation | ~5 min (in Module 0) |
| — | **Total lab** | **~2 hours** |

## Difficulty Level

Advanced

## Environment

**Learner view:** Each participant has a dedicated ARO HCP cluster provisioned before the lab starts. The cluster has a default runc NodePool running. No control plane VMs are visible in the Azure resource group — the hosted control plane runs in Red Hat's management infrastructure. Participants use both `oc` CLI and Azure CLI throughout the lab.

**Automation needed:** Yes
- Provision a per-student ARO HCP cluster with a default runc NodePool
- Configure Azure CLI credentials and resource group access for each student
- Pre-stage container images on quay.io for the isolation and escape demonstration modules

## Infrastructure Requirements

- **Cloud provider:** TBD — confirmed in infrastructure phase
- **Cluster type:** TBD — confirmed in infrastructure phase
- **OCP version:** TBD — confirmed in infrastructure phase
- **Topology:** TBD — confirmed in infrastructure phase
- **Sizing:** TBD — confirmed in infrastructure phase
- **Automation approach:** TBD — confirmed in infrastructure phase
- **AI/MaaS:** TBD — confirmed in infrastructure phase
- **External services:** TBD — confirmed in infrastructure phase
- **AAP version:** TBD — confirmed in infrastructure phase
- **Non-GA products:** TBD — confirmed in infrastructure phase

## Assessment Strategy (Optional)

Classic Showroom lab — assessment is trust-based. Participants observe live command output and UI state at each step. No automated solve/validate buttons. Each module includes a verification step where participants confirm expected state (kata RuntimeClass present, CSV at Succeeded, kernel versions differ between runc and kata pods) before moving on.
