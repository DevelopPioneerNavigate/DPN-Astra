# Security — DPN ASTRA

## Current trust boundary
ASTRA v0.1 is a **single-user loopback prototype**, not a service designed for public network exposure. Do not port-forward port 8766 or deploy behind an unauthenticated reverse proxy. The HTTP handler checks Host and Origin on writes and binds only to 127.0.0.1. These measures do not replace user authentication if future deployments permit other local accounts or remote users.

## Storage
Only explicitly saved facts persist in `astra_memory.sqlite3`; chat history remains in process memory. SQLite notes are **not encrypted at rest**, and database backups or operating-system disk snapshots may retain deleted records. Never store secrets, credentials, personal medical data or production tokens. Use filesystem permissions and disk encryption at the OS level while an encrypted vault is pending.

## Model and browser boundaries
The language model has no tools, shell commands, network connectors, robotics authority or credential access. The configured Ollama endpoint is restricted to loopback. Optional `ASTRA_PROJECT_DIR` context is bounded by directory-entry, file-count and byte limits; common dependency/build directories, symlinks and credential-named files are excluded, and common secret assignments are redacted. These filters are best-effort, not a guarantee that project content contains no sensitive data. Relevant source excerpts are included in prompts sent to the configured local Ollama service. Only enable this feature for a project whose contents you are willing to send to that local model.

The project workbench permits a user-confirmed one-file change only for an existing, eligible UTF-8 text file under the configured project root. It stages a diff first, checks the original file hash before applying, writes through a same-directory temporary file, and keeps an in-memory original snapshot for guarded rollback. Apply and rollback require separate explicit confirmation; rollback refuses to overwrite intervening changes. Proposal snapshots expire after 15 minutes or when ASTRA exits. This is a local single-user prototype, not authenticated authorization: do not expose it to other users or networks. Supported Python/JSON/TOML proposals must pass bounded static parsing before staging; project-wide validation returns at most 100 diagnostics while counts cover all scanned files. These checks do not establish runtime correctness. No project shell, build, or test command is executed: doing so requires a real sandbox and a separate security review. Browser-based dictation availability and privacy are implementation/browser dependent; it must be used only with user consent. The console renders model, source, and diff text with `textContent`.

When project context is enabled, lifecycle events for staged, applied, rolled-back, and expired proposals are stored in `ASTRA_DATA_DIR/astra_project_audit.sqlite3`. The endpoint returns at most the latest 100 events, while the underlying ledger is retained without automatic truncation. Metadata includes timestamps, paths, proposal IDs, states, and content hashes, but not source content. A SHA-256 hash chain detects inconsistencies when verified; it is not authenticated or tamper-proof, since an actor able to modify the database can recompute the chain. This is not a protected security audit boundary. Without project context, the capped 100-event history remains in process memory and is lost on exit.

## Required before DPN integration
- Per-user local authentication and privileged action re-verification.
- Asset allowlists, role-based controls and explicit human confirmation.
- Separate safety systems for physical machinery and doors.
- Signed plugin/connector manifests, least-privilege credentials, audit trails.
- Security testing: CSRF/DNS rebinding, resource exhaustion, prompt injection, dependency audit, malware scanning and backups.
- Incident response and transparent evidence about all failed actions.

Report suspected vulnerabilities privately to the repository maintainers; do not paste secrets into a public issue.
