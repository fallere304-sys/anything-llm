// タチコマの画面: サーバーからの出来事 (SSE) に合わせて、画像の表情・吹き出し・状態表示を更新する。
(() => {
  const $ = (id) => document.getElementById(id);
  const avatar = $("avatar"), img = $("avatar-img"), badge = $("badge"), bubble = $("bubble");
  const BADGES = { studying_ear: "🎧", studying_eye: "📄", reading: "📚", training: "⚙️", listening: "👂" };
  const MODES = { conversing: "会話中", attending: "見守り中", alone: "独りの時間" };
  const ACTIVITIES = { ear_study: "字幕付き動画で聞き取り練習", eye_study: "PDF で読み取り練習",
    ear_train: "耳を学習中", eye_train: "目を学習中", brain_train: "頭を学習中", reading: "論文・資料を読んでる", rest: "お休み (省電力)" };
  let assets = [], textExprUntil = 0, stateExpr = "idle", speaking = false;

  fetch("/assets.json").then((r) => r.json()).then((a) => { assets = a; show(stateExpr); }).catch(() => {});

  // 表情ごとの画像: tachikoma_<表情>.(png|webp|gif|jpg) → tachikoma.(png|…) → 同梱の仮アバター
  function imageFor(expr) {
    for (const ext of ["png", "webp", "gif", "jpg", "svg"]) if (assets.includes(`tachikoma_${expr}.${ext}`)) return `/assets/tachikoma_${expr}.${ext}`;
    for (const ext of ["png", "webp", "gif", "jpg", "svg"]) if (assets.includes(`tachikoma.${ext}`)) return `/assets/tachikoma.${ext}`;
    return "/static/avatar.svg";
  }
  function show(expr) {
    avatar.className = "avatar-wrap expr-" + expr + (speaking ? " talking" : "");
    const src = imageFor(expr);
    if (!img.src.endsWith(src)) img.src = src;
    badge.textContent = BADGES[expr] || "";
  }
  function add(kind, text) {
    const li = document.createElement("li");
    li.className = kind; li.textContent = text;
    $("log").appendChild(li); li.scrollIntoView({ block: "end" });
  }

  const es = new EventSource("/events");
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === "say") {
      add("tachikoma", ev.text);
      bubble.textContent = ev.text; bubble.hidden = false;
      textExprUntil = Date.now() + Math.min(12000, 2500 + ev.text.length * 120);
      show(ev.expression || "talk");
      setTimeout(() => { if (Date.now() >= textExprUntil) { bubble.hidden = true; show(stateExpr); } }, textExprUntil - Date.now() + 50);
    } else if (ev.type === "user") {
      add("user", ev.text);
    } else if (ev.type === "log") {
      add("system", ev.text);
    } else if (ev.type === "state") {
      stateExpr = ev.expression || stateExpr; speaking = !!ev.speaking;
      $("st-mode").textContent = MODES[ev.mode] || ev.mode || "-";
      $("st-ear").textContent = ev.ear_cer == null ? "-" : ev.ear_cer.toFixed(3);
      $("st-eye").textContent = ev.eye_cer == null ? "-" : ev.eye_cer.toFixed(3);
      $("st-w").textContent = ev.watts == null ? "-" : `${Math.round(ev.watts)} W`;
      $("st-model").textContent = ev.model || "-";
      const act = ev.activity;
      $("activity").textContent = act ? `${ACTIVITIES[act.activity] || act.activity} — ${act.why}` : (MODES[ev.mode] || "");
      if (Date.now() >= textExprUntil) show(stateExpr); else show(avatar.className.match(/expr-(\S+)/)[1]);
    }
  };
  es.onerror = () => { $("activity").textContent = "タチコマ本体に接続できません (再接続中…)"; };

  $("form").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("text").value.trim();
    if (!text) return;
    $("text").value = "";
    fetch("/say", { method: "POST", headers: { "Content-Type": "application/json", "X-Tachikoma": "1" },
                    body: JSON.stringify({ text }) });
  });
})();
