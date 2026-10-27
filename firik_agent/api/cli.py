"""Scriptable FIRIK commands. stdout is JSON/text/JSONL; diagnostics use stderr."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Sequence

import httpx
import yaml

from firik_agent.config import load_config
from firik_agent.credentials import DEFAULTS, ensure_api_key
from .client import DEFAULT_BASE_URL, FirikAPI, api_schema, media_data_url


@contextmanager
def output_target(filename: str | None):
    """Only replace output files after success; failed requests preserve existing data."""
    if not filename:
        yield sys.stdout
        return
    target = Path(filename)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.absolute().parent,
                                         prefix=f".{target.name}.", delete=False) as output:
            temporary = Path(output.name)
            yield output
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_json(value: str) -> Any:
    if value == "-":
        return json.load(sys.stdin)
    if value.startswith("@"):
        return json.loads(Path(value[1:]).read_text(encoding="utf-8"))
    return json.loads(value)


def read_text(value: str) -> str:
    if value == "-":
        return sys.stdin.read()
    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8")
    return value


def common_options(parser: argparse.ArgumentParser) -> None:
    # SUPPRESS lets options work both before and after a subcommand without
    # an absent child default overwriting an explicit parent value.
    parser.add_argument("--config", "-c", default=argparse.SUPPRESS, help="config.yaml yolu")
    parser.add_argument("--base-url", default=argparse.SUPPRESS, help="Doğrudan FIRIK API taban adresi")
    parser.add_argument("--timeout", type=float, default=argparse.SUPPRESS, help="İstek zaman aşımı (saniye; varsayılan 180)")
    parser.add_argument("--output", "-o", default=argparse.SUPPRESS, help="Çıktıyı UTF-8 dosyasına yaz")
    parser.add_argument("--text", action="store_true", default=argparse.SUPPRESS, help="JSON yerine yanıt metni (akışta metin deltaları)")
    parser.add_argument("--metadata", action="store_true", default=argparse.SUPPRESS, help="İstek/kredi/kota başlıklarını stderr'e yaz")


def build_parser(prog: str = "firik") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="FIRIK API terminal istemcisi — agent başlatmadan doğrudan işlemler",
                                     epilog="Harness Lab: firik harness doctor | firik harness bench --suite offline-smoke (API anahtarı gerekmez)")
    common_options(parser)
    commands = parser.add_subparsers(dest="command")

    def command(name: str, help: str, parent=commands):
        item = parent.add_parser(name, help=help, description=help)
        common_options(item)
        return item

    login = command("login", "API anahtarını gizli girişle kaydet veya yenile")
    login.add_argument("--provider", choices=list(DEFAULTS), default="firik")
    command("models", "Anahtarın erişebildiği modeller, modaliteler ve fiyatlar")
    command("quota", "Token kotası, yenilenme zamanı, kalan ve rezerve kredi")
    health = command("health", "Servis canlılık/hazırlık kontrolü (anahtar gerekmez)")
    health.add_argument("--ready", action="store_true", help="/readyz kontrolü")
    request = command("request-status", "İsteğin yürütme ve ücretlendirme durumu")
    request.add_argument("request_id")

    terms = command("terms", "Kullanım şartlarını oku, durumunu sorgula veya sürümü kabul et")
    terms_sub = terms.add_subparsers(dest="action", required=True)
    command("status", "Güncel şart kabul durumu", terms_sub)
    command("text", "Kabul edilecek metni oku", terms_sub)
    accept = command("accept", "Okuduğunuz tam sürümü açıkça kabul edin", terms_sub)
    accept.add_argument("version", type=int)

    for name, help in [
        ("chat", "Sohbet, görsel anlama, araç çağrıları ve yapılandırılmış çıktılar"),
        ("completions", "Klasik metin tamamlama"),
        ("responses", "Responses API: metin/yapılandırılmış girdi ve olay akışı"),
        ("embeddings", "Bir veya birden fazla metin için embedding üret"),
        ("rerank", "Belgeleri sorguya göre yeniden sırala"),
        ("ocr", "Görselden belge/metin çıkar"),
    ]:
        item = command(name, help)
        item.add_argument("--model", "-m", help="Model kimliği; güncel liste: firik models")
        item.add_argument("--body", help="Tam JSON gövdesi, @dosya.json veya - (stdin)")
        item.add_argument("--set", action="append", default=[], metavar="ALAN=JSON", help="Ek API parametresi; tekrar edilebilir")
        if name in ("chat", "completions", "responses"):
            item.add_argument("--prompt", "-p", help="Metin, @dosya.txt veya - (stdin)")
            item.add_argument("--stream", action="store_true", help="SSE; varsayılan çıktı JSONL olaylarıdır")
            item.add_argument("--max-tokens", type=int)
            item.add_argument("--temperature", type=float)
        if name == "chat":
            item.add_argument("--system", help="Sistem mesajı, @dosya.txt veya -")
            item.add_argument("--image", action="append", default=[], help="Yerel görsel (tekrar edilebilir)")
            item.add_argument("--image-url", action="append", default=[], help="HTTPS/data görsel URL'si")
            item.add_argument("--firik-tools", action="store_true", help="Sunucunun canlı veri araçlarını etkinleştir")
        elif name == "embeddings":
            item.add_argument("--input", action="append", help="Metin, @dosya.txt veya -; tekrar edilebilir")
        elif name == "rerank":
            item.add_argument("--query", help="Arama sorgusu, @dosya.txt veya -")
            item.add_argument("--document", action="append", help="Belge metni, @dosya.txt veya -; tekrar edilebilir")
            item.add_argument("--documents", help="JSON metin listesi, @dosya.json veya -")
        elif name == "ocr":
            image = item.add_mutually_exclusive_group()
            image.add_argument("--file", help="Yerel görsel dosyası")
            image.add_argument("--image", help="Base64/data URL; belge sözleşmesine uygun image alanı")
            item.add_argument("--content-type", help="Yerel dosya MIME türü")

    audio = command("transcribe", "Ses çözümleme: JSON, metin, SRT ve konuşmacı çıktıları")
    audio.add_argument("file")
    audio.add_argument("--model", "-m", default="qwen3-asr-1.7b")
    audio.add_argument("--language", help="ISO 639-1 dil ipucu (ör. tr)")
    audio.add_argument("--prompt", default="")
    audio.add_argument("--response-format", choices=["json", "text", "srt", "verbose_json", "diarized_json"], default="json")
    audio.add_argument("--temperature", type=float, default=0.0)

    media = command("media", "Medya yükleme ve yaşam döngüsü")
    media_sub = media.add_subparsers(dest="action", required=True)
    upload = command("upload", "Dosyayı tek/çok parçalı yükle ve complete çağır", media_sub)
    upload.add_argument("file")
    upload.add_argument("--content-type")
    upload.add_argument("--finalize", action="store_true", help="Yükleme sonrası kalıcı depolamaya taşı")
    initiate = command("initiate", "Haricî yükleme için imzalı URL/part planı oluştur", media_sub)
    initiate.add_argument("--content-type", required=True)
    initiate.add_argument("--size-bytes", required=True, type=int)
    for name in ("status", "complete", "finalize", "abort"):
        item = command(name, f"Medya {name} işlemi", media_sub)
        item.add_argument("media_id")
        if name == "complete":
            item.add_argument("--parts", help="[{part_number,etag}] JSON dizisi, @dosya.json veya -")

    raw = command("request", "Belgelenmiş bir genel uç noktaya tam JSON gövdesi gönder")
    raw.add_argument("method", choices=["GET", "POST"])
    raw.add_argument("path", help="Ör. /v1/chat/completions; yalnızca belgelenmiş yollar")
    raw.add_argument("--body", help="JSON nesnesi, @dosya.json veya -")
    raw.add_argument("--stream", action="store_true")
    docs = command("api-docs", "Çevrimdışı resmî API sözleşmesi ve uç nokta listesi")
    docs.add_argument("--schema", action="store_true", help="Tam OpenAPI şeması")
    docs.add_argument("--path", help="Bir uç noktanın sözleşmesi")
    command("gui", "firik masaüstü uygulamasını başlat")
    return parser


def payload_for(args: argparse.Namespace, default_model: str) -> dict:
    body = read_json(args.body) if args.body else {}
    if not isinstance(body, dict):
        raise ValueError("--body bir JSON nesnesi olmalıdır.")
    for pair in args.set:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise ValueError("--set ALAN=JSON biçiminde olmalıdır.")
        body[key] = json.loads(value)
    if args.model:
        body["model"] = args.model
    if not body.get("model"):
        if args.command in ("chat", "completions", "responses"):
            body["model"] = default_model
        elif args.command == "ocr":
            body["model"] = "dots-ocr"
        else:
            raise ValueError("--model gerekli; hesabınızdaki uygun modeli firik models ile bulun.")
    name = args.command
    if name in ("chat", "completions", "responses"):
        if args.max_tokens is not None:
            if args.max_tokens <= 0:
                raise ValueError("--max-tokens pozitif olmalıdır.")
            body["max_output_tokens" if name == "responses" else "max_tokens"] = args.max_tokens
        if args.temperature is not None:
            body["temperature"] = args.temperature
        if args.stream:
            body["stream"] = True
        if name == "chat":
            images = [media_data_url(path) for path in args.image] + args.image_url
            if args.prompt is not None or images:
                if "messages" in body:
                    raise ValueError("--body messages ile --prompt/--image birlikte kullanılamaz.")
                prompt = read_text(args.prompt) if args.prompt is not None else "Bu görseli açıkla."
                content = ([{"type": "text", "text": prompt}] + [{"type": "image_url", "image_url": {"url": url}} for url in images]) if images else prompt
                body["messages"] = [{"role": "user", "content": content}]
            if args.system:
                body.setdefault("messages", []).insert(0, {"role": "system", "content": read_text(args.system)})
            if args.firik_tools:
                body["evren_tools"] = True
            if not body.get("messages"):
                raise ValueError("--prompt veya --body içinde messages gerekli.")
        elif args.prompt is not None:
            body["input" if name == "responses" else "prompt"] = read_text(args.prompt)
    elif name == "embeddings" and args.input:
        values = [read_text(item) for item in args.input]
        body["input"] = values[0] if len(values) == 1 else values
    elif name == "rerank":
        if args.query is not None:
            body["query"] = read_text(args.query)
        if args.document and args.documents:
            raise ValueError("--document ve --documents birlikte kullanılamaz.")
        if args.document:
            body["documents"] = [read_text(item) for item in args.document]
        if args.documents:
            body["documents"] = read_json(args.documents)
        if "documents" in body and (not isinstance(body["documents"], list) or not all(isinstance(d, str) for d in body["documents"])):
            raise ValueError("documents bir metin listesi olmalıdır.")
    elif name == "ocr":
        if args.file:
            body["image"] = media_data_url(args.file, args.content_type)
        elif args.image:
            body["image"] = args.image
    required = {"chat": ("messages",), "completions": ("prompt",), "responses": ("input",), "embeddings": ("input",), "rerank": ("query", "documents"), "ocr": ("image",)}[name]
    for key in required:
        if key not in body:
            raise ValueError(f"Eksik alan: {key}; --help ile örnek seçenekleri inceleyin.")
    if body.get("stream") and name not in ("chat", "completions", "responses"):
        raise ValueError("Bu işlem akış desteklemiyor.")
    return body


def text_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("text", "output_text", "content"):
            if isinstance(result.get(key), str):
                return result[key]
        if "choices" in result:
            return "".join(choice.get("text") or choice.get("message", {}).get("content") or "" for choice in result["choices"])
        if "output" in result:
            return "".join(content.get("text", "") for item in result["output"] for content in item.get("content", []) if content.get("type") in ("output_text", "text"))
    return json.dumps(result, ensure_ascii=False, indent=2)


def stream_text(event: dict) -> str:
    data = event["data"]
    if not isinstance(data, dict):
        return ""
    if data.get("type") == "response.output_text.delta":
        return data.get("delta", "")
    return "".join(choice.get("text") or choice.get("delta", {}).get("content") or "" for choice in data.get("choices", []))


async def execute(args: argparse.Namespace, client: FirikAPI, default_model: str, output: Any) -> None:
    name = args.command
    use_text = getattr(args, "text", False)
    if name == "api-docs":
        schema = api_schema()
        if args.path:
            if args.path not in schema["paths"]:
                raise ValueError("Bu yol yayımlanmış genel API sözleşmesinde yok.")
            result = {"path": args.path, "operations": schema["paths"][args.path], "components": schema["components"]}
        else:
            result = schema if args.schema else {"source": schema["x-source"], "retrieved_at": schema["x-retrieved-at"], "operations": [{"method": method.upper(), "path": path, "summary": operation.get("summary")} for path, methods in schema["paths"].items() for method, operation in methods.items()]}
    elif name in ("chat", "completions", "responses", "embeddings", "rerank", "ocr", "request"):
        if name == "request":
            body = read_json(args.body) if args.body else None
            if body is not None and not isinstance(body, dict):
                raise ValueError("--body bir JSON nesnesi olmalıdır.")
            method, path = args.method, args.path
            streaming = args.stream or bool(body and body.get("stream"))
        else:
            body = payload_for(args, default_model)
            method, path = "POST", "/v1/" + ("chat/completions" if name == "chat" else name)
            streaming = body.get("stream", False)
        if streaming:
            if method != "POST":
                raise ValueError("Akış için POST gerekli.")
            async for event in client.stream(path, body or {}):
                output.write(stream_text(event) if use_text else json.dumps(event, ensure_ascii=False) + "\n")
                output.flush()
            if use_text:
                output.write("\n")
            return
        result = await client.request(method, path, body=body)
    elif name == "models":
        result = await client.models()
    elif name == "quota":
        result = await client.quota()
    elif name == "health":
        result = await client.request("GET", "/readyz" if args.ready else "/healthz")
    elif name == "request-status":
        result = await client.request_status(args.request_id)
    elif name == "terms":
        if args.action == "accept":
            result = await client.accept_terms(args.version)
        else:
            result = await (client.terms_text() if args.action == "text" else client.terms_status())
    elif name == "transcribe":
        if not 0 <= args.temperature <= 1:
            raise ValueError("Ses temperature değeri 0 ile 1 arasında olmalıdır.")
        result = await client.transcribe(args.file, args.model, language=args.language, prompt=args.prompt,
                                         response_format=args.response_format, temperature=args.temperature)
    elif name == "media":
        if args.action == "upload":
            result = await client.upload_media(args.file, args.content_type, args.finalize)
        elif args.action == "initiate":
            result = await client.initiate_media(args.content_type, args.size_bytes)
        elif args.action == "status":
            result = await client.media_status(args.media_id)
        elif args.action == "complete":
            parts = read_json(args.parts) if args.parts else None
            if parts is not None and (not isinstance(parts, list) or not all(isinstance(part, dict) and isinstance(part.get("part_number"), int) and part["part_number"] > 0 and isinstance(part.get("etag"), str) for part in parts)):
                raise ValueError("--parts, part_number ve etag içeren bir JSON dizisi olmalıdır.")
            result = await client.complete_media(args.media_id, parts)
        elif args.action == "finalize":
            result = await client.finalize_media(args.media_id)
        else:
            result = await client.abort_media(args.media_id)
    else:
        raise ValueError(f"Bilinmeyen komut: {name}")
    # Text/SRT returned by the API is already an output artifact, not a JSON string.
    output.write((text_result(result) if use_text or isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)) + "\n")


async def run(argv: Sequence[str], *, api_key: str | None = None, base_url: str | None = None,
              default_model: str | None = None, transport: httpx.AsyncBaseTransport | None = None) -> int:
    if argv and argv[0] == "mcp":
        from firik_agent.mcp.cli import run_mcp
        return await run_mcp(argv[1:])
    args = build_parser().parse_args(argv)
    key = api_key or ""
    client = None
    try:
        config_path = getattr(args, "config", None)
        if config_path and not Path(config_path).is_file():
            raise ValueError(f"Config dosyası bulunamadı: {config_path}")
        settings = load_config(config_path)
        if not isinstance(settings, dict) or not isinstance(settings.get("providers", {}), dict):
            raise ValueError("Config ve providers alanı YAML nesnesi olmalıdır.")
        provider = getattr(args, "provider", "firik")
        config = settings.get("providers", {}).get(provider, {})
        if not isinstance(config, dict):
            raise ValueError(f"providers.{provider} bir YAML nesnesi olmalıdır.")
        url = (getattr(args, "base_url", None) or base_url
               or (os.environ.get("FIRIK_BASE_URL") if provider == "firik" else None)
               or config.get("base_url", DEFAULTS[provider][1]))
        if args.command == "login":
            ensure_api_key(provider, config, base_url=url, replace=True)
            return 0
        if args.command == "gui":
            from firik_agent.ui.app import main as gui_main
            return gui_main()
        if args.command is None:
            if sys.stdin.isatty():
                ensure_api_key(provider, config, base_url=url)
            build_parser().print_help()
            return 0
        needs_key = args.command not in ("health", "api-docs", "gui")
        if args.command == "request" and args.path in ("/healthz", "/readyz"):
            needs_key = False
        if needs_key:
            key = api_key if api_key is not None else ensure_api_key(provider, config, base_url=url)
        model = default_model or config.get("default_model", "glm-5.3")
        async with FirikAPI(key, url, getattr(args, "timeout", 180), transport=transport) as client:
            filename = getattr(args, "output", None)
            with output_target(filename) as output:
                await execute(args, client, model, output)
        return 0
    except (ValueError, OSError, RuntimeError, httpx.HTTPError, yaml.YAMLError) as exc:
        message = str(exc)
        if key:
            message = message.replace(key, "[REDACTED]")
        print(f"Hata: {message}", file=sys.stderr)
        return 1
    finally:
        if client and getattr(args, "metadata", False):
            print(json.dumps(client._redact(client.last_metadata), ensure_ascii=False), file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    args_list = sys.argv[1:] if argv is None else list(argv)
    if args_list and args_list[0] in {"benchmark", "harness"}:
        from firik_agent.benchmarks.cli import main as benchmark_main
        return benchmark_main(args_list[1:])
    if args_list and args_list[0] == "mcp":
        from firik_agent.mcp.cli import main as mcp_main
        return mcp_main(args_list[1:])
    try:
        return asyncio.run(run(args_list))
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
