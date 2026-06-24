"""Ingress boundary: wrap external inputs with dual-hash envelopes (R7)."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .canonical import canonical_copy, digest, digest_raw_bytes


class IngressSource(str, Enum):
    LLM = "llm"
    RETRIEVAL = "retrieval"
    VOICE = "voice"
    SYSTEM = "system"
    UI = "ui"


@dataclass(frozen=True)
class ExternalInputEnvelope:
    source: IngressSource
    payload: dict[str, Any]
    raw_payload_hash: str
    normalized_payload_hash: str
    ingest_turn_version: int
    ingest_seq: int = 0
    envelope_id: str = ""

    def __post_init__(self) -> None:
        if not self.envelope_id:
            object.__setattr__(self, "envelope_id", uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.value,
            "payload": self.payload,
            "raw_payload_hash": self.raw_payload_hash,
            "normalized_payload_hash": self.normalized_payload_hash,
            "ingest_turn_version": self.ingest_turn_version,
            "ingest_seq": self.ingest_seq,
            "envelope_id": self.envelope_id,
        }


def _raw_bytes_from_payload(payload: Any) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def wrap_external_input(
    source: IngressSource | str,
    payload: Any,
    *,
    ingest_turn_version: int,
    ingest_seq: int = 0,
) -> ExternalInputEnvelope:
    if isinstance(source, str):
        source = IngressSource(source)
    if not isinstance(payload, dict):
        payload = {"value": payload}
    raw_bytes = _raw_bytes_from_payload(payload)
    raw_hash = digest_raw_bytes(raw_bytes)
    normalized = canonical_copy(payload)
    normalized_hash = digest(normalized)
    return ExternalInputEnvelope(
        source=source,
        payload=normalized,
        raw_payload_hash=raw_hash,
        normalized_payload_hash=normalized_hash,
        ingest_turn_version=ingest_turn_version,
        ingest_seq=ingest_seq,
    )
