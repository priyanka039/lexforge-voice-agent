"""FastAPI app: serves the web client and runs the realtime voice loop.

One WebSocket per client carries everything (JSON frames):

  client -> server : audio (base64 PCM16 @16k), audio_end, text, brief, ping
  server -> client : ready, transcript, intent, agent, speaking, audio
                     (base64 PCM @24k), audio_done, interrupt, precedents,
                     note, turn_done, error

The server bridges three async pieces per connection:
  1. inbound pump   : WS -> Gemini STT (audio) / orchestrator (typed text)
  2. STT events     : transcription + VAD turn boundaries -> trigger the brain
  3. brain pipeline : orchestrator events -> Gemini TTS -> WS audio out
Barge-in: a fresh user `speech_start` while we are speaking cancels playback.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import os
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .gemini_voice import SttSession, TtsSession
from .orchestrator import EventType
from .persistence import SessionStore
from .runtime import EventSource
from .session_runtime import SessionRuntime
from .state import BenchTemperament, PracticeMode

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.join(os.path.dirname(_HERE), "frontend")

app = FastAPI(title="LexForge Moot Court Voice Agent")
store = SessionStore()


@app.get("/health")
async def health() -> dict:
    s = get_settings()
    return {
        "ok": True,
        "voice_enabled": s.has_gemini,
        "brain": "openai" if s.has_openai else "stub",
        "live_model": s.gemini_live_model,
        "indiankanoon_api": bool(s.indiankanoon_api_token),
        "retrieval": {
            "wikipedia": s.enable_wikipedia,
            "web_search": s.enable_web_search,
            "indiankanoon_scrape": s.enable_indiankanoon_scrape,
            "indiankanoon_api": bool(s.indiankanoon_api_token),
            "seed_corpus": True,
        },
    }


@app.get("/api/session/{session_id}")
async def get_session(session_id: str) -> JSONResponse:
    data = store.load_json(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="session not found")
    return JSONResponse(data)


@app.get("/api/session/{session_id}/transcript.md")
async def get_transcript(session_id: str) -> FileResponse:
    path = store.markdown_path(session_id)
    if path is None:
        raise HTTPException(status_code=404, detail="transcript not found")
    return FileResponse(path, media_type="text/markdown",
                        filename=f"moot-{session_id}.md")


class Connection:
    """Owns all state and tasks for a single browser session."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.settings = get_settings()
        self.store = SessionStore()
        self.runtime = SessionRuntime(self.settings, store=self.store)
        self.stt: Optional[SttSession] = None
        self.tts: Optional[TtsSession] = None
        self.voice_enabled = self.settings.has_gemini
        self.speak_task: Optional[asyncio.Task] = None
        self._send_lock = asyncio.Lock()

    async def send(self, obj: dict) -> None:
        async with self._send_lock:
            with contextlib.suppress(Exception):
                await self.ws.send_json(obj)

    # ---- lifecycle ----
    async def open(self) -> None:
        if self.voice_enabled:
            try:
                self.stt = await SttSession(self.settings).__aenter__()
                self.tts = await TtsSession(self.settings, model=self.stt._model).__aenter__()
            except Exception as e:
                self.voice_enabled = False
                short = str(e).split(";")[0][:180]
                await self.send({
                    "type": "voice_status",
                    "enabled": False,
                    "error": short,
                    "message": (
                        "Voice unavailable — use the text box below or fix GEMINI_LIVE_MODEL in .env. "
                        f"({short})"
                    ),
                })
        await self.runtime.start(self._outbound)
        await self.send({
            "type": "ready",
            "voice_enabled": self.voice_enabled,
            "brain": "openai" if self.settings.has_openai else "stub",
            "session_id": self.runtime.state.session_id,
            "sample_rate_in": self.settings.input_sample_rate,
            "sample_rate_out": self.settings.output_sample_rate,
            "bench_temperament": self.runtime.state.bench_temperament.value,
            "live_model": getattr(self.stt, "_model", None) or self.settings.gemini_live_model,
            "runtime": "d-levm-v1",
        })

    async def close(self) -> None:
        await self._cancel_speak()
        with contextlib.suppress(Exception):
            if self.stt:
                await self.stt.__aexit__(None, None, None)
        with contextlib.suppress(Exception):
            if self.tts:
                await self.tts.__aexit__(None, None, None)
        await self.runtime.close()

    async def _outbound(self, obj: dict) -> None:
        if obj.get("type") == "speaking":
            await self.send(obj)
            await self._speak_sentence(obj.get("agent", ""), obj.get("text", ""))
            return
        await self.send(obj)

    # ---- inbound: browser -> server ----
    async def inbound_pump(self) -> None:
        while True:
            msg = await self.ws.receive_json()
            mtype = msg.get("type")
            if mtype == "audio" and self.stt:
                pcm = base64.b64decode(msg["data"])
                await self.stt.feed_audio(pcm)
            elif mtype == "audio_end" and self.stt:
                await self.stt.end_audio()
            elif mtype == "text":
                text = (msg.get("text") or "").strip()
                if text:
                    self._start_turn(text, source=EventSource.UI)
            elif mtype == "brief":
                self._apply_brief(msg.get("brief", {}))
                await self.send({"type": "note", "message": "Case brief updated."})
            elif mtype == "settings":
                self._apply_settings(msg.get("settings", {}))
                await self.send({"type": "note", "message": "Bench settings updated."})
            elif mtype == "feedback":
                self._start_feedback()
            elif mtype == "ping":
                await self.send({"type": "pong"})

    # ---- STT events -> trigger the brain ----
    async def stt_pump(self) -> None:
        if not self.stt:
            return
        async for ev in self.stt.events():
            etype = ev["type"]
            if etype == "speech_start":
                # Barge-in: if we're mid-reply, stop talking immediately.
                if self.speak_task and not self.speak_task.done():
                    await self.send({"type": "interrupt"})
                    await self._cancel_speak()
            elif etype == "partial":
                await self.send({"type": "transcript", "role": "advocate",
                                 "text": ev["text"], "final": False})
            elif etype == "final":
                await self.send({"type": "transcript", "role": "advocate",
                                 "text": ev["text"], "final": True})
                self._start_turn(ev["text"])

    # ---- brain pipeline + TTS ----
    def _start_turn(self, text: str, *, source: EventSource = EventSource.VOICE) -> None:
        if self.speak_task and not self.speak_task.done():
            self.speak_task.cancel()
        self.speak_task = asyncio.create_task(self._run_turn(text, source=source))

    async def _run_turn(self, text: str, *, source: EventSource) -> None:
        try:
            await self.send({"type": "transcript", "role": "advocate",
                             "text": text, "final": True})
            accepted = await self.runtime.enqueue_user_text(text, source=source)
            if not accepted:
                await self.send({"type": "note", "message": "Turn queue full — try again."})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self.send({"type": "error", "message": str(e)})

    def _start_feedback(self) -> None:
        if self.speak_task and not self.speak_task.done():
            self.speak_task.cancel()
        self.speak_task = asyncio.create_task(self._run_feedback())

    async def _run_feedback(self) -> None:
        try:
            async for event in self.runtime.orchestrator.run_feedback():
                await self._forward_event(event)
            self.store.save(self.runtime.state)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self.send({"type": "error", "message": str(e)})

    async def _forward_event(self, event) -> None:
        t = event.type
        if t == EventType.INTENT:
            await self.send({"type": "intent", **event.data})
        elif t == EventType.AGENT_START:
            await self.send({"type": "agent", "status": "start", **event.data})
            await self.send({"type": "session_state", "state": "thinking"})
        elif t == EventType.AGENT_DONE:
            await self.send({"type": "agent", "status": "done", **event.data})
        elif t == EventType.PRECEDENTS:
            await self.send({"type": "precedents", **event.data})
        elif t == EventType.NOTE:
            await self.send({"type": "note", **event.data})
        elif t == EventType.FEEDBACK:
            await self.send({"type": "feedback", **event.data})
        elif t == EventType.SPEAK_SENTENCE:
            await self.send({"type": "session_state", "state": "bench_speaking"})
            await self._speak_sentence(event.data.get("agent", ""), event.data["text"])
        elif t == EventType.SPEAK_DONE:
            await self.send({"type": "audio_done", **event.data})
        elif t == EventType.TURN_DONE:
            self.store.save(self.runtime.state)
            await self.send({"type": "session_state", "state": "idle"})
            await self.send({"type": "turn_done"})

    async def _speak_sentence(self, agent: str, text: str) -> None:
        if not (self.voice_enabled and self.tts):
            return
        try:
            async for pcm in self.tts.synthesize(text):
                await self.send({"type": "audio",
                                 "data": base64.b64encode(pcm).decode("ascii")})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self.send({"type": "note", "level": "warn",
                             "message": f"TTS error: {e}"})

    async def _cancel_speak(self) -> None:
        if self.speak_task and not self.speak_task.done():
            self.speak_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.speak_task
        self.speak_task = None

    def _apply_brief(self, brief: dict) -> None:
        b = self.runtime.state.brief
        for key in ("title", "court", "appellant", "respondent", "user_side", "facts"):
            if key in brief and brief[key]:
                setattr(b, key, brief[key])
        if isinstance(brief.get("issues"), list):
            b.issues = [str(x) for x in brief["issues"]]

    def _apply_settings(self, settings: dict) -> None:
        temp = settings.get("bench_temperament")
        if temp:
            try:
                self.runtime.state.bench_temperament = BenchTemperament(temp)
            except ValueError:
                pass
        if "judge_persona" in settings:
            self.runtime.state.judge_persona = str(settings["judge_persona"] or "")
        mode = settings.get("practice_mode")
        if mode:
            try:
                self.runtime.state.practice_mode = PracticeMode(mode)
                self.runtime.mode = self.runtime.state.practice_mode.value
            except ValueError:
                pass
        lang = settings.get("language")
        if isinstance(lang, dict):
            from .state import LanguageSettings
            self.runtime.state.language = LanguageSettings.from_dict(lang)
        self.runtime._sync_engine_context()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    conn = Connection(ws)
    await conn.open()
    tasks = [
        asyncio.create_task(conn.inbound_pump()),
        asyncio.create_task(conn.stt_pump()),
    ]
    try:
        await asyncio.gather(*tasks)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        with contextlib.suppress(Exception):
            await conn.send({"type": "error", "message": str(e)})
    finally:
        for t in tasks:
            t.cancel()
        await conn.close()


# Serve the frontend (mounted last so /ws and /health take precedence).
if os.path.isdir(_FRONTEND):
    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(os.path.join(_FRONTEND, "index.html"))

    app.mount("/", StaticFiles(directory=_FRONTEND), name="static")


def main() -> None:
    import uvicorn
    s = get_settings()
    uvicorn.run("backend.app:app", host=s.host, port=s.port, reload=False)


if __name__ == "__main__":
    main()
