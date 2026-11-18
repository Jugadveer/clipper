// Shorts engine runtime: every pixel is a pure function of time t, so HyperFrames can seek to any
// frame and render it the same way twice. compose.py writes SPEC (the edit decision list) into the
// page; this file builds the DOM once and redraws it on every hf-seek.
//
// Layers, bottom to top: stage (camera) > grounds, media (videos), scenes | fx (grade, grain,
// vignette) | overlays | captions | transitions.

const SPEC = window.__SPEC;
const W = SPEC.w, H = SPEC.h;
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lerp = (a, b, u) => a + (b - a) * u;
const easeOut = (x) => 1 - Math.pow(1 - clamp(x), 3);
const easeIn = (x) => Math.pow(clamp(x), 3);
const easeInOut = (x) => { x = clamp(x); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; };
const easeBack = (x) => { x = clamp(x); const c = 1.7; return 1 + (c + 1) * Math.pow(x - 1, 3) + c * Math.pow(x - 1, 2); };
const smooth = (x) => { x = clamp(x); return x * x * (3 - 2 * x); };
function rng(seed) { return () => { seed |= 0; seed = (seed + 0x6D2B79F5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
function noise1(x, seed = 1) { const i = Math.floor(x), f = x - i; const a = rng(i * 131 + seed)(), b = rng((i + 1) * 131 + seed)(); return lerp(a, b, smooth(f)) * 2 - 1; }
function el(tag, cls, parent, html) { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; if (parent) parent.appendChild(e); return e; }
const esc = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
// life: 0..1 build-in over `a` seconds, 1..0 build-out over `b` seconds at the end of [s, e]
const life = (t, s, e, a = 0.35, b = 0.3) => Math.min(easeOut((t - s) / a), 1 - easeIn((t - (e - b)) / b));
const T = SPEC.theme || {};
const ACCENT = T.accent || "#ffd400";

const root = document.getElementById("root");
const stage = document.getElementById("stage");
const media = document.getElementById("media");
const groundsEl = el("div", "layer", stage); stage.insertBefore(groundsEl, media);
const scenesEl = el("div", "layer", stage);
const fxEl = el("div", "layer fx", root);
const ovEl = el("div", "layer", root);
const capEl = el("div", "layer", root);
const trEl = el("div", "layer", root);

// ======================================================================= grounds
const GROUNDS = {
  paper: "radial-gradient(120% 90% at 50% 40%, #f1efe9 0%, #d9d6cf 70%, #b9b5ad 100%)",
  cream: "linear-gradient(#f4ecd8, #ece2c8)",
  white: "radial-gradient(90% 70% at 50% 45%, #ffffff 0%, #eceef0 70%, #d9dce0 100%)",
  dark: "radial-gradient(100% 80% at 50% 40%, #1d1d22 0%, #0b0b0e 70%, #050506 100%)",
  grid: "linear-gradient(rgba(255,255,255,.07) 2px, transparent 2px) 0 0/90px 90px, linear-gradient(90deg, rgba(255,255,255,.07) 2px, transparent 2px) 0 0/90px 90px, radial-gradient(100% 80% at 50% 40%, #16161a, #050505)",
  blue: "radial-gradient(120% 90% at 50% 30%, #1d3f8f 0%, #0a1a46 55%, #040a1d 100%)",
  neon: "radial-gradient(90% 70% at 50% 50%, #0d2a12 0%, #04120a 60%, #010402 100%)",
  red: "radial-gradient(100% 80% at 50% 40%, #c0182c 0%, #6d0613 70%, #2a0207 100%)",
  sunset: "linear-gradient(#170a33, #5b1a6b 55%, #e2456b 85%, #ffb35c)",
  // cinematic story sets (DfpnDOvh): a lit stage, a night, a money green, a wine-dark room
  stage: "radial-gradient(48% 16% at 50% 82%, rgba(255,236,190,.42) 0%, transparent 70%), radial-gradient(38% 70% at 50% 8%, rgba(255,240,210,.20), transparent 75%), linear-gradient(#17131c, #08070b)",
  night: "radial-gradient(90% 60% at 50% 18%, #2a3870 0%, #0e1534 60%, #05070f 100%)",
  money: "radial-gradient(80% 55% at 50% 30%, #35562a 0%, #142010 65%, #070b05 100%)",
  wine: "radial-gradient(90% 70% at 50% 40%, #62202a 0%, #2c0b10 70%, #120406 100%)",
};
function newsTexture() { // a newspaper collage drawn once (MoVlt's paper ground)
  const c = document.createElement("canvas"); c.width = 540; c.height = 960; const g = c.getContext("2d"); const r = rng(5);
  g.fillStyle = "#e9e4d8"; g.fillRect(0, 0, 540, 960);
  for (let k = 0; k < 22; k++) { g.save(); g.translate(r() * 540, r() * 960); g.rotate((r() - 0.5) * 0.5);
    g.fillStyle = `rgba(${200 + r() * 40},${195 + r() * 40},${180 + r() * 40},0.95)`; const w = 160 + r() * 200, h = 120 + r() * 220; g.fillRect(-w / 2, -h / 2, w, h);
    g.fillStyle = "rgba(30,30,30,.55)"; for (let y = -h / 2 + 14; y < h / 2 - 6; y += 9) { const lw = w - 24 - r() * 30; g.fillRect(-w / 2 + 12, y, lw, 3); }
    g.fillStyle = "rgba(20,20,20,.85)"; g.fillRect(-w / 2 + 12, -h / 2 + 8, w * 0.6, 8); g.restore(); }
  return `url(${c.toDataURL()}) center/cover`;
}
const groundEls = (SPEC.grounds || []).map((gd) => { const d = el("div", "layer ground", groundsEl);
  d.style.background = gd.type === "news" ? newsTexture() : (GROUNDS[gd.type] || gd.type); return { d, gd }; });

// ======================================================================= templates (scenes + overlays)
// Each returns {el, update(lt, dur)} ; lt = seconds since its start. Templates animate their own in/out.
function box(parent, css) { const d = el("div", "abs", parent); Object.assign(d.style, css || {}); return d; }
const TPL = {
  header(p, parent) { // title band (RoLND / Z-gCH)
    const d = box(parent, { left: 0, right: 0, top: 0, padding: "54px 40px 26px", textAlign: "center",
      background: p.band === "none" ? "none" : "linear-gradient(rgba(0,0,0,.92), rgba(0,0,0,.78))" });
    const hl = (s) => esc(s).replace(/\[(.+?)\]/g, (_, w) => `<span style="color:${p.color || ACCENT}">${w}</span>`);
    d.innerHTML = `<div class="hd-t">${hl(p.title)}</div>${p.sub ? `<div class="hd-s">${hl(p.sub)}</div>` : ""}`;
    return { el: d, update(lt, dur) { const k = easeOut(lt / 0.4); d.style.transform = `translateY(${(1 - k) * -120}px)`; d.style.opacity = k; } };
  },
  rank(p, parent) { // numbered list that fills in as each item plays
    const d = box(parent, { left: "34px", top: (p.top ?? 300) + "px" });
    const cols = p.colors || ["#ffd400", "#ffffff", "#ff3b3b", "#ffffff", "#ff3b3b"];
    const rows = p.items.map((it, k) => { const r = el("div", "rk-row", d); r.innerHTML = `<span class="rk-n" style="color:${cols[k % cols.length]}">${it.n ?? k + 1}.</span><span class="rk-l"></span>`; return { r, it, l: r.querySelector(".rk-l") }; });
    return { el: d, update(lt) { rows.forEach(({ r, it, l }) => { const on = lt >= (it.at ?? 1e9); l.textContent = on ? it.label : ""; const k = easeBack((lt - it.at) / 0.35);
      l.style.transform = on ? `scale(${lerp(0.6, 1, k)})` : ""; l.style.display = "inline-block"; }); } };
  },
  chat(p, parent) { // glass chat card (zak): question types in, answer row appears
    const d = box(parent, { left: "50%", top: (p.y ?? 0.6) * H + "px", width: "760px", marginLeft: "-380px" });
    d.className += " glass"; d.innerHTML = `<div class="ch-q"></div><div class="ch-a"><span class="ch-dot"></span><span class="ch-at"></span></div>`;
    const q = d.querySelector(".ch-q"), a = d.querySelector(".ch-a"), at = d.querySelector(".ch-at");
    return { el: d, update(lt, dur) { const k = life(lt, 0, dur, 0.35, 0.3); d.style.opacity = k; d.style.transform = `translateY(${(1 - k) * 60}px) scale(${lerp(0.92, 1, k)})`;
      const n = Math.floor(clamp((lt - 0.25) / 0.9) * p.q.length); q.textContent = p.q.slice(0, n) + (n < p.q.length && Math.floor(lt * 4) % 2 ? "|" : "");
      const ak = easeBack((lt - 1.3) / 0.35); a.style.opacity = clamp((lt - 1.3) / 0.15); a.style.transform = `scale(${lerp(0.8, 1, ak)})`; at.textContent = p.a; } };
  },
  pill(p, parent) { // white pill, one word in red (zak: "AI is not smart")
    const d = box(parent, { left: "50%", top: (p.y ?? 0.66) * H + "px", transform: "translateX(-50%)" }); d.className += " pill";
    d.innerHTML = `<span class="pl-i">${p.icon || "✦"}</span>` + esc(p.text).replace(/\[(.+?)\]/g, (_, w) => `<b style="color:#ff2d3d">${w}</b>`);
    return { el: d, update(lt, dur) { const k = life(lt, 0, dur, 0.3, 0.25); d.style.opacity = k; d.style.transform = `translateX(-50%) scale(${lerp(0.5, 1, easeBack(lt / 0.35))})`; } };
  },
  grid(p, parent) { // 2x2 glass chips with numbers counting up (zak)
    const d = box(parent, { left: "50%", top: (p.y ?? 0.6) * H + "px", width: "820px", marginLeft: "-410px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: "18px" });
    const chips = p.items.map((it, k) => { const c = el("div", "glass chip", d); c.innerHTML = `<span class="ch-dot" style="background:${it.c || ["#10a37f", "#d97757", "#4285f4", "#1d1d1f"][k % 4]}"></span><span class="cp-l">${esc(it.label)}</span><span class="cp-v"></span>`; return { c, it, v: c.querySelector(".cp-v") }; });
    return { el: d, update(lt, dur) { chips.forEach(({ c, it, v }, k) => { const kk = life(lt - k * 0.12, 0, dur - k * 0.12, 0.3, 0.25); c.style.opacity = kk; c.style.transform = `translateY(${(1 - kk) * 40}px)`;
      if (it.value != null) v.textContent = Math.round(it.value * easeOut((lt - 0.6 - k * 0.1) / 1.0)); }); } };
  },
  checks(p, parent) { // dark interlude, rows with ✓ / ✗ (zak)
    const d = box(parent, { inset: 0, background: "radial-gradient(100% 80% at 50% 40%, #202024, #050506)", display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", gap: "26px" });
    const rows = p.rows.map((r) => { const e = el("div", "glass ck-row", d); e.innerHTML = `<span class="ck-i" style="background:${r.ok ? "#22c55e" : "#ef4444"}">${r.ok ? "✓" : "✕"}</span><span><div class="ck-l">${esc(r.label)}</div>${r.sub ? `<div class="ck-s">${esc(r.sub)}</div>` : ""}</span>`; return e; });
    return { el: d, update(lt, dur) { d.style.opacity = life(lt, 0, dur, 0.2, 0.2); rows.forEach((e, k) => { const kk = easeBack((lt - 0.15 - k * 0.45) / 0.35); e.style.opacity = clamp((lt - 0.15 - k * 0.45) / 0.15); e.style.transform = `scale(${lerp(0.7, 1, kk)})`; }); } };
  },
  object(p, parent) { // hero object (Apple emoji, 160 px art) floating over a spotlight disc (C-yZl brain / eyeball)
    const sz = p.size || 440;
    const d = box(parent, { left: "50%", top: (p.y ?? 0.27) * H + "px", width: sz + "px", height: sz + "px", transform: "translate(-50%,-50%)" });
    d.innerHTML = `<div class="ob-spot"></div><div class="ob-shadow"></div><img class="ob-img" src="${p.img}">`;
    const img = d.querySelector(".ob-img"), sh = d.querySelector(".ob-shadow"), sp = d.querySelector(".ob-spot");
    if (p.spot === false) sp.style.display = "none";
    return { el: d, update(lt, dur) { const k = easeBack(lt / 0.5), out = easeIn((lt - (dur - 0.25)) / 0.25);
      const fy = Math.sin(lt * 2.2) * 14, rot = Math.sin(lt * 1.4) * 6;
      img.style.transform = `translateY(${fy + (1 - easeOut(lt / 0.45)) * -260}px) rotate(${rot}deg) scale(${lerp(0.4, 1, k) * (1 - out * 0.6)})`;
      img.style.opacity = clamp(lt / 0.15) * (1 - out); sh.style.opacity = 0.35 * clamp(lt / 0.4) * (1 - out); sh.style.transform = `translateX(-50%) scale(${1 - fy / 120})`;
      sp.style.opacity = 0.9 * clamp(lt / 0.3) * (1 - out); sp.style.transform = `translate(-50%,-50%) scale(${lerp(0.3, 1, easeOut(lt / 0.5))})`; } };
  },
  sticker(p, parent) { // "???" / emoji pop (-rumn)
    const d = box(parent, { left: (p.x ?? 0.5) * W + "px", top: (p.y ?? 0.3) * H + "px", fontSize: (p.size || 150) + "px", transform: "translate(-50%,-50%)" }); d.className += " stk";
    if (p.textImg) d.innerHTML = `<img src="${p.textImg}" style="width:${p.size || 220}px;height:${p.size || 220}px;display:block">`; else d.textContent = p.text;
    return { el: d, update(lt, dur) { const k = easeBack(lt / 0.3); d.style.opacity = life(lt, 0, dur, 0.08, 0.2); d.style.transform = `translate(-50%,-50%) scale(${lerp(0.2, 1, k)}) rotate(${Math.sin(lt * 9) * 6 * (1 - clamp(lt / 1.2))}deg)`; } };
  },
  name(p, parent) { // big name typed letter by letter + subtitle typewriter (DLND)
    const d = box(parent, { left: 0, right: 0, top: (p.y ?? 0.28) * H + "px", textAlign: "center" });
    d.innerHTML = `<div class="nm-n" style="color:${p.color || "#2a2a2e"}"></div><div class="nm-s" style="color:${p.subColor || "#55555c"}"></div>`;
    const n = d.querySelector(".nm-n"), s = d.querySelector(".nm-s");
    return { el: d, update(lt, dur) { const k = 1 - easeIn((lt - (dur - 0.3)) / 0.3); d.style.opacity = k;
      n.innerHTML = [...p.name].map((ch, i) => { const u = clamp((lt - i * 0.05) / 0.25); return `<span style="opacity:${u};display:inline-block;transform:translateY(${(1 - easeOut(u)) * 30}px)">${ch === " " ? "&nbsp;" : esc(ch)}</span>`; }).join("");
      const m = Math.floor(clamp((lt - 0.5) / 0.8) * (p.sub || "").length); s.textContent = (p.sub || "").slice(0, m); } };
  },
  stat(p, parent) { // big number counting up
    const d = box(parent, { left: 0, right: 0, top: (p.y ?? 0.34) * H + "px", textAlign: "center" });
    if (p.plate) d.className += " st-plate";
    d.innerHTML = `${p.lead ? `<div class="st-l" style="color:${p.ink || "#fff"};margin:0 0 6px">${esc(p.lead)}</div>` : ""}<div class="st-v" style="color:${p.color || "#fff"}"></div><div class="st-l" style="color:${p.ink || "#fff"}">${esc(p.label || "")}</div>`; const v = d.querySelector(".st-v");
    const fmt = (x) => (p.decimals ? x.toFixed(p.decimals) : Math.round(x).toLocaleString("en-US"));
    return { el: d, update(lt, dur) { d.style.opacity = life(lt, 0, dur, 0.25, 0.3); v.textContent = (p.prefix || "") + fmt(p.value * easeOut((lt - 0.1) / 1.2)) + (p.suffix || ""); v.style.fontSize = Math.min(p.plate ? 170 : 210, Math.floor(1500 / Math.max(4, ((p.prefix || "") + fmt(p.value) + (p.suffix || "")).length))) + "px";
      d.style.transform = `scale(${1 + 0.08 * Math.sin(clamp((lt - 1.3) / 0.25) * Math.PI)})`; } };
  },
  slam(p, parent) { // full-frame keyword card: small lead line + one huge word (C-yZl, MoVlt cream page)
    const g = GROUNDS[p.ground || "cream"]; const ink = ["dark", "grid", "blue", "neon", "red", "sunset"].includes(p.ground) ? "#fff" : "#111";
    const d = box(parent, { inset: 0, background: p.ground === "news" ? newsTexture() : g, display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center" });
    d.innerHTML = `<div class="sl-l" style="color:${ink}">${esc(p.lead || "")}</div><div class="sl-w" style="color:${p.color || ink};font-family:${p.serif ? "PF" : "MB"};${p.serif ? "font-style:italic;font-weight:400;" : ""}">${esc(p.word)}</div>`;
    const w = d.querySelector(".sl-w");
    return { el: d, update(lt, dur) { d.style.opacity = life(lt, 0, dur, 0.12, 0.15); const k = easeBack((lt - 0.15) / 0.35); w.style.transform = `scale(${lerp(1.6, 1, k)})`; w.style.opacity = clamp((lt - 0.15) / 0.12); } };
  },
  lower(p, parent) { // lower third: name + role
    const d = box(parent, { left: "60px", top: (p.y ?? 0.74) * H + "px" }); d.className += " lt";
    d.innerHTML = `<div class="lt-n">${esc(p.name)}</div><div class="lt-r">${esc(p.role || "")}</div>`;
    return { el: d, update(lt, dur) { const k = life(lt, 0, dur, 0.4, 0.3); d.style.opacity = k; d.style.transform = `translateX(${(1 - k) * -80}px)`; } };
  },
  coins(p, parent) { // row of white discs that flip in (DLND logo coins), emoji or text icons
    const d = box(parent, { left: 0, right: 0, top: (p.y ?? 0.62) * H + "px", display: "flex", justifyContent: "center", gap: "40px" });
    const cs = p.items.map((it) => { const c = el("div", "coin", d); const ic = it.iconImg ? `<img src="${it.iconImg}" style="width:130px;height:130px">` : esc(it.icon);
      c.innerHTML = `<div class="cn-d">${ic}</div><div class="cn-l" style="color:${p.ink || "#333"}">${esc(it.label || "")}</div>`; return c; });
    return { el: d, update(lt, dur) { cs.forEach((c, k) => { const u = clamp((lt - 0.1 - k * 0.25) / 0.5); c.style.opacity = life(lt - k * 0.25, 0.1, dur - k * 0.25, 0.2, 0.3);
      c.querySelector(".cn-d").style.transform = `rotateY(${(1 - easeOut(u)) * 180}deg) scale(${lerp(0.6, 1, easeBack(u))})`; }); } };
  },
  quote(p, parent) {
    const d = box(parent, { left: "70px", right: "70px", top: (p.y ?? 0.3) * H + "px" });
    d.innerHTML = `<div class="qt-m">“</div><div class="qt-t">${esc(p.text)}</div><div class="qt-b">${esc(p.by || "")}</div>`;
    return { el: d, update(lt, dur) { const k = life(lt, 0, dur, 0.4, 0.3); d.style.opacity = k; d.style.transform = `translateY(${(1 - k) * 40}px)`; } };
  },
  image(p, parent) { // a photo framed and pushed in (cutout-ready: pass a PNG with alpha)
    const d = box(parent, { left: "50%", top: (p.y ?? 0.42) * H + "px", width: (p.w || 760) + "px", height: (p.h || 760) + "px", transform: "translate(-50%,-50%)" });
    d.innerHTML = `<img src="${p.src}" style="width:100%;height:100%;object-fit:${p.fit || "cover"};${p.frame ? "clip-path:polygon(12% 0,88% 0,100% 12%,100% 88%,88% 100%,12% 100%,0 88%,0 12%);" : ""}${p.mono ? "filter:grayscale(1) contrast(1.15);" : ""}">`;
    const img = d.firstChild;
    return { el: d, update(lt, dur) { const k = life(lt, 0, dur, 0.4, 0.3); d.style.opacity = k; img.style.transform = `scale(${lerp(1.0, 1.08, clamp(lt / dur))})`; d.style.transform = `translate(-50%,-50%) translateY(${(1 - easeOut(lt / 0.5)) * 120}px)`; } };
  },
  tag(p, parent) { // persistent top label for a stretch of the story (DfpnDOvh): a year that rolls, money that counts
    // up through its units ($2 BILLION -> $4 TRILLION), or a glowing word ("DAY 3")
    const d = box(parent, { left: 0, right: 0, top: (p.y ?? 0.06) * H + "px", textAlign: "center" });
    const kind = p.kind || "text", a = +p.a || 0, b = +(p.b ?? p.a) || 0;
    const col = p.color || (kind === "money" ? "#9dff6e" : kind === "year" ? "#ffffff" : "#ff3b3b");
    d.innerHTML = `<div class="tg-v" style="color:${col}"></div><div class="tg-u" style="color:${col}"></div>`;
    const v = d.querySelector(".tg-v"), un = d.querySelector(".tg-u");
    if (kind !== "year") { v.style.textShadow = un.style.textShadow = `0 0 26px ${col}, 0 0 6px ${col}`; } else v.style.textShadow = "0 6px 22px rgba(0,0,0,.7)";
    const units = [[1e12, "TRILLION"], [1e9, "BILLION"], [1e6, "MILLION"]];
    const num = (x) => (x < 10 ? (Math.round(x * 10) / 10).toString() : Math.round(x).toLocaleString("en-US"));
    let last = null;
    return { el: d, update(lt, dur) { d.style.opacity = life(lt, 0, dur, 0.18, 0.2);
      const u = easeInOut(lt / Math.min(dur * 0.8, p.over || (kind === "year" ? 1.4 : 3.5)));
      let txt = "", unit = "";
      if (kind === "money") { const x = a > 0 && b / a > 8 ? a * Math.pow(b / a, u) : lerp(a, b, u); const k = units.find(([m]) => x >= m);
        txt = "$" + (k ? num(x / k[0]) : num(x)); unit = k ? k[1] : (p.unit || ""); }
      else if (kind === "year") txt = String(Math.round(lerp(a, b, u))) + (u >= 1 ? (p.suffix || "") : "");
      else { txt = p.text || ""; unit = p.unit || ""; }
      if (txt !== last) { last = txt; v.textContent = txt; un.textContent = unit; }
      const pop = easeBack(lt / 0.3); d.style.transform = `scale(${lerp(0.6, 1, pop)})`; } };
  },
  kinetic(p, parent) { // the spoken words themselves, built word by word, keyword big (s5Zf / C-yZl)
    const d = box(parent, { left: "70px", right: "70px", top: (p.y ?? 0.36) * H + "px", textAlign: p.align || "center", perspective: "900px" });
    const ink = p.ink || "#fff";
    const ws = p.words.map((w) => { const s = el("span", "kn-w", d); s.textContent = w.t + " ";
      Object.assign(s.style, { color: w.em ? (p.accent || ACCENT) : ink, fontSize: (w.em ? (p.big || Math.min(190, Math.floor(1300 / Math.max(5, w.t.length)))) : (p.small || 68)) + "px", fontFamily: w.em ? (p.emFont || "MB") : (p.font || "IN"),
        fontWeight: w.em ? 900 : 600, fontStyle: w.em && p.emFont === "PF" ? "italic" : "normal", display: w.em ? "block" : "inline-block", lineHeight: w.em ? 1.0 : 1.25,
        textShadow: p.glow ? `0 0 28px ${w.em ? (p.accent || ACCENT) : "rgba(255,255,255,.35)"}` : "none" }); return { s, w }; });
    return { el: d, update(lt, dur, t0) { const k = 1 - easeIn((lt - (dur - 0.2)) / 0.2); d.style.opacity = k;
      d.style.transform = p.tilt ? `rotateX(${Math.sin(lt * 0.7) * 6}deg) rotateY(${Math.sin(lt * 0.5 + 1) * 10}deg)` : "";
      ws.forEach(({ s, w }) => { const u = clamp((t0 + lt - w.s + 0.04) / 0.16); s.style.opacity = u; s.style.transform = `translateY(${(1 - easeOut(u)) * 26}px) scale(${lerp(w.em ? 1.4 : 1.1, 1, easeBack(u))})`;
        s.style.filter = p.dof && u < 1 ? `blur(${(1 - u) * 8}px)` : "none"; }); } };
  },
};

function mount(list, parent) { return (list || []).map((o) => { const t = TPL[o.type]; if (!t) { console.warn("no template", o.type); return null; }
  const m = t(o.p || {}, parent); m.o = o; m.el.style.visibility = "hidden"; return m; }).filter(Boolean); }
const scenes = mount(SPEC.scenes, scenesEl);
const overlays = mount(SPEC.overlays, ovEl);

// ======================================================================= captions
const CAP = SPEC.captions;
const PAL = ["#d7ff3d", "#ff3355", "#3dff8a", "#ff4fd8", "#3de0ff", "#ffd400"];
let capGroups = [];
if (CAP) capGroups = CAP.groups.map((g) => { const d = el("div", "cap " + CAP.style, capEl); d.style.top = (CAP.y ?? 0.62) * H + "px";
  const spans = g.words.map((w) => { const s = el("span", "cw" + (w.em ? " em" : ""), d); s.textContent = CAP.style === "clean" ? w.t : w.t.toUpperCase(); return { s, w }; });
  d.style.visibility = "hidden"; return { d, g, spans }; });
function drawCaptions(t) {
  if (!CAP) return;
  for (const { d, g, spans } of capGroups) {
    const on = t >= g.s - 0.02 && t < g.e - 0.02; d.style.visibility = on ? "visible" : "hidden"; if (!on) continue;
    const col = CAP.style === "speaker" ? PAL[(g.spk || 0) % PAL.length] : "#fff";
    const ge = easeOut((t - g.s) / 0.12); d.style.transform = `translate(-50%,-50%) scale(${lerp(0.9, 1, ge)})`;
    spans.forEach(({ s, w }, i) => {
      const said = t >= w.s - 0.03, now = said && (i === spans.length - 1 || t < spans[i + 1].w.s);
      const pop = easeBack((t - w.s + 0.03) / 0.14);
      if (CAP.style === "speaker") { s.style.opacity = said ? 1 : 0; s.style.color = w.em ? "#fff" : col; s.style.transform = `scale(${said ? lerp(0.5, 1, pop) : 0.5})`;
        s.style.textShadow = `0 0 18px ${col}, 0 0 4px ${col}, 0 6px 14px rgba(0,0,0,.6)`; }
      else if (CAP.style === "karaoke") { s.style.opacity = 1; s.style.color = now ? ACCENT : "#fff"; s.style.transform = `scale(${now ? 1.08 : 1})`; }
      else if (CAP.style === "bigword") { s.style.display = now ? "inline-block" : "none"; s.style.color = w.em ? ACCENT : "#fff"; s.style.transform = `scale(${lerp(0.7, 1, pop)})`; }
      else if (CAP.style === "boxed") { s.style.display = now ? "inline-block" : "none"; s.classList.toggle("on", !!w.em); s.style.transform = `scale(${lerp(0.8, 1, pop)})`; }
      else if (CAP.style === "neon") { s.style.opacity = said ? 1 : 0.0; s.style.color = w.em ? ACCENT : "#eafff0"; s.style.transform = `translateZ(${said ? 0 : -200}px) scale(${said ? lerp(0.7, 1, pop) : 0.7})`;
        s.style.textShadow = `0 0 22px ${w.em ? ACCENT : "#3dff8a"}`; }
      else if (CAP.style === "tiered") { s.style.opacity = said ? 1 : 0; s.style.transform = `translateY(${said ? (1 - easeOut((t - w.s) / 0.15)) * 20 : 20}px)`; }
      else { s.style.opacity = said ? 1 : 0.0; s.style.transform = `translateY(${said ? (1 - easeOut((t - w.s) / 0.15)) * 14 : 14}px)`; }
    });
  }
}

// ======================================================================= media (videos placed by compose.py)
const clips = (SPEC.clips || []).map((c) => ({ c, v: document.getElementById(c.id), bg: c.bg ? document.getElementById(c.bg) : null }));
function zoomAt(c, lt) {
  let z = c.zoom0 ?? 1.0; z *= 1 + (c.push || 0) * clamp(lt / Math.max(0.1, c.e - c.s));
  for (const p of c.punches || []) { const d = lt - (p - c.s); if (d >= -0.04 && d < 0.9) z *= 1 + (c.punch || 0.14) * (d < 0.06 ? easeOut((d + 0.04) / 0.1) : Math.pow(1 - (d - 0.06) / 0.84, 2)); }
  return z;
}
function drawMedia(t) {
  for (const { c, v, bg } of clips) {
    if (!v) continue; const on = t >= c.s && t < c.e; const lt = t - c.s;
    if (!on) continue;
    let dx = 0, dy = 0;
    for (const p of c.shakes || []) { const d = t - p; if (d >= 0 && d < 0.35) { const a = (1 - d / 0.35) * (c.shakeAmp || 18); dx += noise1(d * 40, 3) * a; dy += noise1(d * 40, 7) * a; } }
    v.style.transform = `translate(${dx}px,${dy}px) scale(${zoomAt(c, lt)})`;
    v.style.transformOrigin = `${(c.fx ?? 0.5) * 100}% ${(c.fy ?? 0.45) * 100}%`;
    if (bg) bg.style.transform = `scale(1.25)`;
  }
}

// ======================================================================= fx: grade, acid, grain, vignette
const GRADES = { warm: "contrast(1.12) saturate(1.22) sepia(0.12) brightness(1.02)", mono: "grayscale(1) contrast(1.2) brightness(1.05)", punchy: "contrast(1.18) saturate(1.35)",
  cool: "contrast(1.1) saturate(0.9) hue-rotate(-8deg) brightness(1.02)", fade: "contrast(0.92) saturate(0.85) brightness(1.06)", none: "" };
const vig = el("div", "layer", fxEl); vig.style.background = `radial-gradient(100% 75% at 50% 45%, transparent 55%, rgba(0,0,0,${T.vignette ?? 0.35}) 100%)`;
const grain = el("div", "layer", fxEl); const grainTiles = [];
if (T.grain) { for (let k = 0; k < 6; k++) { const c = document.createElement("canvas"); c.width = 270; c.height = 480; const g = c.getContext("2d"); const id = g.createImageData(270, 480); const r = rng(k + 11);
  for (let i = 0; i < id.data.length; i += 4) { const v = r() * 255; id.data[i] = id.data[i + 1] = id.data[i + 2] = v; id.data[i + 3] = 255; } g.putImageData(id, 0, 0); grainTiles.push(`url(${c.toDataURL()})`); }
  grain.style.opacity = T.grain; grain.style.mixBlendMode = "overlay"; grain.style.backgroundSize = `${W}px ${H}px`; }
function drawFx(t) {
  let f = GRADES[T.grade || "none"] || "";
  for (const a of SPEC.acid || []) if (t >= a.s && t < a.e) f += ` hue-rotate(${((t - a.s) * 900) % 360}deg) saturate(3.2) contrast(1.7)`;
  stage.style.filter = f;
  if (grainTiles.length) grain.style.backgroundImage = grainTiles[Math.floor(t * 24) % grainTiles.length];
}

// ======================================================================= transitions
const flash = el("div", "layer"); trEl.appendChild(flash); flash.style.background = "#fff";
const iris = el("div", "layer", trEl);
const tear = el("div", "layer", trEl); tear.style.background = "#efe9dc";
const scan = el("div", "layer", trEl); scan.style.background = "repeating-linear-gradient(0deg, rgba(255,255,255,.08) 0 2px, transparent 2px 6px)";
function tornEdge(seed) { const r = rng(seed); const pts = []; for (let y = 0; y <= 100; y += 4) pts.push(`${50 + (r() - 0.5) * 6}% ${y}%`); return pts; }
const tearPts = tornEdge(9);
function drawTransitions(t) {
  let fl = 0, irisR = null, tearX = null, glitch = 0, camX = 0, camS = 1, blur = 0;
  for (const tr of SPEC.trans || []) { const d = t - tr.t;
    if (tr.type === "flash" && d > -0.06 && d < 0.3) fl = Math.max(fl, d < 0 ? 1 + d / 0.06 : 1 - d / 0.3);
    if (tr.type === "whip" && Math.abs(d) < 0.22) { const u = Math.abs(d) / 0.22; camX += (d < 0 ? -1 : 1) * (1 - u) * 260; blur = Math.max(blur, (1 - u) * 24); }
    if (tr.type === "zoom" && Math.abs(d) < 0.25) { const u = 1 - Math.abs(d) / 0.25; camS *= 1 + 0.35 * u * u; blur = Math.max(blur, u * 14); }
    if (tr.type === "iris" && Math.abs(d) < 0.35) irisR = Math.abs(d) / 0.35;
    if (tr.type === "tear" && d > -0.25 && d < 0.25) tearX = d < 0 ? lerp(-60, 0, easeOut((d + 0.25) / 0.25)) : lerp(0, 160, easeIn(d / 0.25));
    if (tr.type === "glitch" && d > -0.05 && d < 0.25) glitch = 1 - Math.abs(d) / 0.25;
  }
  flash.style.opacity = clamp(fl);
  iris.style.background = irisR == null ? "none" : `radial-gradient(circle at 50% 50%, transparent ${irisR * 110}%, #000 ${irisR * 110 + 0.5}%)`;
  if (tearX == null) tear.style.display = "none"; else { tear.style.display = "block";
    tear.style.clipPath = `polygon(${tearPts.map((p) => { const [x, y] = p.split(" "); return `calc(${x} + ${tearX - 50}%) ${y}`; }).join(",")}, -10% 100%, -10% 0%)`; tear.style.boxShadow = "inset 0 0 40px rgba(0,0,0,.25)"; }
  scan.style.opacity = glitch; root.style.transform = glitch ? `translate(${noise1(t * 60, 2) * 22 * glitch}px, 0)` : "";
  const camera = SPEC.camera || {}; const push = 1 + (camera.push || 0) * (t / SPEC.dur); const dr = camera.drift ? noise1(t * 0.3, 4) * 14 : 0;
  stage.style.transform = `translateX(${camX + dr}px) scale(${push * camS})`;
  if (blur) stage.style.filter = (stage.style.filter || "") + ` blur(${blur}px)`;
  stage.style.setProperty("--glitch", glitch);
  if (glitch) stage.style.filter += ` hue-rotate(${noise1(t * 30, 5) * 90 * glitch}deg) saturate(${1 + 2 * glitch})`;
}

// ======================================================================= frame
function show(list, t) { for (const m of list) { const o = m.o; const on = t >= o.s && t < o.e; m.el.style.visibility = on ? "visible" : "hidden"; if (on) m.update(t - o.s, o.e - o.s, o.s); } }
function frame(t) {
  for (const { d, gd } of groundEls) d.style.visibility = t >= gd.s && t < gd.e ? "visible" : "hidden";
  drawMedia(t); show(scenes, t); show(overlays, t); drawCaptions(t); drawFx(t); drawTransitions(t);
}
async function build() {
  await Promise.all(["900 80px MB", "800 80px MX", "600 80px IN", "400 80px AN", "italic 400 80px PF", "400 80px EM", "400 80px BB", "400 80px AR"].map((f) => document.fonts.load(f).catch(() => {})));
  frame(window.__hfThreeTime || 0);
}
window.__hf = window.__hf || {}; window.__hf.buildReady = window.__hf.buildReady || {};
window.__hf.buildReady["shorts-engine"] = build();
window.addEventListener("hf-seek", (e) => frame(e.detail.time));
