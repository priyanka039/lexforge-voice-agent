"""Indian legal vocabulary / hotwords.

Fed to the STT layer as a biasing prompt so the recogniser favours legal terms
("writ petition" not "right petition", "locus standi" not "locust standi").
Also used to detect legal topics in an utterance for the orchestrator.
"""
from __future__ import annotations

LEGAL_VOCAB_VERSION = "1.0.0"

LEGAL_HOTWORDS: tuple[str, ...] = (
    # Procedure & remedies
    "writ petition", "special leave petition", "SLP", "public interest litigation", "PIL",
    "locus standi", "res judicata", "sub judice", "stare decisis", "obiter dicta",
    "ratio decidendi", "per incuriam", "ultra vires", "intra vires", "mandamus",
    "certiorari", "prohibition", "quo warranto", "habeas corpus", "interim relief",
    "ad interim", "ex parte", "suo motu", "amicus curiae", "vakalatnama",
    "cause of action", "limitation", "cognizance", "remand", "review petition",
    "curative petition", "interlocutory application",
    # Constitutional
    "Article 14", "Article 19", "Article 21", "Article 32", "Article 226",
    "fundamental rights", "directive principles", "basic structure", "due process",
    "equal protection", "reasonable classification", "manifest arbitrariness",
    "doctrine of severability", "doctrine of eclipse", "rule of law",
    "separation of powers", "judicial review", "natural justice", "audi alteram partem",
    "nemo judex in causa sua", "proportionality",
    # Statutes & codes
    "Indian Penal Code", "IPC", "Code of Criminal Procedure", "CrPC",
    "Code of Civil Procedure", "CPC", "Indian Evidence Act", "Bharatiya Nyaya Sanhita",
    "Bharatiya Nagarik Suraksha Sanhita", "Bharatiya Sakshya Adhiniyam",
    "Contract Act", "Specific Relief Act", "Arbitration and Conciliation Act",
    "Companies Act", "Information Technology Act",
    # Parties / actors
    "appellant", "respondent", "petitioner", "complainant", "accused", "plaintiff",
    "defendant", "amicus", "learned counsel", "my lord", "my lady", "your lordship",
    "honourable court", "the bench", "the registry",
    # Citation reporters
    "AIR", "SCC", "SCR", "SCALE", "Supreme Court Cases", "All India Reporter",
    "Indian Kanoon",
    # Latin maxims
    "actus reus", "mens rea", "prima facie", "bona fide", "mala fide", "inter alia",
    "ipso facto", "ratio", "volenti non fit injuria", "ubi jus ibi remedium",
    "audi alteram partem", "ejusdem generis", "noscitur a sociis", "pari materia",
    "ex post facto", "double jeopardy", "autrefois acquit",
)


def stt_biasing_prompt() -> str:
    """A compact string passed to STT as context to bias decoding."""
    return (
        "This is an Indian moot court argument. Expect formal legal English with "
        "Indian case citations and Latin maxims. Likely terms: "
        + ", ".join(LEGAL_HOTWORDS[:120])
        + "."
    )


def detect_topics(text: str) -> list[str]:
    """Cheap keyword topic detection for the shared state."""
    low = text.lower()
    hits = []
    for term in LEGAL_HOTWORDS:
        if term.lower() in low and len(term) > 3:
            hits.append(term)
    # de-dupe preserving order
    seen = set()
    out = []
    for h in hits:
        if h.lower() not in seen:
            seen.add(h.lower())
            out.append(h)
    return out[:6]
