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
        "tarayıcısını yönetir. Masaüstü uygulamasında sohbetin yanındaki entegre "
        "tarayıcı paneli aynı oturumu gösterir; 'yandaki tarayıcı' bu paneldir. "
        "Masaüstündeki bu panel headless Chromium kullanır ve ayrı bir sistem penceresi olarak görünmez. "
        "Paneli keşfetmek için işletim sistemi pencerelerini listeleme veya computer-use "
        "ekran görüntüleri alma; Finder dışında pencere bulunmaması bu tarayıcının "
        "olmadığını göstermez. Kullanıcı genel olarak 'tarayıcıda yap', 'siteyi aç' veya "
        "bir web sayfasını incele derse önce bu araçları kullan; computer-use ile "
        "sistemin tarayıcısını açmaya veya yönetmeye yönelme. "
    )
    if "browser_status" in names:
        instructions += "Önce browser_status ile mevcut oturumu ve etkin sekmeyi kontrol et. "
    if "browser_list_tabs" in names:
        instructions += "Açık sekmeleri ve URL'lerini browser_list_tabs ile bul. "
    if "browser_snapshot" in names:
        instructions += (
            "Mevcut açık sayfayı browser_snapshot ile oku; kullanıcı açık sayfayı "
            "incelemek istiyorsa yeniden başlatma veya başka adrese gitme. "
        )
    if "browser_start" in names:
        instructions += (
            "Yalnız oturum henüz başlatılmamışsa browser_start kullan. "
        )
    if "browser_navigate" in names:
        instructions += "Kullanıcının istediği yeni URL için browser_navigate ile URL'yi aç. "
    instructions += (
        "Sayfa gözlemi için DOM/metin verisini kullan. Bir ekran görüntüsüne ait "
        "dosya yolu veya 'görüntü döndü' yer tutucusu görüntünün pikselleri değildir; "
        "bunlardan görüntüyü gördüğünü iddia etme veya aynı ekran görüntüsünü "
        "tekrar tekrar isteme. "
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
