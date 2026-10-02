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


def script_test(client, script: str, name: str = "Script test", *, active: bool = True,
                headers: dict | None = None, **fields) -> dict:
    """Create a script Test through the API; ``active`` accepts it so it can run."""
    r = client.post("/api/v1/tests/script", json={"name": name, "script": script, **fields},
                    headers=headers or {})
    assert r.status_code == 201, r.text
    test = r.json()
    if active:
        r = client.post(f"/api/v1/tests/{test['id']}/accept", headers=headers or {})
        assert r.status_code == 200, r.text
        test = r.json()
    return test
