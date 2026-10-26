# ASTRA Development Roadmap

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
- **In progress:** opt-in bounded repository retrieval grounds model prompts in local source excerpts with relative-path and line-number citations. A file index, ranked search, non-executing Python class/function/async-function and module/class assignment symbol lookup, AST-detected call-site, inheritance, and import navigation with clickable locations, reviewable one-file change proposals, explicit per-operation apply/rollback, SQLite metadata history with a verifiable SHA-256 hash chain (or capped temporary history when project context is off), non-executing Python/JSON/TOML syntax checks (per-file and project-wide), and clickable source-linked diagnostics are available. Symbol and call-site navigation is not a complete reference index or call graph. The ledger is not authenticated or tamper-proof. Project tests and builds are not executed: a sandbox is still required. This remains a user-directed prototype, not an autonomous multi-file coding engine or sandboxed test runner.

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
