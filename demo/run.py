"""Run every shopper persona against agent v1 and v2, check hard rules, and judge each chat.

Usage: GROQ_API_KEY=... python3 demo/run.py [--only persona_id] [--versions v1,v2]
Writes results/results.json (resumable: finished chats are skipped).
"""
import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from checks import HUMAN_CLAIM, check, long_bubbles  # noqa: E402
from llm import chat_json  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
AGENT_MODEL = "openai/gpt-oss-120b"
SHOPPER_MODEL = "openai/gpt-oss-20b"
JUDGE_MODEL = "openai/gpt-oss-120b"
MAX_AGENT_TURNS = 5
END_STATES = {"purchased", "left", "ignored", "opted_out"}

store = json.loads((ROOT / "data/store.json").read_text())
personas = json.loads((ROOT / "data/personas.json").read_text())
prompt = {p.stem: p.read_text() for p in (ROOT / "prompts").glob("*.md")}
store_text = json.dumps(store, indent=1)


def render(convo, viewer):
    lines = []
    for t in convo:
        if t["role"] == "agent":
            for m in t["messages"]:
                lines.append(f"{store['brand']}: {m}")
            if t.get("action") == "send_checkout_link":
                lines.append(f"{store['brand']}: [CHECKOUT LINK]")
        elif t["text"]:
            lines.append(("You" if viewer == "shopper" else "Shopper") + f": {t['text']}")
    return "\n".join(lines) or "(no messages yet)"


JSON_JUNK = re.compile(r'^[\s"\'\]\[{}:,]*(null|none|action|discount_code|blocker|messages|send_checkout_link|end_conversation|handoff)?[\s"\'\]\[{}:,]*$', re.I)


def clean_bubbles(msgs):
    """Drop fragments of the JSON envelope that a model sometimes leaks into the message list."""
    out = []
    for m in msgs:
        m = re.sub(r'["”]\s*\]\s*$', "", str(m)).strip()
        if m and not JSON_JUNK.match(m):
            out.append(m)
    return out


BOT_QUESTION = re.compile(r"\b(bot|robot|real person|human|a person|ai|automated)\b", re.I)
BAG_TALK = re.compile(r"\b(here'?s your (bag|link|checkout)|your bag is)\b", re.I)


def guardrail_problems(convo, turn):
    """Critical v2 rules checked in code before a reply goes out."""
    problems = []
    text = " ".join(turn["messages"])
    last = next((t["text"] for t in reversed(convo) if t["role"] == "shopper"), "")
    if BOT_QUESTION.search(last) and not re.search(r"\b(AI|assistant)\b", text):
        problems.append(f"The shopper asked if you are a bot or a person. Say plainly: you are {store['brand']}'s AI assistant and a person on the team can step in.")
    if HUMAN_CLAIM.search(text) and not re.search(r"\bnot (a )?(real )?(person|human)", text, re.I):
        problems.append("You claimed to be a person. You are an AI assistant; never say otherwise.")
    if BAG_TALK.search(text) and turn["action"] != "send_checkout_link":
        problems.append("You mentioned the bag/link without sending it. Either use action send_checkout_link or don't mention it.")
    return problems


def agent_turn(version, p, convo, after_stop):
    turn = _agent_turn(version, p, convo, after_stop)
    if version == "v2":
        problems = guardrail_problems(convo, turn)
        if problems:
            fix = "\n".join(f"- {x}" for x in problems)
            turn = _agent_turn(version, p, convo, after_stop, correction=fix)
            turn["guardrail_rewrite"] = problems
    return turn


def _agent_turn(version, p, convo, after_stop, correction=None):
    system = prompt[f"agent_{version}"].format(brand=store["brand"], store=store_text)
    user = (
        f"Shopper: {p['name']}. Cart: {p['cart']}. Abandoned: {p['abandoned']}. Now: {store['today']}.\n\n"
        f"Conversation so far:\n{render(convo, 'agent')}\n\n"
        + ("Write the first message." if not convo else "Write your next reply.")
        + (f"\n\nYour previous draft broke these rules, fix them:\n{correction}" if correction else "")
    )
    out = chat_json(AGENT_MODEL, system, user, temperature=0.4)
    msgs = out.get("messages") or []
    if isinstance(msgs, str):
        msgs = [msgs]
    return {
        "role": "agent",
        "messages": clean_bubbles(msgs),
        "action": (out.get("action") or "none").strip(),
        "discount_code": out.get("discount_code"),
        "blocker": out.get("blocker"),
        "_after_stop": after_stop,
    }


def shopper_turn(p, convo):
    system = prompt["shopper"].format(**p)
    user = f"Conversation so far:\n{render(convo, 'shopper')}\n\nYour reply as JSON."
    out = chat_json(SHOPPER_MODEL, system, user, temperature=0.7, max_tokens=800)
    state = (out.get("state") or "continue").strip()
    return {"role": "shopper", "text": (out.get("reply") or "").strip(), "state": state}


def simulate(version, p):
    convo, outcome, after_stop = [], "no_decision", False
    for _ in range(MAX_AGENT_TURNS):
        a = agent_turn(version, p, convo, after_stop)
        convo.append(a)
        if after_stop or a["action"] in ("end_conversation", "handoff"):
            outcome = outcome if after_stop else a["action"]
            if not after_stop:
                break
        if after_stop:
            # one more shopper turn shows whether the agent kept messaging
            break
        s = shopper_turn(p, convo)
        convo.append(s)
        if s["state"] == "opted_out":
            outcome, after_stop = "opted_out", True
            continue
        if s["state"] in END_STATES:
            outcome = s["state"]
            break
    # the "purchased" state only counts if a checkout link was actually sent
    if outcome == "purchased" and not any(t.get("action") == "send_checkout_link" for t in convo):
        outcome = "said_yes_no_link"
    return convo, outcome


def judge(p, convo):
    user = prompt["judge"].format(store=store_text, label=p["label"], hidden=p["hidden"],
                                  transcript=render(convo, "judge"))
    return chat_json(JUDGE_MODEL, "You are a strict, fair reviewer. Reply only with JSON.", user,
                     temperature=0, max_tokens=2000, reasoning_effort="medium")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--versions", default="v1,v2")
    ap.add_argument("--rejudge", action="store_true", help="re-run checks and judge on saved chats only")
    ap.add_argument("--force", action="store_true", help="re-simulate chats even if already saved")
    args = ap.parse_args()
    out_path = ROOT / "results/results.json"
    out_path.parent.mkdir(exist_ok=True)
    results = json.loads(out_path.read_text()) if out_path.exists() else {}
    if args.rejudge:
        by_id = {p["id"]: p for p in personas}
        for key, r in results.items():
            if r["version"] not in args.versions.split(",") or (args.only and r["persona"] != args.only):
                continue
            r["violations"], r["long_bubbles"] = check(r["convo"]), long_bubbles(r["convo"])
            r["judge"] = judge(by_id[r["persona"]], r["convo"])
            out_path.write_text(json.dumps(results, indent=1))
            print(f"rejudged {key}: {r['violations']} {r['judge'].get('issues')}", flush=True)
        return
    for p in personas:
        if args.only and p["id"] != args.only:
            continue
        for version in args.versions.split(","):
            key = f"{version}:{p['id']}"
            if key in results and not (args.only or args.force):
                continue
            print(f"-> {key}", flush=True)
            convo, outcome = simulate(version, p)
            scores = judge(p, convo)
            results[key] = {
                "version": version, "persona": p["id"], "label": p["label"], "outcome": outcome,
                "replied": any(t["role"] == "shopper" and t["text"] for t in convo),
                "violations": check(convo), "long_bubbles": long_bubbles(convo),
                "judge": scores, "convo": convo,
            }
            out_path.write_text(json.dumps(results, indent=1))
            print(f"   {outcome} | violations={results[key]['violations']} | judge={ {k: scores.get(k) for k in ('human','brevity','accuracy','handling','brand_safety')} }", flush=True)


if __name__ == "__main__":
    main()
