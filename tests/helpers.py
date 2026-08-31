"""Shared lookup helpers for the API-seam tests."""
from __future__ import annotations


def target_id(client, safety_class: str) -> str:
    """The id of the first seeded Target with the given safety class."""
    return next(t for t in client.get("/api/v1/targets").json()
               if t["safety_class"] == safety_class)["id"]


def test_id(client, name: str) -> str:
    """The id of the seeded Test with the given name."""
    return next(t for t in client.get("/api/v1/tests").json() if t["name"] == name)["id"]


def suite_id(client, name: str) -> str:
    """The id of the seeded Suite with the given name."""
    return next(s for s in client.get("/api/v1/suites").json() if s["name"] == name)["id"]
