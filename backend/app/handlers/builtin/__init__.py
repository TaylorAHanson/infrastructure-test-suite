"""Built-in handlers. Importing this package registers all of them."""
from app.handlers.builtin import (  # noqa: F401
    generic,
    lakebase,
    outbound_http,
    sql_warehouse,
)
