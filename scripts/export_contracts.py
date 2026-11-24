import json
from pathlib import Path

from control_plane.entrypoints.api import app
from platform_contracts.models import (
    ArtifactManifest,
    Command,
    NodeEvent,
    NodeUpdate,
    ProtocolError,
)

root = Path(__file__).resolve().parents[1] / "contracts/generated"
root.mkdir(parents=True, exist_ok=True)
(root / "openapi.json").write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n")
for model in [Command, NodeEvent, NodeUpdate, ArtifactManifest, ProtocolError]:
    (root / f"{model.__name__}.json").write_text(
        json.dumps(model.model_json_schema(), indent=2) + "\n"
    )
