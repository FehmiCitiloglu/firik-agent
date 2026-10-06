"""firik masaüstü uygulaması simge ve görsel varlık yöneticisi."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from PIL import Image, ImageDraw


ASSETS_DIR = Path(__file__).parent / "assets"
ICON_PNG_PATH = ASSETS_DIR / "icon.png"
ICON_ICO_PATH = ASSETS_DIR / "icon.ico"


def ensure_app_icon() -> Path:
    """Uygulama simgesi mevcut değilse oluşturur ve yolunu döner."""
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    if ICON_PNG_PATH.exists():
        return ICON_PNG_PATH

    # 256x256 modern kozmik "firik" simgesi çizimi
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Dış dairesel gradyan benzeri arka plan (Kozmik Gece Mavisi)
    center = size // 2
    r_outer = 116
    draw.ellipse(
        [center - r_outer, center - r_outer, center + r_outer, center + r_outer],
        fill=(15, 23, 42, 255),
        outline=(59, 130, 246, 255),
        width=4,
    )

    # Kozmik iç halka / yörünge (Açık Mavi / Camgöbeği)
    r_orbit = 88
    draw.arc(
        [center - r_orbit, center - r_orbit, center + r_orbit, center + r_orbit],
        start=45,
        end=315,
        fill=(6, 182, 212, 230),
        width=5,
    )

    # İkinci eğik yörünge elipsi
    draw.arc(
        [center - 95, center - 45, center + 95, center + 45],
        start=200,
        end=20,
        fill=(99, 102, 241, 200),
        width=4,
    )

    # Merkez gezegen / çekirdek (Vibrant Blue & Cyan)
    r_core = 46
    draw.ellipse(
        [center - r_core, center - r_core, center + r_core, center + r_core],
        fill=(37, 99, 235, 255),
        outline=(147, 197, 253, 255),
        width=3,
    )

    # İç ışık ışıltısı
    r_glow = 16
    draw.ellipse(
        [center - 14 - r_glow, center - 14 - r_glow, center - 14 + r_glow, center - 14 + r_glow],
        fill=(255, 255, 255, 180),
    )

    # Yörünge uydusu / parlak nokta
    draw.ellipse([center + 70, center - 50, center + 82, center - 38], fill=(56, 189, 248, 255))
    draw.ellipse([center - 65, center + 55, center - 55, center + 65], fill=(168, 85, 247, 255))

    img.save(ICON_PNG_PATH, "PNG")

    try:
        # ICO olarak da kaydet (Windows desteği için)
        img.save(ICON_ICO_PATH, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    except Exception:
        pass

    return ICON_PNG_PATH
