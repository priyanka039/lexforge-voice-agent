/* LexForge Moot Court — browser client */

const AGENTS = [
  { id: "judge", name: "The Bench", role: "Presiding judge" },
  { id: "precedent", name: "Precedent", role: "Finds Indian authority" },
  { id: "counter", name: "Opposing Counsel", role: "Rebuts your submission" },
  { id: "weakness", name: "Coach", role: "Spots weaknesses" },
  { id: "citation", name: "Citation Check", role: "Verifies citations" },
];

const ICONS = {
  advocate: "icon-counsel",
  judge: "icon-scales",
  system: "icon-note",
};

const SESSION_LABELS = {
  idle: "Court not yet convened",
  listening: "Bench is listening — make your submission",
  thinking: "The bench is considering your submission",
  bench_speaking: "The bench is addressing you",
  processing: "Processing your submission",
};

const state = {
  ws: null,
  recording: false,
  voiceEnabled: false,
  sessionState: "idle",
  inCtx: null,
  outCtx: null,
  micStream: null,
  workletNode: null,
  source: null,
  outSampleRate: 24000,
  inSampleRate: 16000,
  playHead: 0,
  liveSources: [],
  partialEl: null,
  sessionId: null,
  timerStart: null,
  timerInt: null,
};

const $ = (id) => document.getElementById(id);
const transcriptEl = $("transcript");

function iconHtml(name, cls = "") {
  return `<svg class="ico ${cls}"><use href="/icons.svg#${name}"></use></svg>`;
}

function setPill(id, text, cls) {
  const el = $(id);
  el.className = "status-chip" + (cls ? " " + cls : "");
  el.innerHTML = `<span class="dot"></span>${text}`;
}

function setSessionState(s) {
  state.sessionState = s;
  const el = $("sessionIndicator");
  const text = SESSION_LABELS[s] || s;
  $("sessionStateText").textContent = text;
  const cssClass = { listening: "listening", thinking: "thinking", bench_speaking: "speaking", processing: "thinking" }[s] || "";
  el.className = "session-indicator" + (cssClass ? " " + cssClass : "");
}

function showBanner(msg, isError = true) {
  const b = $("alert-banner");
  if (!msg) {
    b.classList.add("hidden");
    b.textContent = "";
    return;
  }
  b.textContent = msg;
  b.classList.toggle("hidden", false);
  b.style.background = isError ? "" : "rgba(26, 39, 68, 0.9)";
}

function switchTab(tab) {
  document.querySelectorAll(".nav-tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.tab === tab);
  });
  document.querySelectorAll(".pane").forEach((p) => {
    p.classList.toggle("active", p.dataset.pane === tab);
  });
}

function renderAgents() {
  const wrap = $("agents");
  wrap.innerHTML = "";
  for (const a of AGENTS) {
    const row = document.createElement("div");
    row.className = "agent-row";
    row.id = "agent-" + a.id;
    row.innerHTML = `<span class="dot"></span>
      <div><div class="name">${a.name}</div><div class="role">${a.role}</div></div>
      <span class="badge"></span>`;
    wrap.appendChild(row);
  }
}

function setAgent(id, st) {
  const row = $("agent-" + id);
  if (!row) return;
  row.classList.remove("working", "done");
  if (st) row.classList.add(st);
  const badge = row.querySelector(".badge");
  badge.textContent = st === "working" ? "active" : st === "done" ? "ready" : "";
  if (st === "done") setTimeout(() => { row.classList.remove("done"); badge.textContent = ""; }, 4000);
}

function addTurn(role, text, opts = {}) {
  $("transcript-empty")?.remove();
  const div = document.createElement("div");
  div.className = `turn ${role}` + (opts.partial ? " partial" : "");
  const label = role === "advocate" ? "Counsel for the Appellant" : role === "judge" ? "The Bench" : "Chamber";
  div.innerHTML = `<div class="avatar">${iconHtml(ICONS[role] || "icon-note")}</div>
    <div><div class="meta">${label}</div><div class="bubble"></div></div>`;
  div.querySelector(".bubble").textContent = text;
  transcriptEl.appendChild(div);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
  return div;
}

function addNote(text, kind) {
  $("notes").querySelector(".empty")?.remove();
  const n = document.createElement("div");
  n.className = "note" + (kind ? " " + kind : "");
  n.textContent = text;
  $("notes").prepend(n);
  switchTab("notes");
}

function renderPrecedents(list) {
  const wrap = $("precedents");
  if (!list || !list.length) return;
  wrap.querySelector(".empty")?.remove();
  wrap.innerHTML = "";
  for (const p of list.slice(0, 6)) {
    const el = document.createElement("div");
    el.className = "precedent";
    const cite = [p.citation, p.court, p.year].filter(Boolean).join(" · ");
    el.innerHTML = `<div class="t">${escapeHtml(p.title || "Authority")}</div>
      ${cite ? `<div class="c">${escapeHtml(cite)}</div>` : ""}
      ${p.summary ? `<div class="s">${escapeHtml(p.summary)}</div>` : ""}
      <div class="src">${escapeHtml(p.source || "")}${p.url ? ` · <a href="${p.url}" target="_blank" rel="noopener">view</a>` : ""}</div>`;
    wrap.appendChild(el);
  }
  switchTab("authorities");
}

function renderFeedback(fb) {
  if (!fb) return;
  const sec = $("feedback-section");
  sec.classList.remove("hidden");
  const wrap = $("feedback");
  let html = "";
  if (fb.overall_score != null) {
    html += `<div class="score-hero"><div class="big">${fb.overall_score}<span style="font-size:16px;color:var(--ink-muted)">/10</span></div><div class="sub">Overall performance</div></div>`;
  }
  for (const s of fb.scores || []) {
    const pct = Math.max(0, Math.min(100, (Number(s.score) || 0) * 10));
    html += `<div class="dim"><div class="dim-top"><span>${escapeHtml(s.dimension || "")}</span><span class="v">${s.score}/10</span></div>
      <div class="bar"><span style="width:${pct}%"></span></div>
      ${s.comment ? `<div class="cmt">${escapeHtml(s.comment)}</div>` : ""}</div>`;
  }
  if ((fb.strengths || []).length) {
    html += `<div class="fb-h">Strengths</div><ul class="fb-list good">` +
      fb.strengths.map((x) => `<li>${escapeHtml(x)}</li>`).join("") + `</ul>`;
  }
  if ((fb.improvements || []).length) {
    html += `<div class="fb-h">Areas to improve</div><ul class="fb-list bad">` +
      fb.improvements.map((x) => `<li>${escapeHtml(x)}</li>`).join("") + `</ul>`;
  }
  wrap.innerHTML = html;
  switchTab("settings");
}

function updateMatterHeader() {
  $("matter-title").textContent = $("b-title").value || "Untitled Matter";
  $("matter-court").textContent = "Before the " + ($("b-court").value || "Court");
}

function startTimer() {
  if (state.timerStart) return;
  state.timerStart = Date.now();
  state.timerInt = setInterval(() => {
    const s = Math.floor((Date.now() - state.timerStart) / 1000);
    const mm = String(Math.floor(s / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    $("pill-timer").innerHTML = `<span class="dot"></span>${mm}:${ss}`;
  }, 1000);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------- WebSocket ----------
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  state.ws = ws;
  ws.onopen = () => setPill("pill-conn", "Connected", "live");
  ws.onclose = () => {
    setPill("pill-conn", "Disconnected", "warn");
    stopRecording();
    setTimeout(connect, 1500);
  };
  ws.onerror = () => setPill("pill-conn", "Error", "warn");
  ws.onmessage = (e) => handleMessage(JSON.parse(e.data));
}

function send(obj) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) state.ws.send(JSON.stringify(obj));
}

function handleMessage(msg) {
  switch (msg.type) {
    case "ready":
      state.outSampleRate = msg.sample_rate_out || 24000;
      state.inSampleRate = msg.sample_rate_in || 16000;
      state.sessionId = msg.session_id;
      state.voiceEnabled = !!msg.voice_enabled;
      setPill("pill-voice", msg.voice_enabled ? "Voice active" : "Text mode", msg.voice_enabled ? "live" : "warn");
      setPill("pill-brain", msg.brain === "openai" ? "OpenAI" : "Stub", msg.brain === "openai" ? "live" : "warn");
      if (msg.bench_temperament) $("s-temperament").value = msg.bench_temperament;
      $("micBtn").disabled = false;
      if (msg.voice_enabled) {
        showBanner("", false);
        $("voice-hint").textContent = "Microphone ready. Speak clearly when the bench is listening.";
      }
      break;

    case "voice_status":
      state.voiceEnabled = false;
      setPill("pill-voice", "Text mode", "warn");
      showBanner(msg.message || "Voice unavailable. Use the text box below.");
      $("voice-hint").textContent = "Voice is off — type your submission in the box below.";
      break;

    case "session_state":
      if (msg.state) setSessionState(msg.state);
      break;

    case "feedback":
      renderFeedback(msg.feedback);
      break;

    case "transcript":
      handleTranscript(msg);
      break;

    case "intent":
      if (msg.intent) addNote(`Intent: ${msg.intent}${msg.needs_precedent ? " · fetching authority" : ""}`, "");
      break;

    case "agent":
      setAgent(msg.agent, msg.status === "start" ? "working" : "done");
      break;

    case "speaking":
      setSessionState("bench_speaking");
      addTurn(msg.agent === "judge" || msg.agent === "feedback" ? "judge" : "system", msg.text);
      break;

    case "audio":
      playPcm(msg.data);
      break;

    case "interrupt":
      flushPlayback();
      addNote("The bench yielded — you may continue.", "");
      break;

    case "precedents":
      renderPrecedents(msg.precedents);
      break;

    case "note":
      handleNote(msg);
      break;

    case "turn_done":
      setSessionState(state.recording ? "listening" : "idle");
      break;

    case "error":
      addNote("Error: " + msg.message, "warn");
      setSessionState("idle");
      break;
  }
}

function handleTranscript(msg) {
  if (msg.role !== "advocate") return;
  startTimer();
  if (!msg.final) {
    setSessionState("listening");
    if (!state.partialEl) state.partialEl = addTurn("advocate", msg.text, { partial: true });
    else state.partialEl.querySelector(".bubble").textContent = msg.text;
    transcriptEl.scrollTop = transcriptEl.scrollHeight;
  } else {
    setSessionState("processing");
    if (state.partialEl) {
      state.partialEl.classList.remove("partial");
      state.partialEl.querySelector(".bubble").textContent = msg.text;
      state.partialEl = null;
    } else {
      addTurn("advocate", msg.text);
    }
  }
}

function handleNote(msg) {
  if (msg.weaknesses && msg.weaknesses.length) {
    msg.weaknesses.forEach((w) => addNote("Weakness: " + w, "weakness"));
  } else if (msg.citations && msg.citations.length) {
    addNote("Citations: " + msg.citations.join(", "), "citation");
  } else if (msg.message) {
    if (msg.level === "warn") addNote(msg.message, "warn");
    else showBanner(msg.message, false);
  }
}

// ---------- Audio ----------
function ensureOutCtx() {
  if (!state.outCtx) state.outCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (state.outCtx.state === "suspended") state.outCtx.resume();
  return state.outCtx;
}

function playPcm(b64) {
  const ctx = ensureOutCtx();
  const bytes = base64ToBytes(b64);
  const view = new DataView(bytes.buffer);
  const n = bytes.length / 2;
  const buf = ctx.createBuffer(1, n, state.outSampleRate);
  const ch = buf.getChannelData(0);
  for (let i = 0; i < n; i++) ch[i] = view.getInt16(i * 2, true) / 32768;
  const src = ctx.createBufferSource();
  src.buffer = buf;
  src.connect(ctx.destination);
  const now = ctx.currentTime;
  const startAt = Math.max(now, state.playHead);
  src.start(startAt);
  state.playHead = startAt + buf.duration;
  state.liveSources.push(src);
  src.onended = () => { state.liveSources = state.liveSources.filter((s) => s !== src); };
}

function flushPlayback() {
  state.liveSources.forEach((s) => { try { s.stop(); } catch (e) {} });
  state.liveSources = [];
  if (state.outCtx) state.playHead = state.outCtx.currentTime;
}

async function startRecording() {
  if (!state.voiceEnabled) {
    showBanner("Voice is unavailable. Type your submission in the text box below, then press Submit.");
    $("textInput").focus();
    return;
  }
  try {
    state.micStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (e) {
    showBanner("Microphone access denied. Use the text box to submit your argument.");
    return;
  }
  ensureOutCtx();
  state.inCtx = new (window.AudioContext || window.webkitAudioContext)();
  await state.inCtx.audioWorklet.addModule("/pcm-worklet.js");
  state.source = state.inCtx.createMediaStreamSource(state.micStream);
  state.workletNode = new AudioWorkletNode(state.inCtx, "pcm-processor");
  state.workletNode.port.onmessage = (e) => {
    const { samples, rate } = e.data;
    const pcm16 = downsampleToPcm16(samples, rate, state.inSampleRate);
    if (pcm16) send({ type: "audio", data: bytesToBase64(new Uint8Array(pcm16.buffer)) });
  };
  state.source.connect(state.workletNode);
  const sink = state.inCtx.createGain();
  sink.gain.value = 0;
  state.workletNode.connect(sink).connect(state.inCtx.destination);

  state.recording = true;
  $("micBtn").classList.add("recording");
  $("micLabel").textContent = "End submission — bench is listening";
  setSessionState("listening");
  $("transcript-empty")?.remove();
}

function stopRecording() {
  if (!state.recording) return;
  state.recording = false;
  send({ type: "audio_end" });
  try { state.workletNode?.disconnect(); } catch (e) {}
  try { state.source?.disconnect(); } catch (e) {}
  try { state.micStream?.getTracks().forEach((t) => t.stop()); } catch (e) {}
  try { state.inCtx?.close(); } catch (e) {}
  state.inCtx = null;
  $("micBtn").classList.remove("recording");
  $("micLabel").textContent = "Rise and address the bench";
  if (state.sessionState === "listening") setSessionState("processing");
}

function downsampleToPcm16(input, inRate, outRate) {
  if (!input || !input.length) return null;
  let data = input;
  if (inRate !== outRate) {
    const ratio = inRate / outRate;
    const outLen = Math.floor(input.length / ratio);
    const out = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const pos = i * ratio;
      const i0 = Math.floor(pos);
      const i1 = Math.min(i0 + 1, input.length - 1);
      out[i] = input[i0] * (1 - (pos - i0)) + input[i1] * (pos - i0);
    }
    data = out;
  }
  const pcm = new Int16Array(data.length);
  for (let i = 0; i < data.length; i++) {
    const s = Math.max(-1, Math.min(1, data[i]));
    pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return pcm;
}

function base64ToBytes(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function bytesToBase64(bytes) {
  let bin = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(bin);
}

// ---------- Wiring ----------
document.querySelectorAll(".nav-tab").forEach((tab) => {
  tab.addEventListener("click", () => switchTab(tab.dataset.tab));
});

$("micBtn").addEventListener("click", () => (state.recording ? stopRecording() : startRecording()));
$("sendBtn").addEventListener("click", sendTyped);
$("textInput").addEventListener("keydown", (e) => { if (e.key === "Enter") sendTyped(); });

function sendTyped() {
  const v = $("textInput").value.trim();
  if (!v) return;
  addTurn("advocate", v);
  startTimer();
  setSessionState("processing");
  send({ type: "text", text: v });
  $("textInput").value = "";
  $("transcript-empty")?.remove();
}

$("briefBtn").addEventListener("click", () => {
  updateMatterHeader();
  send({
    type: "brief",
    brief: {
      title: $("b-title").value,
      court: $("b-court").value,
      user_side: $("b-side").value,
      appellant: $("b-appellant").value,
      respondent: $("b-respondent").value,
      facts: $("b-facts").value,
      issues: $("b-issues").value.split("\n").map((s) => s.trim()).filter(Boolean),
    },
  });
});

$("settingsBtn").addEventListener("click", () => {
  send({
    type: "settings",
    settings: {
      bench_temperament: $("s-temperament").value,
      judge_persona: $("s-persona").value.trim(),
    },
  });
});

$("counterBtn").addEventListener("click", () => {
  send({ type: "text", text: "Opposing counsel, please rebut my last submission." });
  setSessionState("processing");
});

$("helpBtn").addEventListener("click", () => {
  send({ type: "text", text: "Coach, what is the main weakness in my argument right now?" });
  setSessionState("processing");
});

$("feedbackBtn").addEventListener("click", () => {
  send({ type: "feedback" });
  setSessionState("thinking");
});

$("downloadBtn").addEventListener("click", () => {
  if (!state.sessionId) {
    addNote("Nothing to download yet.", "warn");
    return;
  }
  window.open(`/api/session/${state.sessionId}/transcript.md`, "_blank");
});

["b-title", "b-court"].forEach((id) => {
  $(id).addEventListener("input", updateMatterHeader);
});

renderAgents();
updateMatterHeader();
setSessionState("idle");
connect();
