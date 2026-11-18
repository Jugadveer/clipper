// Clipper Studio front end. No framework: one render function per page, data from /api.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(url, opts = {}) {
  if (opts.json !== undefined) {
    opts = { ...opts, method: opts.method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(opts.json) };
  }
  const r = await fetch(url, opts);
  if (r.status === 401) { location.href = "/login"; throw new Error("passcode needed"); }
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) throw new Error((data && data.detail) || text || r.statusText);
  return data;
}

let toastTimer;
function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("bad", bad);
  t.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("on"), bad ? 6000 : 3200);
}

// ---------------------------------------------------------------- formatting
const clock = (s) => {
  if (s == null) return "";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`;
};
const ago = (ts) => {
  if (!ts) return "";
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
};
const dayName = (d) => {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const x = new Date(d); x.setHours(0, 0, 0, 0);
  const diff = Math.round((x - today) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  return x.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
};
const hhmm = (iso) => new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
const when = (iso) => (iso ? `${dayName(iso)} ${hhmm(iso)}` : "now");
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

// ---------------------------------------------------------------- state + routing
let OV = null;          // /api/overview
let LIB = [];           // /api/library
let SET = null;         // /api/settings
let FILTER = "new";
const SEL = new Set();
const openLogs = new Set();
const PAGES = ["make", "clips", "posting", "requests", "settings"];
const loaders = { make: renderJobs, clips: loadLib, posting: loadPosting, requests: loadRequests, settings: loadSettings };

function route() {
  const name = PAGES.includes(location.hash.slice(1)) ? location.hash.slice(1) : "make";
  $$(".page").forEach((p) => p.classList.toggle("on", p.id === name));
  $$(".nav a, .tabbar a").forEach((a) => a.setAttribute("aria-current", a.getAttribute("href") === "#" + name ? "page" : "false"));
  if (name !== "clips") clearSel();
  loaders[name]?.();
  window.scrollTo({ top: 0 });
}
window.addEventListener("hashchange", route);
const page = () => $(".page.on")?.id;

// ---------------------------------------------------------------- overview poll
async function poll() {
  try { OV = await api("/api/overview"); } catch { return; }
  const c = OV.counts;
  const running = OV.jobs.filter((j) => j.status === "running" || j.status === "queued").length;
  const vals = { running, new: c.new, queued: OV.queued_uploads, inbox: OV.inbox.length };
  $$("[data-count]").forEach((el) => {
    const v = vals[el.dataset.count];
    el.textContent = v || "";
    el.classList.toggle("hot", el.dataset.count === "running" && v > 0);
  });
  $$("[data-pip]").forEach((el) => { const v = vals[el.dataset.pip]; el.hidden = !v; el.textContent = v > 99 ? "99+" : v; });
  const cur = OV.jobs.find((j) => j.status === "running");
  const bot = OV.bot || {};
  const botLine = bot.mode === "off" ? `<span class="dot"></span>Telegram bot off`
    : bot.error ? `<span class="dot off"></span>Bot: ${esc(bot.error.slice(0, 40))}`
    : `<span class="dot on"></span>Bot ${bot.mode === "mailbox" ? "+ mailbox" : "online"}`;
  $("#now").innerHTML = (cur
    ? `<span class="dot live"></span>Cutting<b title="${esc(cur.progress.title)}">${esc(cur.progress.title)}</b>
       <div class="bar"><i style="width:${Math.round(cur.progress.fraction * 100)}%"></i></div>`
    : `<span class="dot"></span>Idle`) +
    `<div style="margin-top:10px">${botLine}</div>` +
    `<div style="margin-top:4px"><span class="dot ${OV.youtube.connected ? "on" : ""}"></span>YouTube ${OV.youtube.connected ? "connected" : "not connected"}</div>`;
  $("#mstatus").innerHTML = cur ? `<span class="dot live"></span>${Math.round(cur.progress.fraction * 100)}%` : "";
  if (page() === "make") renderJobs();
}

// ---------------------------------------------------------------- make: clips
function chipGroup(container) {
  container.addEventListener("click", (e) => {
    const b = e.target.closest("[data-v]");
    if (!b) return;
    $$("[data-v]", container).forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
    container.dispatchEvent(new Event("change"));
  });
}
$$("#clipform [data-name]").forEach(chipGroup);
const chipVal = (name) => $(`#clipform [data-name="${name}"] [aria-pressed="true"]`)?.dataset.v ?? "";
const setChip = (name, v) => $$(`#clipform [data-name="${name}"] [data-v]`).forEach((x) => x.setAttribute("aria-pressed", x.dataset.v === String(v) ? "true" : "false"));

$$("[data-mode]").forEach((b) => b.addEventListener("click", () => {
  $$("[data-mode]").forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
  $("#clipform").hidden = b.dataset.mode !== "clip";
  $("#gtaform").hidden = b.dataset.mode !== "gta";
}));

// the GTA long-form maker is a separate project; hide its button where it is not installed
api("/api/settings").then((s) => { if (!s.gta_available) $('[data-mode="gta"]').hidden = true; }).catch(() => {});

let probeTimer, probeFor = "", PROBE = null;
function probeSoon() {
  clearTimeout(probeTimer);
  probeTimer = setTimeout(probe, 650);
}
async function probe() {
  const v = $("#src").value.trim();
  const box = $("#probe");
  if (!v || v === probeFor) { if (!v) { box.className = "probe"; PROBE = null; estimate(); } return; }
  probeFor = v;
  if (!/^https?:\/\//i.test(v) && !/^[a-z]:[\\/]/i.test(v)) { box.className = "probe"; return; }
  box.className = "probe on";
  box.innerHTML = `<span class="hint">Reading the link…</span>`;
  try {
    const p = await api("/api/probe?url=" + encodeURIComponent(v));
    if ($("#src").value.trim() !== v) return;
    PROBE = p;
    box.innerHTML = `${p.thumbnail ? `<img src="${esc(p.thumbnail)}" alt="" referrerpolicy="no-referrer">` : ""}
      <div><div class="t">${esc(p.title || v)}</div>
      <div class="m">${[p.uploader, p.duration ? clock(p.duration) : "", p.is_live ? "live now" : ""].filter(Boolean).map(esc).join(" · ")}</div></div>`;
    if (p.uploader && !$("[name=streamer]").value) $("[name=streamer]").placeholder = p.uploader;
  } catch (e) {
    PROBE = null;
    box.className = "probe on err";
    box.textContent = e.message;
  }
  estimate();
}
function estimate() {
  const n = +chipVal("clips") || (PROBE?.auto_clips ?? 0);
  const judge = $("[name=judge]").value;
  const per = judge === "local" ? 0 : judge === "llm-text-only" ? 0.00035 : 0.0007;
  $("#autohint").textContent = PROBE?.auto_clips ? `Auto makes about ${PROBE.auto_clips} from this one (one per three minutes).` : "Auto makes about one per three minutes of video.";
  $("#clipnote").textContent = n
    ? `About ${plural(n, "short")}, ${per ? "≈ $" + (n * per).toFixed(3) + " of AI picking" : "$0"}. Runs on this PC, one job at a time.`
    : "Runs on this PC. Jobs go one at a time; you can close the page.";
}
$("#src").addEventListener("input", probeSoon);
$("#src").addEventListener("paste", () => setTimeout(probe, 30));
$("[data-name=clips]").addEventListener("change", estimate);
$("[name=judge]").addEventListener("change", estimate);
$("#pastebtn").addEventListener("click", async () => {
  try { $("#src").value = (await navigator.clipboard.readText()).trim(); probe(); }
  catch { $("#src").focus(); toast("Press Ctrl+V (or long-press, Paste) in the box"); }
});

$("#clipform").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const source = $("#src").value.trim();
  if (!source) { $("#src").focus(); return toast("Paste a link or a file path first"); }
  const o = {
    source, title: PROBE?.title || "", genre: chipVal("genre"), clips: +chipVal("clips") || 0, style: chipVal("style"),
    prompt: f.prompt.value.trim(), layout: f.layout.value, split_order: f.split_order.value, judge: f.judge.value,
    streamer: f.streamer.value.trim(), min_virality: +f.min_virality.value || 50, jobs: +f.jobs.value || 3,
    no_cold_open: f.no_cold_open.checked, keep_dead_air: f.keep_dead_air.checked, keep_source: f.keep_source.checked,
  };
  $("#clipgo").disabled = true;
  try {
    await api("/api/jobs", { json: o });
    const ahead = OV?.jobs.filter((j) => j.status === "running" || j.status === "queued").length || 0;
    toast(ahead ? `Queued behind ${plural(ahead, "job")}` : "Started");
    $("#src").value = ""; f.prompt.value = ""; probeFor = ""; PROBE = null; $("#probe").className = "probe"; estimate();
    poll();
  } catch (err) { toast(err.message, true); }
  $("#clipgo").disabled = false;
});

// ---------------------------------------------------------------- make: GTA
function dropZone(zone) {
  const input = $("input[type=file]", zone);
  const show = () => {
    const f = input.files[0];
    zone.classList.toggle("has", !!f);
    if (f) zone.innerHTML = `<b>${esc(f.name)}</b><br><span class="hint">${(f.size / 1e6).toFixed(1)} MB · click to change</span>`, zone.prepend(input);
  };
  input.addEventListener("change", show);
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("over"); input.files = e.dataTransfer.files; show(); });
}
dropZone($("#scriptdrop"));
dropZone($("#vodrop"));
$("#gtaform").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  fd.set("no_music", e.target.no_music.checked ? "true" : "false");
  if (!e.target.script.files.length) fd.delete("script");
  if (!e.target.voiceover.files.length) fd.delete("voiceover");
  try {
    const j = await api("/api/gta", { method: "POST", body: fd });
    toast(`Queued: ${j.title}`);
    e.target.reset();
    $$("#gtaform .drop").forEach((z) => z.classList.remove("has"));
    poll();
  } catch (err) { toast(err.message, true); }
});

// ---------------------------------------------------------------- make: queue
const STAGES = { clip: ["Download", "Transcribe", "Find moments", "Render"], gta: ["Voice", "Footage", "Render"] };
const STAGE_OF = { Downloading: 0, Transcribing: 1, "Finding moments": 2, Rendering: 3, Voice: 0, "Picking footage": 1 };

function renderJobs() {
  if (!OV) return;
  const box = $("#jobs");
  const sp = OV.spend;
  $("#spend").textContent = sp ? `AI picker: $${sp.month.toFixed(4)} this month · $${sp.total.toFixed(4)} in total` : "";
  if (!OV.jobs.length) {
    box.innerHTML = `<div class="empty">Nothing yet. Paste a link above, or send one to the Telegram bot.</div>`;
    return;
  }
  box.innerHTML = OV.jobs.map((j) => {
    const p = j.progress, stages = STAGES[j.kind];
    const at = j.status === "done" ? stages.length : (STAGE_OF[p.stage] ?? (j.kind === "gta" && p.stage === "Rendering" ? 2 : 0));
    const inStage = j.kind === "clip" && p.stage === "Rendering" && p.total ? p.rendered / p.total : j.status === "running" ? 0.35 : 0;
    const track = j.status === "running" || j.status === "queued"
      ? `<div class="track">${stages.map((_, i) => `<span class="${i < at ? "done" : i === at ? "now" : ""}">${i === at && j.status === "running" ? `<i style="width:${Math.max(8, inStage * 100)}%"></i>` : ""}</span>`).join("")}</div>
         <div class="track-l">${stages.map((s, i) => `<span class="${i === at && j.status === "running" ? "now" : ""}">${s}${i === at && p.total && j.kind === "clip" && s === "Render" ? ` ${p.rendered}/${p.total}` : ""}</span>`).join("")}</div>`
      : "";
    const who = j.origin?.from === "telegram" ? "from Telegram" : "";
    const took = j.ended && j.started ? `took ${Math.round((j.ended - j.started) / 60)} min` : j.started ? `started ${ago(j.started)}` : `added ${ago(j.created)}`;
    const meta = [j.kind === "gta" ? "GTA video" : (j.options.clips ? plural(j.options.clips, "short") : "auto count"),
      p.length ? `${p.length} long` : "", took, who, j.note || ""].filter(Boolean);
    const acts = [];
    if (j.status === "running" || j.status === "queued") acts.push(`<button class="btn sm" data-act="cancel" data-id="${j.id}">Cancel</button>`);
    if (j.status === "done") acts.push(`<a class="btn sm" href="#clips">See ${j.kind === "gta" ? "video" : "clips"}</a>`);
    if (j.status === "failed" || j.status === "cancelled") acts.push(`<button class="btn sm" data-act="retry" data-id="${j.id}">Try again</button>`);
    acts.push(`<button class="btn sm quiet" data-act="log" data-id="${j.id}">${openLogs.has(j.id) ? "Hide log" : "Log"}</button>`);
    if (j.status !== "running" && j.status !== "queued") acts.push(`<button class="btn sm quiet" data-act="remove" data-id="${j.id}" aria-label="Remove from list"><svg><use href="#i-x"/></svg></button>`);
    return `<div class="job ${j.status}" data-job="${j.id}">
      <div class="t" title="${esc(p.title)}">${esc(p.title || j.title)}</div>
      <div class="acts">${acts.join("")}</div>
      <div class="meta"><span class="tag ${j.status}">${j.status === "running" ? "● " + esc(p.stage) : esc(j.status)}</span>${meta.map((m) => `<span>${esc(m)}</span>`).join("")}</div>
      ${track}
      ${j.status === "failed" && j.error ? `<div class="meta" style="color:var(--bad)">${esc(j.error)}</div>` : ""}
      ${openLogs.has(j.id) ? `<pre data-log="${j.id}">…</pre>` : ""}
    </div>`;
  }).join("");
  openLogs.forEach(async (id) => {
    const pre = $(`[data-log="${id}"]`);
    if (!pre) return;
    const { lines } = await api(`/api/jobs/${id}/log`);
    pre.textContent = lines.slice(-80).join("\n") || "(empty)";
    pre.scrollTop = pre.scrollHeight;
  });
}
$("#jobs").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-act]");
  if (!b) return;
  const id = b.dataset.id;
  try {
    if (b.dataset.act === "cancel") { await api(`/api/jobs/${id}/cancel`, { method: "POST" }); toast("Cancelled"); }
    if (b.dataset.act === "retry") { await api(`/api/jobs/${id}/retry`, { method: "POST" }); toast("Queued again"); }
    if (b.dataset.act === "remove") await api(`/api/jobs/${id}`, { method: "DELETE" });
    if (b.dataset.act === "log") openLogs.has(id) ? openLogs.delete(id) : openLogs.add(id);
  } catch (err) { toast(err.message, true); }
  await poll();
  renderJobs();
});

// ---------------------------------------------------------------- clips
async function loadLib() {
  try { LIB = await api("/api/library"); } catch (e) { return toast(e.message, true); }
  renderLib();
}
const matches = (x) => {
  const q = $("#q").value.trim().toLowerCase();
  return !q || (x.title + " " + x.source + " " + (x.streamer || "")).toLowerCase().includes(q);
};
function renderLib() {
  const counts = { new: 0, queued: 0, posted: 0, skipped: 0, all: 0 };
  LIB.filter(matches).forEach((x) => {
    counts.all++;
    const s = x.state === "uploading" ? "queued" : x.state;
    if (s in counts) counts[s]++;
  });
  $$("#cliptabs button").forEach((b) => {
    b.setAttribute("aria-pressed", b.dataset.f === FILTER ? "true" : "false");
    $("span", b).textContent = counts[b.dataset.f] || "";
  });
  const items = LIB.filter(matches).filter((x) => FILTER === "all" || (FILTER === "queued" ? ["queued", "uploading"].includes(x.state) : x.state === FILTER));
  if (!items.length) {
    $("#lib").innerHTML = `<div class="empty" style="border-radius:14px">${
      FILTER === "new" ? (LIB.length ? "Everything has been reviewed. New clips show up here when a job finishes." : "No clips yet. Start a job on the Make page.")
      : FILTER === "queued" ? "Nothing scheduled." : FILTER === "posted" ? "Nothing posted yet." : "Nothing here."}</div>`;
    return syncSel();
  }
  const groups = new Map();
  items.forEach((x) => {
    const k = x.kind === "long" ? "long" : x.work;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(x);
  });
  $("#lib").innerHTML = [...groups.entries()].map(([k, xs]) => {
    xs.sort((a, b) => (b.virality || 0) - (a.virality || 0));
    const long = k === "long";
    const h = long ? "GTA long-form" : xs[0].source;
    const sub = long ? plural(xs.length, "video") : [xs[0].streamer, plural(xs.length, "short"), ago(Math.max(...xs.map((x) => x.made)))].filter(Boolean).join(" · ");
    return `<div class="group"><div class="group-h"><h2>${esc(h)}</h2><span class="hint">${esc(sub)}</span></div>
      <div class="tiles ${long ? "wide" : ""}">${xs.map(tile).join("")}</div></div>`;
  }).join("");
  syncSel();
}
function tile(x) {
  const st = x.state === "uploading" ? "queued" : x.state;
  const label = st === "queued" ? (x.publish_at ? when(x.publish_at) : "queued") : st === "new" ? "" : st;
  return `<div class="tile ${st} ${SEL.has(x.id) ? "picked" : ""}" data-id="${esc(x.id)}">
    <div class="pic">${x.thumb ? `<img loading="lazy" src="${esc(x.thumb)}" alt="">` : ""}
      ${x.virality ? `<span class="score ${x.virality >= 80 ? "hot" : ""}">${x.virality}</span>` : ""}
      ${x.duration ? `<span class="len">${clock(x.duration)}</span>` : ""}
      ${label ? `<span class="state ${st}">${esc(label)}</span>` : ""}
      <button class="sel" type="button" aria-pressed="${SEL.has(x.id)}" aria-label="Select"><svg viewBox="0 0 24 24"><use href="#i-check"/></svg></button>
    </div>
    <div class="tt">${esc(x.title || "(untitled)")}</div></div>`;
}
$("#lib").addEventListener("click", (e) => {
  const t = e.target.closest(".tile");
  if (!t) return;
  const id = t.dataset.id;
  if (e.target.closest(".sel") || SEL.size) {
    SEL.has(id) ? SEL.delete(id) : SEL.add(id);
    t.classList.toggle("picked", SEL.has(id));
    $(".sel", t).setAttribute("aria-pressed", SEL.has(id));
    return syncSel();
  }
  openDrawer(id);
});
$("#cliptabs").addEventListener("click", (e) => {
  const b = e.target.closest("[data-f]");
  if (!b) return;
  FILTER = b.dataset.f;
  clearSel();
  renderLib();
});
$("#q").addEventListener("input", renderLib);

function syncSel() {
  $("#bulk").classList.toggle("on", SEL.size > 0);
  $("#lib").classList.toggle("selecting", SEL.size > 0);
  $("#bulkn").textContent = `${SEL.size} selected`;
}
function clearSel() { SEL.clear(); $$(".tile.picked").forEach((t) => t.classList.remove("picked")); $$(".tile .sel").forEach((b) => b.setAttribute("aria-pressed", "false")); syncSel(); }
$("#bulkclear").addEventListener("click", clearSel);
$("#bulkpost").addEventListener("click", async () => {
  try {
    const r = await api("/api/schedule", { json: { ids: [...SEL] } });
    toast(r.queued ? `Scheduled ${plural(r.queued, "video")}, first ${when(r.rows[0].publish_at)}` : "Those are already scheduled");
    clearSel(); loadLib(); poll();
  } catch (e) { toast(e.message, true); }
});
$("#bulkskip").addEventListener("click", async () => {
  await api("/api/review", { json: { ids: [...SEL], skip: true } });
  toast(`Skipped ${plural(SEL.size, "clip")}`);
  clearSel(); loadLib(); poll();
});

// ---------------------------------------------------------------- drawer
let CUR = null, genSeed = 1;
async function openDrawer(id) {
  CUR = LIB.find((x) => x.id === id);
  if (!CUR) return;
  const long = CUR.kind === "long";
  $("#dpanel").classList.toggle("long", long);
  $("#dplayer").classList.toggle("landscape", long);
  $("#dvideo").src = CUR.video;
  $("#dfacts").innerHTML = [
    CUR.virality ? `<span>Score <b class="num">${CUR.virality}</b></span>` : "",
    CUR.duration ? `<span>Length <b class="num">${clock(CUR.duration)}</b></span>` : "",
    CUR.style && !long ? `<span>Style <b>${esc(CUR.style)}</b></span>` : "",
    CUR.source_ts ? `<span>From <b class="num">${esc(CUR.source_ts)}</b> of ${esc(CUR.source)}</span>` : "",
  ].filter(Boolean).join("");
  $("#dwhy").textContent = CUR.why || "";
  $("#dwhy").hidden = !CUR.why;
  $("#dtitle").value = CUR.title;
  $("#ddesc").value = CUR.description || "";
  $("#dtags").value = (CUR.tags || []).join(", ");
  $("#dvariants").innerHTML = "";
  $("#ddl").href = "/api/download/" + encodeURIComponent(CUR.id);
  count();
  await drawerButtons();
  $("#drawer").classList.add("on");
  document.body.style.overflow = "hidden";
}
async function drawerButtons() {
  const queued = ["queued", "uploading"].includes(CUR.state);
  const posted = CUR.state === "posted";
  const post = $("#dpost"), skip = $("#dskip");
  post.disabled = posted || CUR.state === "uploading";
  skip.hidden = queued || posted;
  skip.textContent = CUR.state === "skipped" ? "Unskip" : "Skip";
  if (posted) post.textContent = "Posted";
  else if (queued) post.textContent = `Unschedule (${when(CUR.publish_at)})`;
  else {
    post.textContent = "Schedule";
    try { const [slot] = await api("/api/slots?n=1"); post.textContent = `Schedule · ${when(slot)}`; } catch {}
  }
}
function closeDrawer() {
  $("#drawer").classList.remove("on");
  $("#dvideo").pause();
  $("#dvideo").removeAttribute("src");
  document.body.style.overflow = "";
  CUR = null;
}
$$("[data-close]").forEach((el) => el.addEventListener("click", closeDrawer));
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && CUR) closeDrawer(); });
function count() {
  const n = $("#dtitle").value.length;
  $("#dcount").textContent = `${n}/100`;
  $("#dcount").classList.toggle("over", n > 90);
}
$("#dtitle").addEventListener("input", count);
async function saveMeta(quiet) {
  const body = { title: $("#dtitle").value.trim(), description: $("#ddesc").value,
    tags: $("#dtags").value.split(",").map((s) => s.trim()).filter(Boolean) };
  await api("/api/meta/" + encodeURIComponent(CUR.id), { json: body });
  Object.assign(CUR, body);
  if (!quiet) toast("Saved");
}
$("#dsave").addEventListener("click", async () => { await saveMeta(); renderLib(); });
$("#dgen").addEventListener("click", async () => {
  const g = await api(`/api/generate/${encodeURIComponent(CUR.id)}?seed=${genSeed++}`);
  $("#dvariants").innerHTML = g.titles.map((t) => `<button type="button">${esc(t)}</button>`).join("");
  if (!$("#ddesc").value.trim()) $("#ddesc").value = g.description;
  if (!$("#dtags").value.trim()) $("#dtags").value = g.tags.join(", ");
});
$("#dvariants").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b) { $("#dtitle").value = b.textContent; count(); }
});
$("#dpost").addEventListener("click", async () => {
  try {
    if (["queued"].includes(CUR.state)) {
      const q = await api("/api/queue");
      const row = q.find((x) => x.item === CUR.id && x.status === "pending");
      if (row) await api("/api/queue/" + row.qid, { method: "DELETE" });
      toast("Taken off the schedule");
    } else {
      await saveMeta(true);
      const r = await api("/api/schedule", { json: { ids: [CUR.id] } });
      toast(r.queued ? `Scheduled for ${when(r.rows[0].publish_at)}` : "Already scheduled");
    }
    closeDrawer(); loadLib(); poll();
  } catch (e) { toast(e.message, true); }
});
$("#dskip").addEventListener("click", async () => {
  const skip = CUR.state !== "skipped";
  await api("/api/review", { json: { ids: [CUR.id], skip } });
  toast(skip ? "Skipped" : "Back in To review");
  closeDrawer(); loadLib(); poll();
});

// ---------------------------------------------------------------- posting
async function loadPosting() {
  const [q, s, ins, yt] = await Promise.all([api("/api/queue"), api("/api/settings"), api("/api/insights"), api("/api/yt/status")]);
  if (!LIB.length) LIB = await api("/api/library");
  SET = s;
  const thumbs = Object.fromEntries(LIB.map((x) => [x.id, x]));
  $("#ytline").innerHTML = yt.connected
    ? `<span class="dot on"></span>${esc(yt.channel?.title || "YouTube connected")}`
    : `<span class="dot warn"></span>YouTube not connected · <a href="#settings">connect</a>`;
  $("#quota").textContent = "About 6 uploads a day fit YouTube's default quota";

  const up = q.filter((x) => ["pending", "uploading", "failed"].includes(x.status))
    .sort((a, b) => (a.publish_at || "").localeCompare(b.publish_at || ""));
  const byDay = new Map();
  up.forEach((x) => {
    const k = x.status === "failed" ? "Needs attention" : x.publish_at ? dayName(x.publish_at) : "As soon as possible";
    if (!byDay.has(k)) byDay.set(k, []);
    byDay.get(k).push(x);
  });
  const row = (x) => {
    const it = thumbs[x.item] || {};
    const st = x.status === "failed" ? `<span class="tag failed">failed</span>` : x.status === "uploading" ? `<span class="tag uploading">uploading</span>` : "";
    const act = x.status === "failed"
      ? `<button class="btn sm" data-retry="${x.qid}">Retry</button>`
      : x.status === "pending" ? `<button class="btn sm quiet" data-unq="${x.qid}" aria-label="Unschedule"><svg><use href="#i-x"/></svg></button>` : "";
    return `<div class="slot"><time>${x.publish_at ? hhmm(x.publish_at) : "—"}</time>
      ${it.thumb ? `<img class="${x.kind === "long" ? "wide" : ""}" src="${esc(it.thumb)}" alt="">` : "<span></span>"}
      <div style="min-width:0"><div class="t">${esc(x.title)}</div><div class="sub">${st} ${esc(x.error || x.note || (x.privacy !== "scheduled" ? x.privacy : ""))}</div></div>${act}</div>`;
  };
  $("#days").innerHTML = up.length
    ? [...byDay.entries()].map(([d, xs]) => `<div class="day"><h3>${esc(d)}</h3>${xs.map(row).join("")}</div>`).join("")
    : `<div class="empty" style="border-radius:14px">Nothing scheduled. Pick clips on the Clips page and press Schedule.</div>`;

  const done = q.filter((x) => x.status === "uploaded").sort((a, b) => (b.uploaded || 0) - (a.uploaded || 0)).slice(0, 20);
  $("#postedwrap").hidden = !done.length;
  $("#posted").innerHTML = done.map((x) => `<div class="slot"><time>${x.publish_at ? hhmm(x.publish_at) : ""}</time>
    ${thumbs[x.item]?.thumb ? `<img src="${esc(thumbs[x.item].thumb)}" alt="">` : "<span></span>"}
    <div style="min-width:0"><div class="t">${esc(x.title)}</div><div class="sub">${x.publish_at ? "public " + when(x.publish_at) : x.privacy}</div></div>
    <a class="btn sm quiet" href="https://youtu.be/${esc(x.video_id)}" target="_blank" rel="noopener">Open</a></div>`).join("");

  renderTimes();
  $("#autopost").value = String(SET.posting.autopost_min_virality || 0);
  $("#shortstag").checked = !!SET.posting.shorts_tag;

  const dims = [...new Set(ins.rows.map((r) => r.dim))];
  $("#perf").innerHTML = ins.rows.length
    ? dims.map((d) => `<table style="margin-top:10px"><thead><tr><th>${esc(d)}</th><th class="n">Videos</th><th class="n">Avg views</th><th class="n">Like %</th></tr></thead><tbody>${
        ins.rows.filter((r) => r.dim === d).map((r) => `<tr><td>${esc(r.value)}</td><td class="n">${r.videos}</td><td class="n">${r.avg_views.toLocaleString()}</td><td class="n">${r.like_rate}</td></tr>`).join("")}</tbody></table>`).join("")
      + `<p class="hint">Updated ${ago(ins.stats_at)}</p>`
    : `<p class="hint">Once videos are up and have views, this compares styles and lengths on your own channel.</p>`;
  $("#research").innerHTML = ins.research.map(([k, v]) => `<li><b>${esc(k)}</b><span>${esc(v)}</span></li>`).join("");
}
function renderTimes() {
  $("#times").innerHTML = SET.posting.times.map((t) => `<span class="time-chip">${esc(t)}<button data-rm="${esc(t)}" aria-label="Remove ${esc(t)}">×</button></span>`).join("");
}
async function savePosting(patch) {
  SET = await api("/api/settings/posting", { json: { ...SET.posting, ...patch } });
  renderTimes();
}
$("#times").addEventListener("click", (e) => {
  const b = e.target.closest("[data-rm]");
  if (!b) return;
  if (SET.posting.times.length === 1) return toast("Keep at least one posting time");
  savePosting({ times: SET.posting.times.filter((t) => t !== b.dataset.rm) });
});
$("#addtime").addEventListener("click", () => {
  const v = $("#newtime").value;
  if (!v) return $("#newtime").focus();
  savePosting({ times: [...SET.posting.times, v] });
  $("#newtime").value = "";
});
$("#autopost").addEventListener("change", (e) => savePosting({ autopost_min_virality: +e.target.value }).then(() => toast("Saved")));
$("#shortstag").addEventListener("change", (e) => savePosting({ shorts_tag: e.target.checked }));
$("#days").addEventListener("click", async (e) => {
  const u = e.target.closest("[data-unq]"), r = e.target.closest("[data-retry]");
  if (u) { await api("/api/queue/" + u.dataset.unq, { method: "DELETE" }); toast("Unscheduled"); }
  if (r) { await api(`/api/queue/${r.dataset.retry}/retry`, { method: "POST" }); toast("Will try again"); }
  if (u || r) { LIB = await api("/api/library"); loadPosting(); poll(); }
});
$("#refstats").addEventListener("click", async () => {
  try { await api("/api/yt/stats", { method: "POST" }); toast("Stats updated"); loadPosting(); }
  catch (e) { toast(e.message, true); }
});

// ---------------------------------------------------------------- requests
const KIND = { clip: "Clip request", "gta-script": "GTA script", "gta-topic": "GTA topic", pair: "Joined" };
const initials = (n) => (n || "?").split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
async function loadRequests() {
  const d = await api("/api/requests");
  const jobs = Object.fromEntries((OV?.jobs || []).map((j) => [j.id, j]));
  $("#feed").innerHTML = d.requests.length ? d.requests.map((r) => {
    const j = jobs[r.job];
    const st = j ? j.status : r.status;
    return `<div class="req"><span class="avatar">${esc(initials(r.name))}</span>
      <div style="min-width:0"><div class="t">${esc(r.text)}</div><div class="sub">${esc(r.name)} · ${esc(KIND[r.kind] || r.kind)} · ${ago(r.at)}</div></div>
      <span class="tag ${esc(st.split(" ")[0])}">${esc(st)}</span></div>`;
  }).join("") : `<div class="empty" style="border-radius:14px">No messages yet. Once the bot is set up (Settings), what people send it shows here.</div>`;
  $("#inbox").innerHTML = d.inbox.length ? d.inbox.map((r) => `<div class="req"><span class="avatar">${esc(initials(r.name))}</span>
      <div style="min-width:0"><div class="t">${esc(r.topic)}</div><div class="sub">${esc(r.name)} · ${ago(r.created)}${r.note ? " · " + esc(r.note) : ""}</div></div>
      <span class="tag ${r.status === "waiting" ? "queued" : r.status === "rendering" ? "running" : esc(r.status)}">${esc(r.status)}</span></div>`).join("")
    : `<p class="hint">No topics waiting.</p>`;
}
$("#topicform").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("/api/claude-inbox", { json: { topic: e.target.topic.value } }); e.target.reset(); toast("Added for Claude"); loadRequests(); poll(); }
  catch (err) { toast(err.message, true); }
});

// ---------------------------------------------------------------- settings
async function loadSettings() {
  const [s, yt] = await Promise.all([api("/api/settings"), api("/api/yt/status")]);
  SET = s;
  const local = OV?.local ?? true;
  const b = s.bot, st = b.status || {};
  $("#setlede").textContent = local ? "Everything here stays on this PC." : "You're on the phone link. Tokens, passcodes and connections can only be changed on the PC itself.";
  const people = Object.entries(b.allowed);
  const botBox = !b.configured ? `
      <ol class="steps">
        <li>In Telegram, open <b>@BotFather</b> and send <code>/newbot</code>. Pick any name.</li>
        <li>It replies with a token like <code>123456:ABC-…</code>. Paste it here.</li>
      </ol>
      <form class="row" data-form="token"><input class="grow" name="token" type="password" placeholder="Bot token from BotFather" autocomplete="off"><button class="btn go">Connect bot</button></form>`
    : `<div class="status-line">${st.error ? `<span class="dot off"></span>${esc(st.error)}` : `<span class="dot on"></span>@${esc(st.username || "your bot")} is listening (${st.mode === "mailbox" ? "through the always-on mailbox" : "while this PC is on"})`}</div>
      <div><div class="hint">Pairing code, give it to whoever should use the bot. They send it to the bot once.</div>
        <div class="row" style="margin-top:8px"><span class="code">${esc(b.pair_code)}</span><button class="btn sm" data-act="newcode">New code</button></div></div>
      <div><h3>People who can use it</h3><div class="people">${people.length ? people.map(([id, n]) => `<div class="person"><span class="avatar">${esc(initials(n))}</span><span class="grow">${esc(n)}</span><button class="btn sm quiet" data-unpair="${esc(id)}">Remove</button></div>`).join("") : `<span class="hint">Nobody yet.</span>`}</div></div>
      <label class="field" style="max-width:260px"><span>Shorts sent per finished job</span><input type="number" min="1" max="40" value="${b.send_top}" data-send-top><small>The rest come behind a “send the others” button.</small></label>
      <details class="more"><summary>Replace the token</summary><form class="row" data-form="token" style="margin-top:12px"><input class="grow" name="token" type="password" placeholder="New bot token" autocomplete="off"><button class="btn">Save</button></form></details>`;
  const mb = s.mailbox.configured
    ? `<div class="status-line"><span class="dot on"></span>Live at <code>${esc(s.mailbox.url)}</code></div><p class="hint">Messages wait there however long this PC is off, and the sender gets an answer straight away.</p>`
    : `<p class="hint" style="margin-top:0">Without it, Telegram keeps messages for 24 hours, so anything sent while the laptop is shut for longer is lost. The mailbox is a free Cloudflare Worker. Set it up once, from this PC:</p>
       <div class="copy-line"><input readonly value="py mailbox\\deploy.py"><button class="btn sm" data-copy>Copy</button></div>
       <p class="hint">Run it in the clipper folder. It signs in to Cloudflare in your browser (free account), then does the rest. ${b.configured ? "" : "Connect the bot first."}</p>`;
  const tn = s.tunnel;
  const remote = `
      <form class="row" data-form="passcode"><input class="grow" name="passcode" type="password" minlength="6" placeholder="${s.remote.passcode_set ? "Change the passcode" : "Choose a passcode (6+ characters)"}" autocomplete="new-password"><button class="btn">${s.remote.passcode_set ? "Change" : "Set passcode"}</button></form>
      ${!tn.available ? `<p class="hint">The phone link needs <b>cloudflared</b> (free, from Cloudflare) in <code>clipper\\web\\bin\\</code> or on the PATH. Not installed yet.</p>`
        : `<label class="check"><input type="checkbox" data-tunnel ${tn.enabled ? "checked" : ""} ${s.remote.passcode_set ? "" : "disabled"}><span>Open a phone link while this PC is on<small>A random https://….trycloudflare.com address. It changes each time; the bot's /site command always has the current one.</small></span></label>
           ${tn.url ? `<div class="copy-line"><input readonly value="${esc(tn.url)}"><button class="btn sm" data-copy>Copy</button></div>` : tn.enabled ? `<p class="hint">${esc(tn.error || "Starting…")}</p>` : ""}
           ${tn.fixed ? `<p class="hint" style="margin-bottom:4px">Fixed address (Vercel, same passcode):</p><div class="copy-line"><input readonly value="${esc(tn.fixed)}"><button class="btn sm" data-copy>Copy</button></div>`
             : tn.fixed_error ? `<p class="hint">Vercel address not updated: ${esc(tn.fixed_error)}</p>` : ""}`}`;
  const ytBox = yt.connected
    ? `<div class="status-line"><span class="dot on"></span>${esc(yt.channel?.title || "Connected")}${yt.channel ? ` · ${Number(yt.channel.subscribers).toLocaleString()} subscribers` : ""}</div><a class="btn sm" href="/api/yt/connect">Connect a different channel</a>`
    : yt.configured ? `<a class="btn go" href="/api/yt/connect">Connect your channel</a>`
    : `<ol class="steps">
        <li>Open <b>console.cloud.google.com</b>, create a project and enable <b>YouTube Data API v3</b>.</li>
        <li>OAuth consent screen: External; add your Google account as a test user.</li>
        <li>Credentials → OAuth client ID → <b>Web application</b>, redirect URI <code>http://localhost:8765/oauth2callback</code>.</li>
        <li>Download the JSON and save it as <code>web\\client_secret.json</code> inside the clipper folder, then reload this page.</li></ol>`;
  const sel = (name, opts, v) => `<select data-default="${name}">${opts.map(([k, l]) => `<option value="${k}" ${k === v ? "selected" : ""}>${l}</option>`).join("")}</select>`;
  const sec = (title, desc, body, onlyLocal) => (onlyLocal && !local) ? "" : `<div class="setting"><div><h2>${title}</h2><p>${desc}</p></div><div class="stack">${body}</div></div>`;
  $("#setbody").innerHTML =
    sec("Telegram bot", "Your friend sends a link, a script or a voiceover from their phone; the shorts come back in the chat with Post and Skip buttons.", botBox, true) +
    sec("Always-on mailbox", "Keeps Telegram messages safe while this PC is off, and answers right away.", mb, true) +
    sec("Phone link", "Open this website from a phone. Anyone not on this PC needs the passcode.", remote, true) +
    sec("YouTube", "Uploads use YouTube's own scheduling, so videos go public on time even with this PC off.", ytBox, true) +
    sec("Defaults", "Used for Telegram requests, and preselected on the Make page.",
      `<div class="grid2"><label class="field"><span>Caption style</span>${sel("style", [["mix", "Mix (changes per clip)"], ["viral", "Viral"], ["pop", "Pop"], ["karaoke", "Karaoke"], ["hormozi", "Hormozi"], ["clean", "Clean"]], s.clip_defaults.style)}</label>
       <label class="field"><span>Moment picker</span>${sel("judge", [["llm", "AI, looks at frames"], ["llm-text-only", "AI, transcript only"], ["local", "Free, signals only"]], s.clip_defaults.judge)}</label></div>`, false);
}
$("#setbody").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    if (f.dataset.form === "token") { await api("/api/settings/bot", { json: { token: f.token.value } }); toast("Bot connected"); }
    if (f.dataset.form === "passcode") { await api("/api/settings/remote", { json: { passcode: f.passcode.value } }); toast("Passcode saved; phone sessions signed out"); }
    loadSettings(); poll();
  } catch (err) { toast(err.message, true); }
});
$("#setbody").addEventListener("click", async (e) => {
  const t = e.target;
  try {
    if (t.closest("[data-act=newcode]")) { await api("/api/settings/bot", { json: { new_code: true } }); toast("New pairing code"); loadSettings(); }
    if (t.closest("[data-unpair]")) { await api("/api/settings/bot", { json: { unpair: t.closest("[data-unpair]").dataset.unpair } }); toast("Removed"); loadSettings(); }
    if (t.closest("[data-copy]")) { const i = t.closest(".copy-line").querySelector("input"); await navigator.clipboard.writeText(i.value).catch(() => i.select()); toast("Copied"); }
  } catch (err) { toast(err.message, true); }
});
$("#setbody").addEventListener("change", async (e) => {
  const t = e.target;
  try {
    if (t.matches("[data-send-top]")) { await api("/api/settings/bot", { json: { send_top: +t.value } }); toast("Saved"); }
    if (t.matches("[data-default]")) { await api("/api/settings/clip_defaults", { json: { [t.dataset.default]: t.value } }); toast("Saved"); applyDefaults(); }
    if (t.matches("[data-tunnel]")) { t.disabled = true; await api("/api/settings/remote", { json: { tunnel: t.checked } }); loadSettings(); }
  } catch (err) { toast(err.message, true); loadSettings(); }
});

async function applyDefaults() {
  const o = await api("/api/options");
  setChip("style", o.defaults.style || "mix");
  $("[name=judge]").value = o.defaults.judge || "llm";
  estimate();
}

// ---------------------------------------------------------------- start
applyDefaults();
poll().then(route);
setInterval(poll, 4000);
setInterval(() => { if (page() === "clips" && !CUR && !SEL.size) loadLib(); }, 30000);
