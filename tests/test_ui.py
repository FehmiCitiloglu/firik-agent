"""firik-agent masaüstü uygulaması birim ve entegrasyon testleri."""
from __future__ import annotations

import httpx
import pytest

from firik_agent.api.client import FirikAPIError
from firik_agent.ui.app import FirikApp
from firik_agent.ui.icons import ensure_app_icon
from firik_agent.ui.service import FirikService, turkce_hata_mesaji
from firik_agent.ui.theme import APP_NAME, THEME_COLORS


def test_app_title_is_firik_agent():
    """Uygulama adı ve pencere başlığının sadece 'firik-agent' olduğunu doğrular."""
    assert APP_NAME == "firik-agent"
    app = FirikApp()
    try:
        assert app.title() == "firik-agent"
    finally:
        app.destroy()


def test_app_views_initialized():
    """Tüm sekmelerin ve görünümlerin başarıyla yüklendiğini test eder."""
    app = FirikApp()
    try:
        expected_views = {"projects", "chat", "mcp", "ocr", "transcribe", "rerank", "quota", "settings", "about"}
        assert set(app.views.keys()) == expected_views


        # Sekmeler arası geçiş testi
        for view_id in expected_views:
            app.show_view(view_id)
            assert app.active_tab == view_id
    finally:
        app.destroy()


def test_theme_colors():
    """Koyu ve açık tema renk paletlerini doğrular."""
    assert "dark" in THEME_COLORS
    assert "light" in THEME_COLORS
    for mode in ("dark", "light"):
        colors = THEME_COLORS[mode]
        assert "bg_primary" in colors
        assert "text_primary" in colors
        assert "accent" in colors
        assert "success" in colors
        assert "danger" in colors


def test_turkce_hata_mesajlari():
    """Teknik istisnaların Türkçe kullanıcı dostu mesajlara dönüştüğünü doğrular."""
    e401 = FirikAPIError(401, {"message": "Invalid key"}, {})
    msg401 = turkce_hata_mesaji(e401)
    assert "Kimlik doğrulama başarısız" in msg401
    assert "401" in msg401

    e402 = FirikAPIError(402, {"message": "Out of credit"}, {})
    assert "Yetersiz Kredi" in turkce_hata_mesaji(e402)

    e403 = FirikAPIError(403, {"message": "Forbidden"}, {})
    assert "Erişim Engellendi" in turkce_hata_mesaji(e403)

    e429 = FirikAPIError(429, {"message": "Too many requests"}, {})
    assert "Hız/Kota Sınırı Aşıldı" in turkce_hata_mesaji(e429)

    e503 = FirikAPIError(503, {}, {})
    assert "Sunucu Hizmet Veremiyor" in turkce_hata_mesaji(e503)

    conn_err = httpx.ConnectError("Connection refused")
    assert "Sunucuya bağlanılamadı" in turkce_hata_mesaji(conn_err)

    timeout_err = httpx.TimeoutException("Timed out")
    assert "İstek zaman aşımına uğradı" in turkce_hata_mesaji(timeout_err)


def test_ensure_app_icon():
    """Uygulama simgesinin başarıyla oluşturulduğunu doğrular."""
    icon_path = ensure_app_icon()
    assert icon_path.exists()
    assert icon_path.stat().st_size > 0


def test_service_settings_update(tmp_path, monkeypatch):
    """Ayarların güncellenip kaydedildiğini test eder."""
    config_file = tmp_path / "config.yaml"
    monkeypatch.chdir(tmp_path)
    service = FirikService()
    service.update_settings("https://custom.firik-agent.local/v1", "custom-model-1", 99.0)
    assert service.base_url == "https://custom.firik-agent.local/v1"
    assert service.default_model == "custom-model-1"
    assert service.timeout == 99.0


def test_frozen_settings_use_user_directory(tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    service = FirikService()
    service.update_settings("https://example.invalid/v1", "test-model", 42)
    assert service.config_path.is_relative_to(tmp_path)
    assert service.config_path.is_file()
    restored = FirikService()
    assert restored.default_model == "test-model"
    assert restored.timeout == 42


def test_worker_callbacks_run_on_gui_thread():
    import queue
    import threading
    from types import SimpleNamespace

    # Exercise dispatch without creating another Tcl interpreter in this process.
    observed = []
    main_thread = threading.get_ident()

    def schedule(*args):
        assert threading.get_ident() == main_thread

    app = SimpleNamespace(_callbacks=queue.SimpleQueue(), _closing=False,
                          after=schedule, _drain_callbacks=lambda: None)
    worker = threading.Thread(target=lambda: FirikApp._enqueue_callback(
        app, lambda: observed.append(threading.get_ident())))
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert not observed
    FirikApp._drain_callbacks(app)
    assert observed == [main_thread]


def test_linux_font_policy_is_bundled(monkeypatch):
    import sys
    import os
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from firik_agent.ui.app import _ensure_tk_environment

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("FONTCONFIG_FILE", "")
    _ensure_tk_environment()
    policy = Path(os.environ["FONTCONFIG_FILE"])
    assert policy.is_file()
    assert ET.parse(policy).find("selectfont/rejectfont/pattern/patelt[@name='color']/bool").text == "true"
