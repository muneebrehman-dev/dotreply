"""Live side-by-side chat demo: you play the shopper, agent v1 and v2 reply over Groq.

Usage: GROQ_API_KEY=... python3 -I live/server.py   then open http://127.0.0.1:8770
The API key stays on this machine; the page only talks to this local server.
"""
import base64
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "demo"))
import llm  # noqa: E402
import run  # noqa: E402  (store, personas, prompts, agent_turn)
from checks import LINKISH, check  # noqa: E402

# Both phones use the same model so the comparison is fair. gpt-oss-20b answers in under a second.
run.AGENT_MODEL = os.environ.get("LIVE_MODEL", "openai/gpt-oss-20b")
llm.MAX_WAIT = 20

PORT = 8770
CHIPS = json.loads((ROOT / "data/chips.json").read_text())
PRICES = {name: p["price"] for name, p in run.store["products"].items()}
VOICE_STATE = {"groq": None}  # None = untested, True/False after the first try


def scenarios():
    out = []
    for p in run.personas:
        product = next((n for n in PRICES if n in p["cart"]), None)
        out.append({"id": p["id"], "label": p["label"], "name": p["name"], "cart": p["cart"],
                    "abandoned": p["abandoned"], "product": product,
                    "chips": CHIPS.get(p["id"], []) + CHIPS["_any"]})
    return out


def delivery(convo, turn):
    """What Sendblue would do with this agent turn: blocked items get iMessage's 'Not Delivered'."""
    replied = any(t["role"] == "shopper" and t.get("text") for t in convo)
    text_blocked = None
    if not replied:
        joined = " ".join(turn["messages"])
        if len(joined) > 300:
            text_blocked = "Over 300 characters before the shopper replied"
        elif LINKISH.search(joined):
            text_blocked = "Contains a link before the shopper replied"
    link_blocked = "Links are blocked until the shopper replies" if (
        turn["action"] == "send_checkout_link" and not replied) else None
    return {"text_blocked": text_blocked, "link_blocked": link_blocked}


def agent_reply(version, persona_id, convo):
    p = next(x for x in run.personas if x["id"] == persona_id)
    after_stop = any(t["role"] == "shopper" and _is_stop(t.get("text", "")) for t in convo)
    turn = run.agent_turn(version, p, convo, after_stop)
    new_convo = convo + [turn]
    result = {"turn": turn, "delivery": delivery(convo, turn), "violations": check(new_convo)}
    save_chat(persona_id, version, new_convo, result["violations"])
    return result


def save_chat(persona_id, version, convo, violations):
    """Keep the latest state of every live chat so it can be reviewed after a session."""
    path = ROOT / "live/chats.json"
    try:
        chats = json.loads(path.read_text()) if path.exists() else {}
    except ValueError:
        chats = {}
    chats[f"{persona_id}:{version}"] = {"saved": time.strftime("%Y-%m-%d %H:%M:%S"), "convo": convo,
                                        "violations": violations}
    path.write_text(json.dumps(chats, indent=1))


def _is_stop(text):
    from checks import STOP_WORDS
    return bool(STOP_WORDS.search(text or ""))


def tts(text):
    """Voice note audio. Groq Orpheus (human-sounding) once its terms are accepted, else the Mac's own voice."""
    if VOICE_STATE["groq"] is not False:
        body = json.dumps({"model": "canopylabs/orpheus-v1-english", "input": text[:600],
                           "voice": "tara", "response_format": "wav"}).encode()
        req = urllib.request.Request("https://api.groq.com/openai/v1/audio/speech", data=body, headers={
            "Authorization": f"Bearer {os.environ['GROQ_API_KEY']}", "Content-Type": "application/json",
            "User-Agent": "dotreply-demo/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                VOICE_STATE["groq"] = True
                return resp.read(), "groq"
        except urllib.error.HTTPError as e:
            if e.code == 400:
                VOICE_STATE["groq"] = False  # terms not accepted yet; don't retry every time
        except urllib.error.URLError:
            pass
    with tempfile.TemporaryDirectory() as d:
        aiff, wav = pathlib.Path(d, "v.aiff"), pathlib.Path(d, "v.wav")
        subprocess.run(["say", "-v", "Samantha", "-o", str(aiff), text[:600]], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@22050", str(aiff), str(wav)], check=True)
        return wav.read_bytes(), "mac"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s\n" % (fmt % args))

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self.send(200, (ROOT / "live/index.html").read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/scenarios":
            return self.send(200, {"scenarios": scenarios(), "brand": run.store["brand"],
                                   "today": run.store["today"], "prices": PRICES,
                                   "model": run.AGENT_MODEL.split("/")[-1]})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/api/turn":
                if req.get("version") not in ("v1", "v2"):
                    return self.send(400, {"error": "version must be v1 or v2"})
                return self.send(200, agent_reply(req["version"], req["persona"], req.get("convo", [])))
            if self.path == "/api/voice":
                audio, engine = tts(req.get("text", ""))
                return self.send(200, {"engine": engine, "audio": base64.b64encode(audio).decode()})
            self.send(404, {"error": "not found"})
        except Exception as e:  # surface the reason in the page instead of a silent failure
            self.send(500, {"error": str(e)[:300]})


if __name__ == "__main__":
    if not os.environ.get("GROQ_API_KEY"):
        sys.exit("Set GROQ_API_KEY first (e.g. set -a; . ~/study-assistant/.env; set +a)")
    print(f"DotReply live demo on http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
