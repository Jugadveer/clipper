"""The judge: one cheap multimodal LLM call per batch of candidates.

Same idea as Opus Clip's virality score (Hook / Flow / Value / Trend, 0-99), but it also sees
the stream's own non-verbal cues (screaming, laughter, chat spikes) and a frame strip, because
on a stream the funniest moment is often a scream over a game death with no words worth reading.

The model only CUTS inside the window it is shown and writes copy; it never invents times
outside the window (enforced after the reply).
"""
from __future__ import annotations

import base64
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from .common import WORK, load_env, log, write_json

SYSTEM = """You edit a top streamer-clips channel (IShowSpeed, Kai Cenat, GTA RP) that posts 15-25 Shorts per stream hour. You get ONE moment from a livestream: timestamped transcript (seconds from stream start), reliable non-verbal cue lines like <SCREAMING, VERY LOUD> or <CHAT SPAMMING>, maybe a scout's note with proposed bounds (verify it), and an image of 4 frames across the moment.

Decide if it is a postable Short and cut it:
- keep: true if it works as a standalone Short (virality >= 45). Funny lines, rage, fails, jump scares, chat trolling him, wins, roasts, stories, wholesome or chaotic moments all count; loud and rude is the content. false only for: dead air, plain gameplay with no payoff, donation/sub reading with no reaction, ads, mostly <MUSIC>, needs an hour of context, unsafe (slurs, sexual).
- start/end (absolute seconds, inside the window): start on the SETUP a new viewer needs, never mid-sentence, on a filler, on "and/but/so", or on an unexplained "he/that"; the first 2 s must already be interesting. End right after the payoff and the reaction to it.
- length: SHORT WINS: the most viral streamer clips are 16-33 s. Default 15-35 s; up to 60 s only if the story needs it.
- cold_open: null, or [s, e] of a 1.5-4 s explosive burst (scream/punchline) 8+ s into the clip, replayed first as a teaser when the opening is slow.
- title: <= 60 chars, a reaction phrase that teases without spoiling, ends with 1-2 emojis ("Speed did Kai dirty 😭😭", "Bro became colorblind 💀💀").
- end_caption: 2-5 word punchline shown in quotes on the frozen last frame ("BRO USING FORTNITE TERMS", "HE WAS NOT READY"); not the title.
- end_emoji: match the moment: 😂/🤣 funny, 😱 scare or shock, 😭 pain/rage/sad, 🔥/🥶 skill, W or clutch, 💀 ONLY for a brutal fail or roast
- notes: 0-2 narrator asides burned on screen, [{"t": second, "text": "*TYPES*"}] style ("WAIT FOR IT", "*CHAT VOTED CREEPER*"); [] is fine.
- emphasis: up to 6 words actually spoken that captions should colour.
- hashtags: 4-6 lowercase, no #. description: one sentence + "Streamer: <name>".
- virality 0-99: ordinary moment 30-45, postable 50-70, strong 70-85, banger 85+. scores: hook/flow/value/trend 0-10.

JSON only: {"id": n, "keep": bool, "start": s, "end": s, "title": "", "hook": "", "end_caption": "", "end_emoji": "", "notes": [], "emphasis": [], "hashtags": [], "description": "", "cold_open": null, "scores": {"hook": n, "flow": n, "value": n, "trend": n}, "virality": n, "why": "<one line>"}"""


def _providers(env: dict, model: str) -> list[dict]:
    prov = []
    if env.get("QUANZIL_API_KEY"):
        prov.append({"url": env.get("QUANZIL_BASE_URL", "https://quanzil.com/v1").rstrip("/") + "/chat/completions",
                     "key": env["QUANZIL_API_KEY"], "model": model.split("/")[-1], "name": "quanzil"})
    if env.get("OPENROUTER_API_KEY"):
        prov.append({"url": "https://openrouter.ai/api/v1/chat/completions",
                     "key": env["OPENROUTER_API_KEY"],
                     "model": model if "/" in model else f"google/{model}", "name": "openrouter"})
    return prov


def _img(path: str) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(Path(path).read_bytes()).decode()


def _parse(text: str) -> list[dict]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        doc = json.loads(m.group(0)) if m else {}
    if isinstance(doc, list):
        return doc
    if "clips" in doc:
        return doc["clips"]
    return [doc] if doc else []


# $ per 1M tokens (input, output), OpenRouter list prices: an upper bound (Quanzil bills less)
PRICES = {"gemini-2.5-flash": (0.30, 2.50), "gemini-2.5-flash-lite": (0.10, 0.40)}
LEDGER = WORK / "llm_ledger.jsonl"


class BudgetExhausted(RuntimeError):
    """The spending cap is reached. Callers fall back to the free local picker; a job never fails on it."""


def spent(month: str = "") -> float:
    """Total LLM spend in the ledger; with `month` ("2026-11"), only that month's."""
    if not LEDGER.exists():
        return 0.0
    rows = (json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip())
    return sum(r.get("usd", 0) for r in rows if not month or str(r.get("t", "")).startswith(month))


def _record(model: str, u: dict, tag: str) -> None:
    pi, po = PRICES.get(model.split("/")[-1], (0.30, 2.50))
    usd = u.get("prompt_tokens", 0) / 1e6 * pi + u.get("completion_tokens", 0) / 1e6 * po
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "tag": tag, "model": model,
                            "in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0),
                            "provider": u.get("provider"), "usd": round(usd, 6)}) + "\n")


def call(messages: list, env: dict, model: str, usage: list, tag: str = "") -> str:
    # optional spend cap (unset = no cap): CLIPPER_BUDGET_USD over the ledger's whole life
    budget = float(env.get("CLIPPER_BUDGET_USD", "0") or 0)
    if budget and spent() >= budget:
        raise BudgetExhausted(f"LLM budget reached: ${spent():.4f} of ${budget} (see {LEDGER})")
    last = None
    for p in _providers(env, model):
        try:
            r = requests.post(p["url"], timeout=180, headers={"Authorization": f"Bearer {p['key']}"},
                              json={"model": p["model"], "messages": messages, "temperature": 0.3,
                                    "max_tokens": 8000, "response_format": {"type": "json_object"}})
            r.raise_for_status()
            d = r.json()
            u = {"provider": p["name"], **(d.get("usage") or {})}
            usage.append(u)
            _record(model, u, tag)
            return d["choices"][0]["message"]["content"]
        except Exception as e:                       # transport failure -> next provider
            last = e
            log(f"  judge via {p['name']} failed: {e}")
    raise RuntimeError(f"all LLM providers failed: {last}")


SYSTEM_PODCAST = """You edit a clips channel for podcasts and interviews (Alex Hormozi, Joe Rogan, Raj Shamani, Diary of a CEO style) that posts 15-20 Shorts per episode hour. You get ONE moment: timestamped transcript (seconds from episode start), audio cue lines like <LAUGHTER>, maybe a scout's note with proposed bounds (verify it), and an image of 4 frames.

Decide if it is a postable Short and cut it:
- keep: true if it stands alone and delivers (virality >= 45): a bold or contrarian claim, a concrete lesson/framework, a surprising number, a story with a payoff, a funny exchange, an emotional admission, a quotable line. false for: intros, sponsor reads, small talk, a point that needs earlier context, rambling with no landing.
- start/end (absolute seconds, inside the window): start on the sentence that sets up the point (never mid-sentence, never on "and/so/but/yeah", never on an unexplained "he/that"); end when the point lands (one beat after the punchline). The first sentence must already hook.
- length: 20-50 s default; up to 75 s for a story that needs it.
- cold_open: null, or [s, e] of a 2-5 s killer line 8+ s in, replayed first when the opening is slow.
- title: <= 70 chars, the curiosity/value angle, speaker named when known ("Hormozi on why most businesses stay small", "Rogan can't believe this number 🤯"). 0-1 emoji.
- hook: on-screen HEADLINE for the top of the video, 3-8 words, a bold claim or curiosity gap grounded in the clip ("The $100M mistake founders make"). Required.
- end_caption: "" ; end_emoji: "" ; notes: [] (podcast clips end on the line, not on a meme).
- emphasis: up to 6 spoken words captions should colour (numbers, key nouns, the punchline word).
- hashtags: 4-6 lowercase, no #. description: one sentence + "Speaker: <name>".
- virality 0-99: ordinary 30-45, postable 50-70, strong 70-85, banger 85+. scores: hook/flow/value/trend 0-10.

JSON only: {"id": n, "keep": bool, "start": s, "end": s, "title": "", "hook": "", "end_caption": "", "end_emoji": "", "notes": [], "emphasis": [], "hashtags": [], "description": "", "cold_open": null, "scores": {"hook": n, "flow": n, "value": n, "trend": n}, "virality": n, "why": "<one line>"}"""


def judge(cands: list[dict], stream_title: str, streamer: str, work: Path,
          model: str = "gemini-2.5-flash-lite", batch: int = 1, use_images: bool = True,
          prompt: str = "", genre: str = "stream") -> list[dict]:
    system = SYSTEM_PODCAST if genre == "podcast" else SYSTEM
    env = load_env()
    usage: list = []
    batches = [cands[i:i + batch] for i in range(0, len(cands), batch)]

    def run(group, images: bool = use_images):
        ask = (f"\nTHE USER ONLY WANTS: {prompt}. Reject (keep=false) every candidate that does "
               f"not contain that, however funny it is.\n") if prompt else ""
        content = [{"type": "text", "text": f"Stream title: {stream_title}\nStreamer: {streamer}\n{ask}"}]
        for c in group:
            content.append({"type": "text", "text":
                            f"\n=== candidate id={c['id']} window={c['window'][0]}-{c['window'][1]}s "
                            f"(peak {c['peak']}s) ===\n" + "\n".join(c["context"])})
            if images:
                content.append({"type": "image_url", "image_url": {"url": _img(c["strip"])}})
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": content}]
        try:
            res = _parse(call(msgs, env, model, usage, tag="judge"))
        except Exception as e:
            log(f"  judge failed for {[c['id'] for c in group]}: {e}")
            return []
        if len(group) == 1 and len(res) == 1:
            res[0]["id"] = group[0]["id"]          # one candidate per call: the id is known
        return res

    out: list[dict] = []
    with ThreadPoolExecutor(8) as ex:
        for res in ex.map(run, batches):
            out.extend(res)
    # a candidate the model skipped or a call that failed gets one more try, alone
    seen = {v.get("id") for v in out}
    missing = [[c] for c in cands if c["id"] not in seen]
    if missing:
        log(f"  re-asking {len(missing)} candidates with no verdict")
        with ThreadPoolExecutor(8) as ex:
            for res in ex.map(run, missing):
                out.extend(res)
    # still nothing: the frame strip itself may be what the providers refuse (one candidate failed on
    # both relays twice while its neighbours passed), so the last try reads the text alone
    seen = {v.get("id") for v in out}
    missing = [[c] for c in cands if c["id"] not in seen]
    if missing and use_images:
        log(f"  re-asking {len(missing)} candidates without their frame strip")
        with ThreadPoolExecutor(8) as ex:
            for res in ex.map(lambda g: run(g, images=False), missing):
                out.extend(res)
    by_id = {c["id"]: c for c in cands}
    verdicts = []
    for v in out:
        c = by_id.get(v.get("id"))
        if c is None:
            continue
        a, b = c["window"]
        try:
            s, e = float(v.get("start", a)), float(v.get("end", b))
        except (TypeError, ValueError):
            continue
        s, e = max(a, s), min(b, e)                 # never outside what the model saw
        v.update(start=s, end=e, peak=c["peak"], excitement=c["excitement"], drivers=c["drivers"])
        verdicts.append(v)
    write_json(work / "verdicts.json", {"model": model, "usage": usage, "verdicts": verdicts})
    tok_in = sum(u.get("prompt_tokens", 0) for u in usage)
    tok_out = sum(u.get("completion_tokens", 0) for u in usage)
    log(f"judge: {len(verdicts)} verdicts, {len(usage)} calls, {tok_in} in / {tok_out} out tokens")
    return verdicts


RERANK = """You are the head editor of a streamer-clips channel deciding which clips from ONE stream to post today. Each candidate below was scored in isolation by different reviewers, so their scores are not comparable. Compare them against EACH OTHER and output one calibrated ranking, best first.

Judge: the first 2 seconds (would a scroller stop?), whether it stands alone, the payoff (funny / shocking / hype / wholesome), how re-watchable and shareable it is, and variety (two clips of the same bit: keep the better one, rank the other low).
Spread the scores: the best clip usually lands 85-95, a solid post 65-80, filler under 50.
Titles: when titles repeat the same opening words or the same emoji across clips, give those clips a fresh "title" (<= 60 chars, specific to that clip's moment, varied structure and emoji); otherwise omit "title".
Reply JSON only: {"ranking": [{"id": n, "score": 0-99, "note": "<few words>", "title": "<optional rewrite>"}]} with every id exactly once."""


def rerank(verdicts: list[dict], words_doc: dict, stream_title: str, work: Path,
           model: str = "gemini-2.5-flash-lite", max_n: int = 40) -> list[dict]:
    """Second, comparative pass (text only, one call): batch scores are not comparable."""
    pool = sorted([v for v in verdicts if v.get("keep")], key=lambda v: -v.get("virality", 0))[:max_n]
    if len(pool) < 2:
        return verdicts
    env = load_env()
    usage: list = []
    blocks = []
    for v in pool:
        said = " ".join(w["w"] for seg in words_doc.get("segments", []) for w in seg["words"]
                        if v["start"] <= w["s"] <= v["end"])
        blocks.append(f"id: {v['id']}\nlength: {v['end'] - v['start']:.0f}s\ntitle: {v.get('title')}\n"
                      f"hook: {v.get('hook')}\nwhy: {v.get('why')}\nsignals: {v.get('drivers')}\n"
                      f"transcript: {said[:900]}")
    msgs = [{"role": "system", "content": RERANK},
            {"role": "user", "content": f"Stream: {stream_title}\n\n" + "\n\n".join(blocks)}]
    try:
        text = call(msgs, env, model, usage, tag="rerank")
        doc = json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip())
        ranking = doc.get("ranking", []) if isinstance(doc, dict) else doc
    except Exception as e:
        log(f"rerank failed ({e}); keeping batch scores")
        return verdicts
    by_id = {r.get("id"): r for r in ranking if isinstance(r, dict)}
    for v in pool:
        r = by_id.get(v["id"])
        if r is not None:
            v["batch_virality"] = v.get("virality")
            v["virality"] = int(r.get("score", v.get("virality", 0)))
            v["rank_note"] = r.get("note", "")
            if isinstance(r.get("title"), str) and 8 <= len(r["title"]) <= 80:
                v["title"] = r["title"]
    doc = json.loads((work / "verdicts.json").read_text(encoding="utf-8"))
    doc["verdicts"] = verdicts
    doc["usage"] = doc.get("usage", []) + usage
    doc["reranked"] = True
    write_json(work / "verdicts.json", doc)
    log(f"rerank: {len(pool)} candidates compared in one call")
    return verdicts


EMOJI_MOOD = [(r"scar|jump|warden|panic|ghost|creep|shock|spawn", "😱"),
              (r"rage|cry|pain|suffer|lost his mind|snap|torture|ruin|betray", "😭"),
              (r"(?<![a-z])w(?![a-z])|clutch|win|getaway|goat|skill|(?<![a-z])pro(?![a-z])|insane|beat", "🔥"),
              (r"troll|funny|laugh|joke|bro|dumb|stupid", "😂")]


def diversify_emoji(picks: list[dict]) -> None:
    """flash-lite answers 💀 for almost everything; when one emoji covers more than a third of a
    batch, re-derive it from the clip's own words (title + punchline), then rotate."""
    from collections import Counter
    cnt = Counter(v.get("end_emoji") for v in picks if v.get("end_emoji"))
    if not cnt:
        return
    top, n = cnt.most_common(1)[0]
    if n <= max(2, len(picks) // 3):
        return
    rot = ["😂", "😭", "🔥", "😱", "🤣", "🥶", "💀"]
    for i, v in enumerate(v for v in picks if v.get("end_emoji") == top):
        if i == 0:
            continue                                   # keep one
        text = f"{v.get('title', '')} {v.get('end_caption', '')}".lower()
        new = next((e for pat, e in EMOJI_MOOD if re.search(pat, text)), rot[i % len(rot)])
        v["end_emoji"] = new


def select(verdicts: list[dict], n: int, min_len: float = 10, max_len: float = 75,
           min_virality: int = 50, rerank_floor: int = 0) -> list[dict]:
    # the threshold applies to the judge's absolute score; the order comes from the
    # comparative rerank (which deliberately spreads scores and would starve a threshold)
    keep = [v for v in verdicts if v.get("keep") and min_len <= v["end"] - v["start"] <= max_len
            and v.get("batch_virality", v.get("virality", 0)) >= min_virality
            and ("batch_virality" not in v or v.get("virality", 0) >= rerank_floor)]
    keep.sort(key=lambda v: -v.get("virality", 0))
    chosen: list[dict] = []
    for v in keep:
        ov = any(min(v["end"], c["end"]) - max(v["start"], c["start"]) > 0.3 * (v["end"] - v["start"])
                 for c in chosen)
        if not ov:
            chosen.append(v)
        if len(chosen) >= n:
            break
    diversify_emoji(chosen)
    return chosen
