# ASTRA Development Roadmap

Static missing-member analysis does not treat `__all__` as proof that an explicitly imported name exists; it requires a detected binding/re-export, submodule, or module-level `__getattr__`.

## v0.1 — Genesis (this PR)
- Local model transport, status checks and honest failure handling.
- Session conversation plus user-approved SQLite memory with deletion.
- Holographic-style local presence UI and optional browser voice/dictation.
- Default-deny COMMAND, disconnected SENSE/WATCH, documented CONTINUITY gap.
- Standard-library unit tests and Linux/Windows CI.

## v0.2 — Reliable Intelligence
- Model registry and selectable local inference providers.
- Encrypted memory at rest with export, full deletion, retention limits, and opt-in RAG.
- Separate facts, hypotheses, source citations, lab notes, and code evidence.
- Real tests for HTTP layer, malformed requests, and storage lifecycle.
- **In progress:** opt-in bounded repository retrieval grounds model prompts in local source excerpts with relative-path and line-number citations. A file index, ranked search, non-executing Python class/function/async-function and module/class assignment symbol lookup, AST-detected call-site and inheritance navigation, bounded local import resolution, capped lexical references to imported aliases, and a dependency inventory capped at 100 Python files, 100 imports per file, and 1,000 imports overall (including explicitly marked wildcard imports) are available; unresolved relative imports and unresolved imports under known local roots are surfaced as likely missing local targets, including absent imported names from otherwise present local modules, without misclassifying unmatched absolute imports as missing. Modules with `__getattr__` or a detected re-export are conservatively allowed to provide names; `__all__` alone does not establish that an explicitly requested attribute exists. Literal `__import__()` and `importlib.import_module()` calls are inventoried when passed a string literal, including syntactically detected aliases and literal `name=` arguments; aliases of the `builtins` module are also recognized. Alias tracking may be confused by shadowing or rebinding. Imports inside recognized `typing.TYPE_CHECKING` guards (including aliases from `typing` and `typing_extensions`) are marked type-only and excluded from runtime cycle analysis. Eager imports directly executed within `try` bodies catching `ImportError` or `ModuleNotFoundError` are marked optional; deferred imports inside function bodies are not treated as protected by the outer handler. Unresolved known-local targets are distinguished from definite missing imports. Relative `importlib.import_module()` names are resolved only with an explicit literal package argument. Package-relative `__import__()` calls are resolved only with a literal positive `level` from a file within an indexed regular package. Computed names and other relative dynamic forms are not resolved. Imports with no detected lexical uses are reported as possibly unused, with conservative exclusions for wildcards, package initializers, and `__all__` modules. Strongly connected groups in the local module graph are reported as potential circular dependencies, including edges where the module resolves but the requested member does not. Lexical references may include shadowed bindings and are not semantic resolution. AST navigation is capped per file at 50,000 nodes and depth 200. Reviewable one-file change proposals, explicit per-operation apply/rollback, SQLite metadata history with a verifiable SHA-256 hash chain (or capped temporary history when project context is off), non-executing Python/JSON/TOML syntax checks (per-file and project-wide), and clickable source-linked diagnostics are also available. Symbol and call-site navigation is not a complete reference index or call graph. The ledger is not authenticated or tamper-proof. Project tests and builds are not executed: a sandbox is still required. This remains a user-directed prototype, not an autonomous multi-file coding engine or sandboxed test runner.

## v0.3 — ASTRA PRESENCE
- Dedicated Windows desktop wrapper and audio pipeline with explicit microphone controls.
- Local STT/TTS voice profiles, interruption handling, reduced latency.
- Perception UI clearly distinguishes measured data, model reasoning, and uncertainty.

## v0.4 — Scientific Workbench
- Research notebook, engineering calculations, reproducible experiment reports.
- Read-only connections to opted-in repositories, project documents, test benches.
- Versioned evidence, units, calibrated readings, simulation mode.

## v0.5 — DPN Ecosystem
- Optional DPN One identity / DPN Operational Control connectors with scoped permissions.
- WATCH authorized feeds and anomaly rules; security boundaries across L1/L4.
- Approval-required command broker; default-deny, audited, verified outcomes.
- No removal of physical interlocks or safe shutdown pathways.

## v1.0 — Release Candidate
- Threat modeling, red-team validation, recovery drills, accessible desktop UX.
- Signed release artifacts and SBOM, privacy and safety review, reproducible builds.
- Clearly bounded semi-autonomous workflows where permitted; no self-proclaimed sentience.

Do not label a roadmap entry complete until implementation, tests, and evidence exist.
