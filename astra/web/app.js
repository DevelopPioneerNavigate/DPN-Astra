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

  async function refreshStatus() {
    try {
      const status = await api("/api/status");
      const model = status.model_status;
      const ready = model.connected && model.model_installed;
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
