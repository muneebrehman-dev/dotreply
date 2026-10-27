"""Build site/index.html (the page shown in the Loom) from results/results.json.

Usage: python3 demo/report.py [--featured persona_id]
"""
import argparse
import html
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCORES = ["human", "brevity", "accuracy", "handling", "brand_safety"]
SCORE_LABELS = {"human": "Sounds human", "brevity": "Short, iMessage-style", "accuracy": "Facts correct",
                "handling": "Solved the real blocker", "brand_safety": "Brand-safe"}
OUTCOME = {"purchased": ("Bought", "good"), "opted_out": ("Opted out", "neutral"),
           "end_conversation": ("Ended politely", "neutral"), "handoff": ("Handed to team", "neutral"),
           "left": ("Left annoyed", "bad"), "ignored": ("Ignored first message", "bad"),
           "no_decision": ("No decision", "neutral"), "said_yes_no_link": ("Wanted to buy, no link sent", "bad"),
           "blocked_link": ("Bought via a link Sendblue blocks", "bad")}
FEATURE_ORDER = ["shipping_cost", "price_haggler", "deadline", "facts_bait", "email_offer", "who_is_this",
                 "bot_check", "size_between", "just_browsing", "stop"]

e = html.escape


def valid_purchase(r):
    if r["outcome"] != "purchased":
        return False
    replied = False
    for t in r["convo"]:
        if t["role"] == "shopper" and t["text"]:
            replied = True
        if t["role"] == "agent" and t.get("action") == "send_checkout_link" and replied:
            return True
    return False


def outcome_of(r):
    if r["outcome"] == "purchased" and not valid_purchase(r):
        return "blocked_link"
    return r["outcome"]


def avg(rows, key):
    vals = [r["judge"].get(key) for r in rows if isinstance(r["judge"].get(key), (int, float))]
    return sum(vals) / len(vals) if vals else 0


def summary(rows):
    return {
        "replied": sum(r["replied"] for r in rows),
        "purchased": sum(valid_purchase(r) for r in rows),
        "lost": sum(r["outcome"] in ("left", "ignored", "said_yes_no_link") for r in rows),
        "violations": sum(len(r["violations"]) for r in rows),
        "discounts": sum(any(t.get("discount_code") for t in r["convo"] if t["role"] == "agent") for r in rows),
        "judge_avg": sum(avg(rows, k) for k in SCORES) / len(SCORES),
        **{k: avg(rows, k) for k in SCORES},
    }


def bubbles(convo):
    out = []
    for t in convo:
        if t["role"] == "agent":
            for m in t["messages"]:
                out.append(f'<div class="b in">{e(m)}</div>')
            if t.get("action") == "send_checkout_link":
                out.append('<div class="b in card"><b>STUDIO</b><span>Your bag is saved</span><i>Continue to checkout ↗</i></div>')
            if t.get("discount_code"):
                out.append(f'<div class="tag">code offered: {e(str(t["discount_code"]))}</div>')
        elif t["text"]:
            out.append(f'<div class="b out">{e(t["text"])}</div>')
        else:
            out.append('<div class="tag">no reply</div>')
    return "\n".join(out)


def outcome_pill(o):
    label, cls = OUTCOME.get(o, (o, "neutral"))
    return f'<span class="pill {cls}">{e(label)}</span>'


def pick_featured(res):
    for pid in FEATURE_ORDER:
        a, b = res.get(f"v1:{pid}"), res.get(f"v2:{pid}")
        if a and b and valid_purchase(b) and (not valid_purchase(a) or a["violations"]):
            return pid
    return FEATURE_ORDER[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--featured")
    args = ap.parse_args()
    res = json.loads((ROOT / "results/results.json").read_text())
    personas = json.loads((ROOT / "data/personas.json").read_text())
    v1 = [r for r in res.values() if r["version"] == "v1"]
    v2 = [r for r in res.values() if r["version"] == "v2"]
    s1, s2, n = summary(v1), summary(v2), len(personas)
    fid = args.featured or pick_featured(res)
    f1, f2 = res.get(f"v1:{fid}"), res.get(f"v2:{fid}")
    flabel = next(p["label"] for p in personas if p["id"] == fid)
    v2_opener = next((r["convo"][0]["messages"][0] for r in v2 if r["persona"] == "size_between"), "")

    rows = []
    for p in personas:
        a, b = res.get(f"v1:{p['id']}"), res.get(f"v2:{p['id']}")
        if not (a and b):
            continue
        va = "<br>".join(e(x) for x in a["violations"]) or "—"
        vb = "<br>".join(e(x) for x in b["violations"]) or "—"
        rows.append(f"<tr><td>{e(p['label'])}</td><td>{outcome_pill(outcome_of(a))}<div class='v'>{va}</div></td>"
                    f"<td>{outcome_pill(outcome_of(b))}<div class='v'>{vb}</div></td></tr>")

    score_rows = "".join(
        f"<tr><td>{SCORE_LABELS[k]}</td><td class='n'>{s1[k]:.1f}</td><td class='n'><b>{s2[k]:.1f}</b></td></tr>" for k in SCORES)

    all_chats = []
    for p in personas:
        a, b = res.get(f"v1:{p['id']}"), res.get(f"v2:{p['id']}")
        if not (a and b):
            continue
        all_chats.append(f"""<details><summary>{e(p['label'])} · v1 {outcome_pill(outcome_of(a))} · v2 {outcome_pill(outcome_of(b))}</summary>
<div class="pair"><div class="phone"><h4>V1 · TYPICAL FIRST PROMPT</h4>{bubbles(a['convo'])}<p class="verdict">Judge: {e(str(a['judge'].get('verdict','')))}</p></div>
<div class="phone"><h4>V2 · IMPROVED</h4>{bubbles(b['convo'])}<p class="verdict">Judge: {e(str(b['judge'].get('verdict','')))}</p></div></div></details>""")

    page = TEMPLATE.format(
        n=n, s1=s1, s2=s2, score_rows=score_rows, rows="".join(rows), flabel=e(flabel),
        f1=bubbles(f1["convo"]) if f1 else "", f2=bubbles(f2["convo"]) if f2 else "",
        f1o=outcome_pill(outcome_of(f1)) if f1 else "", f2o=outcome_pill(outcome_of(f2)) if f2 else "",
        f1v="; ".join(f1["violations"]) if f1 and f1["violations"] else "none",
        v2_opener=e(v2_opener), all_chats="".join(all_chats),
    )
    out = ROOT / "site/index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(page)
    print(f"wrote {out} (featured: {fid})")


TEMPLATE = (ROOT / "demo/template.html").read_text()

if __name__ == "__main__":
    main()
