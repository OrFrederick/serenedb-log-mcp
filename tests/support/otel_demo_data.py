"""Synthetic otel_demo files for the db tests (T21): the SELECTs that COPY writes as Parquet.

See tests/db/otel/conftest.py for the scenario. The column names and types are those of the real
otel_demo files that the views in sql/remote/otel_demo.sql read.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

TRACE = hashlib.md5(b"t21-investigated-trace").hexdigest()
SPAN = {
    name: hashlib.md5(name.encode()).hexdigest()[:16] for name in ("L1", "F1", "C1", "C2", "C3", "P1", "P2")
}

LOGS = f"""
SELECT ts AS "Timestamp", date_trunc('second', ts) AS "TimestampTime", trace AS "TraceId", span AS "SpanId",
       service AS "ServiceName", level AS "SeverityText", body AS "Body"
FROM (
    SELECT TIMESTAMPTZ '2025-05-25 00:30:00.100+00' AS ts, '{TRACE}' AS trace, '{SPAN["P1"]}' AS span,
           'payment' AS service, 'info' AS level, 'Charge request received.' AS body
    UNION ALL SELECT TIMESTAMPTZ '2025-05-25 00:30:00.110+00', '{TRACE}', '{SPAN["P1"]}', 'payment', 'error',
           'Visa cache full: cannot add new item.'
    UNION ALL SELECT TIMESTAMPTZ '2025-05-25 00:30:00.150+00', '{TRACE}', '{SPAN["F1"]}', 'frontend', 'error',
           'Failed to place order'
    UNION ALL
    SELECT TIMESTAMPTZ '2025-05-25 00:00:00+00' + i * INTERVAL '6 seconds',
           CASE WHEN i % 7 = 0 THEN '' ELSE md5('bg' || i) END,
           CASE WHEN i % 7 = 0 THEN '' ELSE left(md5('bgspan' || i), 16) END,
           CASE WHEN i % 3 = 0 THEN 'frontend' ELSE 'cart' END,
           CASE WHEN i % 5 = 0 THEN 'error' ELSE 'info' END,
           'request ' || i
    FROM range(0, 600) AS r(i)
) AS logs
ORDER BY ts
"""

_SPANS = [
    # offset ms, span, parent, service, operation, kind, duration ms, status, message
    (0, "L1", "", "load-generator", "POST", "Client", 200, "Error", ""),
    (10, "F1", "L1", "frontend", "POST", "Server", 180, "Error", ""),
    (20, "C1", "F1", "checkout", "oteldemo.CheckoutService/PlaceOrder", "Server", 100, "Error",
     "failed to charge card: Visa cache full: cannot add new item."),
    (30, "C3", "C1", "cart", "POST /oteldemo.CartService/GetCart", "Server", 50, "Unset", ""),
    (90, "C2", "C1", "checkout", "oteldemo.PaymentService/Charge", "Client", 25, "Error",
     "Visa cache full: cannot add new item."),
    (95, "P1", "C2", "payment", "grpc.oteldemo.PaymentService/Charge", "Server", 20, "Unset", ""),
    (110, "P2", "P1", "payment", "Error: Visa cache full: cannot add new item.", "Internal", 0, "Error", ""),
]  # fmt: skip
_SPAN_VALUES = ",\n    ".join(
    f"(TIMESTAMPTZ '2025-05-25 00:30:00+00' + INTERVAL '{offset} milliseconds', '{TRACE}', '{SPAN[span]}', "
    f"'{SPAN[parent] if parent else ''}', '{service}', '{operation}', '{kind}', {duration * 1_000_000}, "
    f"'{status}', '{message}')"
    for offset, span, parent, service, operation, kind, duration, status, message in _SPANS
)
TRACES = f"""
SELECT ts AS "Timestamp", trace AS "TraceId", span AS "SpanId", parent AS "ParentSpanId",
       service AS "ServiceName",
       operation AS "SpanName", kind AS "SpanKind", CAST(duration_ns AS UBIGINT) AS "Duration",
       status AS "StatusCode", message AS "StatusMessage"
FROM (VALUES
    {_SPAN_VALUES}
) AS t(ts, trace, span, parent, service, operation, kind, duration_ns, status, message)
UNION ALL
SELECT TIMESTAMPTZ '2025-05-25 00:00:00+00' + i * INTERVAL '30 seconds', md5('bg' || i),
       left(md5('bgspan' || i), 16),
       '', 'cart', 'POST /oteldemo.CartService/GetCart', 'Server', CAST(10000000 AS UBIGINT), 'Unset', ''
FROM range(0, 100) AS r(i)
"""

GAUGE = """
SELECT TIMESTAMPTZ '2025-05-25 00:00:00+00' + m * INTERVAL '1 minute' + s * INTERVAL '1 second' AS "TimeUnix",
       service AS "ServiceName",
       CASE WHEN service = '' THEN 'system.cpu.load_average.1m'
            ELSE 'container.cpu.utilization' END AS "MetricName",
       '1' AS "MetricUnit",
       CASE WHEN service = 'payment' AND m BETWEEN 29 AND 31 THEN 0.5 WHEN service = 'payment' THEN 0.02
            WHEN service = 'checkout' THEN 0.05 ELSE 1.0 END AS "Value"
FROM range(0, 60) AS r(m), (VALUES (10), (40)) AS t(s), (VALUES ('payment'), ('checkout'), ('')) AS v(service)
"""

SUM = """
SELECT TIMESTAMPTZ '2025-05-25 00:00:00+00' + m * INTERVAL '1 minute' + INTERVAL '30 seconds' AS "TimeUnix",
       TIMESTAMPTZ '2025-05-25 00:00:00+00' AS "StartTimeUnix",
       service AS "ServiceName", metric AS "MetricName", unit AS "MetricUnit",
       CAST(CASE metric WHEN 'app.payment.transactions' THEN 10 * m - CASE WHEN m >= 30 THEN 9 ELSE 0 END
                        WHEN 'jvm.memory.used' THEN 100
                        ELSE 1000 * m END AS DOUBLE) AS "Value",
       metric <> 'jvm.memory.used' AS "IsMonotonic",
       MAP {'app.payment.card_type': 'visa'} AS "Attributes",
       MAP {'k8s.pod.name': service || '-1'} AS "ResourceAttributes"
FROM range(0, 60) AS r(m),
     (VALUES ('payment', 'app.payment.transactions', ''), ('payment', 'jvm.memory.used', 'By'),
             ('', 'system.network.io', 'By')) AS v(service, metric, unit)
"""

HISTOGRAM = """
SELECT TIMESTAMPTZ '2025-05-25 00:00:00+00' + m * INTERVAL '1 minute' + INTERVAL '30 seconds' AS "TimeUnix",
       TIMESTAMPTZ '2025-05-25 00:00:00+00' AS "StartTimeUnix",
       'payment' AS "ServiceName", 'http.client.duration' AS "MetricName", 'ms' AS "MetricUnit",
       CAST(10 * (m + 1) AS UBIGINT) AS "Count",
       CAST(200 * (m + 1) + CASE WHEN m >= 30 THEN 800 ELSE 0 END AS DOUBLE) AS "Sum",
       MAP {'http.method': 'POST'} AS "Attributes",
       MAP {'k8s.pod.name': 'payment-1'} AS "ResourceAttributes"
FROM range(0, 60) AS r(m)
"""

FILES = {
    "logs": LOGS,
    "traces": TRACES,
    "metrics_gauge": GAUGE,
    "metrics_sum": SUM,
    "metrics_histogram": HISTOGRAM,
}


@dataclass(frozen=True)
class OtelDemo:
    """Where the synthetic files are on the server and the scratch schema set up over them."""

    schema: str
    directory: str
