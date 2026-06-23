"""Local seed corpus of Indian landmark judgments.

Guarantees the system always returns relevant authorities even with no network
or API token. Doubles as a high-trust verification source for citations.

Scored with a simple lexical overlap so it works without embeddings; the
CompositeRetriever blends these with live sources when available.
"""
from __future__ import annotations

import re

from .base import PrecedentResult, PrecedentRetriever

# (title, citation, court, year, summary, tags)
_CASES: list[tuple[str, str, str, int, str, list[str]]] = [
    ("Kesavananda Bharati v. State of Kerala", "AIR 1973 SC 1461", "Supreme Court of India", 1973,
     "Parliament can amend the Constitution but cannot alter its basic structure.",
     ["basic structure", "constitutional amendment", "article 368", "fundamental rights"]),
    ("Maneka Gandhi v. Union of India", "AIR 1978 SC 597", "Supreme Court of India", 1978,
     "Article 21 procedure must be just, fair and reasonable; expanded due process.",
     ["article 21", "due process", "personal liberty", "natural justice", "passport"]),
    ("Indra Sawhney v. Union of India", "AIR 1993 SC 477", "Supreme Court of India", 1992,
     "Upheld OBC reservation with a 50% ceiling and the creamy-layer exclusion.",
     ["reservation", "article 16", "equality", "creamy layer", "obc"]),
    ("Vishaka v. State of Rajasthan", "AIR 1997 SC 3011", "Supreme Court of India", 1997,
     "Laid down binding guidelines against workplace sexual harassment.",
     ["sexual harassment", "workplace", "article 14", "article 21", "gender"]),
    ("S.R. Bommai v. Union of India", "AIR 1994 SC 1918", "Supreme Court of India", 1994,
     "Article 356 is justiciable; secularism is part of the basic structure.",
     ["article 356", "president's rule", "federalism", "secularism", "basic structure"]),
    ("Olga Tellis v. Bombay Municipal Corporation", "AIR 1986 SC 180", "Supreme Court of India", 1985,
     "Right to livelihood is an integral facet of the right to life under Article 21.",
     ["article 21", "right to livelihood", "pavement dwellers", "eviction"]),
    ("Minerva Mills v. Union of India", "AIR 1980 SC 1789", "Supreme Court of India", 1980,
     "Limited amending power and judicial review are part of the basic structure.",
     ["basic structure", "article 368", "judicial review", "directive principles"]),
    ("A.K. Gopalan v. State of Madras", "AIR 1950 SC 27", "Supreme Court of India", 1950,
     "Early narrow reading of Article 21; later overruled by Maneka Gandhi.",
     ["article 21", "preventive detention", "personal liberty"]),
    ("Navtej Singh Johar v. Union of India", "(2018) 10 SCC 1", "Supreme Court of India", 2018,
     "Decriminalised consensual same-sex relations; read down Section 377 IPC.",
     ["section 377", "article 14", "article 15", "article 21", "privacy", "dignity"]),
    ("K.S. Puttaswamy v. Union of India", "(2017) 10 SCC 1", "Supreme Court of India", 2017,
     "Privacy is a fundamental right intrinsic to Articles 14, 19 and 21.",
     ["privacy", "article 21", "aadhaar", "fundamental rights", "proportionality"]),
    ("Shreya Singhal v. Union of India", "AIR 2015 SC 1523", "Supreme Court of India", 2015,
     "Struck down Section 66A IT Act for being vague and chilling free speech.",
     ["section 66a", "article 19", "free speech", "it act", "vagueness"]),
    ("Mohori Bibee v. Dharmodas Ghose", "(1903) ILR 30 Cal 539", "Privy Council", 1903,
     "An agreement by a minor is void ab initio under the Indian Contract Act.",
     ["contract", "minor", "void agreement", "capacity", "section 11"]),
    ("Carlill v. Carbolic Smoke Ball Co.", "[1893] 1 QB 256", "Court of Appeal (England)", 1893,
     "A general offer can be accepted by performance; unilateral contracts.",
     ["contract", "offer", "acceptance", "unilateral", "consideration"]),
    ("Donoghue v. Stevenson", "[1932] AC 562", "House of Lords", 1932,
     "Established the modern law of negligence and the neighbour principle.",
     ["negligence", "duty of care", "tort", "neighbour principle"]),
    ("M.C. Mehta v. Union of India", "AIR 1987 SC 1086", "Supreme Court of India", 1986,
     "Introduced absolute liability for hazardous enterprises (Oleum Gas leak).",
     ["absolute liability", "environment", "hazardous", "tort", "article 21"]),
    ("Rylands v. Fletcher", "(1868) LR 3 HL 330", "House of Lords", 1868,
     "Strict liability for the escape of a dangerous thing from one's land.",
     ["strict liability", "tort", "escape", "non-natural use"]),
    ("Joseph Shine v. Union of India", "(2019) 3 SCC 39", "Supreme Court of India", 2018,
     "Struck down Section 497 IPC (adultery) as violative of Articles 14 and 21.",
     ["adultery", "section 497", "article 14", "article 21", "equality"]),
    ("Indian Young Lawyers Assn. v. State of Kerala", "(2019) 11 SCC 1", "Supreme Court of India", 2018,
     "Sabarimala: barring women of menstruating age violates Articles 14, 15, 21, 25.",
     ["sabarimala", "article 25", "religion", "equality", "essential practices"]),
    ("ADM Jabalpur v. Shivkant Shukla", "AIR 1976 SC 1207", "Supreme Court of India", 1976,
     "Habeas corpus could be suspended in Emergency; widely criticised, since disowned.",
     ["emergency", "habeas corpus", "article 21", "preventive detention"]),
    ("L. Chandra Kumar v. Union of India", "AIR 1997 SC 1125", "Supreme Court of India", 1997,
     "Judicial review under Articles 32 and 226 is part of the basic structure.",
     ["judicial review", "tribunals", "article 323a", "basic structure"]),
]


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


class SeedCorpusRetriever(PrecedentRetriever):
    name = "seed_corpus"

    def __init__(self) -> None:
        self._index = []
        for title, cite, court, year, summary, tags in _CASES:
            blob = " ".join([title, summary, " ".join(tags), str(year)])
            self._index.append((blob, title, cite, court, year, summary, tags))

    async def search(self, query: str, limit: int = 5) -> list[PrecedentResult]:
        q = _tokens(query)
        if not q:
            return []
        scored: list[PrecedentResult] = []
        for blob, title, cite, court, year, summary, tags in self._index:
            doc = _tokens(blob)
            overlap = len(q & doc)
            # weight tag/title hits a little higher
            tag_hits = sum(1 for t in tags if t in query.lower())
            title_hits = sum(1 for w in _tokens(title) if w in q)
            score = overlap + 1.5 * tag_hits + 1.0 * title_hits
            if score <= 0:
                continue
            scored.append(PrecedentResult(
                title=title, citation=cite, court=court, year=year,
                summary=summary, source=self.name,
                url=f"https://www.google.com/search?q={cite.replace(' ', '+')}",
                score=float(score),
            ))
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:limit]
