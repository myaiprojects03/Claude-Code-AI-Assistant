from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("http_request_seconds", "HTTP latency", ["method", "route"])

LLM_REQUESTS = Counter("llm_requests_total", "LLM calls", ["provider", "outcome"])
LLM_LATENCY = Histogram(
    "llm_request_seconds", "LLM latency", ["provider"], buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 32, 64)
)

TOOL_CALLS = Counter("tool_calls_total", "Tool calls", ["tool", "outcome"])

OUTBOX_PROCESSED = Counter("outbox_processed_total", "Outbox items processed", ["kind", "outcome"])
OUTBOX_PENDING = Gauge("outbox_pending", "Outbox items waiting")
OUTBOX_DEAD = Gauge("outbox_dead", "Outbox items that exhausted retries")

ALERTS_SENT = Counter("alerts_sent_total", "Alerts sent", ["severity", "delivered"])
