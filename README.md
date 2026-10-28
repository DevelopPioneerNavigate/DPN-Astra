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

Example PowerShell:

```powershell
$env:ASTRA_MODEL="qwen2.5:7b"
$env:ASTRA_PROJECT_DIR="C:\path\to\your\project"
py -3 run_astra.py
```

When `ASTRA_PROJECT_DIR` is set, each chat request searches a bounded set of source and configuration files in that directory and adds relevant excerpts with relative paths and line numbers to the local model prompt. The Project Inspector also provides ranked source search and a file metadata index. Search parses eligible Python files without executing them and returns matching class/function/async-function definitions and module/class-level assignments with qualified names and line ranges, AST-detected calls to queried names with containing scope and line/column locations, class inheritance declarations matching queried base names, and imports matching queried names. Imports are resolved only when a matching Python module exists in the indexed project; the UI links to a matching top-level exported definition when identified, or labels a module-only result as a module. The inspector also lists up to 100 lexical uses of an imported alias in its importing file and makes those locations clickable. These name matches can be shadowed by a local binding; they are not semantic resolution. External/unresolved imports are explicitly left unresolved. Python AST analysis is additionally capped at 50,000 nodes and a depth of 200 per file; files beyond either cap are skipped for AST navigation with a warning, but remain eligible for text search. These are static syntax-based navigation results, not a complete reference index or call graph: dynamic dispatch, alias resolution, and runtime behavior are not determined. Function-local assignments are intentionally not listed. Invalid Python files are omitted from symbol, call-site, inheritance, and import results while remaining available as text-search results. Its change workbench loads an existing small UTF-8 file, stages a reviewable diff, and only writes after a separate explicit confirmation. Supported Python, JSON, and TOML proposals must pass static syntax validation before they can be staged; this does not establish semantic or runtime correctness. Project-wide checks return summary counts and at most 100 diagnostics; selecting a syntax diagnostic opens its source file at the reported line. Changes can be rolled back during the current process lifetime if the file has not changed since application. Static syntax checks validate the current editor text or the bounded project inventory without executing project code. This is not automatic model editing or a test runner: the user supplies/reviews replacement contents, and project commands/tests are not executed. The index excludes common dependency/build directories and credential-named files, redacts common secret assignments, and enforces directory-entry, file-count and byte limits. Review the files in your project and do not treat these safeguards as a guarantee that arbitrary source contains no secrets. Relevant excerpts are sent to the configured loopback Ollama service. The console indicates whether project context is enabled.

The HTTP service remains loopback-only. `GET /health/live` reports process liveness without requiring Ollama; `GET /health/ready` checks Ollama connectivity and configured model availability, returning HTTP 503 until both are true. Both endpoints retain the local Host-header check. These probes are for local service supervision, not remote monitoring or an authenticated management API.

The Project Inspector's **Inspect Python imports** view lists bounded Python import declarations across the indexed project, including wildcard imports, and literal calls to `__import__()` and `importlib.import_module()`, including statically imported/aliased call names and literal `name=` arguments. Literal dynamic loads are marked separately; absolute string names, relative `importlib.import_module()` names with a literal `package` argument, and package-relative `__import__()` names with a literal positive `level` in a directory containing `__init__.py` files are analyzed. This includes `builtins.__import__()` and imported aliases of the `builtins` module. Imports under recognized `typing.TYPE_CHECKING` guards are marked type-only and excluded from runtime cycle groups. Eager imports directly executed in `try` bodies with an `except ImportError` or `except ModuleNotFoundError` handler are marked optional; deferred imports inside function bodies are not treated as protected by the outer handler. Unresolved known-local targets are reported as optional fallbacks rather than definite missing imports. Computed names and relative dynamic names without sufficient literal package context are not resolved. Imported alias tracking is syntactic and can be confused by shadowing or rebinding. Imports are classified as local, unresolved-relative, unresolved-local, or unresolved-absolute, and links to local modules/definitions are provided when resolvable. It flags unresolved relative imports and absolute imports whose top-level root exists in the project as likely missing local targets; this includes a `from module import name` target when the local module exists but no matching top-level symbol or submodule is found. Modules with `__getattr__` or a statically detected re-export are treated as potentially supplying the name; `__all__` alone does not establish that an explicitly requested attribute exists. Unmatched absolute imports are not called missing, since they may be external. It also flags imports with no detected lexical uses as possibly unused, except wildcard imports, package initializers, and modules that declare `__all__`. The inventory reports strongly connected groups of local module dependencies as potential circular dependencies, including module edges where the imported symbol itself is unresolved. These are static heuristics: dynamic access and imports used only for side effects can appear unused, and conditional/runtime loading can make a reported cycle harmless. The inventory inspects at most 100 Python files, 100 imports per file, and 1,000 imports overall, and reports truncation and AST-analysis warnings. Wildcard imports are identified explicitly but their imported names are not expanded.

For explicit `from module import name` checks, a declared `__all__` is not treated as proof that the named attribute exists; only a statically detected symbol, re-export, submodule, or module-level `__getattr__` can satisfy the check. Dynamic `__all__` declarations remain outside static analysis.

For possibly-unused-import hints, a single literal `__all__` list, tuple, or set exempts only imported names it contains. Computed, mutated, conditional, or otherwise ambiguous `__all__` declarations retain the conservative module-wide exclusion; this remains a static heuristic.

If a model is missing or Ollama is stopped, ASTRA reports the real error; it never fabricates a model response.

### Local inference boundary

CORE consumes the typed `InferenceProvider` contract in `astra/inference.py`: adapter identity, selected model, health, and role-tagged chat messages. The shared `ModelError` represents failed inference, not an answer. Ollama remains the only production adapter; this contract does not install models, route tasks, or add llama.cpp/vLLM support.

The Ollama adapter validates JSON response shapes, limits each response to 4 MiB, and exposes diagnostic errors in `model_status.error` (also visible in the status-chip tooltip). Missing models are distinguished from unavailable or malformed provider responses. A failed chat returns HTTP 503 without appending fabricated history. Requests stay on the configured loopback HTTP endpoint: environment proxies and HTTP redirects are not used. Health requests use a two-second socket timeout and chat requests a 120-second socket timeout; these are not total wall-clock deadlines.

Transport tests use controlled local HTTP fixtures, not a running language model. They verify protocol behavior, not inference quality or sandbox isolation.

When `ASTRA_PROJECT_DIR` is enabled, ASTRA stores project-change lifecycle metadata (staged, applied, rolled back, expired) in `ASTRA_DATA_DIR/astra_project_audit.sqlite3`; only the latest 100 events are returned per request, while the ledger itself is retained without automatic truncation. It stores paths, proposal IDs, timestamps, states, and hashes, never source contents. The ledger has a SHA-256 hash chain that can reveal accidental or unsophisticated modification, but it is not authenticated or tamper-proof: someone able to rewrite the database can also recompute the chain. Without a configured project directory, history is process-local and capped at 100 events.

## Privacy & safety

- Console listens only on `127.0.0.1`. Restrictive Host/Origin and response headers are enforced.
- Chats exist only in process memory. Explicitly saved facts live in `ASTRA_DATA_DIR/astra_memory.sqlite3`.
- Stored facts are **not encrypted** in this prototype; do not save secrets or sensitive personal details.
- A user can view and delete each saved fact. Clearing the chat does not delete saved facts.
- External commands and infrastructure controls cannot execute. Project writes are limited to reviewed existing text files and require a separate per-change confirmation; rollback is guarded against overwriting intervening edits. The metadata ledger is not an authenticated security boundary. Future integrations require stronger authentication, independently protected audit logs, and physical interlocks.
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
