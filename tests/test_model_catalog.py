from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from firik_agent.model_catalog import (
    DownloadManager,
    ModelCatalog,
    ModelCatalogError,
    validate_model_id,
)


def model_info(model_id: str = "Qwen/Qwen2.5-Coder-7B-Instruct") -> SimpleNamespace:
    return SimpleNamespace(
        id=model_id,
        downloads=120_000,
        likes=700,
        pipeline_tag="text-generation",
        library_name="transformers",
        tags=["transformers", "license:apache-2.0", "code"],
        card_data={"license": "apache-2.0"},
        config={"tokenizer_config": {"chat_template": "{% if tools %}<tool_call>"}},
        safetensors=SimpleNamespace(total=7_615_616_512),
        siblings=[
            SimpleNamespace(rfilename="config.json", size=663),
            SimpleNamespace(rfilename="model.safetensors", size=15_200_000_000),
        ],
        last_modified=None,
    )


class FakeApi:
    def __init__(self) -> None:
        self.search_arguments: dict[str, Any] = {}

    def list_models(self, **kwargs: Any) -> list[SimpleNamespace]:
        self.search_arguments = kwargs
        return [model_info(), model_info("unsafe-without-owner")]

    def model_info(self, repo_id: str, **kwargs: Any) -> SimpleNamespace:
        assert kwargs["files_metadata"] is True
        return model_info(repo_id)


def test_catalog_search_and_detail_are_bounded_and_tool_aware() -> None:
    api = FakeApi()
    catalog = ModelCatalog(api=api)

    results = catalog.search("  coding   agent  ", limit=100)
    detail = catalog.detail("Qwen/Qwen2.5-Coder-7B-Instruct")

    assert [item["model_id"] for item in results] == ["Qwen/Qwen2.5-Coder-7B-Instruct"]
    assert results[0]["tool_calling"] is True
    assert api.search_arguments["search"] == "coding agent"
    assert api.search_arguments["limit"] == 25
    assert detail["license"] == "apache-2.0"
    assert detail["parameters"] == 7_615_616_512
    assert detail["size_bytes"] == 15_200_000_663
    assert detail["safe_serialization"] is True


def test_catalog_lists_only_model_repositories() -> None:
    cache = SimpleNamespace(
        repos=[
            SimpleNamespace(
                repo_id="org/model",
                repo_type="model",
                size_on_disk=42,
                nb_files=3,
                last_accessed=4.0,
                last_modified=3.0,
                revisions={"main"},
            ),
            SimpleNamespace(
                repo_id="org/data",
                repo_type="dataset",
                size_on_disk=99,
                nb_files=1,
                last_accessed=1.0,
                last_modified=1.0,
                revisions={"main"},
            ),
        ],
        size_on_disk=141,
    )
    catalog = ModelCatalog(api=FakeApi(), scan_cache=lambda **kwargs: cache)

    assert catalog.local_models() == [
        {
            "model_id": "org/model",
            "size_bytes": 42,
            "files": 3,
            "last_accessed": 4.0,
            "last_modified": 3.0,
            "revisions": 1,
        }
    ]


@pytest.mark.parametrize("model_id", ["model", "../secret/model", "owner/../model", "a/b/c"])
def test_model_id_validation_rejects_unsafe_values(model_id: str) -> None:
    with pytest.raises(ModelCatalogError, match="owner/model"):
        validate_model_id(model_id)


def test_download_manager_reports_file_and_total_progress(tmp_path: Path) -> None:
    finished = threading.Event()
    dry_run = [
        SimpleNamespace(
            filename="config.json",
            file_size=10,
            is_cached=True,
            commit_hash="abc123",
        ),
        SimpleNamespace(
            filename="model.safetensors",
            file_size=100,
            is_cached=False,
            commit_hash="abc123",
        ),
    ]

    def snapshot(*args: Any, **kwargs: Any) -> list[SimpleNamespace]:
        assert kwargs["dry_run"] is True
        assert "*.safetensors" in kwargs["allow_patterns"]
        return dry_run

    def download(*args: Any, **kwargs: Any) -> str:
        bar = kwargs["tqdm_class"](total=100, initial=0)
        with bar:
            bar.update(40)
            bar.update(60)
        target = tmp_path / "snapshots" / "abc123" / kwargs["filename"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"weights")
        finished.set()
        return str(target)

    manager = DownloadManager(cache_dir=tmp_path, snapshot_fn=snapshot, download_fn=download)
    created = manager.start("org/model")

    assert finished.wait(2)
    for _ in range(100):
        job = manager.get(created["job_id"])
        if job and job["status"] == "complete":
            break
        time.sleep(0.01)

    assert job is not None
    assert job["status"] == "complete"
    assert job["downloaded_bytes"] == 110
    assert job["progress"] == 1.0
    assert [item["status"] for item in job["files"]] == ["complete", "complete"]


def test_download_rejects_model_without_safetensors(tmp_path: Path) -> None:
    manager = DownloadManager(
        cache_dir=tmp_path,
        snapshot_fn=lambda *args, **kwargs: [
            SimpleNamespace(
                filename="pytorch_model.bin",
                file_size=10,
                is_cached=False,
                commit_hash="abc",
            )
        ],
        download_fn=lambda *args, **kwargs: "unused",
    )
    created = manager.start("org/unsafe-model")

    for _ in range(100):
        job = manager.get(created["job_id"])
        if job and job["status"] == "failed":
            break
        time.sleep(0.01)

    assert job is not None
    assert job["status"] == "failed"
    assert "safetensors" in (job["error"] or "")
