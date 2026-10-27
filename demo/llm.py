"""Tiny Groq client (stdlib only) with retry on rate limits and JSON parsing."""
import json
import os
import re
import time
import urllib.error
import urllib.request

URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_WAIT = None  # seconds; the live demo sets this so a rate limit fails fast instead of hanging


def chat_json(model, system, user, temperature=0.4, max_tokens=1200, reasoning_effort="low"):
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    if model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = reasoning_effort
    data = json.dumps(body).encode()
    headers = {
        "Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
        "Content-Type": "application/json",
        "User-Agent": "dotreply-demo/1.0",
    }
    for attempt in range(8):
        req = urllib.request.Request(URL, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                out = json.load(resp)
            return parse_json(out["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503):
                wait = float(e.headers.get("retry-after") or 0) or 5 * (attempt + 1)
                if MAX_WAIT is not None and wait > MAX_WAIT:
                    raise RuntimeError(f"Groq free limit reached for {model}, try again in about {int(wait)}s")
                time.sleep(min(wait + 1, 70))
                continue
            if e.code == 400 and attempt < 3:  # occasional json_validate_failed
                time.sleep(2)
                continue
            raise RuntimeError(f"Groq {e.code}: {e.read().decode()[:300]}")
        except (urllib.error.URLError, TimeoutError, ValueError):
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("Groq: too many retries")


def parse_json(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError(f"no JSON in: {text[:200]}")
        return json.loads(m.group(0))
