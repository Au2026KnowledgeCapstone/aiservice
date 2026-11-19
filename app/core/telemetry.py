"""Opt-in tracing. Export operational metadata, never authentication payloads."""
from urllib.parse import urlsplit

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.integrations.httpx import HttpxIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration


def safe_path(value: str) -> str:
    # Callback/download path segments can contain one-time credentials.
    path = urlsplit(value).path
    if "/auth/callback/" in path:
        return "/auth/callback/[redacted]"
    if "/downloads/" in path:
        return "/downloads/[redacted]"
    fixed = set("v1 jobs cancel imports environments verify health heath openapi.json docs redoc".split())
    return "/".join(part if not part or part in fixed or (part.startswith("{") and part.endswith("}")) else "{id}" for part in path.split("/"))


def scrub_span(span: dict) -> dict:
    # SQL and HTTP descriptions/data may contain passwords, emails or queries.
    span["description"] = span.get("op", "operation")
    data = span.get("data", {})
    span["data"] = {key: data[key] for key in ("http.request.method", "http.response.status_code") if key in data}
    return span


def scrub_event(event: dict, hint=None) -> dict:
    event["server_name"] = "blindspot-aiservice"
    for key in ("user", "extra", "breadcrumbs", "logentry", "modules"):
        event.pop(key, None)
    event["tags"] = {key: value for key, value in event.get("tags", {}).items() if key in {"request_id", "service"}}
    request = event.get("request", {})
    event["request"] = {"method": request.get("method"), "url": safe_path(request.get("url", "/"))}
    event["contexts"] = {"trace": {key: value for key, value in event.get("contexts", {}).get("trace", {}).items() if key in {"trace_id", "span_id", "parent_span_id", "op", "status"}}}
    if "message" in event:
        event["message"] = "Application error (details withheld; inspect stack and request ID)."
    for exception in event.get("exception", {}).get("values", []):
        exception["value"] = "[redacted]"
        for frame in exception.get("stacktrace", {}).get("frames", []):
            for key in list(frame):
                if key not in {"filename", "function", "module", "lineno", "colno", "in_app"}:
                    frame.pop(key, None)
    if "transaction" in event:
        event["transaction"] = safe_path(event["transaction"])
    for span in event.get("spans", []):
        scrub_span(span)
    return event


def scrub_log(log: dict, hint=None) -> dict | None:
    # Only our fixed completion message enters Sentry Logs; SDK/third-party
    # logging and arbitrary application messages cannot export payloads.
    if log.get("body") != "Request completed":
        return None
    attrs = log.get("attributes", {})
    allowed = {"request_id", "trace_id", "span_id", "method", "route", "status_code", "duration_ms", "sentry.trace.parent_span_id"}
    log["attributes"] = {key: value for key, value in attrs.items() if key in allowed}
    return log


def scrub_streamed_span(span: dict, hint=None) -> dict:
    attrs = span.get("attributes", {})
    allowed = {"sentry.op", "sentry.origin", "sentry.kind", "http.request.method", "http.response.status_code", "request_id"}
    span["attributes"] = {key: value for key, value in attrs.items() if key in allowed}
    # Names may contain raw URLs or SQL. Retain a sanitized route for servers.
    span["name"] = safe_path(span.get("name", "/")) if span.get("is_segment") else "operation"
    return span


def configure_telemetry(settings) -> bool:
    if not settings.sentry_dsn:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        release=settings.sentry_release,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        trace_lifecycle="stream",
        data_collection={"user_info": False, "cookies": False, "http_headers": False, "http_bodies": [], "url_query_params": False},
        max_request_body_size="never",
        include_local_variables=False,
        default_integrations=False,
        integrations=[StarletteIntegration(), FastApiIntegration(), HttpxIntegration(), SqlalchemyIntegration()],
        # Don't propagate correlation headers to arbitrary application URLs.
        trace_propagation_targets=settings.sentry_trace_propagation_targets,
        before_send=scrub_event,
        before_send_span=scrub_streamed_span,
        enable_logs=True,
        before_send_log=scrub_log,
    )
    return True


class RequestTelemetryMiddleware:
    """Correlate requests without copying headers, request bodies or raw URLs."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from time import perf_counter
        from uuid import UUID, uuid4
        from sentry_sdk import logger
        from sentry_sdk.traces import get_current_span

        raw = next((v.decode("latin1") for k, v in scope.get("headers", []) if k.lower() == b"x-request-id"), "")
        try:
            request_id = str(UUID(raw))
        except (ValueError, AttributeError):
            request_id = str(uuid4())
        start = perf_counter()
        status_code = 500

        async def correlated_send(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message = dict(message)
                message["headers"] = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"] + [(b"x-request-id", request_id.encode("ascii"))]
            await send(message)

        with sentry_sdk.isolation_scope() as isolation:
            isolation.set_tag("request_id", request_id)
            isolation.set_tag("service", "blindspot-aiservice")
            try:
                await self.app(scope, receive, correlated_send)
            finally:
                span = get_current_span()
                if span is not None and span.sampled:
                    route = getattr(scope.get("route"), "path", "/{unmatched}")
                    logger.info("Request completed", attributes={
                        "request_id": request_id, "route": safe_path(route),
                        "method": scope.get("method"), "status_code": status_code,
                        "duration_ms": round((perf_counter() - start) * 1000, 2),
                    })
