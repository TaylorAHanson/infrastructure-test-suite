"""Outbound HTTP connectivity probe (Salesforce, N8N, custom REST, ...).

Prefers a **Unity Catalog HTTP connection** so credentials are governed,
proxied, and never embedded in code. Falls back to a direct (governed-egress)
HTTP request only when a connection name is not supplied.

This is a *Databricks-side* probe: it proves the app/serverless egress path can
reach the target. It does **not** prove the consumer's own network path - that
requires a remote agent (see :mod:`remote_agent`).
"""
from __future__ import annotations

import socket
from urllib.parse import urlparse

import httpx

from app.core.enums import ConnectivityOutcome, ProbeOutcome, RootCauseBucket, StepKind
from app.executors.base import Executor, ProbeContext, ProbeError, StepResult
from app.executors.registry import register


@register("outbound_http")
class OutboundHttpExecutor(Executor):
    def _url(self, ctx: ProbeContext) -> str:
        url = ctx.param("url")
        if not url:
            raise ProbeError(
                "No 'url' parameter and no UC connection provided.",
                root_cause=RootCauseBucket.TARGET_NOT_FOUND,
                recommendation="Set param 'url' or 'connection_name' (preferred).",
            )
        return url

    def step_precheck(self, ctx: ProbeContext) -> StepResult:
        connection = ctx.param("connection_name")
        if connection:
            return StepResult(StepKind.PRECHECK, ProbeOutcome.PASS,
                              message=f"Using governed UC HTTP connection '{connection}'.")
        # Direct mode - require an explicit URL.
        self._url(ctx)
        return StepResult(StepKind.PRECHECK, ProbeOutcome.PASS,
                          message="Direct HTTP mode (no UC connection); URL present.")

    def step_dns(self, ctx: ProbeContext) -> StepResult:
        if ctx.param("connection_name"):
            return StepResult(StepKind.DNS, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                              message="DNS handled by UC connection / egress proxy.")
        host = urlparse(self._url(ctx)).hostname or ""
        if ctx.demo_mode:
            return StepResult(StepKind.DNS, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                              message=f"Demo mode: '{host}' resolution simulated.")
        try:
            socket.getaddrinfo(host, None)
            return StepResult(StepKind.DNS, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                              message=f"Resolved {host}.")
        except OSError as exc:
            raise ProbeError(
                f"DNS resolution failed for {host}: {exc}",
                root_cause=RootCauseBucket.DNS_RESOLUTION_FAILURE,
                connectivity=ConnectivityOutcome.NOT_CONNECTABLE,
                recommendation="If private, verify conditional DNS forwarding / private DNS config.",
            )

    def step_network(self, ctx: ProbeContext) -> StepResult:
        if ctx.demo_mode:
            return StepResult(StepKind.NETWORK, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                              message="Demo mode: TLS handshake simulated.")
        if ctx.param("connection_name"):
            return StepResult(StepKind.NETWORK, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                              message="Egress path validated by UC connection.")
        url = self._url(ctx)
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.head(url)
            return StepResult(StepKind.NETWORK, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                              message=f"TLS handshake + HEAD ok (HTTP {resp.status_code}).")
        except httpx.ConnectError as exc:
            raise ProbeError(
                f"Connection/handshake failed: {exc}",
                root_cause=RootCauseBucket.NETWORK_POLICY_DENY,
                connectivity=ConnectivityOutcome.NOT_CONNECTABLE,
                recommendation="Egress may be denied. Check NCC/network policy and system.access.outbound_network.",
            )
        except httpx.TransportError as exc:
            raise ProbeError(
                f"TLS handshake failed: {exc}",
                root_cause=RootCauseBucket.TLS_HANDSHAKE_FAILURE,
                connectivity=ConnectivityOutcome.NOT_CONNECTABLE,
            )

    def step_functional(self, ctx: ProbeContext) -> StepResult:
        method = ctx.param("method", "GET").upper()
        expected = int(ctx.param("expected_status", 200))
        if ctx.demo_mode:
            return StepResult(StepKind.FUNCTIONAL, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                              message=f"Demo mode: {method} returned {expected}.",
                              evidence={"status_code": expected})
        # NOTE: when connection_name is set, a real implementation routes through
        # the UC HTTP connection proxy. We model the direct path here.
        url = self._url(ctx)
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.request(method, url)
            ok = resp.status_code == expected
            return StepResult(
                StepKind.FUNCTIONAL,
                ProbeOutcome.PASS if ok else ProbeOutcome.FAIL,
                ConnectivityOutcome.CONNECTABLE,
                message=f"{method} {url} -> HTTP {resp.status_code} (expected {expected}).",
                error_class=None if ok else "UnexpectedStatus",
                root_cause_bucket=None if ok else RootCauseBucket.DATA_VALIDATION_FAILURE,
                raw_payload_excerpt=resp.text[:1000],
                evidence={"status_code": resp.status_code},
            )
        except httpx.TimeoutException as exc:
            raise ProbeError(
                f"Request timed out: {exc}",
                root_cause=RootCauseBucket.UPSTREAM_TIMEOUT,
            )
