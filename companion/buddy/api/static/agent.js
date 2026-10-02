// 承認カードとツール結果チップ。表示はすべて textContent(XSS対策)。
const TOOL_LABEL = {
  research_web: "Perplexity 調査", create_deliverable: "Claude 成果物作成",
  list_workspace_files: "作業フォルダ一覧", read_workspace_file: "ファイル読み込み",
  propose_memory: "記憶の提案",
};
export const toolLabel = (name) => TOOL_LABEL[name] || name;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

export function approvalCard(ev, { json, showError }) {
  const card = el("div", "card approval");
  card.dataset.approvalId = ev.id;
  card.append(el("div", "card-head", `承認が必要です · ${ev.level_label}`));
  const dl = el("dl");
  const row = (k, v, cls) => { if (!v) return; dl.append(el("dt", "", k)); dl.append(el("dd", cls || "", v)); };
  row("作業", toolLabel(ev.tool));
  row("送信先", ev.external ? `${ev.external}(PCの外へ送信されます)` : "");
  row("AIの理由", ev.purpose || "(記載なし)");
  row("内容", ev.preview, "pre");
  row("確認する理由", ev.reason);
  card.append(dl);
  const actions = el("div", "card-actions");
  const ok = el("button", "primary", "承認して実行");
  const ng = el("button", "", "拒否");
  const decide = async (approve) => {
    ok.disabled = ng.disabled = true;
    try {
      await json(`/api/approvals/${ev.id}`, { method: "POST", body: JSON.stringify({ approve }) });
    } catch (e) { showError("承認の送信に失敗しました: " + e.message); ok.disabled = ng.disabled = false; }
  };
  ok.onclick = () => decide(true);
  ng.onclick = () => decide(false);
  actions.append(ok, ng);
  card.append(actions);
  return card;
}

export function markApproval(logEl, ev) {
  const card = logEl.querySelector(`[data-approval-id="${CSS.escape(ev.id)}"]`);
  if (!card) return;
  const label = { approved: "✓ 承認しました", denied: "✕ 拒否しました", timeout: "⏱ 時間切れ(実行されませんでした)" }[ev.outcome] || ev.outcome;
  card.classList.add("resolved", ev.outcome);
  const actions = card.querySelector(".card-actions");
  actions.textContent = "";
  actions.append(el("span", "outcome", label));
}

// rec: {tool, ok, summary, data}
export function toolChip(rec, { openOutput }) {
  const box = el("div", "chip" + (rec.ok ? "" : " ng"));
  box.append(el("span", "chip-head", `${rec.ok ? "✓" : "✕"} ${toolLabel(rec.tool)}`), el("span", "", rec.summary || ""));
  const data = rec.data || {};
  if (data.name) {
    const a = el("button", "link", `開く: ${data.file}`);
    a.onclick = () => openOutput(data.name);
    box.append(a);
  }
  if (Array.isArray(data.sources) && data.sources.length) {
    const det = el("details");
    det.append(el("summary", "", `出典 ${data.sources.length}件`));
    const ol = el("ol");
    data.sources.forEach((s) => {
      const li = el("li");
      const a = el("a", "", s.title || s.url);
      if (/^https?:\/\//.test(s.url)) { a.href = s.url; a.target = "_blank"; a.rel = "noopener noreferrer"; }
      li.append(a);
      if (s.date) li.append(el("span", "dim", ` (${s.date})`));
      ol.append(li);
    });
    det.append(ol);
    box.append(det);
  }
  return box;
}
