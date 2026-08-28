# Security Policy

## Reporting a Vulnerability

Do not report security vulnerabilities through public GitHub issues, discussions, or pull requests.

Report vulnerabilities privately through GitHub's private vulnerability reporting:

1. Open <https://github.com/BassemZohdy/open-workflow-agent/security/advisories/new>.
2. Provide a clear description, affected version or commit, reproduction steps, and any impact assessment you can share.

You will receive a response within a reasonable time frame. If the issue is confirmed, a fix will be developed privately, released, and credited (unless you prefer to remain anonymous).

## Supported Versions

The project has not yet cut a formal semantic-version release. Security fixes apply to the current `main` branch and to any published `latest`/`sha-*` container images built from it.

## Security Model Summary

- Treat all workflow input as untrusted. The API endpoints are unauthenticated by default; deployments that expose them must place authentication, authorization, and rate controls in front (reverse proxy, mesh, or gateway).
- Shell, script, and container execution plus external catalogs are disabled by default and are enabled only through deployment configuration.
- The internal sandbox is a controlled execution boundary, not a hard isolation boundary. It does not provide container, pod, VM, or microVM isolation. Stronger isolation requires the external Docker/Kubernetes sandbox backends behind their restricted controllers.
- Never mount an unrestricted Docker socket into the Open Workflow Agent runtime and never grant the runtime cluster-wide Kubernetes/OpenShift permissions. The sandbox controllers exist to hold those credentials behind a least-privilege loopback boundary.
- Never log secrets or place credentials in workflow definitions. Provider credentials are supplied through environment-provided secrets.
