# LexForge — Moot Court Voice Bench

A real-time, multi-agent **voice** agent for practising **Indian moot court**.
You argue out loud; an AI bench questions you, opposing counsel rebuts you,
authorities are pulled from Indian case law, and your citations are verified —
all in a continuous spoken conversation.

> **Voice layer:** Google **Gemini Live API** (used *only* as ears + mouth —
> streaming STT with native VAD/barge-in, and verbatim TTS).
> **Brain:** any **OpenAI-compatible** API (swappable). Gemini never reasons
> about law; all legal reasoning is done by the multi-agent brain.

---

## Architecture

```
  Browser (WebAudio)                       FastAPI backend
 ┌───────────────────┐   PCM16 16k    ┌──────────────────────────────────────┐
 │ mic ──► worklet ──┼───── ws ──────►│  Gemini Live  (STT session)           │
 │                   │                │     │ input transcription + VAD        │
 │ transcript / UI   │◄───── ws ──────┤     ▼                                  │
 │ agent activity    │   events       │  ORCHESTRATOR  (thin supervisor)       │
 │ authorities       │                │   • intent router (fast model)         │
 │ speaker ◄─ queue ─┼───── ws ◄──────┤   • shared MootCourtState (blackboard) │
 └───────────────────┘   PCM 24k      │   • parallel dispatch + turn-taking    │
                                       │        ├─ Judge      (speaks, streamed)│
                                       │        ├─ Precedent  (Indian case law) │
                                       │        ├─ Counter    (opposing counsel)│
                                       │        ├─ Weakness   (silent analyst)  │
                                       │        └─ Citation   (verify cites)    │
                                       │   • sentence-level TTS pipelining      │
                                       │     ──► Gemini Live (TTS session)      │
                                       └──────────────────────────────────────┘
```

**Why this shape**
- **Supervisor/worker**, not a monolith. Each specialist has a narrow job and a
  short, fast prompt. Background agents (Weakness, Precedent pre-fetch) run in
  **parallel** while the Judge speaks.
- **Sentence-level TTS pipelining**: the brain streams tokens; complete
  sentences are voiced immediately, so audio starts before the full reply is
  generated.
- **Barge-in**: when you start speaking while the bench is talking, playback is
  flushed instantly (Gemini's native VAD drives this).

## Key features
- **Live voice bench** — argue out loud; the judge questions you in real time
  with native VAD and **barge-in** (start talking and the bench yields).
- **Five specialists** — Judge, Precedent, Opposing Counsel, Coach (silent
  weakness analyst), Citation checker.
- **Bench temperament** — switch between a **cold**, **balanced**, or **hot**
  (interventionist) bench, plus an optional custom judge persona.
- **Post-hearing feedback** — "End hearing & get feedback" scores you out of 10
  across five dimensions (articulation, use of authority, responsiveness, legal
  soundness, court craft) with concrete strengths and fixes — like a real bench.
- **Quick actions** — spar with opposing counsel, ask your coach for the key
  weakness, all from one click.
- **Moot timer** and **transcript export** — every session is auto-saved to
  `sessions/` as JSON + Markdown and downloadable from the UI
  (`/api/session/{id}/transcript.md`).
- **Resilient voice** — Gemini Live sessions auto-reconnect with session
  resumption, so a full multi-minute round never drops.

## Indian-context handling
- **Citation normalisation** (`backend/retrieval/citations.py`): repairs STT
  artefacts (`"air nineteen seventy-three sc twenty-three sixty-nine"` → `AIR
  1973 SC 2369`) and parses AIR / SCC / SCR formats.
- **Legal hotwords** (`backend/retrieval/legal_vocab.py`): 200+ Indian legal
  terms bias the STT decoder (`"writ petition"` not `"right petition"`).
- **Short spoken answers**: every agent is constrained to ≤3 spoken sentences.
- **Real authorities — free, no token**: by default it blends **Wikipedia**
  (landmark Indian judgments with clean holdings), **DuckDuckGo** web search
  (surfaces live Indian Kanoon / Supreme Court / eCourts judgment links), and a
  **landmark seed corpus** fallback — so it always returns something.

### Precedent sources & how to make them free
| Source | Free? | Default | Notes |
|---|---|---|---|
| Wikipedia | ✅ free, no key | on | Great for landmark cases; clean summaries. |
| DuckDuckGo web search | ✅ free, no key | on | Finds Indian Kanoon / SCI / eCourts links. |
| Seed corpus (20 landmark cases) | ✅ bundled | on | Offline guarantee. |
| Indian Kanoon **official API** | 💸 paid token | off unless `INDIANKANOON_API_TOKEN` set | Best quality if you have it. |
| Indian Kanoon **public scrape** | ⚠️ `robots.txt` disallows `/search/` | off (`ENABLE_INDIANKANOON_SCRAPE`) | Opt-in only; you decide. |

You get full, free Indian case-law retrieval out of the box — no Indian Kanoon
token required. (Indian Kanoon content still shows up as links via DuckDuckGo.)

## Setup

1. **Install** (Windows PowerShell):
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
   ```
2. **Configure**: copy `.env.example` to `.env` and add your keys:
   ```powershell
   Copy-Item .env.example .env
   ```
   - `GEMINI_API_KEY` — required for voice (STT/TTS). Without it the app runs in
     **text mode** (type submissions, read replies).
   - `OPENAI_API_KEY` — the reasoning brain. Without it a clearly-labelled
     offline stub keeps the pipeline runnable.
   - Precedent retrieval is **free with no key** (Wikipedia + DuckDuckGo + seed
    corpus). `INDIANKANOON_API_TOKEN` is optional (paid) and only improves quality.
3. **Run**:
   ```powershell
   .\run.ps1
   # or:
   .\.venv\Scripts\python.exe -m uvicorn backend.app:app --port 8000
   ```
4. Open **http://127.0.0.1:8000**, fill in the case brief, press
   **“Rise & address the bench”**, and argue.

> Microphone capture needs a secure context. `http://127.0.0.1` is treated as
> secure by browsers, so local use works without HTTPS.

## Swapping the reasoning provider
The brain talks to any OpenAI-compatible endpoint via `OPENAI_BASE_URL` +
`OPENAI_API_KEY`. To use a non-compatible provider, implement `chat()` /
`stream_chat()` in a new `LLMClient` subclass (`backend/agents/base.py`) and
return it from `build_llm()`.

## Plugging your own precedent store
Implement `PrecedentRetriever.search()` (`backend/retrieval/base.py`) for your
ChromaDB/HTTP endpoint and add it to `build_retriever()` ahead of the fallbacks.

## Tests
```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```
14 tests cover citation normalisation (incl. spoken-number repair), the seed
corpus, the orchestrator pipeline, the feedback flow, and bench temperament —
all runnable offline with no API keys.

## Project layout
```
backend/
  app.py            FastAPI + WebSocket voice loop (barge-in, audio bridging)
  config.py         env-driven settings
  state.py          shared MootCourtState (blackboard) + transcript + markdown export
  persistence.py    SessionStore (JSON + Markdown) — the Matter-system seam
  gemini_voice.py   Gemini Live STT + verbatim TTS (auto-reconnect + resumption)
  orchestrator.py   supervisor: intent router, dispatch, sentence streaming, feedback
  agents/           judge, precedent, counter, weakness, citation, feedback + LLM client
  retrieval/        wikipedia, duckduckgo, seed corpus, IK API/scrape, citations, vocab
frontend/
  index.html, styles.css, app.js, pcm-worklet.js
tests/
  test_citations.py, test_pipeline.py
```

## Notes & limitations
- Gemini Live is conversational; "pure TTS" uses a strict verbatim system
  instruction. Occasional drift is possible on very short fragments.
- Retrieval is free by default (Wikipedia + DuckDuckGo + seed corpus). The
  Indian Kanoon **public-page scraper is off by default** because their
  `robots.txt` disallows `/search/`; enable `ENABLE_INDIANKANOON_SCRAPE` only if
  you accept that. Be gentle with request volume on any web source.
- Latency target: keep replies under ~1.5 s. Use a fast `ROUTER_MODEL`, keep
  prompts short, and the sentence pipelining does the rest.
