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

When `ASTRA_PROJECT_DIR` is set, each chat request searches a bounded set of source and configuration files in that directory and adds relevant excerpts with relative paths and line numbers to the local model prompt. The Project Inspector also provides ranked source search and a file metadata index. Its change workbench loads an existing small UTF-8 file, stages a reviewable diff, and only writes after a separate explicit confirmation. Changes can be rolled back during the current process lifetime if the file has not changed since application. Static syntax checks validate the currently loaded editor text for Python, JSON, and TOML without executing project code. This is not automatic model editing or a test runner: the user supplies/reviews replacement contents, and project commands/tests are not executed. The index excludes common dependency/build directories and credential-named files, redacts common secret assignments, and enforces directory-entry, file-count and byte limits. Review the files in your project and do not treat these safeguards as a guarantee that arbitrary source contains no secrets. Relevant excerpts are sent to the configured loopback Ollama service. The console indicates whether project context is enabled.

If a model is missing or Ollama is stopped, ASTRA reports the real error; it never fabricates a model response.

## Privacy & safety

- Console listens only on `127.0.0.1`. Restrictive Host/Origin and response headers are enforced.
- Chats exist only in process memory. Explicitly saved facts live in `ASTRA_DATA_DIR/astra_memory.sqlite3`.
- Stored facts are **not encrypted** in this prototype; do not save secrets or sensitive personal details.
- A user can view and delete each saved fact. Clearing the chat does not delete saved facts.
- External commands and infrastructure controls cannot execute. Project writes are limited to reviewed existing text files and require a separate per-change confirmation; rollback is guarded against overwriting intervening edits. Future integrations require stronger authentication, durable audit logs, and independent physical interlocks.
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
