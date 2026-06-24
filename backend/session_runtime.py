"""Session runtime: single owner of D-LEVM kernel per connection (R6)."""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .effects import (
    DEFAULT_EFFECT_DECLARATIONS,
    EffectContext,
    EffectRunner,
    StagingContext,
)
from .event_queue import EventSequencer, PendingEvent
from .canonical import canonical_copy, digest
from .ledger import ArgumentLedger, ledger_digest_from_state
from .orchestrator import Event, Orchestrator
from .persistence import SessionStore
from .retrieval.legal_vocab import LEGAL_HOTWORDS, LEGAL_VOCAB_VERSION
from .runtime import EventSource, RuntimeState, TransitionEngine, VocabSnapshot
from .state import MootCourtState


OutboundHandler = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class SessionRuntime:
    """Sole owner of transition engine, event queue, and session state."""

    settings: Any
    state: MootCourtState = field(default_factory=MootCourtState)
    mode: str = "court"
    store: SessionStore | None = None
    _orchestrator: Orchestrator | None = None
    _engine: TransitionEngine | None = None
    _sequencer: EventSequencer | None = None
    _effect_runner: EffectRunner | None = None
    _vocab: VocabSnapshot | None = None
    _consumer_task: asyncio.Task | None = None
    _outbound: OutboundHandler | None = None
    _pending_orchestrator_events: list[Event] = field(default_factory=list)
    _deferred_queue: list[PendingEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.state.ledger is None:
            self.state.ledger = ArgumentLedger()
        self.mode = self.state.practice_mode.value
        terms = tuple(sorted(LEGAL_HOTWORDS, key=lambda t: t.lower()))
        self._vocab = VocabSnapshot.from_terms(LEGAL_VOCAB_VERSION, terms)
        registry = {d.name: d for d in DEFAULT_EFFECT_DECLARATIONS}
        self._effect_runner = EffectRunner(registry, self._build_effect_handlers())
        self._engine = TransitionEngine(
            self.state.session_id,
            mode=self.mode,
            effect_graph_hash=self._effect_runner.effect_graph_hash,
        )
        self._sequencer = EventSequencer(self.state.session_id)
        self._sequencer.active_turn_version = self._engine.active_turn_version
        self._orchestrator = Orchestrator(self.settings, vocab=self._vocab)
        self._orchestrator.state = self.state
        self._sync_engine_context()

    @property
    def orchestrator(self) -> Orchestrator:
        assert self._orchestrator is not None
        return self._orchestrator

    @property
    def engine(self) -> TransitionEngine:
        assert self._engine is not None
        return self._engine

    @property
    def vocab(self) -> VocabSnapshot:
        assert self._vocab is not None
        return self._vocab

    def _sync_engine_context(self) -> None:
        assert self._engine is not None
        self._engine.set_context(
            mode=self.mode,
            settings={
                "bench_temperament": self.state.bench_temperament.value,
                "judge_persona": self.state.judge_persona,
                "practice_mode": self.state.practice_mode.value,
                "language": self.state.language.to_dict(),
            },
            ledger_digest=ledger_digest_from_state(self.state),
            resource_limits={
                "turn_index": self.state.turn_index,
                "max_turn_index": getattr(self.settings, "max_turn_index", 500),
            },
            effect_graph_hash=self._effect_runner.effect_graph_hash if self._effect_runner else "",
        )

    def _build_effect_handlers(self) -> dict[str, Any]:
        async def process_user_turn(ctx: EffectContext) -> None:
            text = str(ctx.snapshot.event.payload.get("text", "")).strip()
            if not text:
                return
            self.orchestrator.state = ctx.staging.session_state
            events: list[Event] = []
            async for ev in self.orchestrator.handle_turn(text):
                events.append(ev)
            ctx.staging.session_state = self.orchestrator.state
            self._pending_orchestrator_events = events
            ctx.staging.effect_results["process_user_turn"] = len(events)

        async def persist_session_simple(ctx: EffectContext) -> None:
            if self.store:
                self.store.save(ctx.staging.session_state)

        async def emit_turn_done(ctx: EffectContext) -> None:
            ctx.emit("turn_done", session_id=self.state.session_id)

        async def emit_ui_note(ctx: EffectContext) -> None:
            msg = ctx.snapshot.event.payload.get("message", "recoverable error")
            ctx.emit("note", message=msg)

        async def noop(_: EffectContext) -> None:
            return

        names = {d.name for d in DEFAULT_EFFECT_DECLARATIONS}
        handlers = {name: noop for name in names}
        handlers.update({
            "process_user_turn": process_user_turn,
            "persist_session_simple": persist_session_simple,
            "persist_session": persist_session_simple,
            "emit_turn_done": emit_turn_done,
            "emit_ui_note": emit_ui_note,
        })
        return handlers

    async def _emit_outbound(self, obj: dict[str, Any]) -> None:
        if not self._outbound:
            return
        result = self._outbound(obj)
        if asyncio.iscoroutine(result):
            await result

    async def start(self, outbound: OutboundHandler) -> None:
        self._outbound = outbound
        if self._engine.state == RuntimeState.IDLE:
            self._engine.force_state(RuntimeState.AWAITING_USER)
        self._consumer_task = asyncio.create_task(self._consumer_loop())

    async def close(self) -> None:
        if self._sequencer:
            await self._sequencer.shutdown()
        if self._consumer_task:
            self._consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._consumer_task
        if self._orchestrator:
            await self._orchestrator.close()

    async def enqueue_user_text(
        self,
        text: str,
        *,
        source: EventSource = EventSource.UI,
        turn_id: str | None = None,
    ) -> bool:
        text = text.strip()
        if not text:
            return False
        next_version = self.engine.active_turn_version + 1
        if turn_id is None:
            turn_id = digest({
                "session_id": self.state.session_id,
                "turn_version": next_version,
                "text": text,
            })[:12]
        turn_version = self.engine.begin_turn(turn_id)
        self._sequencer.active_turn_version = turn_version
        pending = PendingEvent(
            type="user_text_submit",
            session_id=self.state.session_id,
            payload={"text": text},
            turn_id=turn_id,
            turn_version=turn_version,
            source=source,
        )
        result = await self._sequencer.assign_and_enqueue(pending)
        return result.accepted

    async def _consumer_loop(self) -> None:
        assert self._sequencer is not None
        while True:
            if self._deferred_queue:
                pending = self._deferred_queue.pop(0)
            else:
                pending = await self._sequencer.dequeue()
            if pending is None:
                break
            await self._process_pending(pending)

    async def _process_pending(self, pending: PendingEvent) -> None:
        assert self._sequencer and self._engine and self._effect_runner
        self._sequencer.mark_inflight_start()
        try:
            event = self._sequencer.to_runtime_event(pending)
            plan_result = self.engine.plan_dispatch(
                event,
                queue_depth=self._sequencer.queue_depth,
                inflight_event_count=self._sequencer.inflight_event_count,
                terms_digest=self.vocab.terms_digest,
            )
            if not plan_result.planned or plan_result.plan is None:
                if plan_result.ignored_reason == "stale_event":
                    return
                if self._outbound and plan_result.ignored_reason:
                    await self._emit_outbound({
                        "type": "note",
                        "message": plan_result.ignored_reason,
                    })
                return

            plan = plan_result.plan
            staging = StagingContext.from_live(self.state)
            ctx = EffectContext(
                staging=staging,
                snapshot=self.engine.build_snapshot(
                    event,
                    queue_depth=self._sequencer.queue_depth,
                    inflight_event_count=self._sequencer.inflight_event_count,
                    terms_digest=self.vocab.terms_digest,
                ),
                turn_version=event.turn_version or self.engine.active_turn_version,
                recursion_depth=1 if pending.is_deferred else 0,
            )
            run_result = await self._effect_runner.run(plan.effects, ctx)
            if not run_result.success:
                self.engine.force_state(RuntimeState.ERROR_RECOVERABLE)
                if self._outbound:
                    await self._emit_outbound({
                        "type": "note",
                        "message": run_result.error or "effect_failed",
                    })
                return

            commit = self.engine.commit_transition(plan, run_result.ordered_effects)
            if commit.committed and run_result.staging:
                self.state = run_result.staging.session_state
                self.orchestrator.state = self.state
                self._sync_engine_context()

            if run_result.staging:
                for emission in run_result.staging.pending_emissions:
                    await self._emit_outbound({"type": emission.kind, **emission.payload})

            await self._flush_orchestrator_events()

            for deferred in run_result.deferred_events:
                dep = PendingEvent(
                    type="deferred_effect_event",
                    session_id=self.state.session_id,
                    payload=deferred.get("payload", {}),
                    turn_id=event.turn_id,
                    turn_version=event.turn_version,
                    source=EventSource.ORCHESTRATOR,
                    is_deferred=True,
                )
                self._deferred_queue.append(dep)

        finally:
            self._sequencer.mark_inflight_end()

    async def _flush_orchestrator_events(self) -> None:
        from .orchestrator import EventType

        if not self._outbound:
            self._pending_orchestrator_events.clear()
            return
        for ev in self._pending_orchestrator_events:
            await self._forward_orchestrator_event(ev)
        self._pending_orchestrator_events.clear()

    async def _forward_orchestrator_event(self, event: Event) -> None:
        from .orchestrator import EventType

        t = event.type
        if t == EventType.INTENT:
            await self._emit_outbound({"type": "intent", **event.data})
        elif t == EventType.AGENT_START:
            await self._emit_outbound({"type": "agent", "status": "start", **event.data})
            await self._emit_outbound({"type": "session_state", "state": "thinking"})
        elif t == EventType.AGENT_DONE:
            await self._emit_outbound({"type": "agent", "status": "done", **event.data})
        elif t == EventType.PRECEDENTS:
            await self._emit_outbound({"type": "precedents", **event.data})
        elif t == EventType.NOTE:
            await self._emit_outbound({"type": "note", **event.data})
        elif t == EventType.FEEDBACK:
            await self._emit_outbound({"type": "feedback", **event.data})
        elif t == EventType.SPEAK_SENTENCE:
            await self._emit_outbound({"type": "session_state", "state": "bench_speaking"})
            await self._emit_outbound({
                "type": "speaking",
                "agent": event.data.get("agent", ""),
                "text": event.data["text"],
            })
        elif t == EventType.SPEAK_DONE:
            await self._emit_outbound({"type": "audio_done", **event.data})
        elif t == EventType.TURN_DONE:
            if self.store:
                self.store.save(self.state)
            ledger = self.state.ledger.to_dict() if self.state.ledger else {}
            await self._emit_outbound({"type": "ledger", "ledger": ledger})
            await self._emit_outbound({"type": "session_state", "state": "idle"})
            await self._emit_outbound({"type": "turn_done"})

