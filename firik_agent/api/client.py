from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import re
from importlib.resources import files
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlsplit
from uuid import UUID

import httpx

DEFAULT_BASE_URL = "https://evren-llmapi.ssyz.org.tr/v1"


def api_schema() -> dict:
    """The checked-in public API contract; usable without credentials/network."""
    return json.loads(files("firik_agent.api").joinpath("openapi.json").read_text(encoding="utf-8"))


def media_data_url(filename: str | Path, content_type: str | None = None) -> str:
    path = Path(filename)
    mime = content_type or mimetypes.guess_type(path.name)[0]
    if not mime:
        raise ValueError("Dosya türü belirlenemedi; --content-type belirtin.")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


class FirikAPIError(RuntimeError):
    def __init__(self, status: int, body: Any, metadata: dict[str, str]):
        self.status_code = status
        self.body = body
        self.metadata = metadata
        detail = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
        request_id = metadata.get("x-request-id") or metadata.get("x-evren-request-id")
        hint = {
            401: "API anahtarını ve türünü kontrol edin.",
            402: "Kredi bakiyesini kontrol edin: firik quota.",
            403: "Anahtar yetkilerini ve şart durumunu kontrol edin: firik terms status.",
            429: "Kota/hız sınırı aşıldı; Retry-After ve firik quota çıktısını kontrol edin.",
            503: "Servis şu anda hazır değil veya kapasitesi dolu.",
        }.get(status, "")
        super().__init__(f"FIRIK HTTP {status}: {detail}" + (f" (request_id={request_id})" if request_id else "") + (f"\n{hint}" if hint else ""))


class FirikAPI:
    """All public LLM API operations. No agent startup or automatic terms acceptance.

    Use as an async context manager. POST requests are never automatically retried.
    Response bodies retain FIRIK extensions; headers are available in last_metadata.
    """

    def __init__(self, api_key: str = "", base_url: str = DEFAULT_BASE_URL,
                 timeout: float = 180, transport: httpx.AsyncBaseTransport | None = None):
        parsed = urlsplit(base_url)
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Geçerli bir API taban URL'si belirtin.")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("API anahtarı için HTTPS gereklidir (yerel testler hariç).")
        if timeout <= 0:
            raise ValueError("Timeout pozitif olmalıdır.")
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3]
        self.api_key = api_key
        self.last_metadata: dict[str, str] = {}
        # Authentication is per API request, never a client default: storage PUTs
        # use presigned credentials and must not receive the FIRIK key.
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False)

    async def __aenter__(self) -> FirikAPI:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    def _headers(self, path: str, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {"User-Agent": "FirikCLI/0.2.4"}

        if path.startswith("/v1/"):
            if not self.api_key:
                raise ValueError("FIRIK_API_KEY eksik. Terminalde `firik login` çalıştırın veya FIRIK_API_KEY ortam değişkenini ayarlayın.")
            headers["X-API-Key"] = self.api_key
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _check_path(self, method: str, path: str) -> None:
        for template, methods in api_schema()["paths"].items():
            pattern = re.sub(r"\\\{[^}]+\\\}", "[^/?#]+", re.escape(template))
            if method.lower() in methods and re.fullmatch(pattern, path):
                if "{" in template:
                    # All public path parameters in this contract are UUIDs.
                    index = template.split("/").index(next(p for p in template.split("/") if p.startswith("{")))
                    UUID(path.split("/")[index])
                return
        raise ValueError(f"Belgelenmiş genel API işlemi değil: {method} {path}")

    def _capture(self, response: httpx.Response) -> None:
        self.last_metadata = {
            key: value for key, value in response.headers.items()
            if key.startswith(("x-evren-", "x-ratelimit-")) or key in ("x-request-id", "retry-after")
        }

    def _redact(self, value: Any) -> Any:
        if isinstance(value, str):
            return value.replace(self.api_key, "[REDACTED]") if self.api_key else value
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        return value

    def _decode(self, response: httpx.Response) -> Any:
        self._capture(response)
        if response.status_code == 204:
            return None
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if not response.is_success:
            raise FirikAPIError(response.status_code, self._redact(body), self._redact(self.last_metadata))
        return body

    async def request(self, method: str, path: str, *, body: dict | None = None,
                      idempotency_key: str | None = None, **kwargs: Any) -> Any:
        self._check_path(method, path)
        response = await self._client.request(method, self.base_url + path,
                                              headers=self._headers(path, idempotency_key),
                                              json=body, **kwargs)
        return self._decode(response)

    async def stream(self, path: str, body: dict, *, idempotency_key: str | None = None) -> AsyncIterator[dict]:
        """Parse chat/completion and typed Responses SSE, including multiline events."""
        if path not in ("/v1/chat/completions", "/v1/completions", "/v1/responses"):
            raise ValueError("Bu işlem SSE desteklemiyor.")
        async with self._client.stream("POST", self.base_url + path,
                                       headers=self._headers(path, idempotency_key),
                                       json={**body, "stream": True}) as response:
            self._capture(response)
            if not response.is_success:
                await response.aread()
                self._decode(response)
            if "text/event-stream" not in response.headers.get("content-type", ""):
                await response.aread()
                raise FirikAPIError(response.status_code, {"message": "SSE yanıtı bekleniyordu", "response": self._redact(self._decode(response))}, self.last_metadata)
            event, data = "message", []
            async for line in response.aiter_lines():
                if not line:
                    if data:
                        yield self._sse_event(event, data)
                    event, data = "message", []
                elif line.startswith("event:"):
                    event = line[6:].lstrip(" ")
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip(" "))
            if data:
                yield self._sse_event(event, data)

    def _sse_event(self, event: str, lines: list[str]) -> dict:
        raw = "\n".join(lines)
        if raw == "[DONE]":
            return {"event": "done", "data": None}
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise RuntimeError("Geçersiz SSE JSON yanıtı.") from exc
        if event in ("error", "response.failed") or isinstance(data, dict) and (data.get("error") or data.get("type") in ("error", "response.failed")):
            raise FirikAPIError(200, self._redact(data), self.last_metadata)
        return {"event": event, "data": data}

    async def models(self) -> Any:
        return await self.request("GET", "/v1/models")

    async def quota(self) -> Any:
        return await self.request("GET", "/v1/quota")

    async def terms_status(self) -> Any:
        return await self.request("GET", "/v1/terms/status")

    async def terms_text(self) -> Any:
        return await self.request("GET", "/v1/terms/text")

    async def accept_terms(self, version: int) -> Any:
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError("Şart sürümü tamsayı olmalıdır.")
        return await self.request("POST", "/v1/terms/accept", body={"version": version})

    async def request_status(self, request_id: str) -> Any:
        return await self.request("GET", f"/v1/requests/{UUID(request_id)}")

    async def chat(self, model: str, messages: list[dict], **options: Any) -> Any:
        return await self.request("POST", "/v1/chat/completions", body={**options, "model": model, "messages": messages})

    async def completions(self, model: str, prompt: str | list[str], **options: Any) -> Any:
        return await self.request("POST", "/v1/completions", body={**options, "model": model, "prompt": prompt})

    async def responses(self, model: str, input: str | list[dict], **options: Any) -> Any:
        return await self.request("POST", "/v1/responses", body={**options, "model": model, "input": input})

    async def embeddings(self, model: str, input: str | list[str], **options: Any) -> Any:
        return await self.request("POST", "/v1/embeddings", body={**options, "model": model, "input": input})

    async def rerank(self, model: str, query: str, documents: list[str], **options: Any) -> Any:
        return await self.request("POST", "/v1/rerank", body={**options, "model": model, "query": query, "documents": documents})

    async def ocr(self, model: str, image: str, **options: Any) -> Any:
        return await self.request("POST", "/v1/ocr", body={**options, "model": model, "image": image})

    async def transcribe(self, filename: str | Path, model: str, **options: Any) -> Any:
        path = Path(filename)
        data = {key: str(value) for key, value in options.items() if value is not None}
        data["model"] = model
        with path.open("rb") as audio:
            return await self.request("POST", "/v1/audio/transcriptions", data=data,
                                      files={"file": (path.name, audio, mimetypes.guess_type(path.name)[0] or "application/octet-stream")})

    async def initiate_media(self, content_type: str, size_bytes: int) -> Any:
        if size_bytes <= 0:
            raise ValueError("Boş medya dosyası yüklenemez.")
        return await self.request("POST", "/v1/media", body={"content_type": content_type, "size_bytes": size_bytes})

    async def media_status(self, media_id: str) -> Any:
        return await self.request("GET", f"/v1/media/{UUID(media_id)}")

    async def complete_media(self, media_id: str, parts: list[dict] | None = None) -> Any:
        return await self.request("POST", f"/v1/media/{UUID(media_id)}/complete", body={"parts": parts} if parts is not None else {})

    async def finalize_media(self, media_id: str) -> Any:
        return await self.request("POST", f"/v1/media/{UUID(media_id)}/finalize")

    async def abort_media(self, media_id: str) -> Any:
        return await self.request("POST", f"/v1/media/{UUID(media_id)}/abort")

    async def _put_storage(self, url: str, content: Any, content_type: str, size: int) -> httpx.Response:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Medya yüklemesi için geçerli bir HTTPS imzalı URL gerekli.")
        try:
            response = await self._client.put(url, content=content, headers={"Content-Type": content_type, "Content-Length": str(size)})
        except httpx.HTTPError as exc:
            # Presigned URLs carry credentials: do not include their URL in errors.
            raise RuntimeError("Medya depolama bağlantısı başarısız.") from exc
        if not response.is_success:
            raise RuntimeError(f"Medya depolama HTTP {response.status_code}")
        return response

    async def upload_media(self, filename: str | Path, content_type: str | None = None,
                           finalize: bool = False) -> Any:
        """Upload without loading the whole file; abort unfinished uploads on failure."""
        path = Path(filename)
        mime = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        size = path.stat().st_size
        initiated = await self.initiate_media(mime, size)
        media_id = initiated["media_id"]
        completed = False
        try:
            parts = None
            with path.open("rb") as source:
                if initiated.get("upload_url"):
                    async def chunks() -> AsyncIterator[bytes]:
                        while chunk := source.read(1024 * 1024):
                            yield chunk
                    await self._put_storage(initiated["upload_url"], chunks(), mime, size)
                elif initiated.get("part_urls"):
                    part_size = initiated.get("part_size_bytes")
                    if not isinstance(part_size, int) or part_size <= 0 or len(initiated["part_urls"]) != (size + part_size - 1) // part_size:
                        raise ValueError("Sunucu geçersiz çok parçalı yükleme planı döndürdü.")
                    parts = []
                    for number, url in enumerate(initiated["part_urls"], 1):
                        remaining = min(part_size, size - (number - 1) * part_size)
                        async def part_chunks() -> AsyncIterator[bytes]:
                            nonlocal remaining
                            while remaining:
                                chunk = source.read(min(1024 * 1024, remaining))
                                if not chunk:
                                    raise ValueError("Yükleme sırasında dosya boyutu değişti.")
                                remaining -= len(chunk)
                                yield chunk
                        response = await self._put_storage(url, part_chunks(), mime, remaining)
                        etag = response.headers.get("etag")
                        if not etag:
                            raise RuntimeError("Depolama yanıtında ETag eksik.")
                        parts.append({"part_number": number, "etag": etag})
                else:
                    raise ValueError("Sunucu yükleme URL'si döndürmedi.")
            result = await self.complete_media(media_id, parts)
            completed = True
            return await self.finalize_media(media_id) if finalize else result
        except (Exception, asyncio.CancelledError) as exc:
            cleanup = "tamamlama gerçekleşti; media status ile kontrol edin"
            if not completed:
                try:
                    await self.abort_media(media_id)
                    cleanup = "abort isteği başarılı"
                except Exception:
                    cleanup = "abort başarısız; media status ile kontrol edin"
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise RuntimeError(f"Medya {media_id}: {exc} ({cleanup})") from exc
