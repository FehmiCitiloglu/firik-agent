# Tarayıcı hatasından sonra sohbetin sürmesi — 7 Kasım 2026

Kullanıcının ekran görüntüsünde `neal.fun` insan doğrulaması, `[blocked URL]`
ve `tool_outcome_unknown` nedeniyle durmuş bir sohbet görülüyordu. HTTP veya
özel ağ reddi, bozuk mevcut sayfa URL'si ve gerçek gezinme belirsizliği ayrı
durumlardır.

## Davranış

- Ana sayfa isteği politika nedeniyle engellendiğinde özgün hata kodu korunur.
  Henüz gönderilmemiş isteğe belirsiz sonuç etiketi verilmez. POST sonrasında
  engellenen yönlendirme ise uzlaştırma gerektirmeye devam eder.
- Yeni URL'ye gezinme, mevcut Chromium hata sayfasının URL'sinde takılmaz.
  Yeni hedefin ağ politikası yine doğrulanır. Engellenen ana sayfada `aborted`
  kullanımı, Chromium'un gecikmiş iç hata sayfasının sonraki gezinmeyi kesmesini
  önler; alt kaynaklar engellenmeye devam eder.
- Normal sohbet, yerel tarayıcının gezinme veya gözlem hatasından sonra önceki
  kanıtları korur ve bağımsız kaynaklara devam edebilir. Sonucu belirsiz aynı
  işlem, araç çağrı kimliği değiştirilse de tekrar gönderilmez.
- Tekrarlanan başarısızlıklar mevcut sınırlar içinde araçsız bir son yanıtla
  sonuçlanır. Kullanıcı iptali ve çalışma bütçesi korunur. Belirsiz gönderme ve
  tıklamalar terminaldir. Kodlama çalışmasının belirsiz işlem ve tamamlama
  korumaları korunmuştur; bilinen hedef reddi araştırmayı durdurmaz.
- Son izinli araç envanterinde yerel tarayıcı varsa, her iki ajan akışı önce
  Firik'in kendi tarayıcısını kullanması için yönlendirilir. Açık sistem
  tarayıcısı/profil tercihi korunur. Engellenen site veya insan doğrulamasını
  başka araçla aşma yönlendirmesi verilmez. "Siteyi aç" gibi Türkçe istekler
  daraltılmış araç seçiminde de tanınır.

## Doğrulama

İlgili ajan, MCP, kurtarma, oturum, araç seçimi, tarayıcı ve izin regresyonları:
**425 geçti, 1 gerçek tarayıcı testi normal çalıştırmada atlandı**.

```sh
.venv/bin/python -m pytest -q tests/test_browser_continuation.py tests/test_browser_preference.py tests/test_browser_navigation_failures.py tests/test_browser_runtime.py tests/test_browser_security.py tests/test_browser_surfaces.py tests/test_browser_coding.py tests/test_browser_privacy.py tests/test_browser_provider.py tests/test_browser_packaging.py tests/test_public_web_permissions.py tests/test_public_web_policy.py tests/test_mcp_continuation.py tests/test_mcp_results.py tests/test_recovery_engine.py tests/test_agent_loop.py tests/test_chat_streams.py tests/test_harness_profiles_phase3.py
```

Gerçek Chromium ve yerel HTTP sunucularıyla, engellenen yönlendirme sonrasında
başka kaynağa geçiş dahil: **35 geçti**. Bu grup yukarıdaki bazı testleri de
içerir; sonuçlar benzersiz test sayısı olarak toplanmamalıdır.

```sh
FIRIK_BROWSER_INTEGRATION=1 .venv/bin/python -m pytest -q tests/test_browser_chromium.py tests/test_browser_navigation_failures.py
```

`git diff --check` başarılı. Yeni yazılım kurulmadı. Canlı model sağlayıcısı,
Cloudflare doğrulamasının kendisi ve paketli masaüstü uygulaması denenmedi.
Mevcut çalışma alanındaki diğer değişiklikler korundu; bu çalışma bir sürüm
yayımlamadı.
