# open-workflow-agent-kubernetes-sandbox-controller

Restricted Kubernetes/OpenShift sandbox controller for the Open Workflow Agent external sandbox. Holds cluster lifecycle permissions behind a least-privilege, loopback-only boundary with deployment-owned namespace/ServiceAccount/network-policy controls so the main runtime never receives cluster-wide permissions.

See the [repository root](https://github.com/BassemZohdy/open-workflow-agent) and `docs/external-sandbox-contract.md` for documentation.
