// AI相棒 UI 本体。会話・状態・処理ログ・音声出力と、可視化(viz.js)への橋渡し。
import { NetworkViz } from "./viz.js";
import { MemoryPanel } from "./memory.js";

const $ = (id) => document.getElementById(id);
const STATE_LABEL = { idle: "待機中", thinking: "考え中…", responding: "応答中…", error: "エラー" };
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* 保存できない環境では無視 */ } },
};
let token = store.get("buddyToken") || "";
let convs = [], current = null, busy = false;
let nodeMap = new Map();

// ---------- API ----------
async function api(path, opts = {}) {
  const headers = { "X-Buddy-Token": token, ...(opts.body ? { "Content-Type": "application/json" } : {}) };
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) {
    const t = prompt("アクセストークンを入力してください");
    if (t === null) throw new Error("認証が必要です");
    token = t.trim(); store.set("buddyToken", token);
    return api(path, opts);
  }
  return res;
}
async function json(path, opts) {
  const res = await api(path, opts);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  return res.status === 204 ? null : res.json();
}

// ---------- 可視化・処理ログ ----------
const viz = new NetworkViz($("viz"), { onSelect: showNodeInfo });
const memoryPanel = new MemoryPanel({ json, showError: (m) => showError(m), onProjectsChanged: setupProjects });

function setState(s) {
  $("dot").className = s === "idle" ? "" : s;
  $("state").textContent = STATE_LABEL[s] || s;
  viz.setState(s);
}
function showError(msg) { const b = $("banner"); b.textContent = msg; b.style.display = msg ? "block" : "none"; }

function onActivity(ev) {
  viz.activity(ev);
  const label = nodeMap.get(ev.target)?.label || ev.target;
  $("tickerText").textContent = `${label}: ${ev.summary}`;
  const li = document.createElement("li");
  const time = document.createElement("time");
  time.textContent = new Date((ev.ts || Date.now() / 1000) * 1000).toLocaleTimeString("ja-JP", { hour12: false });
  const who = document.createElement("span"); who.className = "who"; who.textContent = label;
  const what = document.createElement("span"); what.textContent = ev.summary;
  if (ev.ok === false) what.className = "ng";
  li.append(time, who, what);
  $("feed").prepend(li);
  while ($("feed").children.length > 40) $("feed").lastChild.remove();
}

function showNodeInfo(n) {
  const box = $("nodeinfo");
  if (!n) { box.hidden = true; return; }
  box.innerHTML = "";
  const add = (tag, text, cls) => { const e = document.createElement(tag); e.textContent = text; if (cls) e.className = cls; box.appendChild(e); };
  add("h3", n.label);
  add("p", n.description);
  if (n.available) add("p", "状態: 利用可能");
  else add("p", n.planned_phase ? `状態: 未実装(Phase ${n.planned_phase} 予定)` : "状態: 未設定(.env で設定すると使えます)", "off");
  if (n.external && n.available) add("p", "☁ データが外部サービスへ送信されます", "warn");
  add("p", n.lastSummary ? `最新: ${n.lastSummary}` : "まだアクセスはありません", n.lastSummary ? "" : "off");
  box.hidden = false;
}

$("ticker").onclick = () => { $("feed").hidden = !$("feed").hidden; };
function applyVizVisibility(on) {
  document.body.classList.toggle("noviz", !on);
  $("vizToggle").setAttribute("aria-pressed", String(on));
  store.set("buddyViz", on ? "1" : "0");
}
$("vizToggle").onclick = () => applyVizVisibility(document.body.classList.contains("noviz"));
applyVizVisibility(store.get("buddyViz") !== "0");
if (matchMedia("(min-width: 900px)").matches) $("feed").hidden = false;

// ---------- 状態 ----------
let nodeSig = "";
async function refreshStatus() {
  try {
    const s = await json("/api/status");
    if (!busy) setState(s.state);
    const meta = $("meta"); meta.innerHTML = "";
    const add = (t, cls) => { const e = document.createElement("span"); e.textContent = t; if (cls) e.className = cls; meta.appendChild(e); };
    add(s.llm_reachable ? `${s.provider} 接続OK` : `${s.provider} 未接続(${s.llm_detail})`, s.llm_reachable ? "" : "ext");
    if (Object.values(s.profiles || {}).some((p) => p.external)) add("⚠ 会話はクラウドAIへ送信", "ext");
    setupProfiles(s.profiles || {});
    setupTts(s.tts || { enabled: false });
    memoryPanel.refreshBadge().catch(() => {});
    const sig = JSON.stringify(s.nodes || []);
    if (sig !== nodeSig) {
      nodeSig = sig;
      nodeMap = new Map((s.nodes || []).map((n) => [n.id, n]));
      viz.setNodes(s.nodes || []);
    }
  } catch (e) { showError("状態を取得できません: " + e.message); }
}

function setupProfiles(profiles) {
  const sel = $("profile"), names = Object.keys(profiles);
  if (sel.dataset.sig === names.join()) return;
  sel.dataset.sig = names.join(); const prev = sel.value; sel.innerHTML = "";
  names.forEach((n) => {
    const o = document.createElement("option"); o.value = n;
    o.textContent = (n === "fast" ? "高速" : n === "strong" ? "高性能" : n) + ` (${profiles[n].model})`;
    sel.appendChild(o);
  });
  if (names.includes(prev)) sel.value = prev;
}

// ---------- 音声出力 ----------
let ttsOn = false, ttsAvailable = false;
const player = new Audio(); // 同じ要素を使い回す(スマホの自動再生制限対策)
const SILENT = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=";
function setupTts(t) {
  ttsAvailable = !!(t.enabled && t.ok !== false);
  const b = $("speak"); b.disabled = !t.enabled;
  b.title = t.enabled ? `声: ${t.voice || "?"} (${t.provider})${t.ok === false ? " — 利用不可: " + t.detail : ""}` : "";
  if (t.enabled && t.ok === false) { ttsOn = false; b.classList.remove("on"); b.textContent = "🔈 音声: 利用不可"; }
}
$("speak").onclick = () => {
  if (!ttsAvailable) { showError("音声出力を利用できません: " + $("speak").title); return; }
  ttsOn = !ttsOn; $("speak").classList.toggle("on", ttsOn);
  $("speak").textContent = ttsOn ? "🔊 音声: ON" : "🔈 音声: OFF";
  if (ttsOn) { player.src = SILENT; player.play().catch(() => {}); } // ユーザー操作内で再生を許可させる
  else player.pause();
};
async function speak(text) {
  const id = "tts-" + Date.now();
  onActivity({ id, target: "tts", phase: "start", summary: "音声を合成中", ts: Date.now() / 1000 });
  try {
    const res = await api("/api/tts", { method: "POST", body: JSON.stringify({ text: text.slice(0, 2000) }) });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
    const url = URL.createObjectURL(await res.blob());
    player.src = url; player.onended = () => URL.revokeObjectURL(url);
    await player.play();
    onActivity({ id, target: "tts", phase: "end", summary: "再生開始", ok: true, ts: Date.now() / 1000 });
  } catch (e) {
    onActivity({ id, target: "tts", phase: "end", summary: "失敗", ok: false, ts: Date.now() / 1000 });
    showError("音声出力に失敗しました: " + e.message);
  }
}

// ---------- 会話 ----------
function bubble(role, text, msg = null) {
  const d = document.createElement("div");
  d.className = "msg " + role; d.textContent = text; // textContent: XSS対策
  if (msg?.id) { // 保存済みの発言だけ「覚える」を出す
    const b = document.createElement("button");
    b.type = "button"; b.className = "remember"; b.textContent = "＋覚える"; b.title = "この発言を記憶として保存";
    const conv = convs.find((c) => c.id === current);
    b.onclick = () => memoryPanel.openWith({ content: msg.content, messageId: msg.id, projectId: conv?.project_id || null });
    d.appendChild(b);
  }
  $("log").appendChild(d); $("log").scrollTop = $("log").scrollHeight; return d;
}

// ---------- プロジェクト ----------
function setupProjects(projects) {
  const sel = $("project"), prev = sel.value;
  sel.innerHTML = "";
  const none = document.createElement("option"); none.value = ""; none.textContent = "なし"; sel.appendChild(none);
  projects.forEach((p) => { const o = document.createElement("option"); o.value = p.id; o.textContent = p.name; sel.appendChild(o); });
  const conv = convs.find((c) => c.id === current);
  sel.value = conv ? (conv.project_id || "") : prev;
  if (sel.value !== (conv ? (conv.project_id || "") : prev)) sel.value = "";
}
$("project").onchange = async () => {
  if (!current) return; // 新規会話の作成時に適用する
  try {
    const c = await json(`/api/conversations/${current}`, { method: "PATCH", body: JSON.stringify({ project_id: $("project").value || null }) });
    convs = convs.map((x) => (x.id === c.id ? c : x));
  } catch (e) { showError("プロジェクトの設定に失敗しました: " + e.message); }
};
function renderList() {
  const el = $("list"); el.innerHTML = "";
  convs.forEach((c) => {
    const row = document.createElement("div");
    row.className = "item" + (current === c.id ? " active" : "");
    const t = document.createElement("span"); t.textContent = c.title;
    const del = document.createElement("button"); del.className = "del"; del.textContent = "✕"; del.title = "削除";
    del.onclick = async (ev) => {
      ev.stopPropagation();
      if (!confirm(`「${c.title}」を削除しますか?(元に戻せません)`)) return;
      await json(`/api/conversations/${c.id}`, { method: "DELETE" });
      if (current === c.id) current = null;
      await loadConvs();
    };
    row.onclick = () => { openConv(c.id); $("list").classList.remove("open"); };
    row.append(t, del); el.appendChild(row);
  });
}
async function loadConvs() {
  convs = await json("/api/conversations");
  if (!current && convs.length) current = convs[0].id;
  renderList(); await openConv(current);
}
async function openConv(id) {
  current = id; renderList(); $("log").innerHTML = "";
  if (!id) {
    const e = document.createElement("div"); e.className = "empty";
    e.textContent = "メッセージを送って会話を始めましょう。"; $("log").appendChild(e); return;
  }
  const conv = convs.find((c) => c.id === id);
  $("project").value = conv?.project_id || "";
  (await json(`/api/conversations/${id}/messages`)).forEach((m) => bubble(m.role, m.content, m));
}
async function newConv() {
  let c = await json("/api/conversations", { method: "POST", body: JSON.stringify({}) });
  if ($("project").value) {
    c = await json(`/api/conversations/${c.id}`, { method: "PATCH", body: JSON.stringify({ project_id: $("project").value }) });
  }
  current = c.id; convs.unshift(c); renderList(); await openConv(c.id); $("input").focus();
}

async function send(text) {
  if (!current) await newConv();
  busy = true; $("send").disabled = true; showError("");
  bubble("user", text);
  const ai = bubble("assistant", "");
  try {
    const res = await api(`/api/conversations/${current}/messages`, {
      method: "POST", body: JSON.stringify({ content: text, profile: $("profile").value || "fast" }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
    const reader = res.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
        if (!line.startsWith("data:")) continue;
        const ev = JSON.parse(line.slice(5));
        if (ev.type === "status") setState(ev.state);
        else if (ev.type === "activity") onActivity(ev);
        else if (ev.type === "delta") { ai.textContent += ev.text; $("log").scrollTop = $("log").scrollHeight; }
        else if (ev.type === "done") { if (ttsOn) speak(ev.message.content); }
        else if (ev.type === "error") { setState("error"); showError(ev.message); if (!ai.textContent) ai.remove(); }
      }
    }
  } catch (e) {
    setState("error"); showError("送信に失敗しました: " + e.message); if (!ai.textContent) ai.remove();
  }
  busy = false; $("send").disabled = false;
  convs = await json("/api/conversations"); renderList(); refreshStatus();
  if ($("banner").style.display !== "block") await openConv(current); // 保存済みIDで描き直し(「覚える」用)
}

$("form").addEventListener("submit", (e) => {
  e.preventDefault(); const t = $("input").value.trim();
  if (!t || busy) return; $("input").value = ""; $("input").style.height = "auto"; send(t);
});
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("form").requestSubmit(); }
});
$("input").addEventListener("input", (e) => { e.target.style.height = "auto"; e.target.style.height = e.target.scrollHeight + "px"; });
$("new").onclick = newConv;
$("menu").onclick = () => $("list").classList.toggle("open");
document.addEventListener("click", (e) => {
  if ($("list").classList.contains("open") && !$("list").contains(e.target) && e.target !== $("menu")) $("list").classList.remove("open");
});

refreshStatus().then(() => memoryPanel.refreshProjects()).then(loadConvs).catch((e) => showError("起動に失敗しました: " + e.message));
setInterval(refreshStatus, 10000);
