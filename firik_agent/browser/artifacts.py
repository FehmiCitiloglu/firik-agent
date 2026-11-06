"""Private, ephemeral screenshot artifacts. No model-selected filesystem paths."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tempfile
from uuid import uuid4

from firik_agent.browser.models import BrowserError, BrowserIdentity


@dataclass
class BrowserArtifact:
    artifact_id: str
    owner: BrowserIdentity
    path: Path
    size: int


class BrowserArtifacts:
    def __init__(self):
        self._temporary = None
        self._artifacts: dict[str, BrowserArtifact] = {}

    def save(self, identity: BrowserIdentity, data: bytes) -> BrowserArtifact:
        if self._temporary is None:
            self._temporary = tempfile.TemporaryDirectory(prefix="firik-browser-")
        artifact_id = "screenshot_" + uuid4().hex
        path = Path(self._temporary.name) / (artifact_id + ".png")
        with path.open("xb") as stream:
            stream.write(data)
        path.chmod(0o600)
        artifact = BrowserArtifact(artifact_id, identity, path, len(data))
        self._artifacts[artifact_id] = artifact
        return artifact

    def path(self, identity: BrowserIdentity, artifact_id: str) -> Path:
        artifact = self._artifacts.get(artifact_id)
        if artifact is None or artifact.owner != identity:
            raise BrowserError("ownership_denied", "Screenshot does not belong to this execution identity.")
        return artifact.path

    def remove_owner(self, identity: BrowserIdentity) -> None:
        for key, artifact in list(self._artifacts.items()):
            if artifact.owner == identity:
                artifact.path.unlink(missing_ok=True)
                del self._artifacts[key]

    def close(self) -> None:
        self._artifacts.clear()
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
