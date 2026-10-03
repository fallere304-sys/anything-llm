// 知識・処理アクセスの可視化(Canvas 2D・外部依存なし)。
// 中央の核 = AI、周囲の球体 = 知識/処理ノード。バックエンドの activity イベントを受けたノードだけが光る。
// 未実装ノード(available=false)は暗く点線で描き、決して光らせない。

const TAU = Math.PI * 2;
const C = {
  edge: [255, 64, 48],
  mesh: [190, 255, 120],
  cyan: [120, 255, 240],
  green: [60, 255, 120],
  red: [255, 70, 80],
  amber: [245, 209, 66],
  dim: [110, 135, 128],
};
const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
// 関連するものが隣り合う並び
const ORDER = ["persona", "history", "memory", "project", "llm:fast", "llm:strong", "orchestrator", "research", "create", "image", "files", "tts"];
const STATE_SPEED = { idle: 1.2, thinking: 4, responding: 7, working: 5, waiting: 2.5, error: 2 };

function rng(seed) { // mulberry32: リサイズしても同じ配置になるよう決定的に
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export class NetworkViz {
  constructor(canvas, { onSelect } = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.onSelect = onSelect || (() => {});
    this.reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.nodes = [];
    this.mesh = [];
    this.meshEdges = [];
    this.particles = [];
    this.state = "idle";
    this.t = 0;
    this.selected = null;
    this.w = this.h = 0;
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
    canvas.addEventListener("click", (e) => this.handleClick(e));
    this.last = performance.now();
    this.acc = 0;
    this.loop = this.loop.bind(this);
    requestAnimationFrame(this.loop);
  }

  // ---- 外部 API ----
  setNodes(list) {
    const prev = new Map(this.nodes.map((n) => [n.id, n]));
    const rank = (id) => { const i = ORDER.indexOf(id); return i < 0 ? 99 : i; };
    this.nodes = [...list].sort((a, b) => rank(a.id) - rank(b.id)).map((d) => {
      const p = prev.get(d.id);
      return { ...d, x: 0, y: 0, r: 0, glow: p?.glow || 0, active: p?.active || 0,
               flow: p?.flow || "out", fail: p?.fail || 0, emit: 0, lastSummary: p?.lastSummary || "", links: [] };
    });
    if (this.selected) this.selected = this.byId(this.selected.id);
    this.layout();
  }

  setState(state) {
    this.state = state;
    if (state === "responding") this.nodes.forEach((n) => { if (n.active && n.id.startsWith("llm:")) n.flow = "in"; });
  }

  activity(ev) {
    const n = this.byId(ev.target);
    if (!n || !n.available) return;
    n.lastSummary = ev.summary;
    if (ev.phase === "start") {
      n.active += 1; n.flow = "out"; n.glow = 1; this.burst(n, "out", 3);
    } else if (ev.phase === "end") {
      n.active = Math.max(0, n.active - 1); n.glow = 1;
      if (ev.ok === false) n.fail = 1; else this.burst(n, "in", 4);
    } else {
      n.glow = 1; this.burst(n, "out", 4);
    }
  }

  byId(id) { return this.nodes.find((n) => n.id === id); }

  // ---- レイアウト ----
  resize() {
    const box = this.canvas.parentElement.getBoundingClientRect();
    this.w = Math.round(box.width); this.h = Math.round(box.height);
    if (!this.w || !this.h) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.canvas.width = this.w * dpr; this.canvas.height = this.h * dpr;
    this.canvas.style.width = this.w + "px"; this.canvas.style.height = this.h + "px";
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.bg = this.ctx.createRadialGradient(this.w * 0.5, this.h * 0.62, 0, this.w * 0.5, this.h * 0.62, Math.max(this.w, this.h) * 0.75);
    this.bg.addColorStop(0, "#08301f"); this.bg.addColorStop(0.55, "#03140d"); this.bg.addColorStop(1, "#010604");
    this.buildMesh();
    this.layout();
  }

  buildMesh() {
    const rand = rng(7);
    const count = clamp(Math.round((this.w * this.h) / 7000), 24, 110);
    this.mesh = Array.from({ length: count }, () => ({
      bx: rand() * this.w, by: rand() * this.h, ph: rand() * TAU, amp: 3 + rand() * 7, x: 0, y: 0,
    }));
    const seen = new Set(); this.meshEdges = [];
    this.mesh.forEach((p, i) => {
      this.mesh.map((q, j) => [j, (p.bx - q.bx) ** 2 + (p.by - q.by) ** 2])
        .filter(([j]) => j !== i).sort((a, b) => a[1] - b[1]).slice(0, 3)
        .forEach(([j]) => { const k = i < j ? `${i}-${j}` : `${j}-${i}`; if (!seen.has(k)) { seen.add(k); this.meshEdges.push([i, j]); } });
    });
  }

  layout() {
    const { w, h } = this;
    if (!w || !h || !this.nodes.length) return;
    this.core = { x: w * 0.5, y: h * 0.6 };
    const r = clamp(Math.min(w, h) * 0.075, 18, 44);
    const rx = w * 0.4, ry = h * 0.44, n = this.nodes.length;
    const a0 = (170 * Math.PI) / 180, span = (200 * Math.PI) / 180;
    this.nodes.forEach((node, i) => {
      const a = a0 + (n === 1 ? span / 2 : (span * i) / (n - 1));
      const k = i % 2 ? 0.74 : 1;
      node.r = node.available ? r : r * 0.72;
      node.x = clamp(this.core.x + Math.cos(a) * rx * k, r + 6, w - r - 6);
      node.y = clamp(this.core.y + Math.sin(a) * ry * k, r + 6, h - r - 26);
      node.links = this.mesh.map((p, j) => [j, (p.bx - node.x) ** 2 + (p.by - node.y) ** 2])
        .sort((p, q) => p[1] - q[1]).slice(0, 3).map(([j]) => j);
    });
    this.coreR = r * 0.7;
  }

  // ---- 粒子(アクセスの流れ) ----
  burst(node, dir, count) {
    if (this.reduced) return;
    for (let i = 0; i < count; i++) {
      this.particles.push({ node, dir, t: -i * 0.12, speed: 0.9 + Math.random() * 0.5 });
    }
  }

  // ---- ループ ----
  loop(now) {
    requestAnimationFrame(this.loop);
    const dt = Math.min((now - this.last) / 1000, 0.1);
    this.last = now;
    const busy = this.state !== "idle" || this.particles.length || this.nodes.some((n) => n.glow > 0.01 || n.active || n.fail > 0.01);
    // 待機中は約20fpsに間引いて電池を節約
    this.acc += dt;
    if (!busy && this.acc < 0.05) return;
    const step = this.acc; this.acc = 0;
    if (!this.w || !this.h || !this.core) return;
    this.update(step);
    this.draw();
  }

  update(dt) {
    this.t += dt * (this.reduced ? 0.3 : 1);
    for (const n of this.nodes) {
      if (n.active) {
        n.glow = Math.max(n.glow, 0.75 + 0.25 * Math.sin(this.t * 6));
        n.emit -= dt;
        if (n.emit <= 0) { this.burst(n, n.flow, 1); n.emit = 0.18; }
      } else {
        n.glow = Math.max(0, n.glow - dt * 0.6);
      }
      n.fail = Math.max(0, n.fail - dt * 0.4);
    }
    for (const p of this.particles) p.t += dt * p.speed;
    this.particles = this.particles.filter((p) => p.t < 1);
    if (!this.reduced) {
      for (const m of this.mesh) {
        m.x = m.bx + Math.sin(this.t * 0.4 + m.ph) * m.amp;
        m.y = m.by + Math.cos(this.t * 0.33 + m.ph) * m.amp;
      }
    } else {
      for (const m of this.mesh) { m.x = m.bx; m.y = m.by; }
    }
  }

  draw() {
    const { ctx, w, h, core } = this;
    ctx.fillStyle = this.bg; ctx.fillRect(0, 0, w, h);
    this.drawHud();
    this.drawPlatform();

    // 背景の網目(赤いライン + 黄緑の結節点)
    ctx.lineWidth = 1;
    ctx.strokeStyle = rgba(C.edge, 0.2);
    ctx.beginPath();
    for (const [i, j] of this.meshEdges) { ctx.moveTo(this.mesh[i].x, this.mesh[i].y); ctx.lineTo(this.mesh[j].x, this.mesh[j].y); }
    for (const n of this.nodes) for (const j of n.links) { ctx.moveTo(n.x, n.y); ctx.lineTo(this.mesh[j].x, this.mesh[j].y); }
    ctx.stroke();

    // 核 ↔ ノードのライン
    for (const n of this.nodes) {
      ctx.save();
      if (!n.available) { ctx.setLineDash([3, 6]); ctx.strokeStyle = rgba(C.dim, 0.18); ctx.lineWidth = 1; }
      else {
        const col = n.fail > 0.01 ? C.red : C.edge;
        ctx.strokeStyle = rgba(col, 0.38 + n.glow * 0.55); ctx.lineWidth = 1 + n.glow * 1.8;
        if (n.glow > 0.05) { ctx.shadowColor = rgba(col, 0.9); ctx.shadowBlur = 10 * n.glow; }
      }
      ctx.beginPath(); ctx.moveTo(core.x, core.y); ctx.lineTo(n.x, n.y); ctx.stroke();
      ctx.restore();
    }

    // 結節点
    ctx.strokeStyle = rgba(C.mesh, 0.55); ctx.fillStyle = rgba(C.mesh, 0.8);
    for (const m of this.mesh) {
      ctx.beginPath(); ctx.arc(m.x, m.y, 3.2, 0, TAU); ctx.stroke();
      ctx.beginPath(); ctx.arc(m.x, m.y, 1.1, 0, TAU); ctx.fill();
    }

    // 粒子
    for (const p of this.particles) {
      if (p.t < 0) continue;
      const [a, b] = p.dir === "out" ? [core, p.node] : [p.node, core];
      const x = a.x + (b.x - a.x) * p.t, y = a.y + (b.y - a.y) * p.t;
      const col = p.dir === "out" ? C.amber : C.cyan;
      ctx.fillStyle = rgba(col, 0.25); ctx.beginPath(); ctx.arc(x, y, 5, 0, TAU); ctx.fill();
      ctx.fillStyle = rgba(col, 0.95); ctx.beginPath(); ctx.arc(x, y, 2, 0, TAU); ctx.fill();
    }

    this.drawCore();
    for (const n of this.nodes) this.drawNode(n);
  }

  drawHud() {
    const { ctx, w, h, t } = this, R = Math.min(w, h);
    const arc = (cx, cy, r, a0, a1, alpha, width, dash, rot) => {
      ctx.save(); ctx.translate(cx, cy); ctx.rotate(rot || 0);
      ctx.strokeStyle = rgba(C.cyan, alpha); ctx.lineWidth = width; if (dash) ctx.setLineDash(dash);
      ctx.beginPath(); ctx.arc(0, 0, r, a0, a1); ctx.stroke(); ctx.restore();
    };
    arc(w * 1.02, h * 1.08, R * 0.5, Math.PI, Math.PI * 1.5, 0.35, 3);
    arc(w * 1.02, h * 1.08, R * 0.56, Math.PI, Math.PI * 1.5, 0.25, 1.5, [2, 7], t * 0.05);
    arc(-w * 0.02, h * 1.1, R * 0.36, Math.PI * 1.5, TAU, 0.28, 2.5);
    arc(-w * 0.02, h * 1.1, R * 0.42, Math.PI * 1.5, TAU, 0.18, 1, [2, 6], -t * 0.04);
  }

  drawPlatform() {
    const { ctx, core, t } = this, base = this.coreR * 2.4, flat = 0.32;
    ctx.save(); ctx.translate(core.x, core.y + this.coreR * 0.6); ctx.scale(1, flat);
    [[1, 0.5, 2], [1.7, 0.35, 1.5], [2.5, 0.22, 1]].forEach(([k, a, lw], i) => {
      ctx.strokeStyle = rgba(C.cyan, a); ctx.lineWidth = lw / flat * 0.5;
      ctx.setLineDash(i === 1 ? [10, 8] : []);
      ctx.beginPath(); ctx.arc(0, 0, base * k, i === 1 ? t * 0.3 : 0, TAU + (i === 1 ? t * 0.3 : 0)); ctx.stroke();
    });
    ctx.setLineDash([]);
    ctx.fillStyle = rgba(C.edge, 0.85);
    for (let i = 0; i < 16; i++) {
      const a = (i / 16) * TAU + t * 0.1;
      ctx.beginPath(); ctx.arc(Math.cos(a) * base * 2.1, Math.sin(a) * base * 2.1, 3 / flat * 0.6, 0, TAU); ctx.fill();
    }
    ctx.restore();
  }

  drawCore() {
    const { ctx, core, t } = this, s = this.coreR;
    const speed = STATE_SPEED[this.state] || 1.2;
    const pulse = 0.5 + 0.5 * Math.sin(t * speed);
    const col = this.state === "error" ? C.red
      : this.state === "thinking" || this.state === "waiting" ? C.amber
      : this.state === "working" ? C.cyan : C.green;
    // 外側の回転リング
    ctx.save(); ctx.translate(core.x, core.y); ctx.rotate(t * speed * 0.15);
    ctx.strokeStyle = rgba(C.cyan, 0.5); ctx.lineWidth = 1.2; ctx.setLineDash([6, 5]);
    ctx.beginPath(); ctx.arc(0, 0, s * 1.75, 0, TAU); ctx.stroke(); ctx.restore();
    // 発光
    const g = ctx.createRadialGradient(core.x, core.y, s * 0.3, core.x, core.y, s * (2.2 + pulse * 0.6));
    g.addColorStop(0, rgba(col, 0.55 + pulse * 0.25)); g.addColorStop(1, rgba(col, 0));
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(core.x, core.y, s * 2.8, 0, TAU); ctx.fill();
    // チップ本体
    ctx.save(); ctx.translate(core.x, core.y);
    ctx.fillStyle = rgba(col, 0.9); ctx.shadowColor = rgba(col, 1); ctx.shadowBlur = 12 + pulse * 14;
    roundRect(ctx, -s, -s, s * 2, s * 2, s * 0.25); ctx.fill();
    ctx.shadowBlur = 0;
    ctx.strokeStyle = "rgba(0,40,15,0.45)"; ctx.lineWidth = 1;
    for (let i = -2; i <= 2; i++) {
      ctx.beginPath(); ctx.moveTo(-s * 0.7, i * s * 0.3); ctx.lineTo(s * 0.7, i * s * 0.3); ctx.stroke();
    }
    // ピン
    ctx.strokeStyle = rgba(col, 0.9); ctx.lineWidth = 2;
    for (let i = -2; i <= 2; i++) {
      const o = i * s * 0.35;
      [[o, -s, o, -s * 1.3], [o, s, o, s * 1.3], [-s, o, -s * 1.3, o], [s, o, s * 1.3, o]].forEach(([a, b, c, d]) => {
        ctx.beginPath(); ctx.moveTo(a, b); ctx.lineTo(c, d); ctx.stroke();
      });
    }
    ctx.fillStyle = "rgba(0,30,10,0.85)"; ctx.font = `700 ${Math.round(s * 0.62)}px ${HUD_FONT}`;
    ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText("AI", 0, 1);
    ctx.restore();
  }

  drawNode(n) {
    const { ctx, t } = this;
    const r = n.r * (1 + n.glow * 0.08);
    ctx.save();
    if (n.available) {
      // 波紋
      if (n.glow > 0.02) {
        ctx.strokeStyle = rgba(C.cyan, n.glow * 0.55); ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.arc(n.x, n.y, r * (1.15 + (1 - n.glow) * 0.6), 0, TAU); ctx.stroke();
      }
      const g = ctx.createRadialGradient(n.x - r * 0.3, n.y - r * 0.3, r * 0.1, n.x, n.y, r);
      g.addColorStop(0, "rgba(10,30,24,0.35)"); g.addColorStop(0.75, rgba([60, 150, 130], 0.18 + n.glow * 0.15));
      g.addColorStop(1, rgba(C.cyan, 0.3 + n.glow * 0.35));
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, TAU); ctx.fill();
      const rim = n.fail > 0.01 ? C.red : C.cyan;
      ctx.strokeStyle = rgba(rim, 0.6 + n.glow * 0.4); ctx.lineWidth = 1.5 + n.glow * 1.5;
      if (n.glow > 0.05 || n.fail > 0.05) { ctx.shadowColor = rgba(rim, 1); ctx.shadowBlur = 14 * Math.max(n.glow, n.fail); }
      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, TAU); ctx.stroke(); ctx.shadowBlur = 0;
      // ガラスのハイライト
      ctx.strokeStyle = "rgba(255,255,255,0.35)"; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(n.x, n.y, r * 0.78, Math.PI * 1.1, Math.PI * 1.45); ctx.stroke();
    } else {
      ctx.fillStyle = "rgba(20,30,28,0.55)"; ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, TAU); ctx.fill();
      ctx.setLineDash([3, 4]); ctx.strokeStyle = rgba(C.dim, 0.45); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, TAU); ctx.stroke(); ctx.setLineDash([]);
    }
    if (this.selected === n) {
      ctx.save(); ctx.translate(n.x, n.y); ctx.rotate(t * 0.8);
      ctx.strokeStyle = rgba(C.amber, 0.9); ctx.lineWidth = 1.5; ctx.setLineDash([5, 5]);
      ctx.beginPath(); ctx.arc(0, 0, r + 6, 0, TAU); ctx.stroke(); ctx.restore();
    }
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillStyle = n.available ? (n.glow > 0.3 ? "#ffd0c8" : "#ff5a48") : "rgba(140,160,155,0.6)";
    fitText(ctx, n.short, n.x, n.y + 1, r * 1.55, r * 0.42, 600);
    if (n.external && n.available) {
      ctx.fillStyle = rgba(C.amber, 0.95); ctx.font = `${Math.round(r * 0.42)}px system-ui,sans-serif`;
      ctx.fillText("☁", n.x + r * 0.72, n.y - r * 0.72);
    }
    // 狭い画面では未実装ノードのラベルを省く(タップで詳細表示)
    const narrow = this.w < 600;
    if (n.available || !narrow) {
      ctx.font = `${narrow ? 10 : 11}px ${HUD_FONT}`;
      if ("letterSpacing" in ctx) ctx.letterSpacing = "0.5px";
      ctx.fillStyle = n.available ? "rgba(210,255,240,0.9)" : "rgba(140,160,155,0.65)";
      ctx.fillText(n.available ? n.label : `${n.label} (${n.planned_phase ? "planned" : "not set"})`, n.x, n.y + r + 11);
      if ("letterSpacing" in ctx) ctx.letterSpacing = "0px";
    }
    ctx.restore();
  }

  handleClick(e) {
    const box = this.canvas.getBoundingClientRect();
    const x = e.clientX - box.left, y = e.clientY - box.top;
    const hit = this.nodes.find((n) => (n.x - x) ** 2 + (n.y - y) ** 2 <= (n.r + 8) ** 2) || null;
    this.selected = hit;
    this.onSelect(hit);
  }
}

// 可視化パネルの表記は英語(ユーザー指定)。字幅の安定した欧文フォントを使う。
const HUD_FONT = '"Segoe UI", "Helvetica Neue", Arial, sans-serif';

// 指定幅に収まる最大の文字サイズで描く(上限 maxSize)。
function fitText(ctx, text, x, y, maxWidth, maxSize, weight) {
  let size = Math.round(maxSize);
  const spacing = "letterSpacing" in ctx;
  for (; size > 7; size--) {
    ctx.font = `${weight} ${size}px ${HUD_FONT}`;
    if (spacing) ctx.letterSpacing = `${Math.max(0.5, size * 0.08).toFixed(1)}px`;
    if (ctx.measureText(text).width <= maxWidth) break;
  }
  ctx.fillText(text, x, y);
  if (spacing) ctx.letterSpacing = "0px";
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}
