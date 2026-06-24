"""Tests for effect DAG executor (R9, R12)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from backend.effects import (
    EffectClass,
    EffectDAGError,
    EffectDeclaration,
    validate_dag,
    topo_sort_effects,
)


def test_startup_dag_validation_acyclic():
    decls = (
        EffectDeclaration("a"),
        EffectDeclaration("b", ("a",)),
        EffectDeclaration("c", ("b",)),
    )
    graph_hash = validate_dag(decls)
    assert len(graph_hash) == 64


def test_cycle_rejected_at_startup():
    decls = (
        EffectDeclaration("a", ("b",)),
        EffectDeclaration("b", ("a",)),
    )
    with pytest.raises(EffectDAGError):
        validate_dag(decls)


def test_topo_sort_lexicographic_tiebreak():
    registry = {
        "a": EffectDeclaration("a", effect_class=EffectClass.STAGING),
        "b": EffectDeclaration("b", effect_class=EffectClass.STAGING),
        "c": EffectDeclaration("c", ("a", "b"), EffectClass.STAGING),
    }
    ordered = topo_sort_effects(("c", "b", "a"), registry)
    assert ordered.index("a") < ordered.index("c")
    assert ordered.index("b") < ordered.index("c")
