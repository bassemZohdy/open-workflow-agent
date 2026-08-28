# CI Runners and Repository Governance

This document describes the self-hosted runner requirements for container acceptance workflows, the expected recovery behavior, and the repository governance rules that protect `main`.

## Self-hosted Docker runner

The External Sandbox CI workflow requires a maintained self-hosted runner with labels:

```text
[self-hosted, linux, x64, docker]
```

### Bootstrap

1. Provision a Linux x64 host dedicated to CI (do not co-locate with production workloads).
2. Install a maintained Docker Engine release and verify `docker info` works for the runner user.
3. Register the runner (GitHub → Settings → Actions → Runners → New self-hosted runner) and set the labels above during registration.
4. Install the runner as a system service so it restarts with the host.
5. Confirm the runner appears as `Idle` in repository settings before relying on Docker acceptance gates.

### Required Docker access and isolation expectations

- The runner user must be in the `docker` group; this is equivalent to root access on that host, so the host must be treated as a controlled execution boundary.
- The runner is **not** a hard isolation boundary: never run untrusted workloads on it outside the acceptance workflows, and never store secrets on the runner host.
- Egress is required for registry pulls (GHCR, Docker Hub) and PyPI during image builds.

### Patching, cleanup, and failure recovery

- Patch the host and Docker Engine on a regular cadence; Dependabot and the Security workflow cover repository dependencies, not the runner host itself.
- Docker acceptance scripts name their containers/images deterministically and clean up in `trap` handlers; after an aborted run, prune leftovers with `docker container prune` / `docker image prune` scoped to `owa-*` names.
- If Docker acceptance jobs queue indefinitely, check: (a) the runner is online and `Idle`, (b) the runner has the exact label set, (c) disk space for the ~2 GiB image builds.
- If a runner is permanently retired, remove it from repository settings and update this document plus the workflow `runs-on` labels.

## GitHub-hosted jobs

A prior incident showed a CI run pending with zero jobs allocated. Diagnosis distinction:

- **GitHub-hosted scheduling/capacity** — the run shows queued but no runner ever picks it up, with no repository change involved. Retry the run; if it persists across retries, check the GitHub status page before changing repository configuration.
- **Repository workflow configuration** — concurrency groups, self-hosted-only labels, or malformed `runs-on` values. These fail deterministically and must be fixed in the workflow file.

The current workflows use GitHub-hosted runners for all quality/contract gates and the self-hosted runner only for Docker/container acceptance. Green runs for the current head are recorded in `PROJECT.md`.

## Branch and history governance

- A repository ruleset (`protect-main-history`) blocks force pushes and deletion of `main`; repository admins retain a bypass path for controlled recovery.
- Required status checks and pull-request-only merging are deliberately **not** enabled yet: the current maintenance flow pushes directly to `main`, and enabling required checks would block direct pushes. Revisit before the first formal release (R-001) if the flow moves to PR-based merging.
- Stale branches are pruned after verifying their content is present on `main`; keep a branch only when it represents intentional unmerged work.

## Publication gating

The Release workflow publishes images only when:

1. the main CI workflow succeeded for the release head;
2. the latest completed External Sandbox and PostgreSQL runs on `main` are green **and** cover the release head (ancestor check, so path-filtered workflows that did not run for a push are still accounted for);
3. the publish jobs run in the protected `release` GitHub environment with a protected-branch deployment policy (required reviewers are a repository-settings option for the owner);
4. every published image passes the Trivy vulnerability scan gate between build and push.
