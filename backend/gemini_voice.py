"""Gemini Live API voice layer — used ONLY as ears (STT) and mouth (TTS).

Two independent Live sessions per client:

  * SttSession: streams the user's 16 kHz PCM in, surfaces input transcription
    and native VAD turn boundaries (barge-in). The model is instructed to stay
    silent; we consume only `input_transcription` and turn signals.

  * TtsSession: a verbatim text-to-speech engine. We push the brain's response
    text and stream back 24 kHz PCM. A strict system instruction makes the model
    read the text exactly rather than converse with it.

Resilience: Live sessions are time-limited and the server may send `goAway`.
Both sessions enable **session resumption** and transparently **reconnect** on
disconnect, so a full moot-court round (many minutes) keeps working. Gemini Live
is conversational by nature, so "pure STT/TTS" is achieved with tight configs +
system instructions. This is the standard pattern for this use.
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional

from google import genai
from google.genai import types

from .config import Settings
from .retrieval import stt_biasing_prompt

log = logging.getLogger("gemini_voice")

_STT_SILENT_INSTRUCTION = (
    "You are a silent speech-to-text relay. Do not speak, do not answer, do not "
    "comment. Produce no output. You exist only so the system can read the input "
    "transcription. Context to improve transcription accuracy: "
    + stt_biasing_prompt()
)

_TTS_VERBATIM_INSTRUCTION = (
    "You are a strict text-to-speech engine for an Indian courtroom. Read the "
    "user's message ALOUD VERBATIM, word for word, in clear formal Indian English "
    "with a measured, authoritative judicial tone. Do NOT add greetings, "
    "acknowledgements, commentary, or any words that are not in the message. Do "
    "NOT answer or react to the content; only voice it exactly as written."
)

# Exceptions that mean "the socket dropped; reconnect".
_RECONNECT_ERRORS = (ConnectionError, OSError)


def _make_client(settings: Settings) -> genai.Client:
    if not settings.has_gemini:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. The voice layer (STT/TTS) requires a "
            "Gemini API key. Add it to your .env file."
        )
    return genai.Client(api_key=settings.gemini_api_key,
                        http_options={"api_version": "v1beta"})


def _is_reconnectable(exc: Exception) -> bool:
    if isinstance(exc, _RECONNECT_ERRORS):
        return True
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return ("connectionclosed" in name or "closed" in text or "1011" in text
            or "going away" in text or "timeout" in text
            or "policy violation" in text or "not found" in text
            or "not supported" in text)


def _is_model_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return ("not found" in text or "not supported" in text
            or "policy violation" in text or "invalid" in text and "model" in text)


async def _connect_live(client, settings: Settings, config: types.LiveConnectConfig,
                        label: str, model: Optional[str] = None):
    """Open a Live session, trying model candidates until one works."""
    candidates = [model] if model else settings.gemini_model_candidates
    last_err: Optional[Exception] = None
    for m in candidates:
        if not m:
            continue
        try:
            cm = client.aio.live.connect(model=m, config=config)
            session = await cm.__aenter__()
            log.info("%s connected with %s", label, m)
            return cm, session, m
        except Exception as e:
            last_err = e
            log.warning("%s failed on %s: %s", label, m, e)
    raise RuntimeError(
        f"Could not open Gemini Live ({label}). Tried: {', '.join(candidates)}. "
        f"Last error: {last_err}. Set GEMINI_LIVE_MODEL in .env to a supported Live model."
    )


# ---------------------------------------------------------------------------
# Speech-to-text
# ---------------------------------------------------------------------------
class SttSession:
    """Open a Live session, feed PCM, yield transcription + turn events.

    Emitted events (dicts):
      {"type": "speech_start"}                         # user began speaking
      {"type": "partial", "text": "<accumulated>"}     # interim transcript
      {"type": "final",   "text": "<utterance>"}       # user turn finished
    """

    def __init__(self, settings: Settings, model: Optional[str] = None):
        self.settings = settings
        self.client = _make_client(settings)
        self._model = model
        self._cm = None
        self.session = None
        self._cur = ""           # accumulated transcript for the current turn
        self._speaking = False
        self._resume_handle: Optional[str] = None
        self._closed = False
        self._lock = asyncio.Lock()

    @property
    def _config(self) -> types.LiveConnectConfig:
        # Native-audio Live models require AUDIO modality (not TEXT). We stay
        # silent via system instruction and consume input_transcription only.
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            system_instruction=types.Content(
                parts=[types.Part(text=_STT_SILENT_INSTRUCTION)]),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(),
            ),
            session_resumption=types.SessionResumptionConfig(handle=self._resume_handle),
        )

    async def _connect(self) -> None:
        self._cm, self.session, self._model = await _connect_live(
            self.client, self.settings, self._config, "STT", self._model)

    async def _disconnect(self) -> None:
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._cm = None
            self.session = None

    async def _reconnect(self) -> None:
        async with self._lock:
            await self._disconnect()
            await self._connect()
            log.info("STT session reconnected (model=%s handle=%s)",
                     self._model, bool(self._resume_handle))

    async def __aenter__(self) -> "SttSession":
        await self._connect()
        return self

    async def __aexit__(self, *exc) -> None:
        self._closed = True
        await self._disconnect()

    async def feed_audio(self, pcm16: bytes) -> None:
        """Push a chunk of mono 16-bit little-endian PCM @ 16 kHz."""
        if self.session is None:
            return
        try:
            await self.session.send_realtime_input(
                audio=types.Blob(
                    data=pcm16,
                    mime_type=f"audio/pcm;rate={self.settings.input_sample_rate}"))
        except Exception as e:
            if _is_reconnectable(e) and not self._closed:
                await self._reconnect()
            # drop this chunk; the next will arrive on the fresh session

    async def end_audio(self) -> None:
        if self.session is None:
            return
        try:
            await self.session.send_realtime_input(audio_stream_end=True)
        except Exception:
            pass

    async def events(self) -> AsyncIterator[dict]:
        while not self._closed:
            try:
                async for msg in self.session.receive():
                    # capture resumption handle for transparent reconnects
                    sru = getattr(msg, "session_resumption_update", None)
                    if sru is not None and getattr(sru, "new_handle", None):
                        self._resume_handle = sru.new_handle

                    sc = msg.server_content
                    if sc is None:
                        continue
                    it = getattr(sc, "input_transcription", None)
                    if it is not None and getattr(it, "text", None):
                        if not self._speaking:
                            self._speaking = True
                            yield {"type": "speech_start"}
                        self._cur += it.text
                        yield {"type": "partial", "text": self._cur.strip()}

                    if getattr(sc, "turn_complete", False) or \
                            getattr(sc, "generation_complete", False):
                        final = self._cur.strip()
                        self._cur = ""
                        self._speaking = False
                        if final:
                            yield {"type": "final", "text": final}
                # receive() returned cleanly (server closed); reconnect if not us
                if not self._closed:
                    await self._reconnect()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self._closed:
                    break
                if _is_reconnectable(e):
                    await asyncio.sleep(0.3)
                    await self._reconnect()
                    continue
                log.exception("STT receive error: %s", e)
                await asyncio.sleep(0.5)
                await self._reconnect()


# ---------------------------------------------------------------------------
# Text-to-speech
# ---------------------------------------------------------------------------
class TtsSession:
    """Persistent verbatim TTS session. Call `synthesize(text)` to stream PCM."""

    def __init__(self, settings: Settings, model: Optional[str] = None):
        self.settings = settings
        self.client = _make_client(settings)
        self._model = model
        self._cm = None
        self.session = None
        self._lock = asyncio.Lock()
        self._queue: asyncio.Queue = asyncio.Queue()
        self._reader: Optional[asyncio.Task] = None
        self._resume_handle: Optional[str] = None
        self._closed = False

    @property
    def _config(self) -> types.LiveConnectConfig:
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=types.Content(
                parts=[types.Part(text=_TTS_VERBATIM_INSTRUCTION)]),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.settings.tts_voice))),
            session_resumption=types.SessionResumptionConfig(handle=self._resume_handle),
            temperature=0.0,
        )

    async def _connect(self) -> None:
        self._cm, self.session, self._model = await _connect_live(
            self.client, self.settings, self._config, "TTS", self._model)

    async def _disconnect(self) -> None:
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._cm = None
            self.session = None

    async def __aenter__(self) -> "TtsSession":
        await self._connect()
        self._reader = asyncio.create_task(self._read_loop())
        return self

    async def __aexit__(self, *exc) -> None:
        self._closed = True
        if self._reader:
            self._reader.cancel()
        await self._disconnect()

    async def _read_loop(self) -> None:
        while not self._closed:
            try:
                async for msg in self.session.receive():
                    sru = getattr(msg, "session_resumption_update", None)
                    if sru is not None and getattr(sru, "new_handle", None):
                        self._resume_handle = sru.new_handle
                    if msg.data:                       # raw PCM audio bytes (24 kHz)
                        await self._queue.put(("audio", msg.data))
                    sc = msg.server_content
                    if sc is not None and getattr(sc, "turn_complete", False):
                        await self._queue.put(("end", None))
                if not self._closed:
                    await self._reconnect()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self._closed:
                    break
                if _is_reconnectable(e):
                    await self._queue.put(("end", None))  # unblock any waiter
                    await asyncio.sleep(0.3)
                    await self._reconnect()
                    continue
                log.exception("TTS read error: %s", e)
                await self._queue.put(("end", None))
                await asyncio.sleep(0.5)
                await self._reconnect()

    async def _reconnect(self) -> None:
        await self._disconnect()
        await self._connect()
        log.info("TTS session reconnected (model=%s handle=%s)",
                 self._model, bool(self._resume_handle))

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """Voice a chunk of text; yields 24 kHz PCM byte frames in order."""
        text = (text or "").strip()
        if not text or self.session is None:
            return
        async with self._lock:
            while not self._queue.empty():
                self._queue.get_nowait()
            try:
                await self.session.send_client_content(
                    turns=types.Content(role="user", parts=[types.Part(text=text)]),
                    turn_complete=True)
            except Exception:
                return
            while True:
                try:
                    kind, payload = await asyncio.wait_for(self._queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    return
                if kind == "audio":
                    yield payload
                elif kind == "end":
                    return
                elif kind == "error":
                    return
