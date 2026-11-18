// The bot's mailbox: a free Cloudflare Worker + D1 table that is always on.
//
// Telegram delivers every message here the moment it is sent (webhook). The Worker stores it,
// tells the sender at once whether the PC is on, and keeps it until the studio on the PC
// collects it with POST /pull. Telegram on its own keeps a bot's messages for 24 hours; this
// keeps them until they are collected, so the laptop can stay shut for a week.
//
// Free plan: 100,000 requests/day, D1 100,000 row writes/day. The studio pulls every ~6 s while
// the PC is on (about 14,000 requests a day); "last seen" is written at most once a minute.
//
// Secrets (wrangler secret put): BOT_TOKEN, HOOK_SECRET (Telegram sends it on every webhook),
// PULL_SECRET (the studio sends it on every pull). Var: TG_API (tests point it at a fake).

const ONLINE_MS = 90_000;

async function tg(env, method, body) {
  const base = env.TG_API || "https://api.telegram.org";
  try {
    await fetch(`${base}/bot${env.BOT_TOKEN}/${method}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    // a failed courtesy reply must never lose the stored message
  }
}

async function getKV(env, k) {
  const row = await env.DB.prepare("SELECT v FROM kv WHERE k = ?").bind(k).first();
  return row ? row.v : null;
}

async function setKV(env, k, v) {
  await env.DB.prepare("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v")
    .bind(k, v).run();
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
}

async function onTelegram(req, env) {
  if (req.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.HOOK_SECRET) {
    return new Response("forbidden", { status: 403 });
  }
  const update = await req.json();
  await env.DB.prepare("INSERT OR IGNORE INTO updates (id, body, at) VALUES (?, ?, ?)")
    .bind(update.update_id, JSON.stringify(update), Date.now()).run();

  const seen = Number((await getKV(env, "seen")) || 0);
  const online = Date.now() - seen < ONLINE_MS;
  if (online) return new Response("ok");     // the PC answers within seconds itself

  const allowed = JSON.parse((await getKV(env, "allowed")) || "[]");
  const msg = update.message;
  const cq = update.callback_query;
  const chat = String((msg && msg.chat.id) || (cq && cq.message && cq.message.chat.id) || "");
  if (cq) {
    await tg(env, "answerCallbackQuery", {
      callback_query_id: cq.id,
      text: allowed.includes(chat) ? "Saved. The PC is off; this happens when it's back on." : "",
    });
  } else if (msg && allowed.includes(chat)) {
    const waiting = await env.DB.prepare("SELECT COUNT(*) AS n FROM updates").first();
    const ago = seen ? Math.round((Date.now() - seen) / 60000) : null;
    await tg(env, "sendMessage", {
      chat_id: msg.chat.id,
      reply_to_message_id: msg.message_id,
      text: `📥 Got it. The PC is off right now${ago !== null ? ` (last on ${ago < 90 ? ago + " min" : Math.round(ago / 60) + " h"} ago)` : ""}; ` +
            `I'll start the moment it's back. ${waiting.n} message(s) waiting.`,
    });
  }
  return new Response("ok");
}

async function onPull(req, env) {
  if (req.headers.get("Authorization") !== `Bearer ${env.PULL_SECRET}`) {
    return new Response("forbidden", { status: 403 });
  }
  const body = await req.json().catch(() => ({}));
  const ack = (body.ack || []).filter(Number.isInteger);
  if (ack.length) {
    await env.DB.prepare(`DELETE FROM updates WHERE id IN (${ack.map(() => "?").join(",")})`).bind(...ack).run();
  }
  const now = Date.now();
  const seen = Number((await getKV(env, "seen")) || 0);
  if (now - seen > 60_000) await setKV(env, "seen", String(now));
  if (Array.isArray(body.allowed)) {
    const a = JSON.stringify(body.allowed.map(String).sort());
    if (a !== (await getKV(env, "allowed"))) await setKV(env, "allowed", a);
  }
  const rows = await env.DB.prepare("SELECT body FROM updates ORDER BY id LIMIT 50").all();
  return json({ updates: rows.results.map((r) => JSON.parse(r.body)) });
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === "POST" && url.pathname === "/telegram") return onTelegram(req, env);
    if (req.method === "POST" && url.pathname === "/pull") return onPull(req, env);
    if (url.pathname === "/") return new Response("clipper mailbox");
    return new Response("not found", { status: 404 });
  },
};
