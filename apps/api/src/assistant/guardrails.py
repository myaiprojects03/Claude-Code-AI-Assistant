"""Deterministic safety net on public replies.

The system prompt asks the model not to quote prices, promise outcomes or commit to delivery
dates. Models sometimes do anyway, so every public reply is checked here before it is shown.
A blocked reply is replaced with a safe fallback that steers to a discovery call.

Input checks only flag likely prompt-injection attempts (logged + counted); the system prompt
already treats them as untrusted, and blocking legitimate visitors would be worse.
"""

import re
from dataclasses import dataclass

from prometheus_client import Counter

GUARDRAIL_BLOCKS = Counter("guardrail_blocks_total", "Replies replaced by guardrails", ["rule"])
INJECTION_FLAGS = Counter("prompt_injection_flags_total", "User inputs flagged as injection")

_MONEY = (
    r"(?:[£$€]\s?\d[\d,.]*\s?(?:k|m|bn)?"
    r"|\b\d[\d,.]*\s?(?:k|m)?\s?(?:gbp|usd|eur|pounds?|dollars?|euros?)\b)"
)

OUTPUT_RULES: dict[str, re.Pattern[str]] = {
    # Any amount of money in our reply. Visitors' own figures are fine in *their* messages.
    "price": re.compile(_MONEY, re.I),
    "rate": re.compile(r"\b\d[\d,.]*\s?(?:an?|per|/)\s?(?:hour|hr|day|month|user|seat)\b", re.I),
    "discount": re.compile(r"\b\d{1,2}\s?%\s?(?:off|discount)|\bdiscount(?:ed)? (?:of|by)\b", re.I),
    "guarantee": re.compile(
        r"\bguarantee[sd]?\b|\bwe promise\b|\b100\s?%\s?(?:secure|safe|uptime|guaranteed)\b"
        r"|\b(?:we|i)(?: will|'ll) (?:definitely|certainly) (?:fix|solve|deliver)",
        re.I,
    ),
    "deadline": re.compile(
        r"\b(?:we|it|this|the project|migration)\b[^.?!]{0,40}\b(?:within|in|by)\s+"
        r"(?:\d+|a|one|two|three|four|six)\s+(?:business |working )?(?:days?|weeks?|months?)\b",
        re.I,
    ),
    "fits_budget": re.compile(r"\b(?:fits?|within|enough for|covers?) (?:your|the) budget\b", re.I),
}

INJECTION = re.compile(
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier) (?:instructions|rules)"
    r"|disregard (?:your|the) (?:rules|instructions)|reveal (?:your|the) (?:system )?prompt"
    r"|you are now (?:a|an|in)\b|developer mode|jailbreak|\bDAN\b",
    re.I,
)


@dataclass
class Verdict:
    allowed: bool
    rule: str | None = None
    match: str | None = None


# Promise-type rules are fine when negated in the same sentence:
# "No one can guarantee 100% security" is exactly the answer we want.
NEGATABLE = {"guarantee", "deadline", "fits_budget"}
_NEGATION = re.compile(
    r"\b(?:no|not|never|cannot|can't|won't|isn't|aren't|don't|doesn't|nobody|no one|unable"
    r"|impossible|without)\b",
    re.I,
)


def _negated(text: str, start: int, end: int) -> bool:
    """A negation earlier in the same sentence, or inside the match itself."""
    sentence_start = max(text.rfind(p, 0, start) for p in ".!?\n") + 1
    return bool(_NEGATION.search(text[max(sentence_start, start - 60) : end]))


def check_reply(text: str) -> Verdict:
    text = (text or "").replace("’", "'")  # curly apostrophes: "can’t" -> "can't"
    for rule, pattern in OUTPUT_RULES.items():
        for m in pattern.finditer(text):
            if rule in NEGATABLE and _negated(text, m.start(), m.end()):
                continue
            GUARDRAIL_BLOCKS.labels(rule=rule).inc()
            return Verdict(False, rule, m.group(0))
    return Verdict(True)


def find_violations(text: str) -> list[Verdict]:
    """Every rule match in `text` (not just the first), without counting them as blocks.
    Used to check public content before it can be published from the dashboard."""
    text = (text or "").replace("’", "'")
    found = []
    for rule, pattern in OUTPUT_RULES.items():
        for m in pattern.finditer(text):
            if rule in NEGATABLE and _negated(text, m.start(), m.end()):
                continue
            found.append(Verdict(False, rule, m.group(0)))
    return found


def flag_injection(text: str) -> bool:
    hit = bool(INJECTION.search(text or ""))
    if hit:
        INJECTION_FLAGS.inc()
    return hit
