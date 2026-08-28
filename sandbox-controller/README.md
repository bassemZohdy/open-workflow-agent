# open-workflow-agent-sandbox-controller

Restricted Docker execution controller for the Open Workflow Agent external sandbox. Holds the Docker credentials behind a least-privilege, Unix-socket-only boundary so the main runtime never mounts an unrestricted Docker socket.

See the [repository root](https://github.com/BassemZohdy/open-workflow-agent) and `docs/external-sandbox-contract.md` for documentation.
