# DPN ASTRA

**Adaptive Scientific Thinking, Reasoning & Autonomy**  
**DPN Technology • Genesis Prototype v0.1.0 • Standalone Repository**

ASTRA is a locally hosted AI research companion inspired by ASTRA in *The Diesel Paradox*. Its purpose is to grow into a capable, voice-first, memory-aware engineering partner across the DPN ecosystem. This project is **independent of DPN AI**; any future integrations must use authenticated, permission-scoped APIs rather than implicitly sharing control.

## The first working version

| Capability | v0.1.0 state |
| --- | --- |
| CORE | Real Ollama chat API; model must be installed and running |
| ARCHIVE | SQLite saved memories, only created when you explicitly click Save; delete per memory |
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

### Configuration

All variables are optional:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ASTRA_MODEL` | `llama3.2` | Ollama model name already pulled |
| `ASTRA_OLLAMA_URL` | `http://127.0.0.1:11434` | Local loopback Ollama endpoint only |
| `ASTRA_PORT` | `8766` | Local console port |
| `ASTRA_DATA_DIR` | `~/.dpn-astra` | Local saved-memory database directory |
| `ASTRA_PROJECT_DIR` | unset | Opt-in project root for bounded, read-only source context |

Example PowerShell:

```powershell
$env:ASTRA_MODEL="qwen2.5:7b"
$env:ASTRA_PROJECT_DIR="C:\path\to\your\project"
py -3 run_astra.py
```

When `ASTRA_PROJECT_DIR` is set, each chat request searches a bounded set of source and configuration files in that directory and adds relevant excerpts with relative paths and line numbers to the local model prompt. The Project Inspector also provides ranked source search and a file metadata index. Search parses eligible Python files without executing them and returns matching class/function/async-function definitions and module/class-level assignments with qualified names and line ranges, AST-detected calls to queried names with containing scope and line/column locations, class inheritance declarations matching queried base names, and imports matching queried names. Imports are resolved only when a matching Python module exists in the indexed project; the UI links to a matching top-level exported definition when identified, or labels a module-only result as a module. The inspector also lists up to 100 lexical uses of an imported alias in its importing file and makes those locations clickable. These name matches can be shadowed by a local binding; they are not semantic resolution. External/unresolved imports are explicitly left unresolved. Python AST analysis is additionally capped at 50,000 nodes and a depth of 200 per file; files beyond either cap are skipped for AST navigation with a warning, but remain eligible for text search. These are static syntax-based navigation results, not a complete reference index or call graph: dynamic dispatch, alias resolution, and runtime behavior are not determined. Function-local assignments are intentionally not listed. Invalid Python files are omitted from symbol, call-site, inheritance, and import results while remaining available as text-search results. Its change workbench loads an existing small UTF-8 file, stages a reviewable diff, and only writes after a separate explicit confirmation. Supported Python, JSON, and TOML proposals must pass static syntax validation before they can be staged; this does not establish semantic or runtime correctness. Project-wide checks return summary counts and at most 100 diagnostics; selecting a syntax diagnostic opens its source file at the reported line. Changes can be rolled back during the current process lifetime if the file has not changed since application. Static syntax checks validate the current editor text or the bounded project inventory without executing project code. This is not automatic model editing or a test runner: the user supplies/reviews replacement contents, and project commands/tests are not executed. The index excludes common dependency/build directories and credential-named files, redacts common secret assignments, and enforces directory-entry, file-count and byte limits. Review the files in your project and do not treat these safeguards as a guarantee that arbitrary source contains no secrets. Relevant excerpts are sent to the configured loopback Ollama service. The console indicates whether project context is enabled.

The Project Inspector's **Inspect Python imports** view lists bounded Python import declarations across the indexed project, including wildcard imports, and literal calls to `__import__()` and `importlib.import_module()`, including statically imported/aliased call names and literal `name=` arguments. Literal dynamic loads are marked separately; absolute string names and relative `importlib.import_module()` names with a literal `package` argument are analyzed. Computed names, `__import__()` relative names, and relative names without an explicit literal package are not resolved. Imported alias tracking is syntactic and can be confused by shadowing or rebinding. Imports are classified as local, unresolved-relative, unresolved-local, or unresolved-absolute, and links to local modules/definitions are provided when resolvable. It flags unresolved relative imports and absolute imports whose top-level root exists in the project as likely missing local targets; this includes a `from module import name` target when the local module exists but no matching top-level symbol or submodule is found. Modules with `__getattr__`, `__all__`, or a statically detected re-export are treated as potentially supplying the name. Unmatched absolute imports are not called missing, since they may be external. It also flags imports with no detected lexical uses as possibly unused, except wildcard imports, package initializers, and modules that declare `__all__`. The inventory reports strongly connected groups of local module dependencies as potential circular dependencies, including module edges where the imported symbol itself is unresolved. These are static heuristics: dynamic access and imports used only for side effects can appear unused, and conditional/runtime loading can make a reported cycle harmless. The inventory inspects at most 100 Python files, 100 imports per file, and 1,000 imports overall, and reports truncation and AST-analysis warnings. Wildcard imports are identified explicitly but their imported names are not expanded.

If a model is missing or Ollama is stopped, ASTRA reports the real error; it never fabricates a model response.

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
