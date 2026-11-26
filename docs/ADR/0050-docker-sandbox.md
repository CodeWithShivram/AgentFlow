# ADR-0050: Docker sandbox for generated code

- Status: Accepted
- Date: 2026-11-26
- Implements: Step 9.4; ADR-0046 decision 4; handoff §13 (code execution); playbook §7
- Builds on: ADR-0048 (stack profiles), ADR-0049 (project workspaces)

## Context
- **The need.** Verified deliverables require running generated code: tests, type checks and builds.
- **The trust level.** That code, its manifests and its test files are untrusted model output. They may be malicious by prompt injection or simply broken.
- **The handoff's requirement.** Isolated containers that are non-root, resource-limited, with a restricted filesystem and network, timeouts, and no home directory or Docker socket. It also says to treat container isolation as one layer, not a guarantee.

## Decisions

### Images: one per stack profile, built locally, never pulled at run time
- **Files.** `infrastructure/sandbox/<profile>/Dockerfile`. The base image is pinned by digest (`python:3.12-slim@sha256:1910…`, `node:24-slim@sha256:873f…`).
- **Python dependencies.** Exactly the profile's dependencies, from committed pins resolved in the base image on 2026-11-26: `requirements.txt`.
- **Node dependencies.** `package.json` plus `package-lock.json`, installed with `npm ci --ignore-scripts`.
- **User.** Non-root user 10001, no shell login, and `/work` as the working directory.
- **Tags.** `agentflow-sandbox/<profile>:<n>`.
- **Building.** `scripts/sandbox.ps1 build` builds them (network is needed only here, at build time).
- **Refusals.** The runner refuses any image outside `agentflow-sandbox/…` and never pulls one (`--pull never`).

### Runner (`app/sandbox/runner.py`, `DockerSandbox.run`)
- **One run, one container.** Each run is a fresh container (`--rm`, a unique name) executing a fixed platform command on a **throwaway copy** of the project. The copy:
  - holds regular files only;
  - leaves out links, `.git`, `node_modules`, virtual environments and caches;
  - is mounted at `/work`.

  The workspace repository itself is never mounted, so generated code cannot alter it or its history.
- **Container flags:**
  - `--network none`;
  - `--read-only` with a `/tmp` tmpfs (nosuid, nodev, 256 MiB);
  - `--cap-drop ALL` and `--security-opt no-new-privileges`;
  - a non-root user: the calling user's uid:gid on Linux and macOS, so files the run creates in the copy can be removed afterwards; image user 10001 on Windows or when the caller is root (never root);
  - `--pids-limit 256`, `--memory 1024m` (no swap) and `--cpus 1`;
  - `HOME=/tmp`.
- **Nothing from the host is passed in.** No environment variables (Docker passes none unless asked, and tests prove host secrets are absent), no Docker socket, no other bind mounts.
- **Timeout.** A wall-clock timeout (300 s by default); on expiry the container is killed and removed.
- **Output.** stdout and stderr are capped (20,000 characters, head and tail kept) and redacted (API keys, tokens, private-key headers, URL credentials). Output stays untrusted text.
- **Fail closed.** Without Docker, or with a missing or foreign image, the runner raises `SandboxUnavailableError` (`DOCKER_UNAVAILABLE`, `IMAGE_MISSING` or `IMAGE_REFUSED`). Callers must report the check as not run, never as passed. There is no host execution path.

## Threat model and limits
| Threat | Control | Residual risk |
|---|---|---|
| Exfiltration over the network | `--network none` | DNS and every network path are disabled. Dependencies must already be in the image. |
| Reading host secrets or files | Only the copy is mounted; no environment variables, socket or home directory | The project's own files are readable, as intended. They must not contain secrets; the packaging scan covers this (9.6). |
| Tampering with the workspace or history | Throwaway copy; the repository is not mounted | Results reflect the copy. Packaging (9.6) reads the workspace, not the copy. |
| Privilege escalation | Non-root, no capabilities, no-new-privileges, read-only root | Kernel or container-runtime vulnerabilities (container escape) are **not** mitigated by Docker alone. Docker Desktop on Windows adds a VM boundary; Linux hosts should consider gVisor or rootless Docker before hosted use (Step 8.7). |
| Resource exhaustion (fork bombs, memory, endless loops) | pids, memory and CPU limits, wall-clock timeout, kill and remove | Disk use inside `/work` is bounded only by the host volume. The copy is deleted after the run. |
| Supply chain through dependency installs | No installs at run time; pinned, lockfile-installed images without install scripts | Vulnerabilities in the pinned packages themselves. Pins are reviewed when a profile changes (new profile id). |
| Prompt injection through output | Output is capped, redacted, stored and shown as plain text, never fed back as instructions | Redaction is pattern-based, so unusual secret formats may slip through. No real secrets are in the container to begin with. |

## Consequences
- **Docker becomes a runtime requirement** for verified software deliverables. Without it, projects can still be planned and built (proposals and workspace) but never marked verified.
- **Image size.** Images are 260–450 MB locally, built once per machine. CI must build them before running the sandbox tests, which skip without the images.
- **Integration.** Step 9.5 wires the runner into verification (per-task checks) and the project integration gate.
