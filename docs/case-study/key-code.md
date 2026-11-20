# Key Code (for the case study)

Copied from the project source. Lines starting `# ...` mark where I shortened a block to
stay within 25 lines; everything else is the code as it is in the repository.

## 1. Reply guardrail that blocks prices and promises

File: `apps/api/src/assistant/guardrails.py`. Every reply is checked before the visitor
sees it; a match replaces the reply with a safe fallback.

```python
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
    # ... "deadline" and "fits_budget" rules
}


def check_reply(text: str) -> Verdict:
    text = (text or "").replace("’", "'")  # curly apostrophes: "can’t" -> "can't"
    for rule, pattern in OUTPUT_RULES.items():
        for m in pattern.finditer(text):
            if rule in NEGATABLE and _negated(text, m.start(), m.end()):
                continue
            GUARDRAIL_BLOCKS.labels(rule=rule).inc()
            return Verdict(False, rule, m.group(0))
    return Verdict(True)
```

## 2. Access filter applied inside the vector search

File: `apps/api/src/assistant/kb/internal.py`. The role and per-consultant grants become a
SQL condition in the same query as the similarity ranking, so documents a person may not see
are never candidates.

```python
def access_filter(access: Access) -> Any:
    """SQL condition for chunks this user may see. Applied before ranking, in the same query."""
    role_ok = InternalChunk.allowed_roles.overlap(sorted(access.roles) or ["__none__"])
    if access.all_clients:
        return role_ok
    client_ok = InternalChunk.client_id.is_(None)  # firm-wide documents
    if access.client_ids:
        client_ok = or_(client_ok, InternalChunk.client_id.in_(access.client_ids))
    return and_(client_ok, role_ok)
# ... in search():
    dist = InternalChunk.embedding.cosine_distance(qv).label("dist")
    # With a filter, HNSW needs iterative scanning to still return k rows (pgvector >= 0.8).
    await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    rows = (
        await session.execute(
            select(
                # ... document, section, content, title, source, company (+ a join to Company)
                dist,
            )
            .join(Document, Document.id == InternalChunk.document_id)
            .where(access_filter(access))
            .order_by(dist)
            .limit(k)
        )
    ).all()
```

## 3. Outbox worker: SKIP LOCKED claim and retry with backoff

File: `apps/api/src/assistant/outbox/worker.py`. Every email, Slack post and ticket goes
through this queue; several workers can run without picking up the same item twice.

```python
# ... claim(): lock a batch of due items; rows another worker holds are skipped
select(OutboxItem)
.where(
    OutboxItem.status == OutboxStatus.pending,
    OutboxItem.next_attempt_at <= func.now(),
)
.order_by(OutboxItem.id)
.limit(self.s.outbox_batch_size)
.with_for_update(skip_locked=True)
# ... process(): the handler failed; dead-letter it or schedule a retry
elif permanent or item.attempts >= item.max_attempts:
    # ... status=OutboxStatus.dead, an alert is sent
    outcome = "dead"
else:
    delay = backoff_seconds(item.attempts)
    await s.execute(
        update(OutboxItem)
        .where(OutboxItem.id == item.id)
        .values(
            status=OutboxStatus.pending,
            last_error=error,
            locked_at=None,
            next_attempt_at=datetime.now(UTC) + timedelta(seconds=delay),
        )
    )
```

`backoff_seconds` is exponential with ±20% jitter: about 5 s, 10 s, 20 s, 40 s, capped at
1 hour.
