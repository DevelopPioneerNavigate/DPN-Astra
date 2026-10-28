# Security — DPN ASTRA

## Current trust boundary
ASTRA v0.1 is a **single-user loopback prototype**, not a service designed for public network exposure. Do not port-forward port 8766 or deploy behind an unauthenticated reverse proxy. The HTTP handler checks Host and Origin on writes and binds only to 127.0.0.1. These measures do not replace user authentication if future deployments permit other local accounts or remote users.

## Storage
Only explicitly saved entries persist in `astra_memory.sqlite3`; chat history remains in process memory. SQLite notes are **not encrypted at rest**, and database backups or operating-system disk snapshots may retain deleted records. Never store secrets, credentials, personal medical data or production tokens. Use filesystem permissions and disk encryption at the OS level while an encrypted vault is pending.

Memory categories and source references are user assertions, not validated evidence.
New saves record server-generated `user_entry` provenance and a UTC consent/save time;
the save request constitutes consent in this local single-user UI/API, not an
authenticated consent audit. Migrated legacy notes have no known consent timestamp.
Fiction is excluded from normal chat-memory retrieval; all other categories enter as
untrusted reference text, with hypotheses and procedural authority explicitly
qualified. Prompt labels do not enforce model reasoning. There is no
automatic expiry or encrypted export. Schema
migration is transactional and refuses unsupported future versions; back up the
database with ASTRA stopped before an upgrade.

Memory edits use optimistic revision checks in a transaction to prevent a stale
review from overwriting a newer edit. They retain the first known consent time and
record a server-generated edit timestamp, not authenticated authorship. Revision
counters are not a protected audit trail or historical backup. Deletion remains
ID-based, so it is not guarded against edits made after the list was loaded.
Older entries are accessible in bounded cursor pages; those pages are not immutable
snapshots. No new shell, remote access, or model authority is granted.

Project context retrieval includes only global notes and the configured repository's
partition; without a project, retrieval is global-only. Project keys are hashes of
local root paths, not secrets, Git identities, authenticated users or tenant
authorization. HTTP clients select only global/current-project scope, not arbitrary
keys; scope checks and edit/delete mutations share a transaction. Full exports and
local snapshots deliberately include every partition. Existing notes migrate to
global, so legacy project-specific information must be reviewed before cross-project
use. No automatic reclassification or reassignment across moved/cloned roots exists.
This boundary limits context mixing; it does not prevent the model from making
incorrect associations or an OS-level user from reading the whole plaintext database.

JSON export requires an explicit confirmed UI action or a local POST subject to
the existing Host/Origin checks. It includes all saved categories and metadata and
is capped at 10,000 entries and 4 MiB, returning 413 rather than partial data. Export
responses are marked `no-store` and attachment downloads. Files downloaded to disk
are still plaintext; browser download locations and OS permissions are outside
ASTRA's control. There is no JSON import endpoint.

`python -m astra.memory_backup` is an operator-invoked local utility, not model
authority or an HTTP filesystem API. It opens sources read-only, never migrates
them, and exclusively creates a destination rather than overwriting existing
files. Snapshot verification checks SQLite integrity/current schema, not
authenticity, evidence truth, or malicious content. POSIX snapshots request `0600`;
Windows requires suitable ACLs. Snapshots are unencrypted and may retain deleted
records in free pages. Normal failures remove the new destination; abrupt
termination can leave incomplete files. Protect and verify backups independently.
Restore into a new private data directory with ASTRA stopped, keeping the old data
untouched. This is memory-only recovery, not protected or automated disaster recovery.

## Model and browser boundaries
Ollama transport validates the loopback endpoint even when constructed outside environment configuration. Requests ignore environment proxies and reject HTTP redirects; responses are capped at 4 MiB and checked for expected JSON shapes. Invalid responses surface as explicit health diagnostics or failed chat requests, not generated answers. Socket timeouts are not total request deadlines, and this single-user HTTP service still has no production-grade concurrency/resource isolation.

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
