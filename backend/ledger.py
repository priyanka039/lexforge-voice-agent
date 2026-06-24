"""Argument ledger and pure confidence recompute (Phase 2 + R10)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from .canonical import canonical_copy, digest


@dataclass
class LedgerEntry:
    entry_id: str
    issue_id: str
    text: str
    turn_id: str | None = None
    agent: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, issue_id: str, text: str, **kwargs: Any) -> LedgerEntry:
        agent = str(kwargs.get("agent", ""))
        turn_id = kwargs.get("turn_id")
        entry_id = digest({
            "issue_id": issue_id,
            "text": text,
            "agent": agent,
            "turn_id": turn_id,
        })[:12]
        return cls(entry_id=entry_id, issue_id=issue_id, text=text, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "issue_id": self.issue_id,
            "text": self.text,
            "turn_id": self.turn_id,
            "agent": self.agent,
            "meta": canonical_copy(self.meta),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LedgerEntry:
        return cls(
            entry_id=str(data.get("entry_id", uuid.uuid4().hex[:12])),
            issue_id=str(data.get("issue_id", "global")),
            text=str(data.get("text", "")),
            turn_id=data.get("turn_id"),
            agent=str(data.get("agent", "")),
            meta=dict(data.get("meta") or {}),
        )


@dataclass
class ArgumentLedger:
    claims: list[LedgerEntry] = field(default_factory=list)
    counters: list[LedgerEntry] = field(default_factory=list)
    weaknesses: list[LedgerEntry] = field(default_factory=list)
    authorities: list[LedgerEntry] = field(default_factory=list)
    contradictions: list[LedgerEntry] = field(default_factory=list)
    concessions: list[LedgerEntry] = field(default_factory=list)
    superseded_ids: list[str] = field(default_factory=list)
    confidence_by_issue: dict[str, float] = field(default_factory=dict)

    def add_claim(self, issue_id: str, text: str, **kwargs: Any) -> LedgerEntry:
        entry = LedgerEntry.create(issue_id, text, **kwargs)
        self.claims.append(entry)
        return entry

    def add_counter(self, issue_id: str, text: str, **kwargs: Any) -> LedgerEntry:
        entry = LedgerEntry.create(issue_id, text, **kwargs)
        self.counters.append(entry)
        return entry

    def add_weakness(self, issue_id: str, text: str, **kwargs: Any) -> LedgerEntry:
        entry = LedgerEntry.create(issue_id, text, agent="advisor", **kwargs)
        self.weaknesses.append(entry)
        return entry

    def add_authority(self, issue_id: str, text: str, **kwargs: Any) -> LedgerEntry:
        entry = LedgerEntry.create(issue_id, text, agent="precedent", **kwargs)
        self.authorities.append(entry)
        return entry

    def active_entries(self, kind: str) -> list[LedgerEntry]:
        items: list[LedgerEntry] = getattr(self, kind, [])
        return [e for e in items if e.entry_id not in self.superseded_ids]

    def to_dict(self) -> dict[str, Any]:
        return {
            "claims": [e.to_dict() for e in self.claims],
            "counters": [e.to_dict() for e in self.counters],
            "weaknesses": [e.to_dict() for e in self.weaknesses],
            "authorities": [e.to_dict() for e in self.authorities],
            "contradictions": [e.to_dict() for e in self.contradictions],
            "concessions": [e.to_dict() for e in self.concessions],
            "superseded_ids": list(self.superseded_ids),
            "confidence_by_issue": dict(self.confidence_by_issue),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ArgumentLedger:
        if not data:
            return cls()
        return cls(
            claims=[LedgerEntry.from_dict(x) for x in data.get("claims", [])],
            counters=[LedgerEntry.from_dict(x) for x in data.get("counters", [])],
            weaknesses=[LedgerEntry.from_dict(x) for x in data.get("weaknesses", [])],
            authorities=[LedgerEntry.from_dict(x) for x in data.get("authorities", [])],
            contradictions=[LedgerEntry.from_dict(x) for x in data.get("contradictions", [])],
            concessions=[LedgerEntry.from_dict(x) for x in data.get("concessions", [])],
            superseded_ids=list(data.get("superseded_ids", [])),
            confidence_by_issue=dict(data.get("confidence_by_issue", {})),
        )

    def digest(self) -> str:
        return digest(self.to_dict())


@dataclass(frozen=True)
class FinalLedger:
    """Immutable post-compression ledger view — sole input to confidence (R10)."""

    claims: tuple[dict[str, Any], ...] = ()
    counters: tuple[dict[str, Any], ...] = ()
    authorities: tuple[dict[str, Any], ...] = ()
    contradictions: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_ledger(cls, ledger: ArgumentLedger) -> FinalLedger:
        return cls(
            claims=tuple(e.to_dict() for e in ledger.active_entries("claims")),
            counters=tuple(e.to_dict() for e in ledger.active_entries("counters")),
            authorities=tuple(e.to_dict() for e in ledger.active_entries("authorities")),
            contradictions=tuple(e.to_dict() for e in ledger.active_entries("contradictions")),
        )


@dataclass
class ConfidenceMap:
    by_issue: dict[str, float] = field(default_factory=dict)


def authority_set_digest(ledger: ArgumentLedger) -> str:
    """Digest of verified authority entries for replay comparison."""
    authorities = [e.to_dict() for e in ledger.active_entries("authorities")]
    return digest({"authorities": authorities})


def ledger_digest_from_state(state: Any) -> str:
    ledger = getattr(state, "ledger", None)
    if ledger is not None and hasattr(ledger, "digest"):
        return ledger.digest()
    cited = getattr(state, "cited_cases", []) or []
    weaknesses = getattr(state, "known_weaknesses", []) or []
    return digest({
        "cited_cases": list(cited),
        "known_weaknesses": list(weaknesses),
        "turn_index": getattr(state, "turn_index", 0),
    })


def sync_ledger_from_state(state: Any) -> None:
    """Mirror legacy list fields into ledger entries (idempotent best-effort)."""
    if getattr(state, "ledger", None) is None:
        state.ledger = ArgumentLedger()
    ledger: ArgumentLedger = state.ledger

    existing_weak = {e.text for e in ledger.weaknesses}
    for w in getattr(state, "known_weaknesses", []) or []:
        if w and w not in existing_weak:
            ledger.add_weakness("global", w)

    existing_auth = {e.text for e in ledger.authorities}
    for c in getattr(state, "cited_cases", []) or []:
        if c and c not in existing_auth:
            ledger.add_authority("global", c)

    for i, issue in enumerate(getattr(state.brief, "issues", []) or []):
        issue_id = f"issue_{i}"
        if issue and not any(e.text == issue for e in ledger.claims):
            ledger.add_claim(issue_id, issue, agent="brief")


def apply_agent_output_to_ledger(state: Any, agent: str, data: dict[str, Any]) -> None:
    sync_ledger_from_state(state)
    ledger: ArgumentLedger = state.ledger
    issue_id = "global"

    if agent in {"weakness", "advisor"} and data.get("weaknesses"):
        for w in data["weaknesses"]:
            if w:
                ledger.add_weakness(issue_id, str(w))
    if data.get("precedents"):
        for p in data["precedents"]:
            title = p.get("title") or p.get("citation") or ""
            if title:
                ledger.add_authority(issue_id, title, meta=p)


def recompute_confidence(final_ledger: FinalLedger) -> ConfidenceMap:
    """Pure function over final post-compression ledger only (R10)."""
    scores: dict[str, float] = {}
    claim_count = len(final_ledger.claims)
    counter_count = len(final_ledger.counters)
    authority_count = len(final_ledger.authorities)
    contradiction_count = len(final_ledger.contradictions)
    base = 0.5
    if claim_count:
        base += min(0.3, claim_count * 0.05)
    if authority_count:
        base += min(0.2, authority_count * 0.04)
    if contradiction_count:
        base -= min(0.3, contradiction_count * 0.06)
    if counter_count:
        base += min(0.1, counter_count * 0.02)
    scores["global"] = round(max(0.0, min(1.0, base)), 6)

    by_issue: dict[str, set[str]] = {}
    for claim in final_ledger.claims:
        iid = str(claim.get("issue_id", "global"))
        by_issue.setdefault(iid, set()).add("claim")
    for counter in final_ledger.counters:
        iid = str(counter.get("issue_id", "global"))
        by_issue.setdefault(iid, set()).add("counter")
    for iid in by_issue:
        boost = 0.05 * len(by_issue[iid])
        scores[iid] = round(min(1.0, scores.get("global", base) + boost), 6)

    return ConfidenceMap(by_issue=scores)


def _normalize_text(text: str) -> str:
    return " ".join(text.split()).strip().lower()


def _dedupe_entries(entries: list[LedgerEntry]) -> list[LedgerEntry]:
    seen: dict[str, LedgerEntry] = {}
    for e in entries:
        key = _normalize_text(e.text)
        if key not in seen or e.entry_id < seen[key].entry_id:
            seen[key] = e
    return sorted(seen.values(), key=lambda x: x.entry_id)


def _rank_claims(claims: list[LedgerEntry]) -> list[LedgerEntry]:
    """Rank without using confidence as input (Phase 5)."""

    def score(entry: LedgerEntry) -> tuple:
        has_meta = 1 if entry.meta else 0
        text_len = len(entry.text)
        return (-has_meta, -text_len, entry.entry_id)

    return sorted(claims, key=score)


def compress_ledger(ledger: ArgumentLedger) -> FinalLedger:
    """Compression DAG — returns FinalLedger for confidence recompute (R10)."""
    claims = _dedupe_entries(ledger.active_entries("claims"))
    counters = _dedupe_entries(ledger.active_entries("counters"))
    claims = _rank_claims(claims)

    keep_claim_ids = {c.entry_id for c in claims}
    keep_counter_ids = {c.entry_id for c in counters}
    removable = [
        eid for eid in ledger.superseded_ids
    ]
    for entry in ledger.claims + ledger.counters:
        if entry.entry_id not in keep_claim_ids and entry.entry_id not in keep_counter_ids:
            if entry.entry_id not in removable:
                removable.append(entry.entry_id)

    ledger.superseded_ids = sorted(set(ledger.superseded_ids + removable))

    return FinalLedger(
        claims=tuple(c.to_dict() for c in claims),
        counters=tuple(c.to_dict() for c in counters),
        authorities=tuple(e.to_dict() for e in ledger.active_entries("authorities")),
        contradictions=tuple(e.to_dict() for e in ledger.active_entries("contradictions")),
    )
