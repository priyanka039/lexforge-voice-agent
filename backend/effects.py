"""Effect DAG executor with staging rollback (R2, R9, R12)."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from .canonical import digest

EffectHandler = Callable[["EffectContext"], Awaitable[None]]


class EffectClass(str, Enum):
    STAGING = "staging"
    DEFERRED_EXTERNAL = "deferred_external"
    IRREVERSIBLE_EXTERNAL = "irreversible_external"


_CLASS_ORDER = {
    EffectClass.STAGING: 0,
    EffectClass.DEFERRED_EXTERNAL: 1,
    EffectClass.IRREVERSIBLE_EXTERNAL: 2,
}


@dataclass(frozen=True)
class EffectDeclaration:
    name: str
    depends_on: tuple[str, ...] = ()
    effect_class: EffectClass = EffectClass.STAGING

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "depends_on": list(self.depends_on),
            "class": self.effect_class.value,
        }


@dataclass
class PendingEmission:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class StagingContext:
    session_state: Any
    ledger_state: Any | None = None
    pending_emissions: list[PendingEmission] = field(default_factory=list)
    effect_results: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_live(cls, session_state: Any, ledger_state: Any | None = None) -> StagingContext:
        return cls(
            session_state=copy.deepcopy(session_state),
            ledger_state=copy.deepcopy(ledger_state) if ledger_state is not None else None,
        )


@dataclass
class EffectContext:
    staging: StagingContext
    snapshot: Any
    turn_version: int
    recursion_depth: int = 0
    prior_results: dict[str, Any] = field(default_factory=dict)
    deferred_events: list[dict[str, Any]] = field(default_factory=list)

    def emit(self, kind: str, **payload: Any) -> None:
        self.staging.pending_emissions.append(PendingEmission(kind, dict(payload)))

    def defer_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        if self.recursion_depth > 0:
            raise RecursionError("deferred_effect_event cannot re-defer (R12)")
        self.deferred_events.append({
            "type": event_type,
            "payload": payload or {},
        })


class EffectDAGError(ValueError):
    pass


def validate_dag(declarations: tuple[EffectDeclaration, ...]) -> str:
    """Validate acyclic graph at startup; return effect_graph_hash."""
    names = {d.name for d in declarations}
    for decl in declarations:
        for dep in decl.depends_on:
            if dep not in names:
                raise EffectDAGError(f"unknown dependency {dep!r} for effect {decl.name!r}")
    indegree: dict[str, int] = {d.name: 0 for d in declarations}
    adj: dict[str, list[str]] = {d.name: [] for d in declarations}
    for decl in declarations:
        for dep in decl.depends_on:
            adj[dep].append(decl.name)
            indegree[decl.name] += 1
    queue = sorted([n for n, deg in indegree.items() if deg == 0])
    ordered: list[str] = []
    while queue:
        node = queue.pop(0)
        ordered.append(node)
        for nxt in sorted(adj[node]):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
                queue.sort()
    if len(ordered) != len(declarations):
        raise EffectDAGError("effect dependency cycle detected")
    return digest([d.to_dict() for d in sorted(declarations, key=lambda d: d.name)])


def topo_sort_effects(
    effect_names: tuple[str, ...],
    registry: dict[str, EffectDeclaration],
) -> list[str]:
    """Topological sort with lexicographic tie-break (R9)."""
    selected = [registry[n] for n in effect_names if n in registry]
    unknown = [n for n in effect_names if n not in registry]
    if unknown:
        raise EffectDAGError(f"unknown effects: {unknown}")

    indegree: dict[str, int] = {d.name: 0 for d in selected}
    adj: dict[str, list[str]] = {d.name: [] for d in selected}
    for decl in selected:
        for dep in decl.depends_on:
            if dep not in indegree:
                raise EffectDAGError(f"missing dependency {dep!r} for {decl.name!r}")
            adj[dep].append(decl.name)
            indegree[decl.name] += 1

    ready = sorted([n for n, deg in indegree.items() if deg == 0])
    ordered: list[str] = []
    while ready:
        ready.sort(key=lambda n: (_CLASS_ORDER[registry[n].effect_class], n))
        node = ready.pop(0)
        ordered.append(node)
        for nxt in sorted(adj[node]):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
    if len(ordered) != len(selected):
        raise EffectDAGError("cyclic effect dependencies in transition")
    return ordered


@dataclass
class EffectRunResult:
    success: bool
    ordered_effects: list[str] = field(default_factory=list)
    error: str | None = None
    staging: StagingContext | None = None
    deferred_events: list[dict[str, Any]] = field(default_factory=list)


class EffectRunner:
    def __init__(
        self,
        registry: dict[str, EffectDeclaration],
        handlers: dict[str, EffectHandler],
    ):
        self.registry = registry
        self.handlers = handlers
        self.effect_graph_hash = validate_dag(tuple(registry.values()))

    async def run(
        self,
        effect_names: tuple[str, ...],
        ctx: EffectContext,
    ) -> EffectRunResult:
        try:
            ordered = topo_sort_effects(effect_names, self.registry)
        except EffectDAGError as exc:
            return EffectRunResult(False, error=str(exc))

        staging = ctx.staging
        deferred: list[dict[str, Any]] = []
        for name in ordered:
            decl = self.registry[name]
            handler = self.handlers.get(name)
            if handler is None:
                return EffectRunResult(False, ordered, f"missing_handler:{name}", staging)
            child = EffectContext(
                staging=staging,
                snapshot=ctx.snapshot,
                turn_version=ctx.turn_version,
                recursion_depth=ctx.recursion_depth,
                prior_results=dict(staging.effect_results),
            )
            try:
                await handler(child)
            except Exception as exc:
                return EffectRunResult(False, ordered, f"effect_failed:{name}:{exc}", staging)
            staging.effect_results[name] = child.prior_results.get(name)
            deferred.extend(child.deferred_events)

        return EffectRunResult(True, ordered, None, staging, deferred)


DEFAULT_EFFECT_DECLARATIONS: tuple[EffectDeclaration, ...] = (
    EffectDeclaration("record_agent_output"),
    EffectDeclaration("validate_language", ("record_agent_output",)),
    EffectDeclaration("apply_ledger_update", ("record_agent_output", "validate_language")),
    EffectDeclaration("check_compression_needed", ("apply_ledger_update",)),
    EffectDeclaration("emit_response", ("apply_ledger_update",), EffectClass.DEFERRED_EXTERNAL),
    EffectDeclaration("persist_session", ("check_compression_needed", "emit_response"),
                      EffectClass.IRREVERSIBLE_EXTERNAL),
    EffectDeclaration("emit_turn_done", ("persist_session",), EffectClass.DEFERRED_EXTERNAL),
    EffectDeclaration("process_user_turn", effect_class=EffectClass.STAGING),
    EffectDeclaration("start_classification", effect_class=EffectClass.STAGING),
    EffectDeclaration("start_retrieval", effect_class=EffectClass.STAGING),
    EffectDeclaration("resolve_authorities", effect_class=EffectClass.STAGING),
    EffectDeclaration("select_speaker", effect_class=EffectClass.STAGING),
    EffectDeclaration("start_agent_generation", effect_class=EffectClass.STAGING),
    EffectDeclaration("validate_language_legacy", effect_class=EffectClass.STAGING),
    EffectDeclaration("render_response", effect_class=EffectClass.STAGING),
    EffectDeclaration("update_ledger", effect_class=EffectClass.STAGING),
    EffectDeclaration("compress_ledger", effect_class=EffectClass.STAGING),
    EffectDeclaration("generate_final_judgment", effect_class=EffectClass.STAGING),
    EffectDeclaration("emit_ui_note", effect_class=EffectClass.DEFERRED_EXTERNAL),
    EffectDeclaration("persist_session_simple", effect_class=EffectClass.IRREVERSIBLE_EXTERNAL),
)
