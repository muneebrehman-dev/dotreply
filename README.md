# DotReply agent review (Loom demo)

A small test harness showing how I'd test and improve an iMessage cart-recovery agent, built for the DotReply Loom.

- `data/store.json`: fictional store "STUDIO" (the example brand on dotreply.ai) with products, policies and discount rules
- `data/personas.json`: 10 simulated shoppers (sizing, shipping cost, deadline, haggler, "who is this?", STOP, "are you a bot?", browsing, facts bait, email code)
- `prompts/`: agent v1 (typical first prompt), agent v2 (improved), shopper simulator, judge
- `demo/run.py`: plays every shopper against both agents, checks hard rules in code (`demo/checks.py`) and scores each chat with an LLM judge
- `demo/report.py`: builds `site/index.html`, the page shown in the Loom

```bash
set -a; . ~/study-assistant/.env; set +a      # GROQ_API_KEY
python3 -I demo/run.py                        # ~30-40 min on the Groq free tier, resumable
python3 -I demo/report.py                     # writes site/index.html
open site/index.html
```

Models (Groq): agent and judge `openai/gpt-oss-120b`, shopper `openai/gpt-oss-20b`. These are simulated results, not real conversion data.

## Live demo (for the Loom)

Double-click `Start Live Demo.command` (or run `python3 -I live/server.py` with `GROQ_API_KEY` set) and open http://127.0.0.1:8770.

- Two iPhones side by side: agent v1 and v2 answer the same shopper live (both on `gpt-oss-20b`, under a second per reply). "v2 only" shows one phone.
- Pick a shopper on the left, press "Send STUDIO's first message", then reply with the quick-reply chips or type freely.
- Messages Sendblue would block (a link or over 300 characters before the shopper replies) show iMessage's red "Not Delivered".
- "Behind the scenes" tags show the blocker the agent diagnosed, its action and the hard-rule checks (`demo/checks.py`).
- Voice notes: v2 replies as an audio message with a transcript. It uses Groq's Orpheus voice once its terms are accepted at console.groq.com (playground, model `canopylabs/orpheus-v1-english`); until then it falls back to the Mac's built-in voice.
- The API key never reaches the page; only the local server calls Groq.

Groq's free tier allows roughly 200k tokens per model per day. A full test run uses most of that for `gpt-oss-120b`, so don't run `demo/run.py` on the day you record. `results/v2_attempt2_with_issues.json` holds the second v2 attempt whose failures led to the `facts_first` rules in `prompts/agent_v2.md`.
