"""Safe Hugging Face model discovery, cache inventory, and downloads."""

from __future__ import annotations

import re
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_MODEL_FILES = [
    "*.json",
    "*.jinja",
    "*.model",
    "*.safetensors",
    "*.tiktoken",
    "*.txt",
    "LICENSE*",
    "README.md",
    "merges.txt",
    "tokenizer.*",
    "vocab.*",
]
_MAX_RESULTS = 25
_MAX_DOWNLOAD_FILES = 256
_DISK_RESERVE_BYTES = 1024**3


class ModelCatalogError(RuntimeError):
    """Raised when catalog input or a remote model is unsuitable."""


class DownloadCancelled(RuntimeError):
    """Internal control flow for a user-cancelled download."""


class ModelApi(Protocol):
    def list_models(self, **kwargs: Any) -> Iterable[Any]: ...

    def model_info(self, repo_id: str, **kwargs: Any) -> Any: ...


def _default_api() -> ModelApi:
    from huggingface_hub import HfApi

    return cast(ModelApi, HfApi())


def _default_scan_cache(*, cache_dir: Path | None = None) -> Any:
    from huggingface_hub import scan_cache_dir

    return scan_cache_dir(cache_dir=cache_dir)


def _default_snapshot(*args: Any, **kwargs: Any) -> Any:
    from huggingface_hub import snapshot_download

    return snapshot_download(*args, **kwargs)


def _default_download(*args: Any, **kwargs: Any) -> Any:
    from huggingface_hub import hf_hub_download

    return hf_hub_download(*args, **kwargs)


def validate_model_id(model_id: str) -> str:
    """Return a normalized Hub model id or reject traversal-like input."""
    normalized = model_id.strip()
    if not _MODEL_ID.fullmatch(normalized) or ".." in normalized:
        raise ModelCatalogError("Model id must use the form 'owner/model'")
    return normalized


def _bounded_text(value: Any, limit: int = 240) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:limit] if text else None


def _card_value(model: Any, key: str) -> Any:
    card = getattr(model, "card_data", None)
    if card is None:
        return None
    if hasattr(card, "to_dict"):
        return card.to_dict().get(key)
    if isinstance(card, dict):
        return card.get(key)
    return getattr(card, key, None)


def _tool_calling(model: Any) -> bool:
    config = getattr(model, "config", None) or {}
    tokenizer = config.get("tokenizer_config", {}) if isinstance(config, dict) else {}
    template = tokenizer.get("chat_template", "") if isinstance(tokenizer, dict) else ""
    tags = [str(tag).lower() for tag in (getattr(model, "tags", None) or [])]
    return "tools" in template or any(
        marker in tag for tag in tags for marker in ("tool-use", "tool-calling", "function-calling")
    )


def _parameter_count(model: Any) -> int | None:
    safetensors = getattr(model, "safetensors", None)
    total = getattr(safetensors, "total", None)
    return int(total) if isinstance(total, int) else None


def _model_size(model: Any) -> int | None:
    sizes = [
        int(sibling.size)
        for sibling in (getattr(model, "siblings", None) or [])
        if getattr(sibling, "size", None) is not None
    ]
    return sum(sizes) if sizes else None


def _license(model: Any) -> str | None:
    value = _card_value(model, "license")
    if value:
        return _bounded_text(value, 80)
    for tag in getattr(model, "tags", None) or []:
        if str(tag).startswith("license:"):
            return _bounded_text(str(tag).split(":", 1)[1], 80)
    return None


@dataclass(frozen=True, slots=True)
class ModelSummary:
    model_id: str
    author: str
    downloads: int
    likes: int
    pipeline_tag: str | None
    library_name: str | None
    license: str | None
    parameters: int | None
    size_bytes: int | None
    tool_calling: bool
    tags: tuple[str, ...]
    last_modified: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tags"] = list(self.tags)
        return payload


class ModelCatalog:
    """Bounded read-only view of public models and the local Hub cache."""

    def __init__(
        self,
        api: ModelApi | None = None,
        *,
        cache_dir: str | Path | None = None,
        scan_cache: Callable[..., Any] = _default_scan_cache,
    ) -> None:
        self.api = api or _default_api()
        self.cache_dir = Path(cache_dir).expanduser() if cache_dir else None
        self._scan_cache = scan_cache

    def search(self, query: str, *, limit: int = 12) -> list[dict[str, Any]]:
        clean_query = " ".join(query.split())[:120]
        bounded_limit = max(1, min(limit, _MAX_RESULTS))
        models = self.api.list_models(
            search=clean_query or None,
            pipeline_tag="text-generation",
            filter=["transformers"],
            sort="downloads",
            limit=bounded_limit,
            full=True,
            cardData=True,
            fetch_config=True,
        )
        results: list[dict[str, Any]] = []
        for model in models:
            model_id = _bounded_text(getattr(model, "id", None), 225)
            if not model_id or not _MODEL_ID.fullmatch(model_id):
                continue
            summary = self._summary(model)
            if summary.library_name not in {None, "transformers"}:
                continue
            results.append(summary.to_dict())
            if len(results) >= bounded_limit:
                break
        return results

    def detail(self, model_id: str) -> dict[str, Any]:
        identifier = validate_model_id(model_id)
        model = self.api.model_info(
            identifier,
            files_metadata=True,
            securityStatus=True,
        )
        summary = self._summary(model).to_dict()
        summary.update(
            {
                "url": f"https://huggingface.co/{identifier}",
                "description": (
                    f"Published by {summary['author']} for "
                    f"{summary['pipeline_tag'] or 'local inference'}. "
                    "Review the upstream model card and license before use."
                ),
                "safe_serialization": any(
                    str(getattr(sibling, "rfilename", "")).endswith(".safetensors")
                    for sibling in (getattr(model, "siblings", None) or [])
                ),
            }
        )
        return summary

    def local_models(self) -> list[dict[str, Any]]:
        try:
            info = self._scan_cache(cache_dir=self.cache_dir)
        except FileNotFoundError:
            return []
        models = []
        for repo in info.repos:
            if repo.repo_type != "model":
                continue
            models.append(
                {
                    "model_id": _bounded_text(repo.repo_id, 225),
                    "size_bytes": int(repo.size_on_disk),
                    "files": int(repo.nb_files),
                    "last_accessed": float(repo.last_accessed),
                    "last_modified": float(repo.last_modified),
                    "revisions": len(repo.revisions),
                }
            )
        return sorted(models, key=lambda item: item["last_accessed"], reverse=True)

    def storage(self) -> dict[str, int]:
        base = self.cache_dir or Path.home() / ".cache" / "huggingface" / "hub"
        existing = base
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        usage = shutil.disk_usage(existing)
        try:
            cache_bytes = int(self._scan_cache(cache_dir=self.cache_dir).size_on_disk)
        except FileNotFoundError:
            cache_bytes = 0
        return {
            "capacity_bytes": usage.total,
            "free_bytes": usage.free,
            "used_bytes": usage.used,
            "model_cache_bytes": cache_bytes,
        }

    @staticmethod
    def _summary(model: Any) -> ModelSummary:
        identifier = str(model.id)
        author = identifier.split("/", 1)[0]
        last_modified = getattr(model, "last_modified", None)
        return ModelSummary(
            model_id=identifier,
            author=author,
            downloads=int(getattr(model, "downloads", None) or 0),
            likes=int(getattr(model, "likes", None) or 0),
            pipeline_tag=_bounded_text(getattr(model, "pipeline_tag", None), 80),
            library_name=_bounded_text(getattr(model, "library_name", None), 80),
            license=_license(model),
            parameters=_parameter_count(model),
            size_bytes=_model_size(model),
            tool_calling=_tool_calling(model),
            tags=tuple(
                _bounded_text(tag, 80) or "" for tag in (getattr(model, "tags", None) or [])[:24]
            ),
            last_modified=_bounded_text(last_modified, 80),
        )


@dataclass(slots=True)
class DownloadFile:
    filename: str
    size_bytes: int
    downloaded_bytes: int = 0
    status: str = "queued"


@dataclass(slots=True)
class DownloadJob:
    job_id: str
    model_id: str
    status: str = "preparing"
    total_bytes: int = 0
    downloaded_bytes: int = 0
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    local_path: str | None = None
    error: str | None = None
    files: list[DownloadFile] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    pause_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_dict(self) -> dict[str, Any]:
        elapsed = max(0.001, time.time() - self.started_at)
        rate = self.downloaded_bytes / elapsed if self.status in {"downloading", "paused"} else 0.0
        remaining = max(0, self.total_bytes - self.downloaded_bytes)
        eta = remaining / rate if rate > 0 else None
        return {
            "job_id": self.job_id,
            "model_id": self.model_id,
            "status": self.status,
            "total_bytes": self.total_bytes,
            "downloaded_bytes": min(self.downloaded_bytes, self.total_bytes),
            "progress": (
                min(1.0, self.downloaded_bytes / self.total_bytes) if self.total_bytes else 0.0
            ),
            "bytes_per_second": rate,
            "eta_seconds": eta,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "local_path": self.local_path,
            "error": self.error,
            "files": [asdict(item) for item in self.files[:_MAX_DOWNLOAD_FILES]],
        }


class DownloadManager:
    """Run resumable safe-safetensors downloads in background threads."""

    def __init__(
        self,
        *,
        cache_dir: str | Path | None = None,
        snapshot_fn: Callable[..., Any] = _default_snapshot,
        download_fn: Callable[..., Any] = _default_download,
    ) -> None:
        self.cache_dir = Path(cache_dir).expanduser() if cache_dir else None
        self._snapshot = snapshot_fn
        self._download = download_fn
        self._jobs: dict[str, DownloadJob] = {}
        self._lock = threading.RLock()

    def start(self, model_id: str) -> dict[str, Any]:
        identifier = validate_model_id(model_id)
        with self._lock:
            active = next(
                (
                    job
                    for job in self._jobs.values()
                    if job.model_id == identifier
                    and job.status in {"preparing", "downloading", "paused"}
                ),
                None,
            )
            if active:
                return active.to_dict()
            job = DownloadJob(job_id=uuid.uuid4().hex[:12], model_id=identifier)
            self._jobs[job.job_id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job.to_dict()

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda item: item.started_at, reverse=True)
            return [job.to_dict() for job in jobs]

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_dict() if job else None

    def pause(self, job_id: str) -> dict[str, Any]:
        job = self._require_active(job_id)
        job.pause_event.set()
        with self._lock:
            job.status = "paused"
            job.updated_at = time.time()
            return job.to_dict()

    def resume(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "paused":
                raise ModelCatalogError("Download is not paused")
            job.pause_event.clear()
            job.status = "downloading"
            job.updated_at = time.time()
            return job.to_dict()

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self._require_active(job_id)
        job.cancel_event.set()
        job.pause_event.clear()
        with self._lock:
            job.status = "cancelling"
            job.updated_at = time.time()
            return job.to_dict()

    def _require_active(self, job_id: str) -> DownloadJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in {"preparing", "downloading", "paused"}:
                raise ModelCatalogError("Download is not active")
            return job

    def _run(self, job: DownloadJob) -> None:
        try:
            dry_run = cast(
                list[Any],
                self._snapshot(
                    job.model_id,
                    cache_dir=self.cache_dir,
                    allow_patterns=_SAFE_MODEL_FILES,
                    dry_run=True,
                ),
            )
            if not dry_run or not any(item.filename.endswith(".safetensors") for item in dry_run):
                raise ModelCatalogError("Model has no supported safetensors weights")
            if len(dry_run) > _MAX_DOWNLOAD_FILES:
                raise ModelCatalogError("Model repository contains too many files")
            files = [
                DownloadFile(
                    filename=str(item.filename)[:300],
                    size_bytes=int(item.file_size),
                    downloaded_bytes=int(item.file_size) if item.is_cached else 0,
                    status="complete" if item.is_cached else "queued",
                )
                for item in sorted(dry_run, key=lambda entry: entry.filename)
            ]
            total = sum(item.size_bytes for item in files)
            cached = sum(item.downloaded_bytes for item in files)
            target = self.cache_dir or Path.home() / ".cache" / "huggingface" / "hub"
            existing = target
            while not existing.exists() and existing != existing.parent:
                existing = existing.parent
            if total - cached + _DISK_RESERVE_BYTES > shutil.disk_usage(existing).free:
                raise ModelCatalogError("Not enough free disk space for this model")
            with self._lock:
                job.files = files
                job.total_bytes = total
                job.downloaded_bytes = cached
                job.status = "downloading"
                job.updated_at = time.time()

            for item, source in zip(
                files,
                sorted(dry_run, key=lambda entry: entry.filename),
                strict=True,
            ):
                if item.status == "complete":
                    continue
                self._wait_if_paused(job)
                self._check_cancelled(job)
                with self._lock:
                    item.status = "downloading"
                    job.updated_at = time.time()
                progress = self._progress_class(job, item)
                local_path = self._download(
                    job.model_id,
                    filename=item.filename,
                    revision=source.commit_hash,
                    cache_dir=self.cache_dir,
                    tqdm_class=progress,
                )
                with self._lock:
                    delta = item.size_bytes - item.downloaded_bytes
                    item.downloaded_bytes = item.size_bytes
                    item.status = "complete"
                    job.downloaded_bytes += max(0, delta)
                    job.local_path = str(Path(local_path).parent)
                    job.updated_at = time.time()

            with self._lock:
                job.status = "complete"
                job.downloaded_bytes = job.total_bytes
                job.completed_at = time.time()
                job.updated_at = job.completed_at
        except DownloadCancelled:
            with self._lock:
                job.status = "cancelled"
                job.error = "Cancelled; partial files were kept for resume"
                job.completed_at = time.time()
                job.updated_at = job.completed_at
                for item in job.files:
                    if item.status == "downloading":
                        item.status = "partial"
        except Exception as exc:
            with self._lock:
                job.status = "failed"
                job.error = _bounded_text(exc, 500) or type(exc).__name__
                job.completed_at = time.time()
                job.updated_at = job.completed_at

    def _progress_class(self, job: DownloadJob, item: DownloadFile) -> type[Any]:
        manager = self

        class JobProgress(_SilentTqdm):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                initial = max(0, int(kwargs.get("initial") or 0))
                with manager._lock:
                    remaining = max(0, item.size_bytes - item.downloaded_bytes)
                    applied = min(initial, remaining)
                    item.downloaded_bytes += applied
                    job.downloaded_bytes += applied

            def update(self, amount: int | float | None = 1) -> bool | None:
                manager._wait_if_paused(job)
                manager._check_cancelled(job)
                increment = max(0, int(amount or 0))
                with manager._lock:
                    remaining = max(0, item.size_bytes - item.downloaded_bytes)
                    applied = min(increment, remaining)
                    item.downloaded_bytes += applied
                    job.downloaded_bytes += applied
                    job.updated_at = time.time()
                return super().update(amount)

        return JobProgress

    @staticmethod
    def _wait_if_paused(job: DownloadJob) -> None:
        while job.pause_event.is_set() and not job.cancel_event.wait(0.2):
            continue

    @staticmethod
    def _check_cancelled(job: DownloadJob) -> None:
        if job.cancel_event.is_set():
            raise DownloadCancelled


class _SilentTqdm:
    """Small tqdm-compatible no-output implementation used by Hub callbacks."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.total = kwargs.get("total") or 0
        self.n = kwargs.get("initial") or 0
        self.desc = kwargs.get("desc") or ""

    def __enter__(self) -> _SilentTqdm:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def update(self, amount: int | float | None = 1) -> bool | None:
        self.n += amount or 0
        return None

    def close(self) -> None:
        return None

    def refresh(self) -> None:
        return None

    def set_description(self, description: str, refresh: bool = True) -> None:
        self.desc = description

    def set_postfix_str(self, postfix: str, refresh: bool = True) -> None:
        return None

    def set_transfer_postfix_str(self, postfix: str, refresh: bool = True) -> None:
        return None
