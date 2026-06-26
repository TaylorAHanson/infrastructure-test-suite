"""Built-in executors. Importing this package registers all of them."""
from app.executors.builtin import (  # noqa: F401
    lakebase,
    outbound_http,
    remote_agent,
    sql_warehouse,
    stubs,
)
