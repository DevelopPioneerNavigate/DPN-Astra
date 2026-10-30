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

The project workbench permits a user-confirmed one-file change only for an existing, eligible UTF-8 text file under the configured project root. It stages a diff first, checks the original file hash before applying, writes through a same-directory temporary file, and keeps an in-memory original snapshot for guarded rollback. Apply and rollback require separate explicit confirmation; rollback refuses to overwrite intervening changes. Proposal snapshots expire after 15 minutes or when ASTRA exits. This is a local single-user prototype, not authenticated authorization: do not expose it to other users or networks. Supported Python/JSON/TOML proposals must pass bounded static parsing before staging; project-wide validation returns at most 100 diagnostics while counts cover all scanned files. These checks do not establish runtime correctness. The fixed, separately confirmed sandbox test profile is the only project-code execution; arbitrary commands and builds remain unavailable. Browser-based dictation availability and privacy are implementation/browser dependent; it must be used only with user consent. The console renders model, source, and diff text with `textContent`.

The status endpoint checks the configured fixed-profile runner, rootless WSL machine,
pinned local image, and execute opt-in. Its `isolation_verified` readiness field
remains false; only the per-run `isolation_preflight_passed` result reports whether
the in-container checks actually succeeded.

### Fixed-profile project test sandbox

`ASTRA_PROJECT_EXECUTE` defaults to `false` and accepts only `true` or `false`
(case-insensitive after whitespace trimming). Enabling it is a local operator
choice, not an HTTP/API permission: every test run separately requires a literal
confirmation and a fixed `unit_tests` profile. The endpoint rejects extra request
fields; it accepts no shell text or arbitrary command arguments. The runner invokes
the explicit local Podman connection and checks that the named WSL machine is
running, rootless, and configured for 2 CPUs/4096 MiB, the service is rootless, the
pinned Python 3.12 image is already present, and persistent project audit storage
exists. It never pulls an image or installs dependencies.

Each disposable container runs with networking disabled, the project mounted
read-only, a read-only root, UID/GID 1000, all capabilities dropped,
no-new-privileges, and a no-exec `/tmp` tmpfs. CPU time is limited to 300 seconds,
the process limit is 128, wall time is limited to 180 seconds, and output capture
is capped at 64 KiB. Before tests are discovered, an in-container preflight checks
the UID, effective capabilities, no-new-privileges, CPU/process limits, read-only
mounts, absence of the Windows drive mount, no-exec temporary filesystem, and
network denial. An unsuccessful preflight prevents the suite from running. A
separately confirmed cancellation request terminates the active Podman client;
the run thread then force-removes the named disposable container and audits the
cancelled outcome. Cancellation cannot guarantee immediate termination of an
underlying runtime failure; cleanup errors are surfaced and audited.

Rootless Podman on the verified setup has no delegated per-container memory
cgroup, so a per-container memory limit is **not** claimed or enforced. The WSL VM
itself is configured for 4 GiB; this is only a VM-level bound. The status endpoint
reports prerequisites and opt-in state only. Container isolation is
not protection against vulnerabilities in Podman/the VM/kernel, malicious tests
within their limits, or interference by another local user/process. Tests may
perform side effects in their disposable writable `/tmp`; project files are
read-only. Use only with trusted source.

Every attempt that reaches the runner records a persistent project-scoped metadata
event (run ID, profile, pinned image digest, outcome, duration and output-truncated
flag) in the audit database. Output, source, and exception text are not persisted.
The SHA-256 chain is not authenticated and can be recomputed by an actor able to
modify the database. Test output is returned only to the local request that started
the run and is rendered as text. A passing run means only that this specific test
suite passed at that time; it does not establish overall correctness or complete
the master project directive.

When project context is enabled, lifecycle events for staged, applied, rolled-back, and expired proposals, plus bounded sandbox test-run metadata, are stored in `ASTRA_DATA_DIR/astra_project_audit.sqlite3`. The endpoint returns at most the latest 100 events per history, while the underlying ledgers are retained without automatic truncation. Metadata includes timestamps, paths, proposal IDs, states, content hashes, and the sandbox profile/image/outcome; it does not include source content or test output. The proposal and sandbox SHA-256 hash chains detect inconsistencies when verified; they are not authenticated or tamper-proof, since an actor able to modify the database can recompute a chain. This is not a protected security audit boundary.

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

## Drafting task cancellation and budgets

`POST /api/project/draft-tasks` requires literal source-disclosure confirmation;
`POST /api/project/draft-tasks/<id>/cancel` requires separate literal confirmation.
Both retain Host/Origin checks. GET list/status routes retain Host checks and
expose only this handler's process-local tasks. IDs are not authentication tokens.
No new filesystem, execution, remote-access or inference-provider privileges are
granted. Existing direct drafting endpoints remain compatible.

Only one task worker may run/drain at once; the same drafting-service lock also
prevents concurrent direct generation. Cancellation is cooperative: it rejects
late output but cannot stop an in-flight Ollama call or undo disclosure already
made. Budgets (integer 10-120 seconds) gate output acceptance at phase boundaries
and after syntax validation, not provider computation or total wall-clock time.
Once staging acceptance wins the task lock, cancellation cannot undo it. There is
no thread killing, automatic retry, compensation, or project command execution.
Cancelled/timed-out workers retain their slot until they exit.

Latest 20 live task records/results are retained in RAM; restart loses actionable
tasks and no task auto-resumes. Finished output remains subject to proposal expiry and separately
confirmed Apply. Instructions/source are transient worker input, not a durable
task journal. Failures are explicitly reported and logged with task ID. A lost
start/cancel response must be reconciled via Find/Refresh, not blindly retried.

### Persistent task metadata

Audit schema v3 stores project-scoped task metadata only: IDs, relative target paths,
timestamps, budget, last recorded phase/worker flag, fixed outcome code and proposal
IDs. Instructions, source, diffs, full output and arbitrary exception text are
excluded. `GET /api/project/draft-task-history` retains Host checks and returns at
most 100 newest tasks with count/truncation. There is no new write/execute privilege
and historical entries cannot rehydrate live tasks or authorize mutations.

Per-handler owner IDs distinguish current-process tasks from historical records;
these IDs are not credentials, leases or cross-process locks. An active checkpoint
from an unobserved handler is marked unknown, not automatically failed/cancelled or
resumed. Multiple application processes are not coordinated by this mechanism.
The metadata table is plaintext, unauthenticated and outside event-chain verification.
It has no retention limit beyond bounded retrieval and is not included in memory
snapshots.

Checkpoint persistence fails closed before worker launch, at phase transitions and
before staging. A cancellation/timeout remains effective even if its checkpoint
write fails. Failure to record terminal completion cannot undo staged proposals:
live status exposes a checkpoint error while preserving the completed review result.
Task and proposal staging are not one atomic transaction. A crash or failed write
can leave historical metadata stale; inspect actual files and review checkpoints.
No automatic retry, task restoration or recovery authority is granted.

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

`GET /api/project/checkpoints/<id>/inspect` provides explicit, read-only
point-in-time comparison with the current file hash. Lookup is scoped to the
configured project, including records older than the visible 100-row page.
Without durable audit storage it can inspect only retained process-local proposals.
Target reads reapply read permission, root/path, symlink, eligible-name, UTF-8 and
24-KiB limits. Tampered checkpoint paths cannot bypass those checks. The response
contains hashes and comparison metadata, not source content. Inspection never
changes recorded lifecycle state or authorizes Apply/Rollback. Matches do not prove
which actor wrote the file, validate code, authenticate the stored hashes, or
prevent a local process from changing the target immediately after observation.
Failed inspection removes the displayed comparison in favor of an explicit error.

## Required before DPN integration

- Per-user local authentication and privileged action re-verification.
- Asset allowlists, role-based controls and explicit human confirmation.
- Separate safety systems for physical machinery and doors.
- Signed plugin/connector manifests, least-privilege credentials, audit trails.
- Security testing: CSRF/DNS rebinding, resource exhaustion, prompt injection, dependency audit, malware scanning and backups.
- Incident response and transparent evidence about all failed actions.

Report suspected vulnerabilities privately to the repository maintainers; do not paste secrets into a public issue.
