"""Fixed 6-step guard pipeline (D-LEVM v1)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .runtime import DispatchSnapshot


@dataclass(frozen=True)
class GuardResult:
    passed: bool
    reason: str | None = None


GuardFn = Callable[["DispatchSnapshot"], GuardResult]

# R8: queue metrics are observational — guards must not branch on them in v1.
_MAX_PAYLOAD_TEXT_LEN = 32_000
_MAX_TURN_INDEX = 10_000
_MAX_LEDGER_DIGEST_LEN = 128


@dataclass(frozen=True)
class GuardStep:
    name: str
    fn: GuardFn


def session_valid(snapshot: "DispatchSnapshot") -> GuardResult:
    if not snapshot.session_id:
        return GuardResult(False, "missing_session_id")
    return GuardResult(True)


def turn_fresh(snapshot: "DispatchSnapshot") -> GuardResult:
    ev = snapshot.event
    if ev.turn_version is None:
        return GuardResult(True)
    if ev.turn_version < snapshot.active_turn_version:
        return GuardResult(False, "stale_turn_version")
    if ev.turn_version > snapshot.active_turn_version:
        return GuardResult(False, "future_turn_version")
    return GuardResult(True)


def payload_schema_valid(snapshot: "DispatchSnapshot") -> GuardResult:
    payload = snapshot.event.payload
    if not isinstance(payload, dict):
        return GuardResult(False, "payload_not_dict")
    if "normalized_payload_hash" in payload:
        envelope = payload.get("envelope")
        if isinstance(envelope, dict):
            expected = envelope.get("normalized_payload_hash")
            actual = payload.get("normalized_payload_hash")
            if expected and actual and expected != actual:
                return GuardResult(False, "payload_hash_mismatch")
    text = str(payload.get("text", ""))
    if len(text) > _MAX_PAYLOAD_TEXT_LEN:
        return GuardResult(False, "payload_text_too_long")
    return GuardResult(True)


def state_precondition(snapshot: "DispatchSnapshot") -> GuardResult:
    return GuardResult(True)


def mode_provider_language_constraint(snapshot: "DispatchSnapshot") -> GuardResult:
    mode = snapshot.mode
    if mode not in {"court", "debate"}:
        return GuardResult(False, f"invalid_mode:{mode}")
    return GuardResult(True)


def resource_limit(snapshot: "DispatchSnapshot") -> GuardResult:
    # R8: fixed caps only — never branch on queue_depth / inflight_event_count.
    limits = snapshot.resource_limits or {}
    max_turns = int(limits.get("max_turn_index", _MAX_TURN_INDEX))
    turn_index = int(limits.get("turn_index", 0))
    if turn_index > max_turns:
        return GuardResult(False, "max_turn_index_exceeded")
    if len(snapshot.ledger_digest) > _MAX_LEDGER_DIGEST_LEN:
        return GuardResult(False, "ledger_digest_too_long")
    return GuardResult(True)


GLOBAL_GUARD_PIPELINE: tuple[GuardStep, ...] = (
    GuardStep("session_valid", session_valid),
    GuardStep("turn_fresh", turn_fresh),
    GuardStep("payload_schema_valid", payload_schema_valid),
    GuardStep("state_precondition", state_precondition),
    GuardStep("mode_provider_language_constraint", mode_provider_language_constraint),
    GuardStep("resource_limit", resource_limit),
)

_guard_cache: dict[tuple, GuardResult] = {}


def run_guard_pipeline(
    snapshot: "DispatchSnapshot",
    *,
    extra_guards: tuple[str, ...] = (),
    named_guards: dict[str, GuardFn] | None = None,
    use_cache: bool = True,
) -> GuardResult:
    cache_key = (
        snapshot.current_state.value,
        snapshot.event.type,
        snapshot.event.normalized_payload_hash_value(),
        snapshot.snapshot_hash(),
        extra_guards,
    )
    if use_cache and cache_key in _guard_cache:
        return _guard_cache[cache_key]

    named = named_guards or {}
    for step in GLOBAL_GUARD_PIPELINE:
        try:
            result = step.fn(snapshot)
        except Exception as exc:
            result = GuardResult(False, f"guard_error:{step.name}:{exc}")
        if not result.passed:
            if use_cache:
                _guard_cache[cache_key] = result
            return result

    for name in extra_guards:
        guard = named.get(name)
        if guard is None:
            result = GuardResult(False, f"unknown_guard:{name}")
            if use_cache:
                _guard_cache[cache_key] = result
            return result
        try:
            result = guard(snapshot)
        except Exception as exc:
            result = GuardResult(False, f"guard_error:{name}:{exc}")
        if not result.passed:
            if use_cache:
                _guard_cache[cache_key] = result
            return result

    ok = GuardResult(True)
    if use_cache:
        _guard_cache[cache_key] = ok
    return ok


def clear_guard_cache() -> None:
    _guard_cache.clear()
