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

  async function refreshStatus() {
    try {
      const status = await api("/api/status");
      const model = status.model_status;
      const ready = model.connected && model.model_installed;
      $("project-state").textContent = status.project_context.enabled ? "READ-ONLY PROJECT CONTEXT: ON" : "PROJECT CONTEXT: OFF";
      $("project-inspector").hidden = !status.project_context.enabled;
      statusChip.textContent = ready ? "MODEL ONLINE" : model.connected ? "MODEL NOT INSTALLED" : "OLLAMA OFFLINE";
      statusChip.classList.toggle("good", ready);
      const descriptions = {
        CORE: "Reasoning engine", SENSE: "Instrument telemetry", COMMAND: "Authorized operations",
        WATCH: "Security / anomaly", ARCHIVE: "User-approved memory",
        CONTINUITY: "Protected recovery", PRESENCE: "Interface / voice"
      };
      systems.replaceChildren();
      for (const [name, state] of Object.entries(status.modules)) {
        const row = document.createElement("div"); row.className = "system";
        const left = document.createElement("div");
        const title = document.createElement("b"); title.textContent = name;
        const info = document.createElement("small"); info.textContent = descriptions[name] || "";
        left.append(title, info);
        const stateNode = document.createElement("span");
        stateNode.className = "state " + (state === "operational" ? "good" : state === "model unavailable" ? "warning" : "");
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

  async function refreshMemories() {
    try {
      const { memories } = await api("/api/memories");
      $("memory-count").textContent = memories.length + " ENTRIES";
      const list = $("memory-list");
      list.replaceChildren();
      for (const memory of memories) {
        const li = document.createElement("li");
        const span = document.createElement("span"); span.textContent = memory.note;
        const button = document.createElement("button"); button.type = "button"; button.textContent = "Forget";
        button.setAttribute("aria-label", "Forget memory " + memory.id);
        button.addEventListener("click", async () => {
          try { await api("/api/memories/" + memory.id, { method: "DELETE" }); await refreshMemories(); }
          catch (error) { report(error); }
        });
        li.append(span, button); list.append(li);
      }
    } catch (error) { report(error); }
  }

  $("memory-form").addEventListener("submit", async event => {
    event.preventDefault();
    const note = $("memory-input").value.trim();
    if (!note) return;
    try {
      await api("/api/memories", { method: "POST", body: JSON.stringify({ note }) });
      $("memory-input").value = "";
      await refreshMemories();
      notice.textContent = "Memory saved locally at your explicit request.";
    } catch (error) { report(error); }
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
      const unusedCount = data.issues.filter(issue =>
        issue.issue === "possibly-unused-import"
      ).length;
      status.textContent = "Scanned " + summary.files_scanned + " of " +
        summary.eligible_files + " Python file(s); " + summary.local +
        " local and " + summary.unresolved + " unresolved import(s); " +
        missingCount + " likely missing local target(s), " +
        unusedCount + " possibly unused import(s), " +
        data.cycles.length + " circular local import group(s); " +
        summary.dynamic_imports + " literal dynamic load(s).";
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
  $("project-inspector").addEventListener("toggle", () => {
    if (!$("project-inspector").open) return;
    refreshProjectHistory();
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
