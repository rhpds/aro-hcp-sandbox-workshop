# Module 4 — Proving Isolation: Kernel, Process, and Tenant Boundaries

### Brief Overview

This module produces observable, verifiable proof that Kata Containers deliver hard kernel-level isolation. Participants deploy a runc pod and a kata pod from the same base image, then run a three-way kernel version comparison across the host, the runc pod, and the kata pod. The runc pod matches the host kernel; the kata pod returns a different guest kernel — that delta is the proof of isolation. A cross-tenant inspection exercise using two Kata pods and two runc pods on the same node demonstrates the concrete difference between hard and soft multi-tenancy, setting up the CISO-level blast radius discussion that closes the module.

### Audience and Time

- **Target personas:** Field Engineers, Solution Architects, Consultants
- **Prerequisites for this module:** Module 3 completed; kata RuntimeClass verified present; at least one Kata node in Ready state; container images pre-staged on quay.io
- **Estimated duration:** 20 minutes

### Learning Objectives

- Deploy a runc pod and a kata pod from the same base image and compare their kernel versions against the host kernel
- Prove kernel-level isolation by demonstrating that the kata pod's guest kernel version differs from the host and runc pod kernel version
- Demonstrate that cross-tenant process inspection from a kata pod returns no results from a neighboring kata tenant's processes
- Prove that the same cross-tenant inspection from a runc pod exposes a neighboring runc tenant's secrets through the shared /proc filesystem
- Analyze the blast radius difference between a CVE in a kata guest kernel versus the same CVE in a runc container on a shared host kernel

### Lab Structure

| Section | Title | Duration |
|---------|-------|----------|
| 1 | Deploy runc and kata pods; three-way kernel version comparison | 7 min |
| 2 | Cross-tenant inspection: kata tenants | 6 min |
| 3 | Cross-tenant inspection: runc tenants (contrast) | 4 min |
| 4 | CISO blast radius discussion | 3 min |

### Detailed Steps

1. Deploy the runc test pod using the shared base image: `oc apply -f runc-pod.yaml` and confirm it reaches Running state on a standard worker node.
2. Deploy the kata test pod using the same base image with `runtimeClassName: kata` set in the pod spec: `oc apply -f kata-pod.yaml` and confirm it reaches Running state on a Kata node.
3. Record the host kernel version by opening a debug shell on the Kata node: `oc debug node/<kata-node-name>` and running `uname -r` inside the shell. Note the version string. Exit the debug shell.
4. Exec into the runc pod and run `uname -r`: `oc exec <runc-pod-name> -- uname -r`. Note the version string.
5. Exec into the kata pod and run `uname -r`: `oc exec <kata-pod-name> -- uname -r`. Note the version string.
6. Compare all three kernel version strings: confirm the runc pod version matches the host kernel version, and confirm the kata pod version is different — this difference is the verifiable proof of guest kernel isolation.
7. Exec into the runc pod and list running processes: `oc exec <runc-pod-name> -- ls /proc` and observe the process IDs visible through the shared host /proc filesystem.
8. Deploy two Kata tenant pods on the same Kata node — Tenant A and Tenant B — each with a distinct secret injected as an environment variable: `oc apply -f kata-tenant-a.yaml` and `oc apply -f kata-tenant-b.yaml`.
9. Exec into Tenant A's kata pod and attempt to list all processes: `oc exec <kata-tenant-a-pod> -- ps aux` or inspect `/proc`. Observe that only Tenant A's own processes are visible — Tenant B's processes do not appear.
10. Exec into Tenant A's kata pod and attempt to read Tenant B's environment variables by inspecting `/proc/<pid>/environ` for any PID visible from Tenant A. Confirm that Tenant B's secret is not accessible.
11. Deploy two runc tenant pods on the same worker node — runc Tenant A and runc Tenant B — each with a distinct secret injected as an environment variable: `oc apply -f runc-tenant-a.yaml` and `oc apply -f runc-tenant-b.yaml`.
12. Exec into runc Tenant A's pod and list processes visible through /proc: `oc exec <runc-tenant-a-pod> -- ls /proc`. Observe that PIDs from the shared host are visible.
13. Identify a PID belonging to runc Tenant B's process and read its environment variables: `oc exec <runc-tenant-a-pod> -- cat /proc/<tenant-b-pid>/environ`. Confirm that Tenant B's secret environment variable is readable from Tenant A — demonstrating soft multi-tenancy boundaries.
14. Compare the two outputs back to back: kata cross-tenant inspection returned nothing; runc cross-tenant inspection exposed secrets. Discuss the concrete difference between hard and soft multi-tenancy.
15. Discuss the CISO-level question: if the kata guest kernel has a CVE, the blast radius is limited to workloads running inside that single guest VM. Compare this to the same CVE in a runc container on a shared host kernel, where the blast radius extends to all containers on that host — and potentially the host itself.

### Key Takeaways

- The runc pod kernel version matches the host kernel version; the kata pod returns a different guest kernel version — this delta is verifiable, observable proof of isolation
- Process visibility through /proc confirms the isolation boundary: kata tenant pods see only their own guest kernel processes; runc tenant pods can see each other's processes through the shared host /proc
- Cross-tenant environment variable inspection is possible with runc (soft multi-tenancy) and blocked with kata (hard multi-tenancy) — both demonstrated on the same physical node
- A CVE in a kata guest kernel has a blast radius limited to workloads inside that single guest VM; the same CVE in runc has a blast radius extending to all containers sharing the host kernel
- The side-by-side kata vs. runc contrast is the most effective customer-facing demonstration of why kernel-level isolation matters for multi-tenant regulated environments

### Infrastructure Notes

- Container images for runc-pod, kata-pod, and the four tenant pods must be pre-staged on quay.io before the session; ensure they are pullable from the lab cluster without authentication issues
- The tenant pod manifests must pin pod-to-node affinity so that Tenant A and Tenant B land on the same physical node for the cross-tenant inspection steps to be meaningful
- Kata tenant pods require the `kata` runtimeClassName and the Kata SCC grant from Module 3 to be in place before this module begins
