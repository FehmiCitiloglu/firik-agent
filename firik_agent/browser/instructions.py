"""Host-authored browser routing for the currently permitted tool inventory."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def browser_instructions(schemas: Sequence[Mapping[str, Any]]) -> str:
    """Prefer the owned browser only while its tools are actually available.

    Call after permission, profile and provider capability filtering. This is
    guidance for tool selection; it grants no permission or fallback authority.
    """
    names = {schema.get("function", {}).get("name") for schema in schemas}
    if not names & {"browser_navigate", "browser_snapshot"}:
        return ""
    instructions = (
        "Tarayıcı seçimi: browser_* araçları Firik'in bu oturuma ait kendi Chromium "
        "tarayıcısını yönetir. Kullanıcı genel olarak 'tarayıcıda yap', 'siteyi aç' veya "
        "bir web sayfasını incele derse önce bu araçları kullan; computer-use ile "
        "sistemin tarayıcısını açmaya veya yönetmeye yönelme. "
    )
    if {"browser_status", "browser_start", "browser_navigate"} <= names:
        instructions += (
            "browser_status ile mevcut oturumu kontrol et; yalnız başlatılmamışsa "
            "browser_start kullan, ardından browser_navigate ile URL'yi aç. "
        )
    instructions += (
        "Kullanıcı özellikle sistem tarayıcısını, Chrome/Safari'yi, kendi profilini veya "
        "orada açık bir sekmeyi isterse bu açık tercihi izle. Firik tarayıcısında gerekli "
        "işlev yoksa mevcut izinler kapsamında uygun başka aracı değerlendirebilirsin. "
        "Engellenen HTTP/özel ağ adreslerini veya reddedilen işlemleri aşmak için "
        "computer-use, sistem tarayıcısı, shell ya da başka bir araca geçme. "
        "Erişilemeyen siteyi atlayıp bağımsız izinli sitelerde araştırmaya devam et; "
        "erişilemeyen kaynaktan bilgi edinmiş gibi davranma. Sonucu belirsiz gönderme "
        "ve tıklama işlemlerini sayfa durumunu incelemeden tekrarlama. CAPTCHA veya "
        "insan doğrulamasına takılan siteyi de atla; o kaynak görev için zorunluysa "
        "kullanıcıya durumu bildir ve mevcut manuel kontrol devrini kullan."
    )
    return instructions
