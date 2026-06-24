"""Deterministic transition runtime for the D-LEVM kernel."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .canonical import canonical_copy, digest
from .guards import GuardResult


class RuntimeState(str, Enum):
    IDLE = "idle"
    BRIEF_READY = "brief_ready"
    RECORDING_USER_TURN = "recording_user_turn"
    USER_TURN_BUFFERED = "user_turn_buffered"
    CLASSIFYING_TURN = "classifying_turn"
    RETRIEVING_AUTHORITIES = "retrieving_authorities"
    RESOLVING_AUTHORITIES = "resolving_authorities"
    SELECTING_SPEAKER = "selecting_speaker"
    GENERATING_AGENT_OUTPUT = "generating_agent_output"
    VALIDATING_LANGUAGE = "validating_language"
    RENDERING_RESPONSE = "rendering_response"
    UPDATING_LEDGER = "updating_ledger"
    COMPRESSING_LEDGER = "compressing_ledger"
    AWAITING_USER = "awaiting_user"
    GENERATING_FINAL_JUDGMENT = "generating_final_judgment"
    ERROR_RECOVERABLE = "error_recoverable"


class EventSource(str, Enum):
    UI = "ui"
    VOICE = "voice"
    ORCHESTRATOR = "orchestrator"
    RETRIEVAL = "retrieval"
    AGENT = "agent"
    SYSTEM = "system"


@dataclass(frozen=True)
class VocabSnapshot:
    version: str
    terms: tuple[str, ...]
    terms_digest: str

    @classmethod
    def from_terms(cls, version: str, terms: tuple[str, ...]) -> VocabSnapshot:
        td = digest({"version": version, "terms": list(terms)})
        return cls(version=version, terms=terms, terms_digest=td)


@dataclass(frozen=True)
class RuntimeEvent:
    type: str
    session_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    turn_id: str | None = None
    turn_version: int | None = None
    source: EventSource = EventSource.ORCHESTRATOR
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)
    event_seq: int = 0
    ingest_seq: int = 0
    normalized_payload_hash: str | None = None
    raw_payload_hash: str | None = None
    is_deferred: bool = False

    def normalized_payload_hash_value(self) -> str:
        if self.normalized_payload_hash:
            return self.normalized_payload_hash
        return digest(self.payload)


@dataclass(frozen=True)
class DispatchSnapshot:
    current_state: RuntimeState
    session_id: str
    active_turn_id: str | None
    active_turn_version: int
    mode: str
    settings: dict[str, Any]
    ledger_digest: str
    resource_limits: dict[str, Any]
    event: RuntimeEvent
    queue_depth: int = 0
    inflight_event_count: int = 0
    terms_digest: str = ""
    effect_graph_hash: str = ""

    def snapshot_hash(self) -> str:
        return digest({
            "current_state": self.current_state.value,
            "session_id": self.session_id,
            "active_turn_id": self.active_turn_id,
            "active_turn_version": self.active_turn_version,
            "mode": self.mode,
            "settings": self.settings,
            "ledger_digest": self.ledger_digest,
            "resource_limits": self.resource_limits,
            "queue_depth": self.queue_depth,
            "inflight_event_count": self.inflight_event_count,
            "terms_digest": self.terms_digest,
            "effect_graph_hash": self.effect_graph_hash,
            "event": {
                "type": self.event.type,
                "normalized_payload_hash": self.event.normalized_payload_hash_value(),
                "turn_id": self.event.turn_id,
                "turn_version": self.event.turn_version,
                "event_seq": self.event.event_seq,
            },
        })


Guard = Callable[[DispatchSnapshot], GuardResult]


@dataclass(frozen=True)
class TransitionRule:
    from_state: RuntimeState | str
    event_type: str
    to_state: RuntimeState
    effects: tuple[str, ...] = ()
    guards: tuple[str, ...] = ()
    priority: int = 0
    name: str = ""


@dataclass
class TransitionRecord:
    session_id: str
    turn_id: str | None
    turn_version: int | None
    from_state: str
    event: str
    to_state: str
    effects: list[str]
    snapshot_hash: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "turn_version": self.turn_version,
            "from_state": self.from_state,
            "event": self.event,
            "to_state": self.to_state,
            "effects": list(self.effects),
            "snapshot_hash": self.snapshot_hash,
            "ts": self.ts,
        }


@dataclass(frozen=True)
class TransitionPlan:
    from_state: RuntimeState
    to_state: RuntimeState
    effects: tuple[str, ...]
    rule_name: str
    snapshot_hash: str
    event: RuntimeEvent


@dataclass
class PlanResult:
    planned: bool
    plan: TransitionPlan | None = None
    ignored_reason: str | None = None
    stale: bool = False


@dataclass
class CommitResult:
    committed: bool
    state: RuntimeState
    record: TransitionRecord | None = None
    reason: str | None = None


class TransitionEngine:
    """Rule-evaluated deterministic transition engine with two-phase commit."""

    def __init__(self, session_id: str, mode: str = "court", *, effect_graph_hash: str = ""):
        self.session_id = session_id
        self.state = RuntimeState.IDLE
        self.active_turn_id: str | None = None
        self.active_turn_version = 0
        self.mode = mode
        self.settings: dict[str, Any] = {}
        self.ledger_digest = ""
        self.resource_limits: dict[str, Any] = {}
        self.effect_graph_hash = effect_graph_hash
        self.transition_log: list[TransitionRecord] = []
        self._rules: list[TransitionRule] = []
        self._named_guards: dict[str, Guard] = {}
        self._declare_defaults()

    def set_context(
        self,
        *,
        mode: str | None = None,
        settings: dict[str, Any] | None = None,
        ledger_digest: str | None = None,
        resource_limits: dict[str, Any] | None = None,
        effect_graph_hash: str | None = None,
    ) -> None:
        if mode is not None:
            self.mode = mode
        if settings is not None:
            self.settings = canonical_copy(settings)
        if ledger_digest is not None:
            self.ledger_digest = ledger_digest
        if resource_limits is not None:
            self.resource_limits = canonical_copy(resource_limits)
        if effect_graph_hash is not None:
            self.effect_graph_hash = effect_graph_hash

    def begin_turn(self, turn_id: str) -> int:
        self.active_turn_id = turn_id
        self.active_turn_version += 1
        return self.active_turn_version

    def force_state(self, state: RuntimeState) -> None:
        self.state = state

    def build_snapshot(
        self,
        event: RuntimeEvent,
        *,
        queue_depth: int = 0,
        inflight_event_count: int = 0,
        terms_digest: str = "",
    ) -> DispatchSnapshot:
        return DispatchSnapshot(
            current_state=self.state,
            session_id=self.session_id,
            active_turn_id=self.active_turn_id,
            active_turn_version=self.active_turn_version,
            mode=self.mode,
            settings=canonical_copy(self.settings),
            ledger_digest=self.ledger_digest,
            resource_limits=canonical_copy(self.resource_limits),
            event=event,
            queue_depth=queue_depth,
            inflight_event_count=inflight_event_count,
            terms_digest=terms_digest,
            effect_graph_hash=self.effect_graph_hash,
        )

    def plan_dispatch(
        self,
        event: RuntimeEvent,
        *,
        queue_depth: int = 0,
        inflight_event_count: int = 0,
        terms_digest: str = "",
    ) -> PlanResult:
        if event.session_id != self.session_id:
            return PlanResult(False, ignored_reason="session_mismatch")
        if event.turn_version is not None and event.turn_version < self.active_turn_version:
            return PlanResult(False, ignored_reason="stale_event", stale=True)
        if event.is_deferred and event.type != "deferred_effect_event":
            return PlanResult(False, ignored_reason="invalid_deferred_event")

        snap = self.build_snapshot(
            event,
            queue_depth=queue_depth,
            inflight_event_count=inflight_event_count,
            terms_digest=terms_digest,
        )

        candidates = [
            (idx, r) for idx, r in enumerate(self._rules)
            if r.event_type == event.type and (r.from_state == "*" or r.from_state == self.state)
        ]
        candidates.sort(key=lambda pair: (-pair[1].priority, pair[0]))

        for _, rule in candidates:
            from .guards import run_guard_pipeline

            guard_result = run_guard_pipeline(
                snap,
                extra_guards=rule.guards,
                named_guards=self._named_guards,
            )
            if not guard_result.passed:
                continue
            plan = TransitionPlan(
                from_state=self.state,
                to_state=rule.to_state,
                effects=rule.effects,
                rule_name=rule.name or f"{rule.from_state}:{rule.event_type}",
                snapshot_hash=snap.snapshot_hash(),
                event=event,
            )
            return PlanResult(True, plan)

        return PlanResult(False, ignored_reason="no_matching_rule")

    def commit_transition(self, plan: TransitionPlan, ordered_effects: list[str]) -> CommitResult:
        if plan.from_state != self.state:
            return CommitResult(False, self.state, reason="state_changed_since_plan")
        old = self.state
        self.state = plan.to_state
        record = TransitionRecord(
            session_id=self.session_id,
            turn_id=plan.event.turn_id,
            turn_version=plan.event.turn_version,
            from_state=old.value,
            event=plan.event.type,
            to_state=plan.to_state.value,
            effects=list(ordered_effects),
            snapshot_hash=plan.snapshot_hash,
        )
        self.transition_log.append(record)
        return CommitResult(True, self.state, record)

    def dispatch(self, event: RuntimeEvent, **kwargs: Any) -> PlanResult:
        """Legacy helper: plan only (no commit)."""
        return self.plan_dispatch(event, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        canonical_log = [
            {k: v for k, v in r.to_dict().items() if k != "ts"}
            for r in self.transition_log
        ]
        return {
            "state": self.state.value,
            "active_turn_id": self.active_turn_id,
            "active_turn_version": self.active_turn_version,
            "transition_log": [r.to_dict() for r in self.transition_log],
            "transition_digest": digest(canonical_log),
        }

    def _declare_defaults(self) -> None:
        self._named_guards = {
            "payload_text_nonempty": lambda s: GuardResult(
                bool(str(s.event.payload.get("text", "")).strip()), "empty_text"),
            "payload_valid": lambda s: GuardResult(isinstance(s.event.payload, dict), "invalid_payload"),
            "current_turn": lambda s: GuardResult(
                s.event.turn_version is None or s.event.turn_version == s.active_turn_version,
                "not_current_turn",
            ),
            "has_session_work": lambda s: GuardResult(True),
        }
        self._rules = [
            TransitionRule("*", "end_hearing", RuntimeState.GENERATING_FINAL_JUDGMENT,
                           ("generate_final_judgment",), ("has_session_work",), 1000, "end_hearing"),
            TransitionRule(RuntimeState.IDLE, "brief_submitted", RuntimeState.BRIEF_READY,
                           ("persist_session_simple",), ("payload_valid",), 600, "brief_submitted"),
            TransitionRule(RuntimeState.BRIEF_READY, "start_session", RuntimeState.AWAITING_USER,
                           (), (), 600, "start_session"),
            TransitionRule(RuntimeState.AWAITING_USER, "user_text_submit", RuntimeState.AWAITING_USER,
                           ("process_user_turn", "persist_session_simple"), ("payload_text_nonempty", "current_turn"), 600,
                           "user_text_submit"),
            TransitionRule(RuntimeState.USER_TURN_BUFFERED, "classify_requested", RuntimeState.CLASSIFYING_TURN,
                           ("start_classification",), ("current_turn",), 600, "classify_requested"),
            TransitionRule(RuntimeState.CLASSIFYING_TURN, "classification_needs_retrieval",
                           RuntimeState.RETRIEVING_AUTHORITIES, ("start_retrieval",), ("current_turn",), 400),
            TransitionRule(RuntimeState.CLASSIFYING_TURN, "classification_done",
                           RuntimeState.SELECTING_SPEAKER, ("select_speaker",), ("current_turn",), 400),
            TransitionRule(RuntimeState.RETRIEVING_AUTHORITIES, "retrieval_done",
                           RuntimeState.RESOLVING_AUTHORITIES, ("resolve_authorities",), ("current_turn",), 400),
            TransitionRule(RuntimeState.RESOLVING_AUTHORITIES, "authority_set_ready",
                           RuntimeState.SELECTING_SPEAKER, ("select_speaker",), ("current_turn",), 400),
            TransitionRule(RuntimeState.SELECTING_SPEAKER, "speaker_selected",
                           RuntimeState.GENERATING_AGENT_OUTPUT, ("start_agent_generation",), ("current_turn",), 400),
            TransitionRule(RuntimeState.GENERATING_AGENT_OUTPUT, "agent_output_done",
                           RuntimeState.VALIDATING_LANGUAGE, ("validate_language_legacy",), ("current_turn",), 400),
            TransitionRule(RuntimeState.VALIDATING_LANGUAGE, "language_valid",
                           RuntimeState.RENDERING_RESPONSE, ("render_response",), ("current_turn",), 300),
            TransitionRule(RuntimeState.RENDERING_RESPONSE, "response_sent",
                           RuntimeState.UPDATING_LEDGER, ("update_ledger",), ("current_turn",), 200),
            TransitionRule(RuntimeState.UPDATING_LEDGER, "ledger_updated",
                           RuntimeState.AWAITING_USER,
                           ("persist_session_simple", "emit_turn_done"), ("current_turn",), 200),
            TransitionRule(RuntimeState.UPDATING_LEDGER, "compression_needed",
                           RuntimeState.COMPRESSING_LEDGER, ("compress_ledger",), ("current_turn",), 200),
            TransitionRule(RuntimeState.COMPRESSING_LEDGER, "compression_done",
                           RuntimeState.AWAITING_USER,
                           ("persist_session_simple", "emit_turn_done"), ("current_turn",), 200),
            TransitionRule("*", "recoverable_error", RuntimeState.ERROR_RECOVERABLE,
                           ("emit_ui_note",), (), 800, "recoverable_error"),
            TransitionRule(RuntimeState.ERROR_RECOVERABLE, "recovered", RuntimeState.AWAITING_USER,
                           (), (), 800, "recovered"),
            TransitionRule(RuntimeState.AWAITING_USER, "deferred_effect_event", RuntimeState.AWAITING_USER,
                           (), ("current_turn",), 100, "deferred_effect_event"),
        ]
