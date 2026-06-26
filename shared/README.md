# Shared contracts

Cross-cutting definitions shared by the backend, the frontend, and remote agents.

- [`enums.json`](enums.json) - the canonical enum vocabulary (outcomes,
  execution modes, executor kinds, categories, root-cause taxonomy). Generated
  from `backend/app/core/enums.py`; regenerate with:

  ```bash
  cd backend && ./venv/bin/python -c "import json,enum; from app.core import enums as E; \
    print(json.dumps({n:[m.value for m in o] for n in dir(E) \
    if isinstance((o:=getattr(E,n)),type) and issubclass(o,enum.Enum) and o is not enum.Enum}, indent=2))" \
    > ../shared/enums.json
  ```

- [`remote_agent_protocol.md`](remote_agent_protocol.md) - the control-plane /
  remote-agent protocol (registration, work claim, signed result callback).

The frontend mirrors these enums as TypeScript string-literal unions in
[`src/types.ts`](../src/types.ts). The full request/response schema is available
live from the backend at `/docs` (OpenAPI).
