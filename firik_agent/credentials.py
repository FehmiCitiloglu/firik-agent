"""Shared credential lookup and terminal onboarding; never writes project files."""
from __future__ import annotations

import getpass
import hashlib
import os
import sys
import warnings

import keyring


DEFAULTS = {
    "firik": ("FIRIK_API_KEY", "https://evren-llmapi.ssyz.org.tr/v1"),
    "llmtr": ("LLMTR_API_KEY", "https://llmtr.com/v1"),
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1"),
}
PLACEHOLDERS = {"firik_llm_your_key_here", "llmtr-your_key_here", "sk-...", "sk-ant-..."}
NATIVE_BACKENDS = {
    "keyring.backends.macOS", "keyring.backends.Windows",
    "keyring.backends.SecretService", "keyring.backends.kwallet", "keyring.backends.libsecret",
}


def _backend():
    """Allow native OS stores only, including native members of a chainer."""
    backend = keyring.get_keyring()
    candidates = backend.backends if type(backend).__module__ == "keyring.backends.chainer" else [backend]
    for candidate in candidates:
        if type(candidate).__module__ in NATIVE_BACKENDS:
            return candidate
    raise RuntimeError("İşletim sistemi kimlik bilgisi kasası kullanılamıyor.")


def _identity(provider: str, config: dict, base_url: str | None):
    default_env, default_url = DEFAULTS[provider]
    env = config.get("api_key_env", default_env)
    # Scope stored credentials to the provider, environment name AND endpoint.
    # A custom --base-url must not silently receive the production credential.
    url = (base_url or config.get("base_url", default_url)).rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    scope = hashlib.sha256(f"{provider}\n{env}\n{url}".encode()).hexdigest()
    return env, f"firik-agent:{scope}", provider


def _usable(value: str | None) -> str:
    value = (value or "").strip()
    return "" if value in PLACEHOLDERS else value


def get_api_key(provider: str, config: dict, *, base_url: str | None = None) -> str:
    """Non-interactive lookup: environment (including legacy .env), then OS store."""
    env, service, account = _identity(provider, config, base_url)
    value = _usable(os.environ.get(env))
    if value:
        return value
    try:
        return _usable(_backend().get_password(service, account))
    except Exception:
        # Backend errors may contain secrets; never print their raw messages.
        return ""


def set_api_key(provider: str, config: dict, key: str, *, base_url: str | None = None) -> None:
    """Save an API key directly into the OS credential store."""
    clean_key = _usable(key)
    if not clean_key or any(char.isspace() for char in clean_key):
        raise ValueError("Geçerli bir API anahtarı girin; boş değer, şablon veya boşluk kabul edilmez.")
    env, service, account = _identity(provider, config, base_url)
    _backend().set_password(service, account, clean_key)


def ensure_api_key(provider: str, config: dict, *, base_url: str | None = None,
                   replace: bool = False) -> str:
    """Prompt only in a terminal, save to the OS store, and return the key."""
    env, service, account = _identity(provider, config, base_url)
    if not replace:
        existing = get_api_key(provider, config, base_url=base_url)
        if existing:
            return existing
    if not sys.stdin.isatty():
        raise ValueError(f"{env} eksik. Terminalde `firik login --provider {provider}` çalıştırın "
                         f"veya {env} ortam değişkenini ayarlayın.")

    print(f"{provider.upper()} API anahtarı kurulumu. Anahtar işletim sisteminin "
          "kimlik bilgisi kasasında saklanacak; yazarken görünmez.", file=sys.stderr)
    if provider == "firik":
        print("LLM Çıkarım anahtarı: https://evren.ssyz.org.tr/api-keys", file=sys.stderr)
    try:
        with warnings.catch_warnings():
            # Do not let getpass fall back to echoing a secret to the terminal.
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = _usable(getpass.getpass(f"{provider.upper()} API anahtarı: ", stream=sys.stderr))
    except (EOFError, KeyboardInterrupt):
        raise ValueError("Anahtar girişi iptal edildi.") from None
    except getpass.GetPassWarning:
        raise ValueError("Gizli giriş kullanılamıyor. `firik login` komutunu bir terminalde çalıştırın.") from None
    if not key or any(char.isspace() for char in key):
        raise ValueError("Geçerli bir API anahtarı girin; boş değer, şablon veya boşluk kabul edilmez.")
    try:
        _backend().set_password(service, account, key)
    except Exception:
        if replace:
            raise RuntimeError("Anahtar kaydedilemedi. İşletim sistemi kimlik bilgisi kasasını "
                               "etkinleştirin veya ortam değişkeni kullanın.") from None
        print("Kimlik bilgisi kasasına kaydedilemedi; anahtar yalnızca bu çalıştırmada kullanılacak. "
              "Sonraki açılışta tekrar istenir.", file=sys.stderr)
    else:
        print("API anahtarı kimlik bilgisi kasasına kaydedildi.", file=sys.stderr)
    if replace and _usable(os.environ.get(env)):
        print(f"{env} ortam değişkeni veya mevcut .env değeri önceliklidir. "
              "Kaydettiğiniz anahtarı kullanmak için bu değeri kaldırın.", file=sys.stderr)
    return key
