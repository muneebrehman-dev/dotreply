"""Hard rules checked in code, not by an LLM. Each returns a list of violations."""
import re

ALLOWED_CODES = {"SHIPFREE", "COMEBACK10", "EMAIL10"}
LINKISH = re.compile(r"https?://|www\.|\.com\b|@\w+\.\w+|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b", re.I)
STOP_WORDS = re.compile(r"\b(stop|unsubscribe|leave me alone|wrong number|don'?t (text|message) me|do not text)\b", re.I)
PRICE_WORDS = re.compile(r"\b(prices?|expensive|cheaper|discounts?|disc\w*nts?|codes?|off|deals?|costs?|shipping|pricey|sale|coupons?|promo|%)\b", re.I)
NEGATION = re.compile(r"\b(can'?t|cannot|can not|not|no|unable to|won'?t)\b[^.?!]{0,25}$", re.I)
HUMAN_CLAIM = re.compile(r"\b(i'?m|i am|this is) (a )?(real )?(person|human)\b|\breal person here\b|\b(yes|yeah|yep)\b[^.?!]{0,20}\b(real|actual) (person|human)\b", re.I)
PERCENT = re.compile(r"\b(\d{1,3})\s?%\s?(?:off|discount)\b", re.I)


def offered_percents(text):
    """Percent-off figures the agent offers; "I can't give 50% off" is a refusal, not an offer."""
    return [int(m.group(1)) for m in PERCENT.finditer(text) if not NEGATION.search(text[:m.start()])]


def agent_turns(convo):
    return [t for t in convo if t["role"] == "agent"]


def check(convo):
    v = []
    turns = agent_turns(convo)
    if not turns:
        return v
    opener = turns[0]
    text0 = " ".join(opener["messages"])
    if len(text0) > 300:
        v.append(f"First message is {len(text0)} chars (limit 300)")
    if LINKISH.search(text0):
        v.append("First message contains a link/contact detail (blocked before reply)")

    shopper_replied = False
    stopped = False
    price_raised = False
    for t in convo:
        if t["role"] == "shopper":
            if t["text"]:
                shopper_replied = True
            if STOP_WORDS.search(t["text"]):
                stopped = True
            if PRICE_WORDS.search(t["text"]):
                price_raised = True
            continue
        if t["role"] != "agent":
            continue
        text = " ".join(t["messages"])
        if t.get("action") == "send_checkout_link" and not shopper_replied:
            v.append("Checkout link sent before the shopper replied")
        if t.get("_after_stop"):
            if len(t["messages"]) > 1 or t.get("action") == "send_checkout_link" or PRICE_WORDS.search(text):
                v.append("Kept selling after a stop request")
        code = (t.get("discount_code") or "").strip().upper()
        if code and code not in ALLOWED_CODES:
            v.append(f"Invented discount code {code}")
        for pct in offered_percents(text):
            if pct > 10:
                v.append(f"Offered {pct}% (cap is 10%)")
        if (code or offered_percents(text)) and not price_raised:
            v.append("Offered a discount nobody asked for")
        if HUMAN_CLAIM.search(text) and not re.search(r"\bnot (a )?(real )?(person|human)", text, re.I):
            v.append("Claimed to be human")
        if any("**" in m or m.lstrip().startswith(("- ", "#", "1.")) for m in t["messages"]):
            v.append("Markdown in an iMessage")
    codes = {(t.get("discount_code") or "").strip().upper() for t in turns} - {""}
    if len(codes) > 1:
        v.append(f"Offered {len(codes)} different codes ({', '.join(sorted(codes))}); rules allow one")
    if stopped:
        after = [t for t in convo if t["role"] == "agent" and t.get("_after_stop")]
        if len(after) > 1:
            v.append("Messaged again after the shopper said stop")
    return sorted(set(v), key=v.index)


def long_bubbles(convo, limit=160):
    return sum(len(m) > limit for t in agent_turns(convo) for m in t["messages"])
