"""Write the API's OpenAPI schema to a file (frontend `npm run gen:api` turns it into TypeScript types).

    uv run python -m scripts.dump_openapi ../frontend/openapi.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.main import create_app


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")
    schema = create_app().openapi()  # no lifespan: building the schema needs no DB or LLM
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(schema['paths'])} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
