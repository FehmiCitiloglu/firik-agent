"""evren masaüstü uygulaması tema ve görsel stil tanımlamaları."""
from __future__ import annotations

# Uygulama adı
APP_NAME = "evren"
APP_SUBTITLE = "Yapay Zekâ ve Dil Modelleri Masaüstü İstemcisi"
APP_VERSION = "0.2.17"


# Renk Paleti (Koyu / Açık Mod Destekli)
THEME_COLORS = {
    "dark": {
        "bg_primary": "#0f172a",       # Slate 900 - Derin ana arkaplan
        "bg_secondary": "#1e293b",     # Slate 800 - Kartlar ve paneller
        "bg_sidebar": "#0b0f19",       # Çok koyu yan menü
        "bg_input": "#334155",         # Slate 700 - Girdi kutuları
        "bg_hover": "#283548",
        "text_primary": "#f8fafc",     # Neredeyse beyaz
        "text_secondary": "#94a3b8",   # Slate 400
        "text_muted": "#64748b",       # Slate 500
        "accent": "#3b82f6",           # Mavi
        "accent_hover": "#2563eb",
        "accent_secondary": "#06b6d4", # Camgöbeği
        "success": "#10b981",          # Zümrüt yeşili
        "warning": "#f59e0b",          # Kehribar
        "danger": "#ef4444",           # Kırmızı
        "border": "#334155",
        "user_bubble": "#1d4ed8",      # Kullanıcı mesaj balonu
        "bot_bubble": "#1e293b",       # Asistan mesaj balonu
    },
    "light": {
        "bg_primary": "#f8fafc",
        "bg_secondary": "#ffffff",
        "bg_sidebar": "#f1f5f9",
        "bg_input": "#e2e8f0",
        "bg_hover": "#e2e8f0",
        "text_primary": "#0f172a",
        "text_secondary": "#475569",
        "text_muted": "#94a3b8",
        "accent": "#2563eb",
        "accent_hover": "#1d4ed8",
        "accent_secondary": "#0891b2",
        "success": "#059669",
        "warning": "#d97706",
        "danger": "#dc2626",
        "border": "#cbd5e1",
        "user_bubble": "#2563eb",
        "bot_bubble": "#f1f5f9",
    },
}

# Tipografi
FONT_FAMILY = "SF Pro Display, Segoe UI, Roboto, Helvetica, Arial, sans-serif"
FONT_MONO = "SF Mono, Menlo, Consolas, Monaco, monospace"
