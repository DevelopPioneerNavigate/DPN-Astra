(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const transcript = $("conversation");
  const messageInput = $("message");
  const messageForm = $("chat-form");
  const statusChip = $("model-state");
  const sendButton = $("send");
  const systems = $("systems");
  const notice = $("notice");
  let speaking = false;
  let busy = false;
  let stagedChange = null;
  let rollbackChange = null;
  let sandboxRunInProgress = false;

  function display(role, value, label) {
    const div = document.createElement("div");
    div.className = "message " + role;
    const speaker = document.createElement("span");
    speaker.className = "speaker";
    speaker.textContent = label || (role === "user" ? "OPERATOR" : "ASTRA / CORE");
    const p = document.createElement("p");
    p.textContent = value;
    div.append(speaker, p);
    transcript.append(div);
    transcript.scrollTop = transcript.scrollHeight;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
    let data;
    try { data = await response.json(); } catch { throw new Error("Invalid response from ASTRA local server"); }
    if (!response.ok) throw new Error(data.error || "Request failed");
    return data;
  }

  function report(error) {
    notice.textContent = "Notice: " + (error.message || String(error));
  }

  async function openProjectLocation(path, line) {
    const file = await api("/api/project/file?path=" + encodeURIComponent(path));
    $("project-file-path").value = file.path;
    $("project-file-content").value = file.content;
    $("project-file-content").disabled = false;
    $("project-propose").disabled = false;
    $("project-validate").disabled = false;
    const editor = $("project-file-content");
    if (line) {
      const lines = file.content.split("\n");
      const offset = lines.slice(0, Math.max(0, line - 1)).join("\n").length + (line > 1 ? 1 : 0);
      editor.focus();
      editor.setSelectionRange(offset, offset);
      const lineHeight = parseFloat(getComputedStyle(editor).lineHeight) || 16;
      editor.scrollTop = Math.max(0, (line - 1) * lineHeight);
    } else {
      editor.focus();
      editor.setSelectionRange(0, 0);
    }
    $("project-change-status").textContent = "Opened " + file.path +
      (line ? " at line " + line : "") + ". Editing is staged only.";
  }

  async function refreshProjectHistory() {
    const status = $("project-history-status");
    const list = $("project-change-history");
    status.textContent = "Loading current-process change events…";
    list.replaceChildren();
    try {
      const response = await api("/api/project/changes");
      for (const event of response.events) {
        const item = document.createElement("li");
        item.textContent = new Date(event.timestamp).toLocaleString() + " — " +
          event.event + " — " + event.path + " — " +
          event.proposal_id + " — SHA-256 " + event.proposed_sha256;
        list.append(item);
      }
      status.textContent = response.durable
        ? "Persistent history: showing " + response.events.length + " of " + response.total + " event(s)."
        : "Temporary history: " + response.total + " event(s); cleared when ASTRA exits.";
      if (response.integrity) {
        status.textContent += response.integrity.valid
          ? " Hash chain verified (" + response.integrity.checked + " events)."
          : " HASH CHAIN INVALID at event " + response.integrity.first_invalid_sequence + ".";
      }
      if (!response.total) status.textContent += " No project change events recorded.";
    } catch (error) {
      status.textContent = "Could not load project change history: " + (error.message || String(error));
    }
  }

  async function refreshSandboxReadiness(updateStatus = true) {
    const status = $("project-sandbox-status");
    const button = $("project-test-run");
    button.disabled = true;
    try {
      const readiness = await api("/api/project/sandbox");
      if (updateStatus) status.textContent = readiness.status.toUpperCase() + ": " + readiness.reason;
      button.disabled = !readiness.enabled;
    } catch (error) {
      if (updateStatus) status.textContent = "Sandbox readiness check failed: " + (error.message || String(error));
    }
  }

  async function refreshSandboxHistory() {
    const status = $("project-sandbox-history-status");
    const list = $("project-sandbox-history");
    status.textContent = "Loading persistent sandbox run metadata…";
    list.replaceChildren();
    try {
      const response = await api("/api/project/sandbox-runs");
      for (const event of response.events) {
        const item = document.createElement("li");
        item.textContent = new Date(event.timestamp).toLocaleString() + " — " +
          event.state + " — " + event.profile + " — exit " +
          (event.exit_code === null ? "n/a" : event.exit_code) + " — " +
          event.duration_ms + " ms — image " + event.image_digest +
          (event.output_truncated ? " — output truncated" : "");
        list.append(item);
      }
      status.textContent = "Persistent metadata: showing " + response.events.length +
        " of " + response.total + " run(s).";
      if (response.integrity) {
        status.textContent += response.integrity.valid
          ? " Hash chain verified (" + response.integrity.checked + " events)."
          : " HASH CHAIN INVALID at event " + response.integrity.first_invalid_sequence + ".";
      }
      if (!response.total) status.textContent += " No sandbox runs recorded.";
    } catch (error) {
      status.textContent = "Could not load sandbox run history: " + (error.message || String(error));
    }
  }

  async function runSandboxTests() {
    const button = $("project-test-run");
    const cancelButton = $("project-test-cancel");
    const status = $("project-sandbox-status");
    const output = $("project-sandbox-output");
    if (!confirm(
      "Run the fixed Python unit-test profile in the pinned local Podman sandbox?\n\n" +
      "The configured repository is mounted read-only. Network is disabled; the container " +
      "runs as an unprivileged user with no capabilities, a read-only root, bounded CPU/" +
      "processes and a 180-second wall timeout. Temporary output is capped at 64 KiB. " +
      "The 4 GiB limit applies to the Podman VM; there is no verified per-container memory limit. " +
      "Tests may be slow. Only run this for source you trust."
    )) return;
    button.disabled = true;
    sandboxRunInProgress = true;
    cancelButton.disabled = false;
    output.textContent = "";
    status.textContent = "Starting the confirmed, fixed-profile sandbox test run…";
    try {
      const response = await api("/api/project/test-run", {
        method: "POST",
        body: JSON.stringify({ confirm: true, profile: "unit_tests" })
      });
      const run = response.run;
      output.textContent = run.output || "(No test output.)";
      status.textContent = "Sandbox run " + run.state + " in " + run.duration_ms + " ms. " +
        (run.isolation_preflight_passed
          ? "In-container isolation preflight passed. "
          : "WARNING: in-container isolation preflight did not pass. ") +
        run.memory_limit_note;
      if (run.output_truncated) status.textContent += " Output was capped at 64 KiB.";
      await refreshSandboxHistory();
    } catch (error) {
      status.textContent = "Sandbox run was not completed: " + (error.message || String(error));
    } finally {
      sandboxRunInProgress = false;
      cancelButton.disabled = true;
      await refreshSandboxReadiness(false);
    }
  }

  async function cancelSandboxTests() {
    const button = $("project-test-cancel");
    const status = $("project-sandbox-status");
    if (!confirm("Stop the active sandbox test process and remove its disposable container?")) return;
    button.disabled = true;
    try {
      const response = await api("/api/project/test-cancel", {
        method: "POST",
        body: JSON.stringify({ confirm: true })
      });
      status.textContent = "Cancellation requested for sandbox run " + response.cancel.run_id +
        ". ASTRA is stopping the runner and cleaning up its container.";
    } catch (error) {
      status.textContent = "Could not cancel sandbox run: " + (error.message || String(error));
      button.disabled = !sandboxRunInProgress;
    }
  }

  async function refreshStatus() {
    try {
      const status = await api("/api/status");
      const model = status.model_status;
      const ready = model.connected && model.model_installed;
      $("project-state").textContent = status.project_context.enabled ? "READ-ONLY PROJECT CONTEXT: ON" : "PROJECT CONTEXT: OFF";
      $("project-inspector").hidden = !status.project_context.enabled;
      $("memory-project-option").disabled = !status.project_context.memory_key;
      $("memory-project-status").textContent = status.project_context.memory_key
        ? "Current project partition: " + status.project_context.memory_key
          + ". Chat uses global notes plus this project only."
        : "No project configured. Chat uses global notes only.";
      statusChip.textContent = ready ? "MODEL ONLINE" : model.connected ? "MODEL NOT INSTALLED" : "PROVIDER UNAVAILABLE";
      statusChip.title = model.error || status.provider;
      statusChip.classList.toggle("good", ready);
      const descriptions = {
        CORE: "Reasoning engine", SENSE: "Instrument telemetry", COMMAND: "Authorized operations",
        WATCH: "Security / anomaly", ARCHIVE: "User-approved memory",
        CONTINUITY: "Protected recovery", PRESENCE: "Interface / voice",
        "CODE EXECUTION": status.coding_execution.reason
      };
      systems.replaceChildren();
      const entries = Object.entries(status.modules);
      entries.push(["CODE EXECUTION", status.coding_execution.status]);
      for (const [name, state] of entries) {
        const row = document.createElement("div"); row.className = "system";
        const left = document.createElement("div");
        const title = document.createElement("b"); title.textContent = name;
        const info = document.createElement("small"); info.textContent = descriptions[name] || "";
        left.append(title, info);
        const stateNode = document.createElement("span");
        stateNode.className = "state " +
          (name === "CODE EXECUTION" || state === "model unavailable"
            ? "warning" : state === "operational" ? "good" : "");
        stateNode.textContent = state;
        row.append(left, stateNode);
        systems.append(row);
      }
    } catch (error) { statusChip.textContent = "CORE UNAVAILABLE"; report(error); }
  }

  function speak(text) {
    if (!speaking || !("speechSynthesis" in window)) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "en-US";
    utterance.rate = 0.98;
    utterance.pitch = 1.04;
    window.speechSynthesis.speak(utterance);
  }

  messageForm.addEventListener("submit", async event => {
    event.preventDefault();
    const value = messageInput.value.trim();
    if (!value || busy) return;
    busy = true; sendButton.disabled = true;
    messageInput.value = "";
    display("user", value);
    notice.textContent = "Awaiting the configured local model…";
    try {
      const data = await api("/api/chat", { method: "POST", body: JSON.stringify({ message: value, session: "default" }) });
      display("astra", data.reply);
      speak(data.reply);
      notice.textContent = "Response from your local model. Facts should be verified before physical experiments.";
    } catch (error) { display("astra", "Unable to produce a verified model response: " + error.message, "ASTRA / DIAGNOSTIC"); report(error); }
    finally { busy = false; sendButton.disabled = false; refreshStatus(); }
  });

  messageInput.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); messageForm.requestSubmit(); }
  });

  $("clear").addEventListener("click", async () => {
    if (!confirm("Clear this in-memory conversation? Saved memories will remain.")) return;
    try {
      await api("/api/history/clear", { method: "POST", body: JSON.stringify({ session: "default" }) });
      transcript.replaceChildren();
      display("astra", "Conversation context cleared. Saved archive notes are unchanged.", "ASTRA / SESSION");
    } catch (error) { report(error); }
  });

  let memoryBeforeId = null;
  let memoryNextId = null;
  let editingMemory = null;
  let memoryLoading = false;
  let memorySaving = false;
  let memoryScope = "global";

  $("memory-scope").addEventListener("change", async () => {
    if (($("memory-input").value || $("memory-source").value)
      && !confirm("Discard the current draft and switch memory scope?")) {
      $("memory-scope").value = memoryScope;
      return;
    }
    cancelMemoryEdit();
    const scope = $("memory-scope").value;
    const refreshed = await refreshMemories(null, scope);
    if (!refreshed) $("memory-scope").value = memoryScope;
  });

  function cancelMemoryEdit() {
    editingMemory = null;
    $("memory-input").value = "";
    $("memory-source").value = "";
    $("memory-kind").value = "note";
    $("memory-save").textContent = "Save";
    $("memory-cancel").hidden = true;
    $("memory-edit-status").textContent = "";
  }

  $("memory-cancel").addEventListener("click", cancelMemoryEdit);
  $("memory-export").addEventListener("click", async () => {
    if (!confirm("Download all saved memories, including fiction and source references, as unencrypted JSON? Keep the file private.")) return;
    const button = $("memory-export");
    const status = $("memory-export-status");
    button.disabled = true;
    status.textContent = "Preparing complete memory export...";
    try {
      const data = await api("/api/memories/export", { method: "POST", body: "{}" });
      const url = URL.createObjectURL(new Blob([JSON.stringify(data)], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = "astra-memories.json";
      document.body.append(link);
      try { link.click(); }
      finally {
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      status.textContent = "Download requested for " + data.memory_count
        + " memories. Confirm the file was saved by your browser; it is not encrypted.";
    } catch (error) {
      status.textContent = "Memory export failed: " + error.message;
      report(error);
    } finally { button.disabled = false; }
  });
  $("memory-newest").addEventListener("click", () => refreshMemories(null));
  $("memory-older").addEventListener("click", () => {
    if (memoryNextId !== null) refreshMemories(memoryNextId);
  });

  async function refreshMemories(beforeId = memoryBeforeId, scope = memoryScope) {
    if (memoryLoading) return;
    memoryLoading = true;
    $("memory-newest").disabled = true;
    $("memory-older").disabled = true;
    $("memory-scope").disabled = true;
    try {
      const { memories, next_before_id: nextId } = await api(
        "/api/memories?scope=" + scope + (beforeId === null ? "" : "&before_id=" + beforeId)
      );
      memoryBeforeId = beforeId;
      memoryNextId = nextId;
      memoryScope = scope;
      $("memory-count").textContent = memories.length + (beforeId === null ? " NEWEST ENTRIES" : " OLDER ENTRIES");
      const list = $("memory-list");
      list.replaceChildren();
      for (const memory of memories) {
        const li = document.createElement("li");
        const span = document.createElement("span");
        const text = document.createElement("span"); text.textContent = memory.note;
        const metadata = document.createElement("small");
        metadata.className = "memory-metadata";
        metadata.textContent = memory.kind + " | " + memory.provenance
          + " | " + (memory.project_key ? "current project" : "global")
          + " | " + (memory.consented_at ? "Saved with approval: " + memory.consented_at : "Legacy note; consent time unknown")
          + (memory.source ? " | Source (user-supplied): " + memory.source : "")
          + " | Revision " + memory.revision
          + (memory.updated_at ? " | Edited: " + memory.updated_at : "")
          + (memory.kind === "fiction" ? " | Excluded from chat memory" : "");
        span.append(text, metadata);
        const actions = document.createElement("div"); actions.className = "memory-actions";
        const edit = document.createElement("button"); edit.type = "button"; edit.textContent = "Edit";
        edit.disabled = memorySaving;
        edit.setAttribute("aria-label", "Edit memory " + memory.id);
        edit.addEventListener("click", () => {
          if (($("memory-input").value || $("memory-source").value)
            && !confirm("Discard the current unsaved memory draft?")) return;
          editingMemory = memory;
          $("memory-input").value = memory.note;
          $("memory-source").value = memory.source;
          $("memory-kind").value = memory.kind;
          $("memory-save").textContent = "Save changes";
          $("memory-cancel").hidden = false;
          $("memory-edit-status").textContent = "Editing memory " + memory.id
            + ", revision " + memory.revision + ". Saving approves retention of the revised entry.";
          $("memory-input").focus();
        });
        const button = document.createElement("button"); button.type = "button"; button.textContent = "Forget";
        button.disabled = memorySaving;
        button.setAttribute("aria-label", "Forget memory " + memory.id);
        button.addEventListener("click", async () => {
          try {
            await api("/api/memories/" + memory.id + "?scope=" + memoryScope, { method: "DELETE" });
            if (editingMemory && editingMemory.id === memory.id) cancelMemoryEdit();
            await refreshMemories();
          }
          catch (error) { report(error); }
        });
        actions.append(edit, button);
        li.append(span, actions); list.append(li);
      }
      return true;
    } catch (error) {
      report(error);
      return false;
    }
    finally {
      memoryLoading = false;
      $("memory-newest").disabled = memorySaving;
      $("memory-older").disabled = memorySaving || memoryNextId === null;
      $("memory-scope").disabled = memorySaving;
    }
  }

  $("memory-form").addEventListener("submit", async event => {
    event.preventDefault();
    const note = $("memory-input").value.trim();
    if (!note) return;
    if (memorySaving) return;
    memorySaving = true;
    $("memory-scope").disabled = true;
    for (const control of document.querySelectorAll("#memory-form input, #memory-form select, #memory-form button, #memory-list button, .memory-navigation button")) {
      control.disabled = true;
    }
    const edited = editingMemory;
    try {
      await api(edited ? "/api/memories/" + edited.id + "/edit" : "/api/memories", {
        method: "POST", body: JSON.stringify({
          note, kind: $("memory-kind").value, source: $("memory-source").value.trim(),
          scope: memoryScope,
          ...(edited ? { revision: edited.revision } : {})
        })
      });
      cancelMemoryEdit();
      const refreshed = await refreshMemories(edited ? memoryBeforeId : null);
      notice.textContent = (edited ? "Memory updated locally at your explicit request."
        : "Memory saved locally at your explicit request.")
        + (refreshed ? "" : " Could not refresh the memory list; use Newest memories to reload.");
    } catch (error) {
      report(error);
      $("memory-edit-status").textContent = "Save failed: " + error.message
        + ". Your draft is retained. For a conflict, copy it before reloading the entry.";
    }
    finally {
      memorySaving = false;
      for (const control of document.querySelectorAll("#memory-form input, #memory-form select, #memory-form button, #memory-list button")) {
        control.disabled = false;
      }
      $("memory-newest").disabled = memoryLoading;
      $("memory-older").disabled = memoryLoading || memoryNextId === null;
      $("memory-scope").disabled = memoryLoading;
    }
  });

  $("project-search-form").addEventListener("submit", async event => {
    event.preventDefault();
    const query = $("project-search-input").value.trim();
    if (!query) return;
    const status = $("project-search-status");
    const results = $("project-search-results");
    status.textContent = "Searching the configured project within read-only indexing limits…";
    results.replaceChildren();
    try {
      const data = await api("/api/project/search?q=" + encodeURIComponent(query));
      for (const symbol of data.symbols) {
        const section = document.createElement("section");
        const button = document.createElement("button");
        button.className = "ghost";
        button.type = "button";
        button.textContent = symbol.path + ":" + symbol.line + " · " +
          symbol.kind + " " + symbol.qualified_name;
        button.addEventListener("click", () => {
          openProjectLocation(symbol.path, symbol.line).catch(error => {
            status.textContent = "Could not open symbol location: " + (error.message || String(error));
          });
        });
        section.append(button);
        results.append(section);
      }
      for (const callSite of data.call_sites) {
        const section = document.createElement("section");
        const button = document.createElement("button");
        button.className = "ghost";
        button.type = "button";
        button.textContent = callSite.path + ":" + callSite.line + ":" +
          callSite.column + " · call to " + callSite.name +
          (callSite.scope ? " in " + callSite.scope : "");
        button.addEventListener("click", () => {
          openProjectLocation(callSite.path, callSite.line).catch(error => {
            status.textContent = "Could not open call-site location: " + (error.message || String(error));
          });
        });
        section.append(button);
        results.append(section);
      }
      for (const relation of data.inheritance) {
        const section = document.createElement("section");
        const button = document.createElement("button");
        button.className = "ghost";
        button.type = "button";
        button.textContent = relation.path + ":" + relation.line + " · class " +
          relation.qualified_name + " inherits " + relation.base;
        button.addEventListener("click", () => {
          openProjectLocation(relation.path, relation.line).catch(error => {
            status.textContent = "Could not open inheritance location: " + (error.message || String(error));
          });
        });
        section.append(button);
        results.append(section);
      }
      for (const imported of data.imports) {
        const section = document.createElement("section");
        const button = document.createElement("button");
        button.className = "ghost";
        button.type = "button";
        button.textContent = imported.path + ":" + imported.line + " · imports " +
          imported.imported_name + " as " + imported.local_name +
          (imported.scope ? " in " + imported.scope : "");
        button.addEventListener("click", () => {
          openProjectLocation(imported.path, imported.line).catch(error => {
            status.textContent = "Could not open import location: " + (error.message || String(error));
          });
        });
        section.append(button);
        if (imported.resolved_path) {
          const target = document.createElement("button");
          target.className = "ghost";
          target.type = "button";
          target.textContent = "Open local " +
            (imported.resolved_kind === "module"
              ? "module"
              : imported.resolved_kind + " definition") +
            " " + imported.resolved_path + ":" + imported.resolved_line;
          target.addEventListener("click", () => {
            openProjectLocation(imported.resolved_path, imported.resolved_line).catch(error => {
              status.textContent = "Could not open local import target: " + (error.message || String(error));
            });
          });
          section.append(target);
        }
        for (const reference of imported.references) {
          const usage = document.createElement("button");
          usage.className = "ghost";
          usage.type = "button";
          usage.textContent = "Open lexical reference " + reference.path +
            ":" + reference.line + ":" + reference.column;
          usage.addEventListener("click", () => {
            openProjectLocation(reference.path, reference.line).catch(error => {
              status.textContent = "Could not open lexical reference: " + (error.message || String(error));
            });
          });
          section.append(usage);
        }
        results.append(section);
      }
      for (const match of data.matches) {
        const section = document.createElement("section");
        const heading = document.createElement("b");
        heading.textContent = match.path + " · relevance " + match.score;
        const excerpt = document.createElement("pre");
        excerpt.textContent = match.excerpt;
        section.append(heading, excerpt);
        results.append(section);
      }
      status.textContent = data.matches.length || data.symbols.length ||
        data.call_sites.length || data.inheritance.length || data.imports.length
        ? "Read-only search returned " + data.symbols.length + " Python symbol(s), " +
          data.call_sites.length + " Python call site(s), " +
          data.inheritance.length + " inheritance relationship(s), " +
          data.imports.length + " import(s), and " +
          data.matches.length + " source excerpt(s)."
        : "No matching source excerpts found.";
      if (data.warnings.length) status.textContent += " Indexing note: " + data.warnings.join("; ");
    } catch (error) {
      status.textContent = "Project search failed: " + (error.message || String(error));
    }
  });

  $("project-dependencies").addEventListener("click", async () => {
    const status = $("project-dependencies-status");
    const results = $("project-dependencies-results");
    status.textContent = "Inspecting bounded Python import declarations…";
    results.replaceChildren();
    try {
      const data = await api("/api/project/dependencies");
      for (const imported of data.imports) {
        const item = document.createElement("li");
        const source = document.createElement("button");
        source.className = "ghost";
        source.type = "button";
        source.textContent = imported.path + ":" + imported.line +
          (imported.dynamic ? " dynamically loads " : " imports ") +
          imported.imported_name +
          (imported.dynamic ? "" : " as " + imported.local_name) +
          (imported.type_checking ? " (TYPE_CHECKING)" : "") +
          (imported.optional ? " (ImportError fallback)" : "") +
          (imported.wildcard ? " (wildcard)" : "") +
          " [" + imported.status + "]" +
          (imported.issue ? " — " + imported.issue : "");
        source.addEventListener("click", () => {
          openProjectLocation(imported.path, imported.line).catch(error => {
            status.textContent = "Could not open import declaration: " + (error.message || String(error));
          });
        });
        item.append(source);
        if (imported.resolved_path) {
          const target = document.createElement("button");
          target.className = "ghost";
          target.type = "button";
          target.textContent = "Open " + imported.resolved_kind + " " +
            imported.resolved_path + ":" + imported.resolved_line;
          target.addEventListener("click", () => {
            openProjectLocation(imported.resolved_path, imported.resolved_line).catch(error => {
              status.textContent = "Could not open local import target: " + (error.message || String(error));
            });
          });
          item.append(target);
        }
        results.append(item);
      }
      for (const issue of data.issues) {
        const item = document.createElement("li");
        const button = document.createElement("button");
        button.className = "ghost";
        button.type = "button";
        button.textContent = issue.path + ":" + issue.line + " · " +
          issue.message + ": " + issue.imported_name;
        button.addEventListener("click", () => {
          openProjectLocation(issue.path, issue.line).catch(error => {
            status.textContent = "Could not open unresolved import: " + (error.message || String(error));
          });
        });
        item.append(button);
        results.append(item);
      }
      for (const cycle of data.cycles) {
        const group = document.createElement("li");
        const heading = document.createElement("span");
        heading.textContent = "Circular local imports: " + cycle.paths.join(" ↔ ");
        group.append(heading);
        for (const imported of cycle.imports) {
          const button = document.createElement("button");
          button.className = "ghost";
          button.type = "button";
          button.textContent = imported.path + ":" + imported.line +
            " imports " + imported.target_path;
          button.addEventListener("click", () => {
            openProjectLocation(imported.path, imported.line).catch(error => {
              status.textContent = "Could not open circular import: " +
                (error.message || String(error));
            });
          });
          group.append(button);
        }
        results.append(group);
      }
      const summary = data.summary;
      const missingCount = data.issues.filter(issue =>
        issue.issue === "missing-local-target"
      ).length;
      const optionalMissingCount = data.issues.filter(issue =>
        issue.issue === "optional-missing-target"
      ).length;
      const unusedCount = data.issues.filter(issue =>
        issue.issue === "possibly-unused-import"
      ).length;
      status.textContent = "Scanned " + summary.files_scanned + " of " +
        summary.eligible_files + " Python file(s); " + summary.local +
        " local and " + summary.unresolved + " unresolved import(s); " +
        missingCount + " likely missing local target(s), " +
        optionalMissingCount + " optional fallback target(s), " +
        unusedCount + " possibly unused import(s), " +
        data.cycles.length + " circular local import group(s); " +
        summary.dynamic_imports + " literal dynamic load(s); " +
        summary.type_checking_imports + " type-checking-only import(s).";
      if (summary.truncated) status.textContent += " Results were capped.";
      if (data.warnings.length) status.textContent += " " + data.warnings.join("; ");
      if (!summary.imports_returned) status.textContent += " No imports found.";
    } catch (error) {
      status.textContent = "Could not inspect project imports: " + (error.message || String(error));
    }
  });

  $("project-file-form").addEventListener("submit", async event => {
    event.preventDefault();
    const path = $("project-file-path").value.trim();
    if (!path) return;
    const status = $("project-change-status");
    try {
      const file = await api("/api/project/file?path=" + encodeURIComponent(path));
      $("project-file-path").value = file.path;
      $("project-file-content").value = file.content;
      $("project-file-content").disabled = false;
      $("project-propose").disabled = false;
      $("project-validate").disabled = false;
      stagedChange = null;
      $("project-change-diff").textContent = "";
      $("project-apply").hidden = true;
      $("project-rollback").hidden = !rollbackChange;
      status.textContent = "Loaded " + file.path + " (" + file.bytes + " bytes). Editing is staged only; the file has not changed.";
    } catch (error) {
      status.textContent = "Project file load failed: " + (error.message || String(error));
    }
  });

  $("project-history-refresh").addEventListener("click", refreshProjectHistory);
  $("project-sandbox-history-refresh").addEventListener("click", refreshSandboxHistory);
  $("project-test-run").addEventListener("click", runSandboxTests);
  $("project-test-cancel").addEventListener("click", cancelSandboxTests);
  $("project-inspector").addEventListener("toggle", () => {
    if (!$("project-inspector").open) return;
    refreshProjectHistory();
    refreshSandboxReadiness();
    refreshSandboxHistory();
  });

  $("project-propose").addEventListener("click", async () => {
    const status = $("project-change-status");
    $("project-propose").disabled = true;
    try {
      const response = await api("/api/project/proposals", {
        method: "POST",
        body: JSON.stringify({
          path: $("project-file-path").value.trim(),
          content: $("project-file-content").value
        })
      });
      stagedChange = response.proposal;
      $("project-change-diff").textContent = stagedChange.diff;
      $("project-apply").hidden = false;
      $("project-rollback").hidden = !rollbackChange;
      status.textContent = (response.validation
        ? response.validation.language + " syntax passed. "
        : "") + "Review the complete diff for " + stagedChange.path + ". No file has been written.";
      refreshProjectHistory();
    } catch (error) {
      stagedChange = null;
      $("project-change-diff").textContent = "";
      $("project-apply").hidden = true;
      status.textContent = "Change preview failed: " + (error.message || String(error));
    } finally {
      $("project-propose").disabled = false;
    }
  });

  $("project-draft").addEventListener("click", async () => {
    const path = $("project-file-path").value.trim();
    const instruction = $("project-draft-instruction").value.trim();
    const status = $("project-change-status");
    if (!path || !instruction) {
      status.textContent = "Select an existing file and describe the requested change before drafting.";
      return;
    }
    if (!confirm("Send the on-disk file " + path
      + " verbatim plus bounded repository excerpts to local inference?\n\nReview for secrets first. Unsaved editor text will be replaced on success. No file writes or tests occur until separately authorized.")) return;
    const controls = [...document.querySelectorAll("#project-inspector button, #project-inspector input, #project-inspector textarea")];
    const states = controls.map(control => control.disabled);
    controls.forEach(control => { control.disabled = true; });
    stagedChange = null;
    $("project-apply").hidden = true;
    $("project-change-diff").textContent = "";
    status.textContent = "Generating a local model draft for " + path + ". No file writes or commands...";
    try {
      const response = await api("/api/project/draft", {
        method: "POST", body: JSON.stringify({ path, instruction, confirm: true })
      });
      $("project-file-path").value = response.proposal.path;
      $("project-file-content").value = response.content;
      stagedChange = response.proposal;
      $("project-change-diff").textContent = stagedChange.diff;
      $("project-apply").hidden = false;
      status.textContent = "Draft from " + response.provider + " / " + response.model
        + ". " + (response.validation ? response.validation.language + " syntax passed. " : "No syntax validator for this file type. ")
        + "Review the entire diff. No files written; no tests executed.";
      refreshProjectHistory();
    } catch (error) {
      status.textContent = "Model draft failed: " + error.message + ". No draft was applied.";
    } finally {
      controls.forEach((control, index) => { control.disabled = states[index]; });
      if (stagedChange) {
        $("project-file-content").disabled = false;
        $("project-propose").disabled = false;
        $("project-validate").disabled = false;
      }
    }
  });

  $("project-validate").addEventListener("click", async () => {
    const status = $("project-validation-status");
    $("project-validate").disabled = true;
    status.textContent = "Checking syntax without executing project code…";
    try {
      const response = await api("/api/project/validate", {
        method: "POST",
        body: JSON.stringify({
          path: $("project-file-path").value.trim(),
          content: $("project-file-content").value
        })
      });
      const result = response.validation;
      if (result.valid) {
        status.textContent = result.language + " syntax valid: " + result.path;
      } else {
        const location = result.error.line ? " at " + result.error.line + ":" + result.error.column : "";
        status.textContent = result.language + " syntax error" + location + ": " + result.error.message;
      }
    } catch (error) {
      status.textContent = "Static validation failed: " + (error.message || String(error));
    } finally {
      $("project-validate").disabled = false;
    }
  });

  $("project-validate-all").addEventListener("click", async () => {
    const button = $("project-validate-all");
    const summary = $("project-validation-summary");
    const results = $("project-validation-results");
    button.disabled = true;
    summary.textContent = "Checking bounded project source without executing project code…";
    results.replaceChildren();
    try {
      const response = await api("/api/project/validate-all", {
        method: "POST",
        body: JSON.stringify({})
      });
      const report = response.validation;
      const counts = report.summary;
      summary.textContent = "Static checks: " + counts.valid + " valid, " +
        counts.invalid + " invalid, " + counts.skipped + " skipped.";
      for (const result of report.results) {
        const item = document.createElement("li");
        const detail = (result.language ? result.language + " " : "") +
          (result.error.message.startsWith("Skipped:") ? result.error.message :
            "syntax error" +
            (result.error.line ? " at " + result.error.line + ":" + result.error.column : "") +
            ": " + result.error.message);
        if (result.error.message.startsWith("Skipped:")) {
          item.textContent = result.path + " — " + detail;
        } else {
          const link = document.createElement("button");
          link.type = "button";
          link.className = "ghost";
          link.textContent = result.path + " — " + detail;
          link.setAttribute("aria-label", "Open " + result.path +
            (result.error.line ? " at line " + result.error.line : ""));
          link.addEventListener("click", async () => {
            try {
              await openProjectLocation(result.path, result.error.line);
            } catch (error) {
              summary.textContent = "Could not open diagnostic source: " + (error.message || String(error));
            }
          });
          item.append(link);
        }
        results.append(item);
      }
      if (report.truncated) summary.textContent += " Only the first 100 diagnostics are listed.";
      if (counts.checked === 0 && counts.skipped === 0) summary.textContent += " No supported files found.";
      if (report.warnings.length) {
        summary.textContent += " Scan notes: " + report.warnings.join("; ");
      }
    } catch (error) {
      summary.textContent = "Project syntax validation failed: " + (error.message || String(error));
    } finally {
      button.disabled = false;
    }
  });

  $("project-file-content").addEventListener("input", () => {
    if (stagedChange && stagedChange.state === "pending") {
      stagedChange = null;
      $("project-change-diff").textContent = "";
      $("project-apply").hidden = true;
      $("project-change-status").textContent = "The editor changed after preview. Generate and review a new diff before applying.";
    }
  });

  $("project-apply").addEventListener("click", async () => {
    if (!stagedChange) return;
    const proposal = stagedChange;
    if (!confirm("Apply the reviewed change to " + proposal.path + "?\n\nASTRA will not run commands.")) return;
    $("project-apply").disabled = true;
    try {
      const response = await api("/api/project/proposals/" + encodeURIComponent(proposal.id) + "/apply", {
        method: "POST",
        body: JSON.stringify({ confirm: true })
      });
      stagedChange = response.proposal;
      rollbackChange = response.proposal;
      $("project-apply").hidden = true;
      $("project-rollback").hidden = false;
      $("project-change-status").textContent = "Applied " + proposal.path + ". Rollback remains available until ASTRA restarts or this proposal expires.";
      refreshProjectHistory();
      try {
        const file = await api("/api/project/file?path=" + encodeURIComponent(proposal.path));
        $("project-file-content").value = file.content;
      } catch (error) {
        $("project-change-status").textContent += " The file was applied, but the editor refresh failed: " + (error.message || String(error));
      }
    } catch (error) {
      $("project-change-status").textContent = "Apply failed: " + (error.message || String(error));
    } finally {
      $("project-apply").disabled = false;
    }
  });

  $("project-rollback").addEventListener("click", async () => {
    if (!rollbackChange) return;
    const proposal = rollbackChange;
    if (!confirm("Restore the exact pre-change contents of " + proposal.path + "? This will fail if the file changed since Apply.")) return;
    $("project-rollback").disabled = true;
    try {
      const response = await api("/api/project/proposals/" + encodeURIComponent(proposal.id) + "/rollback", {
        method: "POST",
        body: JSON.stringify({ confirm: true })
      });
      rollbackChange = null;
      $("project-rollback").hidden = true;
      $("project-change-status").textContent = "Rolled back " + proposal.path + " to its exact reviewed original bytes.";
      refreshProjectHistory();
      try {
        const file = await api("/api/project/file?path=" + encodeURIComponent(proposal.path));
        $("project-file-content").value = file.content;
      } catch (error) {
        $("project-change-status").textContent += " Rollback succeeded, but the editor refresh failed: " + (error.message || String(error));
      }
    } catch (error) {
      $("project-change-status").textContent = "Rollback failed: " + (error.message || String(error));
    } finally {
      $("project-rollback").disabled = false;
    }
  });

  let reviewSet = [];
  let reviewSetBusy = false;

  function appendCheckpointInspection(container, proposalId, path) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost checkpoint-inspect";
    button.textContent = "Inspect current file hashes";
    button.setAttribute("aria-label", "Inspect current file hashes for " + path);
    button.disabled = reviewSetBusy;
    const status = document.createElement("p");
    status.className = "notice";
    status.setAttribute("role", "status");
    button.addEventListener("click", async () => {
      if (reviewSetBusy || button.disabled) return;
      button.disabled = true;
      status.textContent = "Reading current file bytes without writes or execution...";
      try {
        const response = await api("/api/project/checkpoints/" + encodeURIComponent(proposalId) + "/inspect");
        const inspection = response.inspection;
        const labels = {
          matches_original: "Current bytes match the recorded original",
          matches_proposed: "Current bytes match the recorded proposal",
          diverged: "Current bytes differ from both recorded versions"
        };
        if (!Object.hasOwn(labels, inspection.comparison)) throw new Error("Unknown checkpoint comparison");
        status.textContent = labels[inspection.comparison] + " at " + inspection.checked_at
          + ". SHA-256: " + inspection.current_sha256
          + ". Point-in-time comparison against unauthenticated metadata only; not proof of operation success or code correctness."
          + " No tests run, file writes, restored rollback bytes or mutation approval.";
      } catch (error) {
        status.textContent = "File hash inspection failed: " + error.message
          + ". No current comparison is available; no operation was retried.";
      } finally { button.disabled = reviewSetBusy; }
    });
    container.append(button, status);
  }

  function renderReviewSet() {
    const results = $("project-set-results");
    results.replaceChildren();
    const counts = {};
    for (const entry of reviewSet) {
      const state = entry.uncertain ? "unverified" : entry.proposal.state;
      counts[state] = (counts[state] || 0) + 1;
      const section = document.createElement("section");
      section.className = "change-set-file";
      const heading = document.createElement("h4");
      heading.textContent = entry.proposal.path + " / " + state;
      const diagnostic = document.createElement("p");
      diagnostic.className = "notice";
      diagnostic.textContent = entry.recovered
        ? "Recovered process-local review. Syntax has not been rechecked; no tests executed. " + (entry.error || "")
        : (entry.validation
        ? entry.validation.language + " syntax passed at preview. "
        : "No static syntax validator for this file type. ")
        + (entry.error || "No tests executed.");
      const diff = document.createElement("pre");
      diff.className = "change-diff";
      diff.textContent = entry.proposal.diff;
      const actions = document.createElement("div");
      actions.className = "memory-actions";
      if (!entry.uncertain && ["pending", "applied"].includes(entry.proposal.state)) {
        const action = entry.proposal.state === "pending" ? "apply" : "rollback";
        const button = document.createElement("button");
        button.type = "button";
        button.className = "ghost";
        button.textContent = action === "apply" ? "Apply this file" : "Rollback this file";
        button.setAttribute("aria-label", (action === "apply" ? "Apply " : "Rollback ") + entry.proposal.path);
        button.disabled = reviewSetBusy;
        button.addEventListener("click", () => mutateReviewSet(entry, action));
        actions.append(button);
      }
      section.append(heading, diagnostic, diff, actions);
      appendCheckpointInspection(section, entry.proposal.id, entry.proposal.path);
      results.append(section);
    }
    if (reviewSet.length) {
      $("project-set-status").textContent = "Review set: " + Object.entries(counts)
        .map(([state, count]) => count + " " + state).join(", ")
        + ". Each file is independent; partial application is possible. Refresh verifies proposal state, not runtime correctness or current file hashes.";
    } else {
      $("project-set-status").textContent = "No retained review set loaded. Historical metadata cannot restore expired or restarted proposals. No tests or commands run.";
    }
  }

  function lockReviewSet(locked) {
    reviewSetBusy = locked;
    $("project-set-input").disabled = locked;
    $("project-set-preview").disabled = locked;
    $("project-set-refresh").disabled = locked;
    $("project-set-paths").disabled = locked;
    $("project-set-instruction").disabled = locked;
    $("project-set-draft").disabled = locked;
    $("project-set-recover").disabled = locked;
    for (const button of $("project-checkpoints").querySelectorAll("button")) button.disabled = locked;
    renderReviewSet();
  }

  async function mutateReviewSet(entry, action) {
    if (reviewSetBusy) return;
    if (!confirm((action === "apply" ? "Apply the reviewed change to " : "Restore the reviewed original of ")
      + entry.proposal.path + " only?\n\nOther files will not be changed. No commands run.")) return;
    lockReviewSet(true);
    try {
      const response = await api("/api/project/proposals/" + encodeURIComponent(entry.proposal.id) + "/" + action, {
        method: "POST", body: JSON.stringify({ confirm: true })
      });
      entry.proposal = response.proposal;
      entry.error = "";
      entry.uncertain = false;
      refreshProjectHistory();
    } catch (error) {
      entry.error = action + " failed or its response was lost: " + error.message
        + ". Refresh states before retrying; inspect the file if the outcome is uncertain.";
      entry.uncertain = true;
    } finally { lockReviewSet(false); }
  }

  $("project-set-preview").addEventListener("click", async () => {
    if (reviewSetBusy) return;
    if (reviewSet.length && !confirm("Replace the visible review set? Existing proposals remain until expiry; applied changes are NOT undone. Keep any rollback IDs you need.")) return;
    lockReviewSet(true);
    let failure = "";
    try {
      let files;
      try { files = JSON.parse($("project-set-input").value); }
      catch { throw new Error("Replacement files must be a valid JSON array"); }
      const response = await api("/api/project/change-set", {
        method: "POST", body: JSON.stringify({ files })
      });
      reviewSet = response.changes.map(entry => ({ ...entry, uncertain: false, error: "" }));
      renderReviewSet();
      refreshProjectHistory();
    } catch (error) {
      failure = "Review-set preview failed: " + error.message
        + ". No file writes were requested. The previous visible set is retained.";
    } finally {
      lockReviewSet(false);
      if (failure) $("project-set-status").textContent = failure;
    }
  });

  $("project-set-draft").addEventListener("click", async () => {
    if (reviewSetBusy) return;
    const paths = $("project-set-paths").value.split(/\r?\n/).map(path => path.trim()).filter(Boolean);
    const instruction = $("project-set-instruction").value.trim();
    if (!paths.length || paths.length > 5 || !instruction) {
      $("project-set-status").textContent = "Select 1-5 existing files and describe a coordinated change.";
      return;
    }
    if (!confirm("Send these on-disk files verbatim plus bounded repository evidence to local inference?\n\n"
      + paths.join("\n") + "\n\nReview for secrets first. Unsaved editor text is not used. Generation only stages diffs; no writes or tests run.")) return;
    const budget = Number($("project-task-budget").value);
    if (!Number.isInteger(budget) || budget < 10 || budget > 120) {
      $("project-task-status").textContent = "Choose an integer budget from 10 to 120 seconds.";
      return;
    }
    const controls = [...document.querySelectorAll("#project-inspector button, #project-inspector input, #project-inspector textarea")];
    const states = controls.map(control => control.disabled);
    lockReviewSet(true);
    controls.forEach(control => { control.disabled = true; });
    $("project-task-status").textContent = "Starting local drafting task. No writes or commands...";
    let outcome = "";
    try {
      const response = await api("/api/project/draft-tasks", {
        method: "POST", body: JSON.stringify({ paths, instruction, confirm: true, budget_seconds: budget })
      });
      $("project-task-id").value = response.task.id;
      showDraftTask(response.task);
      outcome = " Task accepted. Use Refresh draft task, then Load completed draft review. Previous review retained.";
    } catch (error) {
      outcome = "Could not start drafting task: " + error.message
        + ". A lost response may leave a running task; do not assume no task started. No draft was applied.";
    } finally {
      controls.forEach((control, index) => { control.disabled = states[index]; });
      lockReviewSet(false);
      $("project-task-status").textContent += " " + outcome;
    }
  });

  function showDraftTask(task) {
    $("project-task-status").textContent = "Task " + task.id + " / " + task.state
      + " / worker " + (task.worker_active ? "active (may be draining)" : "exited")
      + " / acceptance budget " + task.budget_seconds + "s. "
      + (task.error || "No task error recorded.")
      + (task.checkpoint_error ? " CHECKPOINT ERROR: " + task.checkpoint_error
        : task.checkpoint_enabled ? " Metadata checkpoint storage enabled." : " No durable task checkpoint storage.")
      + " Transport interruption is unsupported. No project writes or tests executed."
      + (task.result ? " Provider: " + task.result.provider + " / " + task.result.model + "." : "")
      + (task.state === "completed" ? " Load the review explicitly; proposals still expire after 15 minutes or restart." : "");
  }

  $("project-task-list").addEventListener("click", async () => {
    if (reviewSetBusy) return;
    const button = $("project-task-list");
    button.disabled = true;
    try {
      const response = await api("/api/project/draft-tasks");
      const list = $("project-task-list-results");
      list.replaceChildren();
      for (const task of response.tasks) {
        const item = document.createElement("li");
        const select = document.createElement("button");
        select.type = "button";
        select.className = "ghost";
        select.textContent = task.id + " / " + task.state + " / " + task.paths.join(", ");
        select.addEventListener("click", () => {
          $("project-task-id").value = task.id;
          $("project-task-status").textContent = "Task selected. Refresh its current status before taking action.";
        });
        item.append(select);
        list.append(item);
      }
      $("project-task-status").textContent = response.tasks.length
        + " current-process task(s). Latest 20 retained; no automatic retries or resumption. Select a task and refresh status.";
    } catch (error) {
      $("project-task-status").textContent = "Could not list draft tasks: " + error.message;
    } finally { button.disabled = false; }
  });

  $("project-task-history").addEventListener("click", async () => {
    if (reviewSetBusy) return;
    const button = $("project-task-history");
    const status = $("project-task-history-status");
    button.disabled = true;
    status.textContent = "Reading recorded task metadata...";
    try {
      const report = await api("/api/project/draft-task-history");
      const list = $("project-task-history-results");
      list.replaceChildren();
      for (const record of report.checkpoints) {
        const item = document.createElement("li");
        const continuity = record.continuity === "current_process"
          ? "current-process task; use Find/Refresh for live status"
          : record.continuity === "historical_unverified"
            ? "interrupted or unobserved worker; final outcome unknown"
            : "historical recorded outcome; no live task restored";
        item.textContent = record.task_id + " / " + record.paths.join(", ")
          + " / recorded " + record.state + " / " + continuity
          + " / created " + record.created_at + " / checkpoint " + record.updated_at
          + " / budget " + record.budget_seconds + "s"
          + (record.outcome_code ? " / " + record.outcome_code : "")
          + (record.proposal_ids.length ? " / recorded proposal IDs: " + record.proposal_ids.join(", ") : "");
        list.append(item);
      }
      status.textContent = (report.durable ? "Persistent" : "No persistent")
        + " task checkpoint storage. " + report.total + " record(s). "
        + (report.truncated ? "Showing newest 100 tasks. " : "")
        + "Metadata only; unauthenticated and possibly stale. Recorded completion means staging, not Apply or test success."
        + " Unknown outcomes require file inspection. Nothing is resumed or authorized.";
    } catch (error) {
      $("project-task-history-results").replaceChildren();
      status.textContent = "Task checkpoint history failed: " + error.message
        + ". No verified history is available; no task was resumed.";
    } finally { button.disabled = false; }
  });

  for (const action of ["refresh", "cancel", "load"]) {
    $("project-task-" + action).addEventListener("click", async () => {
      if (reviewSetBusy) return;
      const id = $("project-task-id").value.trim();
      if (!id) { $("project-task-status").textContent = "Enter a draft task ID first."; return; }
      if (action === "cancel" && !confirm("Cancel acceptance of this draft's output? In-flight inference cannot be interrupted. Already staged proposals will not be undone.")) return;
      if (action === "load" && reviewSet.length && !confirm("Replace visible reviews with this task's completed proposals? Applied changes are not undone.")) return;
      const button = $("project-task-" + action);
      button.disabled = true;
      try {
        const base = "/api/project/draft-tasks/" + encodeURIComponent(id);
        const response = await api(base + (action === "cancel" ? "/cancel" : ""),
          action === "cancel" ? { method: "POST", body: JSON.stringify({ confirm: true }) } : undefined);
        showDraftTask(response.task);
        if (action === "load") {
          if (response.task.state !== "completed" || response.task.worker_active || !response.task.result) {
            throw new Error("Task has no completed review result available");
          }
          const entries = [];
          for (const entry of response.task.result.changes) {
            const current = await api("/api/project/proposals/" + encodeURIComponent(entry.proposal.id));
            entries.push({ ...entry, proposal: current.proposal, uncertain: false, error: "" });
          }
          reviewSet = entries;
          renderReviewSet();
          refreshProjectHistory();
        }
      } catch (error) {
        $("project-task-status").textContent = "Draft task " + action + " failed or response was lost: "
          + error.message + ". Refresh status before retrying. Previous visible reviews retained.";
      } finally { button.disabled = false; }
    });
  }

  $("project-set-refresh").addEventListener("click", async () => {
    if (reviewSetBusy || !reviewSet.length) return;
    lockReviewSet(true);
    try {
      for (const entry of reviewSet) {
        try {
          const response = await api("/api/project/proposals/" + encodeURIComponent(entry.proposal.id));
          entry.proposal = response.proposal;
          entry.uncertain = false;
          entry.error = "";
        } catch (error) {
          entry.uncertain = true;
          entry.error = "State unavailable (possibly expired or restarted): " + error.message
            + ". Inspect the actual file; no operation was retried.";
        }
      }
    } finally { lockReviewSet(false); }
  });

  $("project-set-recover").addEventListener("click", async () => {
    if (reviewSetBusy) return;
    if (reviewSet.length && !confirm("Replace the visible review with all retained proposals? No files will be changed. Original set grouping is not retained.")) return;
    lockReviewSet(true);
    try {
      const response = await api("/api/project/checkpoints");
      reviewSet = response.changes.map(entry => ({ ...entry, uncertain: false, error: "" }));
      const records = $("project-checkpoints");
      records.replaceChildren();
      for (const record of response.checkpoints) {
        const item = document.createElement("li");
        item.textContent = record.timestamp + " / " + record.path + " / recorded "
          + record.event + " (" + record.state + ") / "
          + (record.recoverable ? "review retained in current process" : "metadata only; re-inspect and create a fresh proposal")
          + " / " + record.proposal_id;
        appendCheckpointInspection(item, record.proposal_id, record.path);
        records.append(item);
      }
      $("project-checkpoint-status").textContent = (response.durable ? "Persistent" : "Process-only")
        + " review checkpoints: " + response.total + " recorded; " + response.changes.length
        + " retained reviews recovered. " + (response.truncated ? "Showing latest 100 records. " : "")
        + "Recorded state is not disk verification. Historical metadata never restores write authorization or rollback bytes.";
    } catch (error) {
      $("project-checkpoint-status").textContent = "Could not recover review checkpoints: " + error.message
        + ". No file operation was attempted; previous visible reviews retained.";
    } finally { lockReviewSet(false); }
  });

  $("speech").addEventListener("click", () => {
    if (!("speechSynthesis" in window)) { notice.textContent = "Speech output is unsupported by this browser."; return; }
    speaking = !speaking;
    $("speech").textContent = speaking ? "◈ Voice: On" : "◈ Voice: Off";
    $("speech").setAttribute("aria-pressed", String(speaking));
    if (!speaking) window.speechSynthesis.cancel();
  });

  $("mic").addEventListener("click", () => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) { notice.textContent = "Microphone dictation requires a browser supporting SpeechRecognition."; return; }
    const recog = new SpeechRecognition();
    recog.lang = "en-US"; recog.interimResults = false; recog.maxAlternatives = 1;
    recog.onresult = event => { messageInput.value = event.results[0][0].transcript; messageInput.focus(); };
    recog.onerror = event => { notice.textContent = "Dictation error: " + event.error; };
    recog.start();
    notice.textContent = "Browser microphone dictation started with your permission. Review text before sending.";
  });

  function startRain() {
    const canvas = $("rain");
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const ctx = canvas.getContext("2d");
    let drops = []; const font = 13;
    const resize = () => {
      canvas.width = innerWidth; canvas.height = innerHeight;
      drops = Array.from({ length: Math.ceil(canvas.width / font) }, () => Math.random() * -60);
    };
    window.addEventListener("resize", resize);
    resize();
    setInterval(() => {
      ctx.fillStyle = "rgba(5, 4, 9, .12)";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.font = font + "px monospace";
      ctx.fillStyle = "#f43f60";
      drops.forEach((y, x) => {
        ctx.fillText(Math.random() > .5 ? "1" : "0", x * font, y * font);
        if (y * font > canvas.height && Math.random() > .965) drops[x] = 0;
        else drops[x] += .45;
      });
    }, 60);
  }

  setInterval(() => { $("clock").textContent = new Date().toLocaleTimeString(); }, 1000);
  $("clock").textContent = new Date().toLocaleTimeString();
  refreshStatus();
  refreshMemories();
  startRain();
})();
