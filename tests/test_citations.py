"""Citation normalisation tests — the Indian-context edge cases."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.retrieval.citations import (extract_citations, normalize_citation,
                                          normalize_spoken_numbers)


def test_spoken_year_air():
    c = normalize_citation("air nineteen seventy three sc fourteen sixty one")
    assert c is not None
    assert c.canonical == "AIR 1973 SC 1461"


def test_spoken_pagepair_air():
    c = normalize_citation("air nineteen seventy three sc twenty three sixty nine")
    assert c is not None
    assert c.canonical == "AIR 1973 SC 2369"


def test_spaced_acronym_air():
    c = normalize_citation("A.I.R 1997 S C 3011")
    assert c is not None
    assert c.canonical == "AIR 1997 SC 3011"


def test_scc_paren_format():
    cites = [c.canonical for c in extract_citations("relied on (2017) 10 SCC 1")]
    assert "(2017) 10 SCC 1" in cites


def test_multiple_citations():
    cites = [c.canonical for c in extract_citations("see (2017) 10 SCC 1 and AIR 1978 SC 597")]
    assert "AIR 1978 SC 597" in cites
    assert "(2017) 10 SCC 1" in cites


def test_no_false_positive_in_prose():
    # ordinary prose with isolated number words must not produce a citation
    assert normalize_citation("I have one final point on the third issue") is None


def test_spoken_numbers_repair_year():
    out = normalize_spoken_numbers("decided in nineteen seventy three")
    assert "1973" in out


def test_two_thousand_form():
    out = normalize_spoken_numbers("in two thousand seventeen")
    assert "2017" in out
