# Module 5 — Build Pipeline — Host Escape Attempt

### Brief Overview

This module simulates a realistic supply-chain attack scenario: malicious code submitted through a build pipeline that attempts to escape its container and affect the host. Participants deploy a build pod under the kata runtime and execute two escape attempts — a sysrq-trigger that would reboot a runc host node, and a kernel module load targeting the host filesystem. Both attempts are absorbed by the Kata guest VM without affecting the host node or neighboring workloads. The module closes by reframing the field conversation around blast radius and providing a practical decision tree and discovery questions participants can use in a customer meeting.

### Audience and Time

- **Target personas:** Field Engineers, Solution Architects, Consultants
- **Prerequisites for this module:** Module 4 completed; kata RuntimeClass verified present; build pod image pre-staged on quay.io; all cluster nodes in Ready state
- **Estimated duration:** 20 minutes

### Learning Objectives

- Deploy a build pod under the kata runtime to simulate a build pipeline workload
- Demonstrate that a sysrq-trigger escape attempt inside a kata pod does not reboot or affect the host node
- Demonstrate that a kernel module load escape attempt inside a kata pod does not affect the host filesystem
- Verify that all cluster nodes remain Ready and neighboring workloads are unaffected after each escape attempt
- Analyze the field decision framework: when to recommend kata, when not to, and three discovery questions for a customer conversation

### Lab Structure

| Section | Title | Duration |
|---------|-------|----------|
| 1 | Deploy build pod under kata runtime | 3 min |
| 2 | Escape attempt 1: sysrq-trigger | 5 min |
| 3 | Escape attempt 2: kernel module load | 5 min |
| 4 | Field conversation reframe: blast radius decision tree and discovery questions | 5 min |
| 5 | Q&A | 2 min |

### Detailed Steps

1. Review the build pod manifest provided in the lab materials, confirming that `runtimeClassName: kata` is set in the pod spec and that the Kata SCC is configured for the pod's service account.
2. Deploy the build pod: `oc apply -f build-pod-kata.yaml` and confirm it reaches Running state on a Kata node.
3. In a second terminal, open a watch on all cluster nodes to monitor Ready status throughout the escape attempts: `oc get nodes -w`.
4. In a third terminal, verify a neighboring workload pod is running on the same Kata node so you can confirm it remains unaffected after each attempt.
5. Exec into the build pod: `oc exec -it <build-pod-name> -- /bin/bash`
6. **Escape attempt 1 — sysrq-trigger:** Inside the build pod, run `echo b > /proc/sysrq-trigger` (the command that triggers an immediate system reboot on a Linux host).
7. Observe that the command executes inside the Kata guest VM without error from the guest's perspective, but the host node does not reboot. Switch to the node watch terminal and confirm all nodes remain in Ready state.
8. Confirm the neighboring workload pod is still running: `oc get pods` in the third terminal.
9. Note that the sysrq event was absorbed by the Kata guest kernel — it rebooted or affected only the guest VM's internal state, not the host.
10. **Escape attempt 2 — kernel module load:** Inside the build pod, attempt to load a kernel module targeting the host filesystem: `insmod /path/to/test-module.ko` (using the pre-staged test module in the build pod image).
11. Observe the attempt's outcome inside the guest. Switch to the node watch terminal and confirm all host nodes remain in Ready state.
12. Confirm the neighboring workload pod is still running.
13. Note that the kernel module load targeted the Kata guest kernel, not the host kernel — the host filesystem and kernel are unaffected.
14. Exit the build pod exec session.
15. Reframe the field conversation: the question is not whether malicious code gets submitted to a build pipeline — it will. The question is what the blast radius is when it does. With runc, a successful escape affects the host and all containers on that host. With kata, the blast radius is limited to the guest VM.
16. Walk through the decision tree for recommending kata: recommend kata when workloads are multi-tenant, when the workload source is untrusted (e.g., external build pipelines, third-party code), or when the compliance requirement mandates kernel-level isolation. Do not recommend kata when workloads are single-tenant and fully trusted, when the Azure VM SKU does not support nested virtualization, or when the performance overhead of a guest kernel is unacceptable for the workload profile.
17. Share three discovery questions for a customer conversation: (1) Do you run workloads from multiple tenants or untrusted sources on shared nodes? (2) What is your incident response plan if a container escape occurs today? (3) Does your compliance framework require kernel-level workload isolation or only namespace isolation?
18. Open the floor for Q&A in the remaining time.

### Key Takeaways

- A sysrq-trigger inside a kata pod does not reboot the host node — the sysrq is processed by the Kata guest kernel, not the host kernel
- A kernel module load attempt inside a kata pod targets the guest kernel and does not affect the host filesystem or host kernel
- After both escape attempts, all cluster nodes remain Ready and neighboring workloads are unaffected — the isolation boundary held
- The field conversation should be reframed around blast radius: not if malicious code reaches the runtime, but what happens when it does
- Kata is recommended for multi-tenant environments, untrusted workload sources (e.g., external build pipelines), and compliance requirements mandating kernel-level isolation
- Three discovery questions surface the right customer context to recommend kata: multi-tenancy, incident response posture, and compliance isolation requirements

### Infrastructure Notes

- The build pod image must be pre-staged on quay.io and must include the test kernel module (`test-module.ko`) compiled for the Kata guest kernel version used in the lab environment
- The sysrq-trigger and kernel module test artifacts must be validated against the specific Kata guest kernel version before the session; different Kata versions ship different guest kernels
- Ensure the lab cluster has a neighboring workload pod running on the same Kata node before the session so step 4 is meaningful — include this in the environment setup playbook
