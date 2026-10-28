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

## Model-assisted drafting

The operator must explicitly authorize each `POST /api/project/draft` or
`POST /api/project/draft-set` before the selected files' full, unredacted source and bounded retrieved excerpts go to the
local inference provider. Review targets for sensitive content; filename filtering
does not prove a file is secret-free. Existing Host/Origin checks and repository
path/size restrictions apply. Drafts cannot name a different output path: the model
only returns replacement content in single-file mode. Multi-file output must
cover exactly the operator-selected paths, with no extras or duplicates. Embedded source instructions are marked
untrusted; prompt instructions cannot guarantee a model ignores them.

Generation grants no write or execution authority. Output is bounded and parsed,
then supported syntax is checked before a pending proposal is staged. A source
hash precondition rejects stale baselines; Apply remains separately confirmed with
its existing hash guard. Human review must check the actual diff for malicious or
incorrect code, not just syntax validity. There is one concurrent draft allowance
per HTTP service, but no production isolation or total inference deadline. There
is no automatic retry, test execution, dependency installation or shell access.

Multi-file drafting is restricted to 1-5 eligible existing targets with 48 KiB
aggregate source and replacement content, and 24 KiB per file. Raw JSON response
parsing is capped at 384 KiB. Every target's pre-inference source hash is checked
when preparing the set; all existing staging guards still apply. The model cannot
choose additional files, create/delete files, or bypass a rejected entry. All
selected files must change; unchanged entries reject the set rather than silently
weakening scope validation. Single- and multi-file generation share the same lock.

## Multi-file review sets

The change-set endpoint accepts at most five existing eligible files and 48 KiB
of supplied UTF-8 content, within the existing 64-KiB HTTP body bound. It grants no
new path, creation, execution or remote-access capability. All syntax/size/path
checks and baseline checks pass before actionable proposals are registered. Audit
failure during staging can leave attempted-stage metadata, but does not publish an
actionable subset. The audit ledger remains unauthenticated.

Apply/Rollback stays individually confirmed per file; there is no "Apply all"
transaction. Partial application and rollback conflicts are possible and explicitly
displayed. An error/lost response marks the UI state unverified until state refresh.
State refresh reports process-local proposal state, not independent disk/hash
verification. A concurrent local writer can still race filesystem operations;
this is not isolation from hostile processes. No crash-safe multi-file journal or
automatic compensation is implemented. Refresh after expiry/restart cannot recover
lost rollback bytes. Back up important projects independently.

## Review checkpoints

Review recovery uses `GET /api/project/checkpoints` under the existing Host check.
It exposes only the configured project's checkpoint rows and process-local reviews.
Checkpoint rows are a plaintext, unauthenticated latest-event index in the project
audit database; the existing event-chain verification does not authenticate or
verify that separate index. Tampered/stale metadata never grants mutation authority.
Only original process-local proposals can be recovered into Apply/Rollback controls,
and those operations retain confirmation, path and hash guards. Metadata after
restart/expiry is explicitly non-actionable. Original set grouping and validation
results are not persisted; recovering a review does not revalidate syntax or disk
state. The checkpoint table contains no source/replacement/diff/instruction bytes.
An audit failure can leave attempted-stage metadata without an actionable proposal;
an audit failure after a write can leave recorded state behind actual disk state.
Inspect files whenever outcomes are uncertain. There is no automatic reconciliation,
retention, encrypted source recovery or crash-safe task journal.

## Required before DPN integration

- Per-user local authentication and privileged action re-verification.
- Asset allowlists, role-based controls and explicit human confirmation.
- Separate safety systems for physical machinery and doors.
- Signed plugin/connector manifests, least-privilege credentials, audit trails.
- Security testing: CSRF/DNS rebinding, resource exhaustion, prompt injection, dependency audit, malware scanning and backups.
- Incident response and transparent evidence about all failed actions.

Report suspected vulnerabilities privately to the repository maintainers; do not paste secrets into a public issue.
