from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from firik_agent.cli import build_parser
from firik_agent.model_server import ModelLibraryApplication, ModelLibraryServer


class FakeCatalog:
    def storage(self) -> dict[str, int]:
        return {"capacity_bytes": 100, "free_bytes": 60, "used_bytes": 40, "model_cache_bytes": 10}

    def search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        return [{"model_id": "org/model", "query": query, "limit": limit}]

    def local_models(self) -> list[dict[str, Any]]:
        return [{"model_id": "org/local"}]

    def detail(self, model_id: str) -> dict[str, Any]:
        return {"model_id": model_id, "safe_serialization": True}


class FakeDownloads:
    def __init__(self) -> None:
        self.started: list[str] = []

    def list(self) -> list[dict[str, Any]]:
        return []

    def get(self, job_id: str) -> dict[str, Any] | None:
        return None

    def start(self, model_id: str) -> dict[str, Any]:
        self.started.append(model_id)
        return {"job_id": "job-1", "model_id": model_id, "status": "preparing"}

    def pause(self, job_id: str) -> dict[str, Any]:
        return {"job_id": job_id, "status": "paused"}

    def resume(self, job_id: str) -> dict[str, Any]:
        return {"job_id": job_id, "status": "downloading"}

    def cancel(self, job_id: str) -> dict[str, Any]:
        return {"job_id": job_id, "status": "cancelling"}


@contextmanager
def running_server() -> Iterator[tuple[str, FakeDownloads]]:
    downloads = FakeDownloads()
    app = ModelLibraryApplication(catalog=FakeCatalog(), downloads=downloads)  # type: ignore[arg-type]
    server = ModelLibraryServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", downloads
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def read_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.loads(response.read())


def test_server_exposes_api_and_packaged_ui() -> None:
    with running_server() as (base, _):
        assert read_json(f"{base}/api/health")["ok"] is True
        assert read_json(f"{base}/api/models/search?q=coding&limit=4")["models"][0] == {
            "model_id": "org/model",
            "query": "coding",
            "limit": 4,
        }
        with urllib.request.urlopen(base, timeout=2) as response:
            page = response.read().decode("utf-8")
            assert "Firik Agent · Model Library" in page
            assert response.headers["X-Frame-Options"] == "DENY"


def test_server_starts_download_only_from_json_post() -> None:
    with running_server() as (base, downloads):
        request = urllib.request.Request(
            f"{base}/api/downloads",
            data=json.dumps({"model_id": "org/model"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.loads(response.read())
        assert payload["status"] == "preparing"
        assert downloads.started == ["org/model"]

        invalid = urllib.request.Request(
            f"{base}/api/downloads",
            data=b"org/other",
            headers={"Content-Type": "text/plain"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(invalid, timeout=2)
        assert error.value.code == 415
        assert downloads.started == ["org/model"]


def test_models_cli_is_loopback_server_command() -> None:
    arguments = build_parser().parse_args(["models", "--port", "9876", "--no-open"])

    assert arguments.command == "models"
    assert arguments.port == 9876
    assert arguments.no_open is True
