"""Export the canonical desktop wire schema without importing GUI or services."""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pydantic import create_model
from firik_agent.desktop_rpc.models import METHODS, DesktopEvent


def export() -> dict:
    models = {model.__name__: (model, ...) for pair in METHODS.values() for model in pair}
    models["DesktopEvent"] = (DesktopEvent, ...)
    schema = create_model("DesktopWireTypes", **models).model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://firik.local/desktop/v1"
    schema["x-methods"] = {name: {"params": pair[0].__name__, "result": pair[1].__name__}
                           for name, pair in METHODS.items()}
    return schema


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "packages/contracts/wire.schema.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(export(), ensure_ascii=False, indent=2) + "\n")
    print(f"Exported {len(METHODS)} desktop methods")
