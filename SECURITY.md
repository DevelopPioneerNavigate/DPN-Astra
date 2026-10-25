# Security — DPN ASTRA

## Current trust boundary
ASTRA v0.1 is a **single-user loopback prototype**, not a service designed for public network exposure. Do not port-forward port 8766 or deploy behind an unauthenticated reverse proxy. The HTTP handler checks Host and Origin on writes and binds only to 127.0.0.1. These measures do not replace user authentication if future deployments permit other local accounts or remote users.

## Storage
Only explicitly saved facts persist in `astra_memory.sqlite3`; chat history remains in process memory. SQLite notes are **not encrypted at rest**, and database backups or operating-system disk snapshots may retain deleted records. Never store secrets, credentials, personal medical data or production tokens. Use filesystem permissions and disk encryption at the OS level while an encrypted vault is pending.

## Model and browser boundaries
The language model has no tools, shell commands, file writes, network connectors, robotics authority or credential access. The configured Ollama endpoint is restricted to loopback. Optional `ASTRA_PROJECT_DIR` context is read-only and bounded by directory-entry, file-count and byte limits; common dependency/build directories, symlinks and credential-named files are excluded, and common secret assignments are redacted. Repository permissions default to denying reads, edits and execution; the explicitly configured project context grants read access only. These filters are best-effort, not a guarantee that project content contains no sensitive data. Relevant source excerpts are included in prompts sent to the configured local Ollama service. Only enable this feature for a project whose contents you are willing to send to that local model. Browser-based dictation availability and privacy are implementation/browser dependent; it must be used only with user consent. The console renders message text with `textContent`, not HTML.

## Required before DPN integration
- Per-user local authentication and privileged action re-verification.
- Asset allowlists, role-based controls and explicit human confirmation.
- Separate safety systems for physical machinery and doors.
- Signed plugin/connector manifests, least-privilege credentials, audit trails.
- Security testing: CSRF/DNS rebinding, resource exhaustion, prompt injection, dependency audit, malware scanning and backups.
- Incident response and transparent evidence about all failed actions.

Report suspected vulnerabilities privately to the repository maintainers; do not paste secrets into a public issue.
