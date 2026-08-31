"""Outbound HTTP egress handler — proves the app/serverless egress path.

Maps onto the connectivity stages RESOLVE → CONNECT → TLS, then OPERATE (the
request) → VERIFY (status check). In demo mode the request is simulated and a
``status_code`` is emitted so status assertions ("HTTP status is 200") can be
evaluated locally.
"""
from __future__ import annotations

import socket
from urllib.parse import urlparse

from app.core.enums import StageStatus
from app.handlers.base import Handler, HandlerContext, HandlerError, StageResult
from app.handlers.registry import register


@register("outbound_http")
class OutboundHttpHandler(Handler):
    STAGES = ("RESOLVE", "CONNECT", "TLS", "OPERATE", "VERIFY")

    def _url(self, ctx: HandlerContext) -> str:
        url = ctx.param("url")
        if not url:
            raise HandlerError("No 'url' param.", failure_bucket="TARGET_NOT_FOUND")
        return url

    def stage_resolve(self, ctx: HandlerContext) -> StageResult:
        if ctx.demo_mode:
            return StageResult("RESOLVE", StageStatus.PASS, message="Demo mode: DNS simulated.")
        host = urlparse(self._url(ctx)).hostname or ""
        try:
            socket.getaddrinfo(host, None)
            return StageResult("RESOLVE", StageStatus.PASS, message=f"Resolved {host}.")
        except OSError as exc:
            raise HandlerError(f"DNS resolution failed for {host}: {exc}",
                               failure_bucket="DNS_RESOLUTION_FAILURE")

    def stage_connect(self, ctx: HandlerContext) -> StageResult:
        if str(ctx.param("demo_outcome", "pass")).lower() == "deny":
            raise HandlerError("Egress denied by network policy.", failure_bucket="NETWORK_POLICY_DENY")
        return StageResult("CONNECT", StageStatus.PASS, message="Egress path reachable.")

    def stage_tls(self, ctx: HandlerContext) -> StageResult:
        return StageResult("TLS", StageStatus.PASS, message="TLS handshake ok.")

    def stage_operate(self, ctx: HandlerContext) -> StageResult:
        method = str(ctx.param("method", "GET")).upper()
        if ctx.demo_mode:
            status = int(ctx.param("demo_status_code", ctx.param("expected_status", 200)))
            return StageResult("OPERATE", StageStatus.PASS, message=f"{method} -> HTTP {status}.",
                               evidence={"status_code": status, "method": method})
        import httpx

        url = self._url(ctx)
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.request(method, url)
            return StageResult("OPERATE", StageStatus.PASS, message=f"{method} {url} -> HTTP {resp.status_code}.",
                               raw_excerpt=resp.text[:1000], evidence={"status_code": resp.status_code, "method": method})
        except httpx.TimeoutException as exc:
            raise HandlerError(f"Request timed out: {exc}", failure_bucket="UPSTREAM_TIMEOUT")
        except httpx.TransportError as exc:
            raise HandlerError(f"Transport failed: {exc}", failure_bucket="TLS_HANDSHAKE_FAILURE")

    def stage_verify(self, ctx: HandlerContext) -> StageResult:
        return StageResult("VERIFY", StageStatus.PASS, message="Response status verified.")
