// タチコマの画面: サーバーからの出来事 (SSE) に合わせて、タチコマの動き・吹き出し・状態・スイッチを更新する。
(() => {
  const $ = (id) => document.getElementById(id);
  const avatar = $("avatar"), img = $("avatar-img"), sheen = $("sheen"), bubble = $("bubble");
  const mover = $("mover"), floor = $("floor");
  const MODES = { conversing: "会話中", attending: "見守り中", alone: "独りの時間" };
  const ACTIVITIES = { ear_study: "字幕付き動画で聞き取り練習", eye_study: "PDF で読み取り練習",
    ear_train: "耳を学習中", eye_train: "目を学習中", brain_train: "頭を学習中", reading: "論文・資料を読んでる", rest: "お休み (省電力)" };
  const WALKERS = new Set(["reading", "studying_ear", "studying_eye", "idle"]);   // 歩き回ってよい状態
  const CALM = new Set(["idle", "listening", "reading", "studying_ear", "studying_eye"]);
  let assets = [], version = Date.now(), textExprUntil = 0, stateExpr = "idle", speaking = false, current = "idle";
  let mode = "attending", x = 0, dir = 1;

  // ------------------------------------------------------------ 画像
  // 表情ごとの画像: tachikoma_<表情>.(png|webp|gif|jpg) → tachikoma.(png|…) → 同梱の仮アバター
  function imageFor(expr) {
    for (const ext of ["png", "webp", "gif", "jpg", "svg"]) if (assets.includes(`tachikoma_${expr}.${ext}`)) return `/assets/tachikoma_${expr}.${ext}?v=${version}`;
    for (const ext of ["png", "webp", "gif", "jpg", "svg"]) if (assets.includes(`tachikoma.${ext}`)) return `/assets/tachikoma.${ext}?v=${version}`;
    return "/static/avatar.svg";
  }
  function loadAssets() {
    return fetch("/assets.json").then((r) => r.json()).then((a) => { assets = a; show(current, true); }).catch(() => {});
  }
  function show(expr, force) {
    current = expr;
    avatar.className = "avatar-wrap expr-" + expr + (speaking ? " talking" : "");
    const src = imageFor(expr);
    if (force || !img.src.endsWith(src)) {
      img.src = src;
      sheen.style.setProperty("--mask", `url("${src}")`);   // 光は画像の形 (透明でないところ) にだけ乗せる
    }
  }

  // ------------------------------------------------------------ 生きている感じ (歩く・振り向く・まばたき・気まぐれ)
  function place(nx, ndir, fast) {
    mover.classList.toggle("turning", !!fast);
    x = nx; dir = ndir;
    mover.style.setProperty("--x", `${x}px`);
    mover.style.setProperty("--dir", dir);
    avatar.style.setProperty("--dir", dir);
  }
  function range() { return Math.max(0, (floor.clientWidth - mover.clientWidth) / 2 - 8); }
  function stroll() {
    const can = WALKERS.has(current) && !speaking && !document.body.classList.contains("offline")
      && (mode === "alone" || current === "idle") && Date.now() >= textExprUntil;
    if (can && Math.random() < (mode === "alone" ? 0.7 : 0.35)) {
      const r = range(), nx = Math.round((Math.random() * 2 - 1) * r);
      if (Math.abs(nx - x) > 16) {
        // 元の画像は左を向いている想定: 右へ歩くときは振り向く
        place(x, nx > x ? -1 : 1, true);
        setTimeout(() => { mover.classList.add("walking"); place(nx, dir, false); }, 380);
        setTimeout(() => mover.classList.remove("walking"), 380 + 2400);
      }
    } else if (can && current === "idle" && Math.random() < 0.5) {
      fidget();
    }
    setTimeout(stroll, 3500 + Math.random() * 4000);
  }
  function fidget() {
    const kind = ["fidget-hop", "fidget-wiggle", "look"][Math.floor(Math.random() * 3)];
    if (kind === "look") {           // きょろきょろ: 反対を向いて、また戻る
      const back = dir;
      place(x, -dir, true);
      setTimeout(() => place(x, back, true), 1300);
      return;
    }
    avatar.classList.add(kind);
    setTimeout(() => avatar.classList.remove(kind), 800);
  }
  function blink() {
    if (!speaking) { avatar.classList.add("blink"); setTimeout(() => avatar.classList.remove("blink"), 140); }
    setTimeout(blink, 3500 + Math.random() * 4000);
  }

  // ------------------------------------------------------------ 会話
  function add(kind, text) {
    const li = document.createElement("li");
    li.className = kind; li.textContent = text;
    $("log").appendChild(li); li.scrollIntoView({ block: "end" });
  }
  function note(text) {
    $("note").textContent = text || ""; $("note").hidden = !text;
  }

  // ------------------------------------------------------------ スイッチ (カメラ・マイク)
  const switches = document.querySelectorAll(".switch");
  function renderDevices(devices) {
    for (const el of switches) {
      const d = devices[el.dataset.dev];
      if (!d) continue;
      el.removeAttribute("aria-busy");
      el.setAttribute("aria-checked", d.on ? "true" : "false");
      el.disabled = !d.available;
      el.title = d.why || (d.on ? `${d.label}を切る (機器を手放します)` : `${d.label}を入れる`);
      el.querySelector(".sw-state").textContent = !d.available ? "使えない" : d.on ? "使用中" : "オフ";
    }
    const why = Object.values(devices).filter((d) => d.why).map((d) => `${d.label}: ${d.why}`);
    note(why.join(" / "));
  }
  for (const el of switches) {
    el.addEventListener("click", () => {
      if (el.disabled || el.getAttribute("aria-busy") === "true") return;
      const on = el.getAttribute("aria-checked") !== "true";
      el.setAttribute("aria-busy", "true");
      post("/switch", JSON.stringify({ name: el.dataset.dev, on }), "application/json")
        .catch(() => { el.removeAttribute("aria-busy"); note("タチコマ本体に届きませんでした"); });
      setTimeout(() => el.removeAttribute("aria-busy"), 8000);
    });
  }
  function post(path, body, type) {
    return fetch(path, { method: "POST", headers: { "Content-Type": type, "X-Tachikoma": "1" }, body })
      .then((r) => { if (!r.ok) throw new Error(r.status); return r; });
  }

  // ------------------------------------------------------------ 画像を変える (背景をこの PC の中で抜く)
  $("pick").addEventListener("click", () => $("file").click());
  $("reset").addEventListener("click", () => post("/avatar/reset", "", "text/plain").then(() => note("同梱の仮の絵に戻しました")));
  $("file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    note("画像を整えています…");
    try {
      const { blob, cut } = await cutout(file);
      await post("/avatar", blob, "image/png");
      note(cut ? "背景を抜いて、タチコマの画像にしました" : "背景が一色ではなかったので、そのまま使います (透明な PNG を選ぶときれいに動きます)");
    } catch (err) {
      note(`画像を使えませんでした (${err.message || err})`);
    }
  });

  // 縁から背景色に近い色を塗りつぶして透明にし、輪郭をぼかして、余白を詰める
  async function cutout(file) {
    const bmp = await createImageBitmap(file);
    const s = Math.min(1, 720 / Math.max(bmp.width, bmp.height));
    const w = Math.max(1, Math.round(bmp.width * s)), h = Math.max(1, Math.round(bmp.height * s));
    const cv = document.createElement("canvas"); cv.width = w; cv.height = h;
    const cx = cv.getContext("2d", { willReadFrequently: true });
    cx.drawImage(bmp, 0, 0, w, h);
    const im = cx.getImageData(0, 0, w, h), px = im.data;
    let transparent = 0;
    for (let i = 3; i < px.length; i += 4) if (px[i] < 250) transparent++;
    let cut = false;
    if (transparent < w * h * 0.01) {
      // 背景色: 縁の画素の中央値
      const edge = [];
      for (let i = 0; i < w; i++) edge.push(i, (h - 1) * w + i);
      for (let j = 0; j < h; j++) edge.push(j * w, j * w + w - 1);
      const med = (k) => edge.map((p) => px[p * 4 + k]).sort((a, b) => a - b)[edge.length >> 1];
      const bg = [med(0), med(1), med(2)];
      const dist = (p) => Math.hypot(px[p * 4] - bg[0], px[p * 4 + 1] - bg[1], px[p * 4 + 2] - bg[2]);
      const TOL = 42, seen = new Uint8Array(w * h), stack = [];
      for (const p of edge) if (!seen[p] && dist(p) < TOL) { seen[p] = 1; stack.push(p); }
      let filled = 0;
      while (stack.length) {
        const p = stack.pop(); filled++;
        const i = p % w, j = (p / w) | 0;
        for (const q of [i > 0 ? p - 1 : -1, i < w - 1 ? p + 1 : -1, j > 0 ? p - w : -1, j < h - 1 ? p + w : -1]) {
          if (q >= 0 && !seen[q] && dist(q) < TOL) { seen[q] = 1; stack.push(q); }
        }
      }
      if (filled > w * h * 0.05) {
        cut = true;
        // 背景からの距離 (画素 2 つ分まで) を測る: 輪郭のすぐ内側は、写真の背景色が混ざっている
        const ring = new Uint8Array(w * h);
        let front = [];
        for (let p = 0; p < w * h; p++) if (seen[p]) { px[p * 4 + 3] = 0; front.push(p); }
        for (let step = 1; step <= 2; step++) {
          const next = [];
          for (const p of front) {
            const i = p % w, j = (p / w) | 0;
            for (const q of [i > 0 ? p - 1 : -1, i < w - 1 ? p + 1 : -1, j > 0 ? p - w : -1, j < h - 1 ? p + w : -1]) {
              if (q >= 0 && !seen[q] && !ring[q]) { ring[q] = step; next.push(q); }
            }
          }
          front = next;
        }
        for (let p = 0; p < w * h; p++) {
          if (!ring[p]) continue;
          // 背景色に近いほど薄く。さらに混ざった背景色を差し引いて、白いふちが暗い画面で浮かないようにする
          const a = Math.min(1, Math.max(ring[p] === 1 ? 0.1 : 0.45, (dist(p) - TOL * 0.6) / (TOL * 2.4)));
          for (let k = 0; k < 3; k++) {
            px[p * 4 + k] = Math.max(0, Math.min(255, Math.round((px[p * 4 + k] - bg[k] * (1 - a)) / a)));
          }
          px[p * 4 + 3] = Math.round(255 * a);
        }
      }
    }
    // 余白を詰める (見えている部分の外枠 + 4%)
    let x0 = w, y0 = h, x1 = -1, y1 = -1;
    for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) if (px[(j * w + i) * 4 + 3] > 16) {
      if (i < x0) x0 = i; if (i > x1) x1 = i; if (j < y0) y0 = j; if (j > y1) y1 = j;
    }
    if (x1 < 0) throw new Error("画像が空です");
    cx.putImageData(im, 0, 0);
    const pad = Math.round(Math.max(x1 - x0, y1 - y0) * 0.04);
    const cw = x1 - x0 + 1 + pad * 2, ch = y1 - y0 + 1 + pad * 2;
    const out = document.createElement("canvas"); out.width = cw; out.height = ch;
    out.getContext("2d").drawImage(cv, x0, y0, x1 - x0 + 1, y1 - y0 + 1, pad, pad, x1 - x0 + 1, y1 - y0 + 1);
    const blob = await new Promise((res) => out.toBlob(res, "image/png"));
    return { blob, cut };
  }

  // ------------------------------------------------------------ 本体からの出来事
  function connect() {
    const es = new EventSource("/events");
    es.onopen = () => document.body.classList.remove("offline");
    es.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      if (ev.type === "say") {
        add("tachikoma", ev.text);
        bubble.textContent = ev.text; bubble.hidden = false;
        textExprUntil = Date.now() + Math.min(12000, 2500 + ev.text.length * 120);
        mover.classList.remove("walking");
        show(ev.expression || "talk");
        setTimeout(() => { if (Date.now() >= textExprUntil) { bubble.hidden = true; show(stateExpr); } }, textExprUntil - Date.now() + 50);
      } else if (ev.type === "user") {
        add("user", ev.text);
      } else if (ev.type === "log") {
        add("system", ev.text);
      } else if (ev.type === "assets") {
        version = Date.now(); loadAssets();
      } else if (ev.type === "state") {
        stateExpr = ev.expression || stateExpr; speaking = !!ev.speaking; mode = ev.mode || mode;
        $("st-mode").textContent = MODES[ev.mode] || ev.mode || "-";
        $("st-ear").textContent = ev.ear_cer == null ? "-" : ev.ear_cer.toFixed(3);
        $("st-eye").textContent = ev.eye_cer == null ? "-" : ev.eye_cer.toFixed(3);
        $("st-w").textContent = ev.watts == null ? "-" : `${Math.round(ev.watts)} W`;
        $("st-model").textContent = ev.model || "-";
        const act = ev.activity;
        $("activity").textContent = act ? `${ACTIVITIES[act.activity] || act.activity} — ${act.why}` : (MODES[ev.mode] || "");
        if (ev.devices) renderDevices(ev.devices);
        if (!CALM.has(stateExpr)) mover.classList.remove("walking");
        show(Date.now() >= textExprUntil ? stateExpr : current);
      }
    };
    es.onerror = () => {
      document.body.classList.add("offline");
      $("activity").textContent = "タチコマ本体に接続できません (再接続中…)";
    };
  }

  $("form").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("text").value.trim();
    if (!text) return;
    $("text").value = "";
    post("/say", JSON.stringify({ text }), "application/json").catch(() => note("タチコマ本体に届きませんでした"));
  });

  loadAssets();
  connect();
  setTimeout(stroll, 3000);
  setTimeout(blink, 2500);
})();
