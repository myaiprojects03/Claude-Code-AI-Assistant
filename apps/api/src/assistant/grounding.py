"""Citation accuracy: are the specific facts in an answer actually in its sources?

"Key facts" are the things hallucinations hurt most and that can be checked mechanically:
money, quantities, percentages, times, years and dates, IP ranges and identifiers/codes
(T-1003, NBL-YYYY-####, 600F, Exchange 2016...). Wording can be paraphrased; these can't.

Rules:
* A fact in a sentence that cites [n] must appear in source n (the citation must be right).
* A fact in a sentence with no citation must appear in at least one source.
* Facts the user supplied in their own question are allowed.
* Citation markers must point at a source that exists.
"""

import re
from dataclasses import dataclass, field

_MONEY = r"£\s?\d[\d,]*(?:\.\d+)?\s?(?:k|m)?"
_TIME = r"\b\d{1,2}[:.]\d{2}\b|\b\d{1,2}\s?(?:am|pm)\b"
_IP = r"\b\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?\b"
_PCT = r"\b\d+(?:\.\d+)?\s?%"
# Identifiers: case-sensitive, and must contain a digit or "#" (T-1003, NBL-YYYY-####, 600F),
# so ordinary hyphenated words ("follow-up", "site-to-site") are not treated as facts.
_CODE = (
    r"(?-i:(?<![\w])[A-Z]{1,5}-(?=[A-Z0-9#-]*[0-9#])[A-Z0-9#]+(?:-[A-Z0-9#]+)*(?![\w#])"
    r"|\b\d+[A-Z]{1,3}\b)"
)
_NUM = r"\b\d[\d,]*(?:\.\d+)?\b"
FACT = re.compile("|".join(f"(?:{p})" for p in (_MONEY, _IP, _TIME, _PCT, _CODE, _NUM)), re.I)
CITE = re.compile(r"\[(\d{1,2})\]")
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def _norm(fact: str) -> str:
    f = fact.lower().replace(" ", "").replace(",", "")
    if m := re.fullmatch(r"(\d{1,2})(?:[:.](\d{2}))?(am|pm)", f):  # 6pm -> 18:00
        h = int(m.group(1)) % 12 + (12 if m.group(3) == "pm" else 0)
        return f"{h:02d}:{m.group(2) or '00'}"
    if re.fullmatch(r"\d{1,2}\.\d{2}", f):  # 18.00 -> 18:00
        f = f.replace(".", ":")
    if re.fullmatch(r"\d:\d{2}", f):
        f = "0" + f
    return f


def facts_in(text: str) -> set[str]:
    text = CITE.sub(" ", text)  # citation markers are not facts
    text = re.sub(r"(?m)^\s*\d{1,2}[.)]\s", " ", text)  # list numbering "1. " is not a fact
    out = set()
    for m in FACT.finditer(text):
        f = _norm(m.group(0))
        if re.fullmatch(r"\d", f):  # single digits ("3 batches") are too ambiguous to check
            continue
        out.add(f)
    return out


def _haystack(text: str) -> str:
    t = text.lower()
    t = re.sub(r"(?<=\d),(?=\d)", "", t)  # 38,400 -> 38400
    t = re.sub(r"£\s+", "£", t)
    # Times in sources may be written 18:00 or 6pm; index both spellings.
    extra = [_norm(m.group(0)) for m in re.finditer(_TIME, text, re.I)]
    return t + " | " + " | ".join(extra)


def _supported(fact: str, haystack: str) -> bool:
    # Whole-token match: "18" must not be "found" inside "180".
    return re.search(rf"(?<![\w]){re.escape(fact)}(?![\w])", haystack) is not None


@dataclass
class Grounding:
    unsupported: list[str] = field(default_factory=list)  # fact (sentence)
    bad_citations: list[int] = field(default_factory=list)  # [n] with no source n
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.unsupported and not self.bad_citations


def check(answer: str, sources: list[str], question: str = "") -> Grounding:
    """`sources` are the full texts the model was given, in citation order ([1] = sources[0])."""
    g = Grounding()
    allowed = facts_in(question)
    hay_all = _haystack(" ".join(sources))
    hay_n = [_haystack(s) for s in sources]
    for sentence in SENTENCE.split(answer):
        cited = [int(n) for n in CITE.findall(sentence)]
        for n in cited:
            if not 1 <= n <= len(sources):
                g.bad_citations.append(n)
        valid = [n for n in cited if 1 <= n <= len(sources)]
        for fact in facts_in(sentence) - allowed:
            g.checked += 1
            if valid:
                ok = any(_supported(fact, hay_n[n - 1]) for n in valid)
            else:
                ok = _supported(fact, hay_all)
            if not ok:
                g.unsupported.append(f"{fact} ({sentence.strip()[:80]})")
    return g


TO_CONFIRM = "[TO CONFIRM]"


def redact_unsupported(answer: str, given: str, user_text: str = "") -> tuple[str, list[str]]:
    """Runtime safety net for the internal assistant: replace any key fact (figure, code,
    date...) that appears neither in the text the model was given nor in the person's own
    messages with [TO CONFIRM]. Small local models invent prices and percentages in drafts
    even when told not to (eval finding). Returns (new answer, facts replaced)."""
    allowed = facts_in(user_text)
    hay = _haystack(given)
    # Citation markers and list numbering are not facts; never touch them.
    protected = [m.span() for m in CITE.finditer(answer)]
    protected += [m.span() for m in re.finditer(r"(?m)^\s*\d{1,2}[.)]\s", answer)]
    out, removed, pos = [], [], 0
    for m in FACT.finditer(answer):
        s, e = m.span()
        if any(ps <= s < pe for ps, pe in protected):
            continue
        f = _norm(m.group(0))
        if re.fullmatch(r"\d", f) or f in allowed or _supported(f, hay):
            continue
        out.append(answer[pos:s] + TO_CONFIRM)
        removed.append(m.group(0).strip())
        pos = e
    out.append(answer[pos:])
    return "".join(out), removed
