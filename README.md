# DPN ASTRA

**Adaptive Scientific Thinking, Reasoning & Autonomy**  
**DPN Technology • Genesis Prototype v0.1.0 • Standalone Repository**

ASTRA is a locally hosted AI research companion inspired by ASTRA in *The Diesel Paradox*. Its purpose is to grow into a capable, voice-first, memory-aware engineering partner across the DPN ecosystem. This project is **independent of DPN AI**; any future integrations must use authenticated, permission-scoped APIs rather than implicitly sharing control.

## The first working version

| Capability | v0.1.0 state |
| --- | --- |
| CORE | Real Ollama chat API; model must be installed and running |
| ARCHIVE | Versioned categorized SQLite memory; explicit saves, conflict-checked edits, deletion, JSON export and local snapshot/recovery tools |
| PRESENCE | Responsive black/red DPN console, binary rain, abstract ASTRA hologram, chat, optional browser speech output and dictation |
| SENSE | Disabled until instruments are explicitly connected |
| COMMAND | Deny-by-default stub, no execution permissions |
| WATCH | Disabled until telemetry integrations are approved |
| CONTINUITY | Planned; encrypted backup not yet implemented |

ASTRA does **not** claim sentience, genuine emotion, autonomously acting hardware, real research findings without evidence, or access to systems that were never linked. The fictional ASTRA's care for Diesel informs the character design; this implementation cannot establish or prove subjective experiences.

## Run locally (Windows / Linux / macOS)

1. Install **Python 3.11+** and [Ollama](https://ollama.com/).
2. In a terminal, install and run a local model:

   ```sh
   ollama pull llama3.2
   ollama serve
   ```

   If Ollama is already running as a service, do not launch a second server.

3. In a separate terminal from this repository:

   ```sh
   python run_astra.py
   ```

   On Windows, `py -3 run_astra.py` is also supported.

4. Open **http://127.0.0.1:8766**.

No extra Python packages or cloud AI API key are required. Performance depends on the model and your local hardware.

### Verify local inference and recover setup failures

Installing Ollama does not install a model. Model installation downloads
third-party weights from the Ollama registry; review the model's license and
ensure sufficient disk space first. The default `llama3.2` is the approximately
2 GB, 3.2B-parameter Q4_K_M model, subject to Meta's Llama 3.2 Community License
and acceptable-use policy. These are not DPN-trained or DPN-owned weights.
Once downloaded, local inference does not require a cloud inference account.

On Windows, if `ollama` is not on the current terminal's PATH:

```powershell
$ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
& $ollama list
& $ollama pull llama3.2
```

If a pull fails with `open ...\.ollama\id_ed25519: The system cannot find the
file specified`, quit and relaunch the Ollama application, then retry the pull.
If you started `ollama serve` manually, stop that instance and restart it instead.
Do not delete the model/data directory or copy private keys into ASTRA.
Restarting Ollama interrupts active inference; check for running work first.

With ASTRA running, verify the actual service and model rather than relying on
the presence of an executable:

```powershell
Invoke-RestMethod "http://127.0.0.1:8766/api/status"
Invoke-RestMethod "http://127.0.0.1:8766/health/ready"
Invoke-RestMethod "http://127.0.0.1:8766/api/chat" -Method Post `
  -ContentType "application/json" `
  -Body '{"message":"What is 17 times 19?","session":"local-smoke-test"}'
```

Readiness requires both a reachable Ollama service and the configured installed
model. A real chat response verifies the inference path, not general reasoning
or coding quality. Clearing that test session uses the normal clear-history API;
chat is not automatically saved to Archive. Keep both services loopback-only.
This desktop setup is not an independent production server deployment.

### Consented memory foundation

The Archive saves only deliberately submitted entries, not conversations. The console
offers notes, preferences, decisions, episodes, procedures, hypotheses, and fiction.
An optional source reference is stored as user-supplied text, never fetched or treated
as verified evidence. Each new save records its UTC timestamp and `user_entry`
provenance. Saving through the local API is the same explicit retention request as
clicking Save; these markers are not authenticated proof of consent.

Existing three-column, version-1 and version-2 databases migrate transactionally to schema version 3 while
preserving note IDs, contents, timestamps, and ID sequencing. Legacy entries are
labeled `legacy_user_note` with an unknown (`null`) consent timestamp, not retroactively
assigned a consent event. Newer unsupported or incompatible schemas fail startup
rather than being overwritten. Stop ASTRA and back up `astra_memory.sqlite3` before
upgrading; no automatic backup or downgrade is provided. Migrated entries start at
revision 1 with no invented edit timestamp.

Use **Edit** to load an entry, then **Save changes** to approve its revision.
**Cancel edit** discards the draft without changing storage. Saves retain the
original creation timestamp and first known consent time, record a new edit time,
and increment the revision. A stale revision returns a conflict rather than
overwriting another edit; your draft remains in the form. Copy it before reloading
the current entry. These revision counters do not retain historical note contents.
Editing a legacy entry records its first known consent at that explicit edit.

The latest 20 non-fiction memories may enter chat context, labeled user-supplied and
not independently verified. Hypotheses are not findings; procedures do not grant
execution permission. Fiction remains visible/deletable in Archive but is excluded
from normal memory retrieval. This is prompt-level labeling, not a guarantee of
model behavior. The console displays 40 entries per page; **Older memories** and
**Newest memories** make earlier saved entries available for review/edit/deletion.
Pagination is based on descending IDs, not a frozen snapshot: concurrent inserts
appear when you return to Newest, and deletions disappear. Storage remains
unencrypted; retention policies, encrypted recovery,
and evidence verification are not implemented.

### Project memory partitions

With `ASTRA_PROJECT_DIR` configured, the console offers **Current configured project**
as a separate browse/save scope. The default remains **Global** for compatibility.
Choose the scope before saving; editing keeps the original scope. Chat retrieves
the newest 20 non-fiction entries from global plus the configured project only,
filtering out other projects before applying the limit. Without repository context,
only global memories enter chat.

The project key is a SHA-256 digest of the normalized, resolved repository root
path. It is local directory identity, **not** a Git remote identity, authorization
token, or confidentiality mechanism. Different clones/paths intentionally receive
different partitions; moving a repository or changing OS/path spelling conventions
can change its key. There is no project-key reassignment/move tool yet. Export and
SQLite snapshots retain all projects and their keys, including partitions not
currently configured. To browse an existing partition, configure its original root.

**Existing memories migrate to global**, because their original project is unknown.
Review them before using ASTRA across projects; legacy project-specific notes can
still appear in any project until you remove/recreate them in the appropriate scope.
Global saves are explicit permission to reuse that note across projects. Prompt
partition labels do not guarantee that the model will reason correctly. This is
single-user context separation, not a multi-user access-control boundary.

### Export and recovery

**Export all memories** requests an unencrypted JSON download after confirmation.
It includes every category, including fiction, all source references, and original
save/edit metadata, not just the visible page. The format is
`dpn-astra-memory-export`, format version 1, with the storage schema version,
UTC export time, record count, and records in ascending ID order. The export uses
one SQLite read transaction so its count and contents agree. It refuses exports
over 10,000 entries or **4 MiB of UTF-8 JSON** instead of silently truncating them.
For larger archives, use a SQLite snapshot. JSON import is not implemented; JSON
is a readable portability export, **not a restorable backup**.

The standard-library snapshot command uses SQLite's backup API (including committed
WAL data), verifies database integrity and the current memory schema, and refuses
to overwrite any destination. It requests private file permissions on POSIX.
Verification does not authenticate provenance or validate whether notes are true.
Snapshot creation has a 30-second copy deadline; verification time depends on file
size. Parents must already exist. These files are **not encrypted**; protect them
with appropriate OS access controls/disk encryption. Snapshots can retain deleted
data in SQLite free pages and are not a secure-erasure mechanism.

Run from the repository root, using your configured Python interpreter. PowerShell:

```powershell
# Stop ASTRA first for the documented backup/recovery workflow.
# Use your actual ASTRA_DATA_DIR if it differs from the default.
$source = Join-Path $HOME ".dpn-astra\astra_memory.sqlite3"
# Choose an existing private backup directory and a NEW file name.
$backup = "C:\ASTRA-Backups\memory-2026-10-28.sqlite3"
python -m astra.memory_backup snapshot $source $backup
python -m astra.memory_backup verify $backup
```

Successful commands print JSON with `schema_version`, `memory_count`, and
`integrity: "ok"` and exit 0. Failures print an explicit error on stderr and exit 1.
Only current-schema databases are accepted; use ASTRA to migrate older databases
before snapshotting them. Before migrating an older database, stop ASTRA and preserve
a filesystem copy of the complete old data directory, including any SQLite
sidecars; the snapshot utility deliberately does not migrate legacy files.
Normal copy/verification failures remove the newly created
destination. A crash or power loss can leave an incomplete file; verify snapshots
before relying on them. Never assume a snapshot exists just because a path exists.

To recover **without overwriting live data**, stop ASTRA and choose a new empty,
private data directory. Copy the verified snapshot through the same command:

```powershell
# Create/secure this new directory first; keep the old data directory untouched.
$recovered = "C:\ASTRA-Recovered"
$restored = Join-Path $recovered "astra_memory.sqlite3"
python -m astra.memory_backup snapshot $backup $restored
python -m astra.memory_backup verify $restored
# Continue only if BOTH commands exited successfully.
$env:ASTRA_DATA_DIR = $recovered
python run_astra.py
```

Check saved categories, source references and edit revisions in the console.
The recovery tests reopen snapshots and compare records, metadata and ID sequencing;
they also test committed WAL data, corruption and no-overwrite behavior.
This recovers **memory only**, not the separate project audit database, application
configuration, model weights, or RAM-only conversations. No scheduled backups,
automatic retention, JSON restore, encrypted recovery, or full-system disaster
recovery is claimed.

### Configuration

All variables are optional:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ASTRA_MODEL` | `llama3.2` | Ollama model name already pulled |
| `ASTRA_OLLAMA_URL` | `http://127.0.0.1:11434` | Local loopback Ollama endpoint only |
| `ASTRA_PORT` | `8766` | Local console port |
| `ASTRA_DATA_DIR` | `~/.dpn-astra` | Persistent local data directory; created on startup (new POSIX directories request mode `0700`) |
| `ASTRA_PROJECT_DIR` | unset | Opt-in project root for bounded, read-only source context |
| `ASTRA_PROJECT_EXECUTE` | `false` | Explicit opt-in for the single fixed, sandboxed unit-test profile |

Example PowerShell:

```powershell
$env:ASTRA_MODEL="qwen2.5:7b"
$env:ASTRA_PROJECT_DIR="C:\path\to\your\project"
# Optional; still requires a separate confirmation in the Project Inspector.
$env:ASTRA_PROJECT_EXECUTE="true"
py -3 run_astra.py
```

When `ASTRA_PROJECT_DIR` is set, each chat request searches a bounded set of source and configuration files in that directory and adds relevant excerpts with relative paths and line numbers to the local model prompt. The Project Inspector also provides ranked source search and a file metadata index. Search parses eligible Python files without executing them and returns matching class/function/async-function definitions and module/class-level assignments with qualified names and line ranges, AST-detected calls to queried names with containing scope and line/column locations, class inheritance declarations matching queried base names, and imports matching queried names. Imports are resolved only when a matching Python module exists in the indexed project; the UI links to a matching top-level exported definition when identified, or labels a module-only result as a module. The inspector also lists up to 100 lexical uses of an imported alias in its importing file and makes those locations clickable. These name matches can be shadowed by a local binding; they are not semantic resolution. External/unresolved imports are explicitly left unresolved. Python AST analysis is additionally capped at 50,000 nodes and a depth of 200 per file; files beyond either cap are skipped for AST navigation with a warning, but remain eligible for text search. These are static syntax-based navigation results, not a complete reference index or call graph: dynamic dispatch, alias resolution, and runtime behavior are not determined. Function-local assignments are intentionally not listed. Invalid Python files are omitted from symbol, call-site, inheritance, and import results while remaining available as text-search results. Its change workbench loads an existing small UTF-8 file, stages a reviewable diff, and only writes after a separate explicit confirmation. Supported Python, JSON, and TOML proposals must pass static syntax validation before they can be staged; this does not establish semantic or runtime correctness. Project-wide checks return summary counts and at most 100 diagnostics; selecting a syntax diagnostic opens its source file at the reported line. Changes can be rolled back during the current process lifetime if the file has not changed since application. Static syntax checks validate the current editor text or the bounded project inventory without executing project code. The only execution capability is the separately enabled and confirmed fixed unit-test sandbox described below; no arbitrary commands are accepted. The index excludes common dependency/build directories and credential-named files, redacts common secret assignments, and enforces directory-entry, file-count and byte limits. Review the files in your project and do not treat these safeguards as a guarantee that arbitrary source contains no secrets. Relevant excerpts are sent to the configured loopback Ollama service. The console indicates whether project context is enabled.

### Fixed-profile sandbox tests

The Project Inspector's **Run unit tests in sandbox** control is available only
when `ASTRA_PROJECT_DIR` is set, `ASTRA_PROJECT_EXECUTE=true`, persistent audit
storage is configured, and the pinned local Podman runtime/image checks pass.
Each run also requires a separate confirmation. ASTRA accepts only the fixed
Python unittest-discovery profile (equivalent to
`python -m unittest discover -s tests -q`); the API rejects extra command fields
and offers no shell, arbitrary arguments, build, install, or network
access. The project is mounted read-only in a disposable Python 3.12 container
with networking disabled, an unprivileged UID, all Linux capabilities dropped,
no-new-privileges, read-only root filesystem, a no-exec temporary filesystem,
CPU/process limits, and a 180-second wall timeout. An in-container preflight
checks those runtime properties before discovering tests. Captured output is
limited to 64 KiB and is displayed only for that request. A separate confirmed
**Cancel active sandbox run** action terminates the runner process; the request
thread then removes the disposable container and records a `cancelled` outcome.

On the verified Windows setup, the rootless WSL Podman VM is limited to 2 CPUs
and 4 GiB RAM. Rootless Podman does not expose a verified per-container memory
limit in this configuration; **the VM limit is not a per-container memory cap**.
The image is pinned by digest and must already exist locally; ASTRA will not pull
it during a run. Tests can still consume resources within the stated VM limits
and can have side effects in their temporary container. Only run tests for source
you trust. A run's persistent, project-scoped audit record contains metadata and
the image digest, not test output or source. Its hash chain is tamper-evident
only, not authenticated. A passing test run is evidence for that fixed suite at
that time, not proof of overall correctness or completion of the master issue.

The HTTP service remains loopback-only. `GET /health/live` reports process liveness without requiring Ollama; `GET /health/ready` checks Ollama connectivity and configured model availability, returning HTTP 503 until both are true. Both endpoints retain the local Host-header check. These probes are for local service supervision, not remote monitoring or an authenticated management API.

The Project Inspector's **Inspect Python imports** view lists bounded Python import declarations across the indexed project, including wildcard imports, and literal calls to `__import__()` and `importlib.import_module()`, including statically imported/aliased call names and literal `name=` arguments. Literal dynamic loads are marked separately; absolute string names, relative `importlib.import_module()` names with a literal `package` argument, and package-relative `__import__()` names with a literal positive `level` in a directory containing `__init__.py` files are analyzed. This includes `builtins.__import__()` and imported aliases of the `builtins` module. Imports under recognized `typing.TYPE_CHECKING` guards are marked type-only and excluded from runtime cycle groups. Eager imports directly executed in `try` bodies with an `except ImportError` or `except ModuleNotFoundError` handler are marked optional; deferred imports inside function bodies are not treated as protected by the outer handler. Unresolved known-local targets are reported as optional fallbacks rather than definite missing imports. Computed names and relative dynamic names without sufficient literal package context are not resolved. Imported alias tracking is syntactic and can be confused by shadowing or rebinding. Imports are classified as local, unresolved-relative, unresolved-local, or unresolved-absolute, and links to local modules/definitions are provided when resolvable. It flags unresolved relative imports and absolute imports whose top-level root exists in the project as likely missing local targets; this includes a `from module import name` target when the local module exists but no matching top-level symbol or submodule is found. Modules with `__getattr__` or a statically detected re-export are treated as potentially supplying the name; `__all__` alone does not establish that an explicitly requested attribute exists. Unmatched absolute imports are not called missing, since they may be external. It also flags imports with no detected lexical uses as possibly unused, except wildcard imports, package initializers, and modules that declare `__all__`. The inventory reports strongly connected groups of local module dependencies as potential circular dependencies, including module edges where the imported symbol itself is unresolved. These are static heuristics: dynamic access and imports used only for side effects can appear unused, and conditional/runtime loading can make a reported cycle harmless. The inventory inspects at most 100 Python files, 100 imports per file, and 1,000 imports overall, and reports truncation and AST-analysis warnings. Wildcard imports are identified explicitly but their imported names are not expanded.

For explicit `from module import name` checks, a declared `__all__` is not treated as proof that the named attribute exists; only a statically detected symbol, re-export, submodule, or module-level `__getattr__` can satisfy the check. Dynamic `__all__` declarations remain outside static analysis.

For possibly-unused-import hints, a single literal `__all__` list, tuple, or set exempts only imported names it contains. Computed, mutated, conditional, or otherwise ambiguous `__all__` declarations retain the conservative module-wide exclusion; this remains a static heuristic.

If a model is missing or Ollama is stopped, ASTRA reports the real error; it never fabricates a model response.

### Local model-assisted code drafts

With `ASTRA_PROJECT_DIR` enabled, enter an existing eligible relative file path in
the Project Inspector, describe one concrete change, and click **Generate reviewed
draft**. Confirm that the file may be sent to your configured local inference
provider. The selected **on-disk** file is sent verbatim, alongside bounded
repository evidence matched to the instruction; unsaved editor text is not input
and is replaced only on a successful draft. Review source for secrets first:
target content is deliberately not redacted because it must be faithfully edited.
Other repository excerpts retain the existing best-effort filters/redaction.

The provider must return a JSON object with exactly one `content` string containing
the complete replacement. ASTRA rejects malformed, fenced, duplicate-key,
oversized, non-text, and invalid-Unicode output. Files/replacements remain limited
to 24 KiB of UTF-8. Python/JSON/TOML drafts must pass existing static syntax checks;
other eligible text types explicitly have no validator. Syntax success does not
establish correctness or test success.

A successful request fills the editor and stages a complete diff. **No file is
written until you separately confirm Apply.** Existing guarded rollback remains
available. A SHA-256 baseline check rejects a file changed while the model was
generating; the normal Apply check also rejects intervening edits after staging.
One drafting request may run at a time per HTTP service; others return a conflict.
Failures do not fall back to invented code or append conversation history.

This single-file control remains available alongside the multi-file drafting
workflow below. Neither is an autonomous agent loop or a test/build runner.
Drafting requires a working installed local model.
Automated tests use explicitly controlled provider replies to verify the workflow,
not to establish code-generation quality. Invalid model output must be retried
explicitly. Commands, project tests and builds remain disabled.

### Multi-file review sets

The Project Inspector's **Multi-file review set** accepts a JSON array containing
1-5 existing files with their complete replacements:

```json
[
  {"path": "src/example.py", "content": "value = 2\n"},
  {"path": "config.json", "content": "{\"value\":2}\n"}
]
```

The JSON-array preview accepts **operator-supplied replacements**.
ASTRA checks the entire request's shape and bounds, validates supported syntax,
prepares every diff, rejects duplicate targets and changed baselines, then registers
the proposals together. A failed preview leaves no actionable subset staged and
never writes project files. Total supplied content is limited to 48 KiB UTF-8;
individual files/replacements retain the 24-KiB limit. The HTTP request—including
JSON escape overhead—must also fit the existing 64-KiB body limit.

Alternatively, enter 1-5 existing relative paths (one per line), describe the
coordinated change, and choose **Generate multi-file draft**. Approve sending all
selected on-disk files verbatim and bounded repository evidence to local inference.
Unsaved editor contents and saved memories are not supplied. Source is limited
to 24 KiB per file and 48 KiB total; review every target for secrets first.

The model must return exactly `{"files":[{"path":"...","content":"..."}]}` with
every selected target exactly once, no extra paths/keys, and complete changed
contents. Response JSON is capped at 384 KiB, replacement content at 48 KiB total
and 24 KiB per file. A missing target, unchanged file, invalid supported syntax,
stale source baseline or other invalid entry rejects the whole draft set before
proposals become actionable. Select only files that need changes. Single- and
multi-file generation share one concurrency allowance. There is no automatic
retry or repair. A failed request retains the previous visible review set.

After completion, **Load completed draft review** displays ordinary per-file diffs
and validation results; the task result also includes provider/model identity.
**Generation writes no project files:** review and separately confirm each Apply.
No tests or builds execute, and application remains non-atomic across files.
Controlled provider/browser fixtures verify this pipeline, not live model quality.

Each card has its own confirmed **Apply this file** and **Rollback this file**
action. **Apply is not atomic across the set.** If the second file conflicts or
fails, the first can remain applied; its card and rollback remain available. No
automatic compensation overwrites another file. After a lost/error response,
actions on that card are disabled until **Refresh set states** retrieves the
server's proposal state. Refresh does not revalidate current file hashes, run tests,
or prove filesystem correctness; inspect actual files if an outcome is uncertain.
The existing hash guards run on each Apply/Rollback.

The set view is browser-memory-only. Proposals and rollback snapshots expire after
15 minutes or process restart; after a page reload, use the recovery control below
to retrieve still-retained reviews (without their original grouping). Replacing
the visible set does not undo applied changes or discard pending server proposals.
Keep necessary proposal IDs and review the existing change history before leaving.
There is no multi-file transactional filesystem commit, crash recovery, automatic
retry, new-file creation, dependency ordering, or test/build execution.

### Cancellable drafting tasks

The multi-file generation button now starts a **process-local drafting task**
instead of holding a browser request open for inference. Set an integer
**10-120 second acceptance budget**, approve source disclosure, then use:

- **Refresh draft task** to read its current phase, worker status and error.
- **Cancel draft task** to prevent accepting its output if staging has not won
  the cancellation race.
- **Load completed draft review** to explicitly replace visible reviews, after
  checking that every proposal still exists. Apply remains separately confirmed.
- **Find draft tasks** to recover an ID after browser reload or a lost start
  response. The latest 20 tasks are retained; oldest inactive tasks are evicted
  when capacity is needed. There is no automatic polling or retry.

Tasks report queued/preparing/inference/validating/completed/failed,
cancel_requested/cancelled, or timed_out. **Cancellation cannot interrupt an
in-flight provider call.** The worker remains active while that call drains,
and no second task is accepted until it exits. Late cancelled/timed-out output
is discarded before staging. Single-file and direct synchronous multi-file
requests still share the existing inference lock.

The budget uses monotonic elapsed time and is checked at phase boundaries and
again after syntax validation before accepting staging. It is not a hard wall-
clock, GPU, token, or transport deadline: parsing, file access, and an already
accepted staging/audit operation are not forcibly interrupted. Cancellation is
serialized against validation/staging acceptance; if staging wins, cancellation
reports completed rather than pretending the proposals were undone.

Live tasks, detailed errors and review results are RAM-only. Reload can rediscover
them while the same server process lives; restart cannot restore them. Instructions and source
are used transiently for the running draft, not persisted as task records. Completed
task results do not extend proposal expiry or restore lost rollback bytes.
Failures are reported explicitly and logged; no repair, retry, write or project
command is automatically performed. This is not a durable task scheduler or an
autonomous engineering loop.

The existing synchronous `/api/project/draft-set` endpoint remains available for
compatibility; the new controls use `/api/project/draft-tasks`.

### Persistent drafting-task checkpoints

With normal project audit storage configured, **Show task checkpoint history**
reads the newest 100 task metadata records for the configured project, including
tasks from earlier server processes. It records the task ID, target paths, creation
and checkpoint times, acceptance budget, last phase, recorded worker activity,
fixed outcome code, and staged proposal IDs when a completion checkpoint succeeds.
It does **not** store the instruction, source, replacement content, diff, full
provider response or exception text. Live detailed errors remain transient.

History distinguishes:
- **Current process:** use Find/Refresh for actual live status.
- **Historical recorded outcome:** the last checkpoint recorded worker exit.
- **Interrupted or unobserved worker:** a prior checkpoint recorded an active
  worker, but this handler cannot observe its final outcome. A second running
  ASTRA process is also unobserved; history does not assert that it stopped.

Historical entries have no task execution, Cancel, Load or Apply actions. Nothing
auto-resumes, regenerates or restores proposal/rollback bytes. Recorded completion
means a draft was staged, not applied or tested. Staging and task checkpoint writes
are separate transactions: after a crash or storage failure, the last task phase
may be stale even if proposals were staged. Use review checkpoints and file
inspection to investigate; do not infer success or failure from an unknown outcome.

Audit schema v3 adds the task metadata table transactionally, preserving existing
events, hashes and review checkpoints. Initial/phase/pre-staging storage failures
prevent further generation/staging and are reported. Cancellation still takes
effect if its checkpoint cannot be saved. If final persistence fails after staging,
the completed live result remains available with a **checkpoint error** warning.
No automatic checkpoint retry is performed.

Metadata is plaintext, unauthenticated, not covered by the event hash chain, and
has no automatic retention policy. The 100-record display bound does not delete
older rows. The memory-only snapshot command does not back up this audit database.
Without audit storage, live task behavior is unchanged and history reports that
persistent checkpoints are unavailable. This is metadata continuity, not durable
task execution or crash-safe workflow recovery.

### Review checkpoints and reload recovery

Choose **Recover reviews / show checkpoints** to retrieve all retained proposals
for the current project into the per-file review controls. This recovers diffs
after a browser reload while the same ASTRA process and proposal lifetime remain
active. Each mutation still needs confirmation and existing hash checks. Recovery
does not rerun syntax checks, execute tests, verify current disk contents, or
preserve the original set grouping. It replaces the visible review only after a
successful request.

With normal project audit storage configured, lifecycle metadata also survives
ASTRA restart: proposal ID, path, last recorded event/state, timestamps and content
hashes. These records are isolated by normalized resolved project-root identity.
**No source, replacement content, diff, instruction or rollback bytes is persisted
by this feature.** After restart or expiry, records are metadata only: inspect
the current files and create fresh proposals. Recovery never reconstructs an
actionable proposal from historical metadata or performs an automatic write.

Audit schema v2 adds a project-scoped checkpoint table transactionally, preserving
the existing event/hash chain. Legacy events lack project identity and are not
guessed into a project checkpoint; they remain available in Change History.
The checkpoint display returns at most the latest 100 records plus all currently
retained reviews. Metadata has no automatic retention policy, encryption or
authentication. It is not crash-safe task execution recovery or a source backup.
The separate memory snapshot command does not back up this audit database.

Use **Inspect current file hashes** on a retained review or historical checkpoint
to compare the current file's exact bytes against the recorded original and
proposal hashes. Results distinguish **matches original**, **matches proposal**,
and **diverged**, with a timestamp and current SHA-256. Line-ending differences
count as different bytes. Missing, unreadable, binary, oversized or no-longer-
eligible targets produce explicit errors rather than a reassuring comparison.

Inspection does not send source to inference or return source contents. It does
not modify project files, checkpoint state, or mutation approval. A match with a
proposal is **not proof that ASTRA applied it**; another writer could produce
identical bytes. Results are point-in-time observations against unauthenticated
metadata, not a correctness check, permission grant, or guarantee that the file
will remain unchanged. After restart, inspecting a checkpoint still cannot restore
Apply/Rollback controls or lost rollback bytes.

Repository evidence lookup accepts the full 4,000-character chat message or
draft instruction, including matching terms near its end. It does not truncate
the instruction sent to the model. The Project Inspector's explicit search API
retains its separate 200-character query limit. Existing file-count, byte,
excerpt, and context-output limits apply to both paths.

### Local inference boundary

CORE consumes the typed `InferenceProvider` contract in `astra/inference.py`: adapter identity, selected model, health, and role-tagged chat messages. The shared `ModelError` represents failed inference, not an answer. Ollama remains the only production adapter; this contract does not install models, route tasks, or add llama.cpp/vLLM support.

Adapters also accept the optional keyword-only `json_mode` flag. Single-file
and multi-file drafting request it; the Ollama adapter sends `format: "json"`.
Ordinary chat keeps the existing free-text request format. JSON mode does not
establish that a draft is complete or correct: ASTRA still rejects duplicate keys,
unexpected shapes, oversized content, invalid supported syntax, and stale source
hashes. It never applies a generated draft without separate approval.

The Ollama adapter validates JSON response shapes, limits each response to 4 MiB, and exposes diagnostic errors in `model_status.error` (also visible in the status-chip tooltip). Missing models are distinguished from unavailable or malformed provider responses. A failed chat returns HTTP 503 without appending fabricated history. Requests stay on the configured loopback HTTP endpoint: environment proxies and HTTP redirects are not used. Health requests use a two-second socket timeout and chat requests a 120-second socket timeout; these are not total wall-clock deadlines.

Transport tests use controlled local HTTP fixtures, not a running language model. They verify protocol behavior, not inference quality or sandbox isolation.

When `ASTRA_PROJECT_DIR` is enabled, ASTRA stores project-change lifecycle metadata (staged, applied, rolled back, expired) in `ASTRA_DATA_DIR/astra_project_audit.sqlite3`; only the latest 100 events are returned per request, while the ledger itself is retained without automatic truncation. It stores paths, proposal IDs, timestamps, states, and hashes, never source contents. The ledger has a SHA-256 hash chain that can reveal accidental or unsophisticated modification, but it is not authenticated or tamper-proof: someone able to rewrite the database can also recompute the chain. Without a configured project directory, history is process-local and capped at 100 events.

## Privacy & safety

- Console listens only on `127.0.0.1`. Restrictive Host/Origin and response headers are enforced.
- Chats exist only in process memory. Explicitly saved facts live in `ASTRA_DATA_DIR/astra_memory.sqlite3`.
- Stored facts are **not encrypted** in this prototype; do not save secrets or sensitive personal details.
- A user can view and delete each saved fact. Clearing the chat does not delete saved facts.
- Arbitrary external commands and infrastructure controls cannot execute. The only project-code execution is the fixed, separately confirmed sandbox test profile, disabled by default and isolated as documented above. Project writes are limited to reviewed existing text files and require a separate per-change confirmation; rollback is guarded against overwriting intervening edits. The metadata ledger is not an authenticated security boundary. Future integrations require stronger authentication, independently protected audit logs, and physical interlocks.
- Browser dictation and speech depend on browser support; dictation is initiated only after clicking Mic.
- No unattended agent loop is enabled.

## Verify

```sh
python -m unittest discover -s tests -v
python -m compileall -q astra run_astra.py
```

## Documentation

- [Canon-to-system architecture](docs/ARCHITECTURE.md)
- [Phased engineering roadmap](docs/ROADMAP.md)
- [Security model and limitations](SECURITY.md)
- [Copyright and licensing notice](NOTICE.md)

> This is an early implementation, not an independently validated scientific AI or production-safe infrastructure controller.
