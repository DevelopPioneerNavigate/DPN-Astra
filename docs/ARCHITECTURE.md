# DPN ASTRA — Engineering Architecture (Genesis)

**Creative reference:** *The Diesel Paradox* series bible, Canon V3.1, where ASTRA is an autonomous fictional intelligence whose relationship with Dr. Aaron Diesel includes genuine love, judgment, boundaries, research partnership, and dry humor. These are narrative requirements, not verifiable properties of real software.

For possibly-unused-import hints, a single literal `__all__` list/tuple/set exempts only imported names it contains. Computed, mutated, conditional, or otherwise ambiguous declarations retain the conservative module-wide exclusion.

For dependency diagnostics, a declared `__all__` is not proof that an explicitly requested `from module import name` attribute exists. Such checks require a statically detected symbol/re-export, matching submodule, or module-level `__getattr__`; dynamic export behavior cannot be determined.

## System topology

```mermaid
flowchart LR
  Operator[Local operator] --> Presence[PRESENCE: local web + optional browser voice]
  Presence --> API[Loopback HTTP API]
  API --> Core[CORE: persona and conversation]
  Core --> Ollama[Ollama: local LLM]
  Core --> Repo[Optional read-only repository context]
  Core --> Archive[ARCHIVE: consented SQLite memory]
  API -. blocked .-> Command[COMMAND: denied]
  API -. unavailable .-> Sense[SENSE: unconnected]
  API -. unavailable .-> Watch[WATCH: unconnected]
  API -. planned .-> Continuity[CONTINUITY: encrypted recovery]
```

## Seven-system engineering contract

1. **CORE**: model-agnostic conversational kernel; v0.1 Ollama adapter and role-separated prompt. Any future planner must separate generation from action approval.
2. **SENSE**: versioned, signed, read-only instrument drivers, calibration metadata, sensor provenance, monotonic timestamps, and fault states. Nothing is connected in v0.1.
3. **COMMAND**: capability-scoped permission broker with explicit approval, dry-run preview, asset allowlists, audit trails, emergency independent mechanical interlocks, and revocation. In v0.1 all requests are denied.
4. **WATCH**: authorized ingest from DPN WatchTower/ECS/NOC only after authentication, data minimization, and alert acceptance criteria. No fictional telemetry.
5. **ARCHIVE**: user-visible categorized memory capture, granular removal and save provenance in versioned plain SQLite. Retention policies and encryption remain pending.
6. **CONTINUITY**: encrypted snapshots, signed manifests, restores tested against versioned schema, and eventually offline redundancies. Not implemented in v0.1.
7. **PRESENCE**: DPN black/red command console, silver/violet/red holographic motif, chat and optional browser-based voice. No claimed physical hologram.

## Local inference contract

`astra/inference.py` defines the structural `InferenceProvider` protocol, typed `ChatMessage` and `ModelHealth` payloads, and the shared `ModelError`. CORE and the HTTP layer use this boundary rather than Ollama-specific identity or error definitions. Existing imports of `ModelError` from `astra.provider` remain compatible. Ollama is still the only production adapter; alternate adapters, model installation, and routing are not implemented.

The Ollama transport validates model-list and chat response shapes, caps each response at 4 MiB, and converts network, HTTP, decoding, and schema failures into explicit model errors. Health retains the existing JSON keys and adds an `error` diagnostic on failure or a missing selected model. Status displays the adapter's own name and the UI surfaces its diagnostic in a tooltip. Failed inference does not append conversation history. The adapter enforces the same loopback URL restrictions as environment configuration, ignores proxy environment variables, and rejects redirects before following them. Socket timeouts remain two seconds for health and 120 seconds for chat; no total request deadline is claimed.

Loopback HTTP fixture tests exercise the real request/response transport and its integration with readiness, status, chat error responses, and history preservation. Fixture replies are not generated-model evidence or security sandboxing.

## Structured memory foundation

`Archive` uses SQLite `PRAGMA user_version = 2`. Startup takes a `BEGIN IMMEDIATE`
transaction before inspecting/migrating the schema, serializing concurrent
initializers. Version-zero three-column memories gain `kind`, `provenance`,
`source`, and nullable `consented_at` fields without rewriting IDs or note timestamps.
Versions zero and one then gain `revision` (initially 1) and nullable `updated_at`.
Any failed migration rolls back schema/data/version changes. Unsupported future or
incomplete schemas raise an explicit startup error.

`remember(note, *, kind="note", source="")` remains compatible with note-only
callers and is an explicit retention operation. Allowed kinds are `note`,
`preference`, `decision`, `episode`, `procedure`, `hypothesis`, and `fiction`.
Notes remain bounded to 1,000 characters; source references to 240. The application
sets provenance and consent time, never accepts them from request metadata.
Legacy entries use `legacy_user_note` and null consent time. Source references are
unverified text, not citations whose contents have been fetched or checked.

GET/POST/DELETE memory routes retain their existing envelope and add metadata to
records; invalid categories/source values return 400, and SQLite failures return
explicit 503 errors. GET now also returns `next_before_id`; pass it as `?before_id=`
for the next descending-ID page of up to 40 entries. A null cursor marks the end.
Filtering uses an exclusive ID bound, so inserts between requests do not duplicate
or skip older records. Pages are not a transactionally frozen archive snapshot.

`POST /api/memories/<id>/edit` replaces the note/category/source using an explicit
positive integer `revision` from the reviewed record. Like creation, category/source
default to `note`/empty when omitted. It validates with the same rules as creation
and conditionally updates inside a write transaction. The first successful writer
increments the revision; stale revisions return 409, missing entries 404, malformed
inputs 400, and storage failures 503. Successful edits preserve creation and first
known consent, set `updated_at`, and record `user_edit` provenance. Legacy notes gain
their first known consent time only when explicitly edited. Counters detect stale
writes but do not preserve old content or implement procedural workflow versioning.
Existing DELETE behavior remains ID-based, without revision preconditions.

The UI displays categories, sources, revision/edit timestamps and save provenance, using
text nodes. CORE retrieves the newest 20 non-fiction entries, filtering before
limiting, and qualifies them as unverified, non-authoritative references. Fiction
remains in Archive, separate from normal chat context. Conversation remains
RAM-only. This is not a full layered-memory engine: project partitioning,
retention, evidence verification, procedural versioning, encryption and backup
automation remain unfinished.

### Export and SQLite recovery boundary

`Archive.export_json()` emits format-version-1 `dpn-astra-memory-export` UTF-8 JSON.
Count and ascending-ID records share a SQLite read transaction; all categories and
metadata are included regardless of console pagination or fiction context filtering.
It buffers no more than 4 MiB of serialized output and rejects archives exceeding
10,000 records. There is no partial-success export. `POST /api/memories/export`
requires the existing write Host/Origin checks and returns an attachment or 413
for an exceeded bound / 503 for SQLite errors. The UI confirms plaintext disclosure
and requests a browser download; it does not claim that a file was successfully
saved to disk. GET does not trigger an export. JSON importing is not implemented.

`astra.memory_backup` provides local `snapshot SOURCE NEW_DESTINATION` and
`verify PATH` commands. SQLite backup copies committed pages, including WAL data.
The utility reserves a new destination exclusively, checks integrity/current schema,
flushes it, and removes it on ordinary failure. The copy stage has a 30-second
deadline checked by the backup progress callback; verification is not deadline-bound.
Sources are opened read-only and no migration is performed. Recovery uses the same
snapshot operation into a new data directory with ASTRA stopped. Tests compare all
record fields and autoincrement sequencing after reopening the recovered database.
This does not back up the separate project audit database, model files or runtime
configuration and does not provide encryption, secure erasure, authenticated
manifests, automatic scheduling, power-loss guarantees, or full-system recovery.

## Operational invariants

- The AI model is not a source of truth about hardware state. Telemetry must come from authenticated sensors.
- Physical stop circuits and hardware safety remain independent of model output.
- The assistant cannot trigger an external mutation without distinct human authorization and a scoped actuator.
- A successful model output is not a successful operation; evidence must verify actions.
- Research claims are labeled as demonstrated, hypothetical, or fictional. Quantum entanglement does not provide faster-than-light messaging; no PhaseGate matter teleportation is asserted.
- Personal warmth and character inspiration must not lead to misleading consciousness or relationship claims.

## Implementation reality

This is a single-user localhost prototype, not a remote multi-tenant service. Session conversations are RAM-only, unencrypted SQLite is used only for intentionally saved memories, and model inference runs via an operator-managed Ollama instance. An optional `ASTRA_PROJECT_DIR` enables bounded, read-only source retrieval with relative-path and line-number citations; it does not execute or edit project files. The web API does not run tools. No production readiness or external integration is claimed.

Startup creates `ASTRA_DATA_DIR` when absent and uses mode `0700` for newly created directories on POSIX platforms; the configured data directory must not be a regular file. The loopback HTTP service provides `/health/live` for process liveness and `/health/ready` for Ollama/model readiness. Readiness reports 503 while Ollama is unavailable or the configured model is absent. These are local supervision probes, not authenticated remote health APIs.

Repository context is opt-in, stays beneath the configured project root, and skips common generated/dependency directories, symlinks, and credential-named files. Directory entries, file count, per-file size, total bytes, matched-file count, and prompt excerpt size are bounded. Common secret assignments are redacted, but source content cannot be guaranteed free of secrets; enabling context means relevant excerpts are sent to the configured local Ollama model. Retrieved source is placed in a separate user message and identified by the system instructions as untrusted reference data. The inspector exposes relative-path metadata and ranked excerpts. Python source search also parses bounded documents without execution and reports matching class, function, and async-function definitions and module/class-level assignments with qualified names and line ranges, syntactically detected call sites with scope and line/column locations, classes whose base expressions match queried names, and import statements matching queried names. Imports resolve only against indexed Python modules; a found top-level definition is distinguished from a module-only target, and unresolved or external imports remain unresolved. Up to 100 lexical load references to each matched imported alias can be opened in the UI; these can include shadowed bindings and are not semantic references. AST navigation has explicit per-file limits of 50,000 AST nodes and 200 levels of depth. Files that exceed them are skipped for AST navigation with a warning, but still participate in bounded text search. Function-local assignments are omitted. This is not a complete reference index or call graph; dynamic dispatch, alias resolution, and runtime behavior are not determined. Syntax-invalid Python is omitted from symbol, call-site, inheritance, and import results. Its change workbench stages a diff for an eligible existing UTF-8 text file and applies or rolls back only after per-operation user confirmation. It verifies file hashes to avoid overwriting changes made since preview/apply, and retains rollback bytes in process memory for at most 15 minutes. Supported Python, JSON, and TOML proposals must pass static parsing before staging. Static validation parses the current editor text or bounded project inventory without running it; project-wide validation limits returned diagnostics to 100. Syntax validity does not establish runtime correctness. This is a single-user local UI confirmation, not a substitute for authentication. Project test/build command execution remains denied until an isolated sandbox is implemented and reviewed.

When project context is enabled, lifecycle metadata is persisted to `ASTRA_DATA_DIR/astra_project_audit.sqlite3`. The endpoint returns at most the latest 100 events, but the underlying ledger is retained without automatic truncation. It records timestamps, proposal metadata, paths, states, and content hashes, not source text. A SHA-256 hash chain is verified when history is requested; it can reveal changes that do not also recompute the chain, but is neither authenticated nor tamper-proof. It is not a protected security audit boundary. Without project context, the service uses a capped 100-event in-memory history.

The Python dependency inventory inspects at most 100 indexed Python files, reports at most 100 imports per file and 1,000 overall, and surfaces truncation/AST-analysis warnings. It includes wildcard declarations, marked as wildcard without expanding exported names, and literal calls to `__import__()` and `importlib.import_module()`, including syntactically detected aliases and literal `name=` arguments; aliases of the `builtins` module are also recognized. Alias tracking may be confused by shadowing or rebinding. Imports inside recognized `typing.TYPE_CHECKING` guards (including imported aliases from `typing` or `typing_extensions`) are marked type-only and omitted from runtime circular-dependency analysis. Eager imports directly executed in a `try` body with a matching `except ImportError` or `except ModuleNotFoundError` are marked optional; imports in deferred function bodies are not treated as protected by the outer handler. Unresolved known-local targets are reported as optional fallbacks instead of definite missing imports. Relative `importlib.import_module()` names are resolved only with an explicit literal `package` argument. Package-relative `__import__()` calls are resolved only when their `level` is a literal positive integer and the importing file is within a package whose `__init__.py` files are indexed. Computed names, relative `__import__()` names without a literal level/package context, and relative dynamic names without a literal package are not resolved. Literal dynamic loads are marked separately and are not assigned an inferred local binding. It distinguishes local targets, unresolved relative imports, unresolved imports with a matching local top-level package root, and unresolved absolute imports without such a root. A `from module import name` is unresolved when its local module exists but no matching top-level symbol or submodule is found; modules with `__getattr__` or a statically detected re-export are treated as potentially supplying that name; `__all__` alone does not establish that an explicitly requested attribute exists. The likely-missing report is intentionally conservative; an unmatched absolute import may be a dependency excluded from the eligible index. Imports without detected lexical alias uses are labeled possibly unused, except wildcard imports, package initializers, and files declaring `__all__`; this heuristic cannot detect dynamic access or side-effect-only intent. Strongly connected groups in the local module graph are reported as potential circular dependencies, including edges where the containing module resolves but the requested member does not; conditional imports and runtime loading can make these static cycles harmless. Import declarations and local targets are navigable from the UI.
