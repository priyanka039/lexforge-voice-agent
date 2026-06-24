# LexForge — Moot Court Voice Bench

A real-time, multi-agent **voice** agent for practising **Indian moot court**.
You argue out loud; an AI bench questions you, opposing counsel rebuts you,
authorities are pulled from Indian case law, and your citations are verified —
all in a continuous spoken conversation.

> **Voice layer:** Google **Gemini Live API** (used *only* as ears + mouth —
> streaming STT with native VAD/barge-in, and verbatim TTS).
> **Brain:** any **OpenAI-compatible** API (swappable). Gemini never reasons
> about law; all legal reasoning is done by the multi-agent brain.
> **Runtime:** **D-LEVM v1** — a deterministic execution kernel that routes
> every user turn through a single-writer event queue, guarded FSM transitions,
> and a staged effect DAG before state is committed.

---

## Architecture

```
  Browser (WebAudio)                       FastAPI backend
 ┌───────────────────┐   PCM16 16k    ┌──────────────────────────────────────┐
 │ mic ──► worklet ──┼───── ws ──────►│  Gemini Live  (STT session)           │
 │                   │                │     │ input transcription + VAD        │
 │ transcript / UI   │◄───── ws ──────┤     ▼                                  │
 │ ledger / agents   │   events       │  SESSION RUNTIME  (D-LEVM v1)          │
 │ speaker ◄─ queue ─┼───── ws ◄──────┤   • EventSequencer (single writer)   │
 └───────────────────┘   PCM 24k      │   • TransitionEngine (guarded FSM)     │
                                       │   • EffectRunner (staged DAG)          │
                                       │        └─ process_user_turn            │
                                       │              └─ ORCHESTRATOR           │
                                       │   • ArgumentLedger + confidence      │
                                       │        ├─ Judge      (speaks)          │
                                       │        ├─ Precedent  (Indian case law) │
                                       │        ├─ Counter    (opposing counsel)│
                                       │        ├─ Advisor    (silent analyst)  │
                                       │        └─ Citation   (verify cites)    │
                                       │   • sentence-level TTS pipelining      │
                                       │     ──► Gemini Live (TTS session)      │
                                       └──────────────────────────────────────┘
```

**Why this shape**
- **Supervisor/worker**, not a monolith. Each specialist has a narrow job and a
  short, fast prompt. Background agents (Advisor, Precedent pre-fetch) run in
  **parallel** while the Judge speaks.
- **D-LEVM kernel** keeps turn processing deterministic: ingress envelopes carry
  dual hashes, effects run on a staging copy, and only successful commits update
  live session state. The WebSocket `ready` message reports `"runtime": "d-levm-v1"`.
- **Sentence-level TTS pipelining**: the brain streams tokens; complete
  sentences are voiced immediately, so audio starts before the full reply is
  generated.
- **Barge-in**: when you start speaking while the bench is talking, playback is
  flushed instantly (Gemini's native VAD drives this).

### D-LEVM v1 (deterministic execution kernel)

| Module | Role |
|---|---|
| `canonical.py` | NFKC normalization, stable JSON digests, float rounding |
| `ingress.py` | Dual-hash ingress envelopes (`raw` + `normalized`) |
| `event_queue.py` | Single-writer sequencer, bounded queue, stale-event discard |
| `guards.py` | Six-step guard pipeline before every transition |
| `runtime.py` | Two-phase `plan_dispatch` → `commit_transition` FSM |
| `effects.py` | Staged effect DAG with rollback and recursion lock |
| `session_runtime.py` | Sole owner of engine, queue, and `MootCourtState` |
| `ledger.py` | Argument ledger, compression, pure confidence recompute |
| `replay.py` | Offline E2E replay harness for determinism verification |

Legal vocabulary is **frozen at session start** and included in transition
snapshots. Retrieval scoring is deterministic when the frozen vocab is supplied.

## Key features
- **Live voice bench** — argue out loud; the judge questions you in real time
  with native VAD and **barge-in** (start talking and the bench yields).
- **Five specialists** — Judge, Precedent, Opposing Counsel, Advisor (silent
  weakness analyst; legacy alias `WeaknessAgent`), Citation checker.
- **Practice modes** — **Court** (full bench), **Spar** (opposing counsel focus),
  or **Coach** (guided feedback).
- **Language settings** — UI language, spoken language, and optional custom hint
  applied before TTS and on-screen output.
- **Argument ledger** — structured claims, counters, authorities, weaknesses,
  and per-issue confidence; visible in the **Ledger** tab after each turn.
- **Bench temperament** — switch between a **cold**, **balanced**, or **hot**
  (interventionist) bench, plus an optional custom judge persona.
- **Post-hearing feedback** — "End hearing & get feedback" scores you out of 10
  across five dimensions (articulation, use of authority, responsiveness, legal
  soundness, court craft) with concrete strengths and fixes — like a real bench.
- **Quick actions** — spar with opposing counsel, ask your advisor for the key
  weakness, all from one click.
- **Moot timer** and **transcript export** — every session is auto-saved to
  `sessions/` as canonical JSON + Markdown and downloadable from the UI
  (`/api/session/{id}/transcript.md`).
- **Resilient voice** — Gemini Live sessions auto-reconnect with session
  resumption, so a full multi-minute round never drops.

## Indian-context handling
- **Citation normalisation** (`backend/retrieval/citations.py`): repairs STT
  artefacts (`"air nineteen seventy-three sc twenty-three sixty-nine"` → `AIR
  1973 SC 2369`) and parses AIR / SCC / SCR formats.
- **Legal hotwords** (`backend/retrieval/legal_vocab.py`): 200+ Indian legal
  terms bias the STT decoder (`"writ petition"` not `"right petition"`).
- **Deterministic retrieval** (`backend/retrieval/deterministic.py`): tiered
  scoring with frozen vocab for reproducible authority ranking.
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
   - `SESSIONS_DIR` — optional override for where session JSON/Markdown is stored.
   - `MAX_TURN_INDEX` — optional cap on turns per session (default `500`).
3. **Run**:
   ```powershell
   .\run.ps1
   # or:
   .\.venv\Scripts\python.exe -m uvicorn backend.app:app --port 8000
   ```
4. Open **http://127.0.0.1:8000**, fill in the case brief, press
   **“Rise & address the bench”**, and argue.

5. **Smoke check** (optional):
   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests/ -q
   Invoke-RestMethod http://127.0.0.1:8000/health
   ```
   `/health` returns `ok`, voice/brain status, and active retrieval sources.

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
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

**50 tests**, all runnable offline with no API keys:

| Suite | Covers |
|---|---|
| `test_canonical` | Stable encoding, digests, float edge cases |
| `test_ingress` | Dual-hash ingress envelopes |
| `test_event_queue` | Single-writer sequencing, backpressure |
| `test_effects` | Staged DAG, rollback, recursion lock |
| `test_runtime` | FSM plan/commit, transition digests, guards |
| `test_ledger`, `test_compression` | Ledger, confidence recompute, compression |
| `test_session_runtime` | End-to-end turn through D-LEVM kernel |
| `test_retrieval_scoring` | Deterministic authority scoring |
| `test_agents_schema`, `test_language` | Structured outputs, language middleware |
| `test_e2e_determinism` | Fixed event-log replay → identical digests |
| `test_concurrency_boundary` | State commits only via `SessionRuntime` |
| `test_citations`, `test_pipeline` | Citation repair, orchestrator smoke |

E2E fixtures live in `tests/fixtures/e2e/` (`event_log.json`, `golden_snapshot.json`).

## Project layout
```
backend/
  app.py              FastAPI + WebSocket voice loop (routes via SessionRuntime)
  config.py           env-driven settings (SESSIONS_DIR, MAX_TURN_INDEX, …)
  state.py            MootCourtState, practice mode, language settings
  persistence.py      SessionStore — canonical JSON + Markdown export
  session_runtime.py  D-LEVM session owner (queue + engine + effects)
  runtime.py          TransitionEngine FSM (plan_dispatch / commit_transition)
  event_queue.py      EventSequencer (R1 single writer)
  ingress.py          ExternalInputEnvelope (R7 dual hash)
  guards.py           Pre-transition guard pipeline
  effects.py          Staged effect DAG executor
  canonical.py        Canonical encoding + digests
  ledger.py           ArgumentLedger, compression, confidence
  language.py         Spoken/UI language middleware
  replay.py           Offline determinism replay harness
  gemini_voice.py     Gemini Live STT + verbatim TTS
  orchestrator.py     intent router, dispatch, sentence streaming, feedback
  agents/             judge, precedent, counter, advisor, citation, feedback
  retrieval/          wikipedia, duckduckgo, seed corpus, deterministic scoring
frontend/
  index.html, styles.css, app.js, pcm-worklet.js
tests/
  test_*.py           kernel, agents, retrieval, e2e determinism
  fixtures/e2e/       replay event log + golden digests
sessions/             auto-saved session JSON + Markdown (gitignored)
```

## Notes & limitations
- Gemini Live is conversational; "pure TTS" uses a strict verbatim system
  instruction. Occasional drift is possible on very short fragments.
- Retrieval is free by default (Wikipedia + DuckDuckGo + seed corpus). The
  Indian Kanoon **public-page scraper is off by default** because their
  `robots.txt` disallows `/search/`; enable `ENABLE_INDIANKANOON_SCRAPE` only if
  you accept that. Be gentle with request volume on any web source.
- The orchestrator still runs a full turn inside the `process_user_turn` effect;
  finer-grained FSM decomposition is a future refinement.
- Latency target: keep replies under ~1.5 s. Use a fast `ROUTER_MODEL`, keep
  prompts short, and the sentence pipelining does the rest.
