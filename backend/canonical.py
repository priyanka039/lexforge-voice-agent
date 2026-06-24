"""Canonical serialization and digest (D-LEVM CANONICAL_SPEC_VERSION 1.0)."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Any

CANONICAL_SPEC_VERSION = "1.0"
_FLOAT_QUANTIZE = Decimal("0.000001")

# Fields where array element order is semantically meaningful.
ORDERED_ARRAY_FIELDS: frozenset[str] = frozenset({
    "transcript",
    "issues",
    "effects",
    "depends_on",
    "topics",
    "citations",
    "terms",
})

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_string(value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    text = text.strip()
    return _WHITESPACE_RE.sub(" ", text)


def normalize_number(value: Any) -> int | str:
    if isinstance(value, bool):
        raise ValueError("booleans are not canonical numbers")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("NaN/Infinity forbidden in canonical floats")
        dec = Decimal(str(value)).quantize(_FLOAT_QUANTIZE, rounding=ROUND_HALF_EVEN)
        return format(dec, "f")
    if isinstance(value, str):
        try:
            dec = Decimal(value).quantize(_FLOAT_QUANTIZE, rounding=ROUND_HALF_EVEN)
        except InvalidOperation as exc:
            raise ValueError(f"invalid numeric string: {value!r}") from exc
        return format(dec, "f")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("NaN/Infinity forbidden in canonical floats")
        dec = value.quantize(_FLOAT_QUANTIZE, rounding=ROUND_HALF_EVEN)
        return format(dec, "f")
    raise TypeError(f"unsupported number type: {type(value)!r}")


def _normalize_value(value: Any, *, field_name: str | None = None) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return normalize_string(value)
    if isinstance(value, (int, float, Decimal)):
        return normalize_number(value)
    if isinstance(value, dict):
        return {
            normalize_string(str(k)): _normalize_value(v, field_name=str(k))
            for k, v in sorted(value.items(), key=lambda item: str(item[0]).encode("utf-8"))
        }
    if isinstance(value, (list, tuple)):
        normalized = [_normalize_value(v, field_name=field_name) for v in value]
        if field_name not in ORDERED_ARRAY_FIELDS:
            normalized = sorted(
                normalized,
                key=lambda item: digest(item) if not isinstance(item, str) else item,
            )
        return normalized
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return normalize_string(str(value))


def canonical_copy(value: Any) -> Any:
    return _normalize_value(value)


def canonical_dumps(value: Any) -> str:
    wrapped = {
        "canonical_spec_version": CANONICAL_SPEC_VERSION,
        "value": _normalize_value(value),
    }
    return _serialize(wrapped)


def _serialize(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
            return value
        try:
            Decimal(value)
            if "." in value or "e" in value.lower():
                return json.dumps(value, ensure_ascii=False)
        except InvalidOperation:
            pass
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        inner = ",".join(_serialize(v) for v in value)
        return f"[{inner}]"
    if isinstance(value, dict):
        parts = []
        for key in sorted(value.keys(), key=lambda k: str(k).encode("utf-8")):
            parts.append(f"{json.dumps(str(key), ensure_ascii=False)}:{_serialize(value[key])}")
        return "{" + ",".join(parts) + "}"
    return json.dumps(str(value), ensure_ascii=False)


def digest(value: Any) -> str:
    raw = canonical_dumps(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def digest_raw_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
