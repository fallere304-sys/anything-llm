// 記憶パネル: 記憶一覧 / 承認待ち / 作業履歴 / プロジェクト。
// 表示はすべて textContent で行う(XSS対策)。
const KIND = { long_term: "長期記憶", project: "プロジェクト記憶" };
const SOURCE = { user: "ユーザー入力", conversation: "会話から", ai_proposal: "AIの提案" };
const ACTOR = { ai: "AI", user: "ユーザー" };

function el(tag, props = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) e.append(c);
  return e;
}
const day = (iso) => (iso || "").slice(0, 10);
const time = (iso) => new Date(iso).toLocaleString("ja-JP", { hour12: false, month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });

export class MemoryPanel {
  constructor({ json, showError, onProjectsChanged }) {
    this.json = json;
    this.showError = showError;
    this.onProjectsChanged = onProjectsChanged || (() => {});
    this.projects = [];
    this.tab = "list";
    this.filter = "all";
    this.prefill = null;
    this.root = document.getElementById("memPanel");
    this.body = document.getElementById("mpBody");
    document.getElementById("memBtn").onclick = () => (this.root.hidden ? this.open() : this.close());
    document.getElementById("memClose").onclick = () => this.close();
    this.root.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => this.show(b.dataset.tab)));
  }

  async open(tab) { this.root.hidden = false; await this.show(tab || this.tab); }
  close() { this.root.hidden = true; this.prefill = null; }

  // 会話の発言から「覚える」
  async openWith(prefill) { this.prefill = prefill; await this.open("list"); }

  projectName(id) { return this.projects.find((p) => p.id === id)?.name || "(不明なPJ)"; }

  async refreshProjects() {
    this.projects = await this.json("/api/projects");
    this.onProjectsChanged(this.projects);
    return this.projects;
  }

  async refreshBadge() {
    const pending = await this.json("/api/memories?status=pending");
    for (const id of ["pendingBadge", "pendingBadge2"]) {
      const b = document.getElementById(id);
      b.textContent = pending.length ? String(pending.length) : "";
      b.hidden = !pending.length;
    }
    return pending;
  }

  async show(tab) {
    this.tab = tab;
    this.root.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
    this.body.innerHTML = "";
    try {
      await this.refreshProjects();
      if (tab === "list") await this.renderList();
      else if (tab === "pending") await this.renderPending();
      else if (tab === "log") await this.renderLog();
      else await this.renderProjects();
      await this.refreshBadge();
    } catch (e) { this.showError("記憶の読み込みに失敗しました: " + e.message); }
  }

  // ---------- 記憶一覧 + 追加 ----------
  kindSelect(selected) {
    const sel = el("select", { "aria-label": "保存先" },
      el("option", { value: "long_term" }, "長期記憶"),
      this.projects.map((p) => el("option", { value: "pj:" + p.id }, `PJ: ${p.name}`)));
    if (selected) sel.value = selected;
    return sel;
  }

  async renderList() {
    const pre = this.prefill;
    const content = el("textarea", { rows: 3, maxlength: 2000, placeholder: "覚えておく内容" });
    content.value = pre?.content || "";
    const where = this.kindSelect(pre?.projectId ? "pj:" + pre.projectId : "long_term");
    const imp = el("input", { type: "checkbox" });
    const form = el("form", { class: "mp-form" },
      pre ? el("p", { class: "note" }, "会話の発言から保存します。必要なら内容を編集してください。") : null,
      content,
      el("div", { class: "row" }, where, el("label", {}, imp, " 重要事項"),
        el("button", { class: "primary", type: "submit" }, "保存")));
    form.onsubmit = async (e) => {
      e.preventDefault();
      const text = content.value.trim();
      if (!text) return;
      const isPj = where.value.startsWith("pj:");
      try {
        await this.json("/api/memories", { method: "POST", body: JSON.stringify({
          content: text, kind: isPj ? "project" : "long_term", project_id: isPj ? where.value.slice(3) : null,
          important: imp.checked, source_message_id: pre?.messageId ?? null,
        }) });
        this.prefill = null;
        await this.show("list");
      } catch (err) { this.showError("保存に失敗しました: " + err.message); }
    };

    const filter = el("select", { "aria-label": "絞り込み" },
      el("option", { value: "all" }, "すべて"),
      el("option", { value: "important" }, "重要事項のみ"),
      el("option", { value: "long_term" }, "長期記憶のみ"),
      this.projects.map((p) => el("option", { value: "pj:" + p.id }, `PJ: ${p.name}`)));
    filter.value = this.filter;
    filter.onchange = () => { this.filter = filter.value; this.show("list"); };

    let q = "?status=active";
    if (this.filter === "important") q += "&important=true";
    else if (this.filter === "long_term") q += "&kind=long_term";
    else if (this.filter.startsWith("pj:")) q += "&project_id=" + encodeURIComponent(this.filter.slice(3));
    const items = await this.json("/api/memories" + q);

    this.body.append(form, el("div", { class: "row" }, el("span", { class: "dim" }, `保存済み ${items.length}件`), filter));
    if (!items.length) this.body.append(el("p", { class: "dim" }, "まだ記憶はありません。"));
    items.forEach((m) => this.body.append(this.memItem(m)));
    if (pre) content.focus();
  }

  memMeta(m) {
    const where = m.kind === "project" ? `PJ: ${this.projectName(m.project_id)}` : KIND[m.kind];
    return `${where} · ${SOURCE[m.source] || m.source} · 信頼度 ${m.confidence.toFixed(1)} · 作成 ${day(m.created_at)} · 更新 ${day(m.updated_at)}`;
  }

  memItem(m) {
    const star = el("button", { class: "icon" + (m.important ? " on" : ""), title: "重要事項の切替" }, m.important ? "★" : "☆");
    star.onclick = () => this.patch(m.id, { important: !m.important });
    const edit = el("button", { class: "icon", title: "編集" }, "✎");
    edit.onclick = () => {
      const v = prompt("記憶を編集", m.content);
      if (v !== null && v.trim() && v.trim() !== m.content) this.patch(m.id, { content: v.trim() });
    };
    const del = el("button", { class: "icon", title: "削除" }, "🗑");
    del.onclick = async () => {
      if (!confirm("この記憶を削除しますか?(元に戻せません)")) return;
      try { await this.json(`/api/memories/${m.id}`, { method: "DELETE" }); await this.show(this.tab); }
      catch (e) { this.showError("削除に失敗しました: " + e.message); }
    };
    return el("div", { class: "mem" + (m.important ? " important" : "") },
      el("p", {}, m.content), el("div", { class: "meta" }, this.memMeta(m)),
      el("div", { class: "actions" }, star, edit, del));
  }

  async patch(id, body) {
    try { await this.json(`/api/memories/${id}`, { method: "PATCH", body: JSON.stringify(body) }); await this.show(this.tab); }
    catch (e) { this.showError("更新に失敗しました: " + e.message); }
  }

  // ---------- 承認待ち ----------
  async renderPending() {
    const items = await this.json("/api/memories?status=pending");
    this.body.append(el("p", { class: "note" }, "AIが保存を提案した情報です。承認するまで会話には使われません。"));
    if (!items.length) this.body.append(el("p", { class: "dim" }, "承認待ちはありません。"));
    items.forEach((m) => {
      const ok = el("button", { class: "primary" }, "承認");
      ok.onclick = () => this.decide(m.id, "approve");
      const ng = el("button", {}, "却下");
      ng.onclick = () => this.decide(m.id, "reject");
      this.body.append(el("div", { class: "mem pending" }, el("p", {}, m.content),
        el("div", { class: "meta" }, this.memMeta(m)), el("div", { class: "actions" }, ok, ng)));
    });
  }

  async decide(id, action) {
    try { await this.json(`/api/memories/${id}/${action}`, { method: "POST" }); await this.show("pending"); }
    catch (e) { this.showError("処理に失敗しました: " + e.message); }
  }

  // ---------- 作業履歴 ----------
  async renderLog() {
    const rows = await this.json("/api/worklog?limit=100");
    this.body.append(el("p", { class: "note" }, "AIとユーザーが行った操作の記録(追記のみ)。"));
    if (!rows.length) this.body.append(el("p", { class: "dim" }, "記録はまだありません。"));
    const list = el("ol", { class: "log" });
    rows.forEach((r) => list.append(el("li", { class: r.ok ? "" : "ng" },
      el("time", {}, time(r.ts)), el("span", { class: "who" }, ACTOR[r.actor] || r.actor),
      el("span", {}, r.summary), el("span", { class: "dim" }, r.target))));
    this.body.append(list);
  }

  // ---------- プロジェクト ----------
  async renderProjects() {
    const name = el("input", { maxlength: 60, placeholder: "プロジェクト名" });
    const desc = el("input", { maxlength: 500, placeholder: "説明(任意)" });
    const form = el("form", { class: "mp-form" }, el("div", { class: "row" }, name,
      el("button", { class: "primary", type: "submit" }, "作成")), desc);
    form.onsubmit = async (e) => {
      e.preventDefault();
      if (!name.value.trim()) return;
      try {
        await this.json("/api/projects", { method: "POST", body: JSON.stringify({ name: name.value.trim(), description: desc.value.trim() }) });
        await this.show("projects");
      } catch (err) { this.showError("作成に失敗しました: " + err.message); }
    };
    this.body.append(el("p", { class: "note" }, "会話をプロジェクトに紐付けると、そのプロジェクトの記憶が会話で使われます(上部の「PJ」で選択)。"), form);
    if (!this.projects.length) this.body.append(el("p", { class: "dim" }, "プロジェクトはまだありません。"));
    this.projects.forEach((p) => {
      const ren = el("button", { class: "icon", title: "名前の変更" }, "✎");
      ren.onclick = async () => {
        const v = prompt("プロジェクト名", p.name);
        if (v === null || !v.trim() || v.trim() === p.name) return;
        try { await this.json(`/api/projects/${p.id}`, { method: "PATCH", body: JSON.stringify({ name: v.trim() }) }); await this.show("projects"); }
        catch (e) { this.showError("変更に失敗しました: " + e.message); }
      };
      const del = el("button", { class: "icon", title: "削除" }, "🗑");
      del.onclick = async () => {
        if (!confirm(`プロジェクト「${p.name}」を削除しますか?`)) return;
        try { await this.json(`/api/projects/${p.id}`, { method: "DELETE" }); await this.show("projects"); }
        catch (e) { this.showError(e.message); }
      };
      this.body.append(el("div", { class: "mem" }, el("p", {}, p.name),
        el("div", { class: "meta" }, `${p.description || "説明なし"} · 記憶 ${p.memory_count}件`),
        el("div", { class: "actions" }, ren, del)));
    });
  }
}
