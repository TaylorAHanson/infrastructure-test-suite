"""Script text the platform writes: the new-Test template."""
from __future__ import annotations


def new_test_script(name: str = "", description: str = "") -> str:
    title = name.strip() or "New test"
    about = description.strip() or "What this test proves about a workspace, in a sentence or two."
    return f'''"""{title}

{about}
"""
# `w` is the Databricks SDK WorkspaceClient, running as the target's identity.
# Each `with check("...")` block is one result: it passes when the block
# finishes, and fails on an `assert` or an API error. Every call goes through
# the safety gate for the target's safety class; quick runs make only reads.

with check("the identity authenticates"):
    me = w.current_user.me()
    record("identity", me.user_name)
    assert me.active is not False, f"{{me.user_name}} is deactivated"

with check("at least one SQL warehouse exists"):
    warehouses = w.warehouses.list()
    record("warehouses", [wh.name for wh in warehouses])
    assert warehouses, "no SQL warehouses on this workspace"
'''
