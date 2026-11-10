# Hibrit desktop: aşama 1, 2 ve 3 geçişi

Güncelleme: 10 Kasım 2026. Kapsam: mimari planın 1, 2 ve 3. aşamalarındaki sohbet, backend
bağlantısı ve TypeScript Playwright Browser geçişi. Mevcut native masaüstü ve CLI
giriş noktaları korunur.

## Çalışan parçalar

- Electron penceresi, React/TypeScript arayüz, Vite derlemesi, Tailwind,
  Radix/shadcn yaklaşımındaki bileşenler, Zustand UI durumu ve TanStack Query.
- Mevcut Python Agent üzerinden akışlı sohbet, konuşma başına çalışma/iptal,
  bağımsız taslaklar, sohbetler arası geçiş ve SQLite geçmişi.
- API adresi, varsayılan model, zaman aşımı, mevcut keyring servisi üzerinden
  anahtar yazma; UI'ya kayıtlı anahtar değeri döndürülmez.
- Model kataloğunu yenileme, doğrulanmış reasoning seviyeleri, mevcut MCP'leri
  konuşma başına seçme ve mevcut ajan onay callback'lerini cevaplama.
- Özel stdio üzerinde çift yönlü sürümlü JSON-RPC 2.0 (`browser.configureExecutor`, `browser.prepare`,
  `browser.execute`, `browser.revoke` Python → Host; `browser.configure`, `browser.userCommand`, `browser.userInput`
  Host → Python); Pydantic'ten üretilen ortak JSON Schema ve TypeScript sözleşmeleri.
- **Aşama 3 TypeScript Browser Yürütücüsü ve Host Katmanı**:
  - `apps/desktop/src/browser-worker/`: Playwright tabanlı izole Node/Electron `utilityProcess` worker;
    DNS/IP ve private-origin denetimi, canlı grant doğrulama,
    request interception, redirect takibi, AI modlu ARIA snapshot (native ref/revision),
    tek kullanımlık `prepare` → `dispatch` onay jetonları, şifre/OTP formlarında zorunlu `request_handoff`,
    maskelenmiş ekran yakalama.
  - `apps/desktop/src/main/browser-host.ts`: Worker'ı `MessagePort` üzerinden yöneten Electron Main
    yöneticisi; execution ID tekilleştirme (`tool_outcome_unknown` / `requires_reconciliation`),
    oturum yaşam döngüsü ve Renderer'a doğrudan `Uint8Array` binary IPC kanalı (`firik:browser.preview`)
    sayesinde ham görsel baytları asla JSON-RPC veya event log'larına girmez.
  - `firik_agent/desktop_rpc/browser_client.py`: Python tarafında `BrowserManager` arayüzünü
    uyumunu koruyup Main BrowserHost'a delege eden `RemoteBrowserManager`.
  - `apps/desktop/src/renderer/browser-panel.tsx`: Sekme yönetimi, adres/arama çubuğu,
    ajandan kontrolü alma/ajana devretme, elle giriş (tıklama, kaydırma, metin, tuş) ve
    canlı `Blob` görsel önizleme paneli.
- GUI bağımsız Python uygulama servisi, ayrı PyInstaller backend spec'i ve
  electron-builder önizleme paketleme betiği.

Electron bu aşamada Python'daki `chat_agent_stream_async` yöntemini çağırır.
Yeni sağlayıcı istemcisi veya ikinci bir ajan döngüsü yazılmadı. Ekran akışının
kalıcı verisi, tool filter ve recovery davranışı mevcut Python çekirdeğindedir.

`firik_agent/application/` altına taşınan service/coding/browser/improvement
modülleri GUI import etmez. `firik_agent/ui/` altındaki eski modül yolları aynı
modül nesnesine yönlenir; eski Tk tüketicileri ve bağımlılık enjeksiyonu çalışır.

## Kaynaktan çalıştırma

Geliştirme için Node 22.12+ (bu çalışma Node 26 ile doğrulandı), Python 3.10+
ve mevcut backend bağımlılıkları gerekir. Proje kökünde:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
npm exec --yes --package=@yarnpkg/cli-dist@4.18.1 -- yarn install --immutable
npm run desktop:browser:install
npm run desktop:dev
```

Windows'ta Python yolu `.venv\Scripts\python.exe` olur. Python ve Yarn'ın global
kurulumunu değiştirmek gerekmez. Yarn sürümü `packageManager` alanında, doğrudan
bağımlılıklar ve çözümlenen bağımlılıklar manifest/kilit dosyasında sabittir.
Yarn'ın paket yayın yaşı koruması kapatılmadı.

Vite sunucusu geliştirmede kullanılır. Derlenmiş arayüzü sunucusuz açmak için:

```sh
npm run desktop:build
npm run desktop:start
```

Bu mod derlenmiş UI'yı `firik://app` üzerinden yükler; Python backend kaynak
ortamından çalışır. Backend bağımlılıklarını da içine alan yerel uygulama paketi:

```sh
.venv/bin/python -m pip install -e '.[desktop-build]'
npm run desktop:package
```

`desktop:browser:install` yalnız proje içindeki `build/hybrid-browser/` dizinine,
kilitli Playwright sürümünün beklediği Chromium'u kurar. Geliştirmede tarayıcı
kullanılmayacaksa bu adım atlanabilir; eksik binary hata verir. Uygulama
başlangıcında indirme veya farklı bir global cache sürümüne geri dönüş yapılmaz.

Paketleme UI'yı, sözleşmeleri ve backend'i sırasıyla yeniden derler; hata alan
aşamadan sonra devam etmez. Eşleşen Chromium, Playwright sürücüsü ve
`firik-browser-manifest.json` uygulama kaynaklarına eklenir. Paketlenmiş worker
yalnız bu browser dizinini kullanır. Çıktılar `dist/hybrid-desktop/` altındadır. Bu ilk
önizleme paketleme ayarı macOS Developer ID imzası/noter işlemi veya yayınlama
yapmaz. Release pipeline ve `/Applications` altındaki kurulu uygulama
değiştirilmez. Kurulum paketi/imza ve diğer platform paketleri ayrı geçiş
kapılarıdır.

## Veri, izinler ve yaşam döngüsü

Varsayılan olarak mevcut native desktop config/geçmişi bulunursa aynı veri
dizini kullanılır. Açıkça verilen `FIRIK_DESKTOP_CONFIG` yolu önceliklidir.
Mevcut desktop verisi yoksa kaynak çalışma düzeninin config seçimi korunur.
Paketlenmiş backend mevcut platformun `firik` config dizinini kullanır;
Electron'un yeni userData dizinine sessiz bir veritabanı taşıması yapılmaz.

Geliştirme/test için `FIRIK_DESKTOP_PYTHON` farklı Python yürütücüsü,
`FIRIK_DESKTOP_CONFIG` izole config dosyası seçebilir. Paketlenmiş uygulama
Python yürütücüsü için ortam değişkenini kullanmaz; kendi backend'ini başlatır.
Bu yollar arayüzden veya model argümanlarından değiştirilemez.

Ortak uygulama servisindeki dosya kilidi, bu kaynağı kullanan Tk/Electron
süreçlerinin aynı veriyi bağımsız yazmasını engeller. Önceden dağıtılmış eski
binary bu yeni kilidi bilmez; aynı veriyle deneme yaparken eski kurulu uygulama
kapalı tutulmalıdır. Doğrulama testleri tamamen ayrı geçici veri dizinleri kullanır.

Renderer Node erişimi almaz; sandbox/context isolation açıktır. Preload yalnız
sabit yöntemleri sunar. Main her IPC çağrısında gönderen pencere/frame ve origin
denetler. Model veya uzak içerik genel IPC, shell, dosya sistemi ya da CDP
erişimi kazanmaz. Uzak görseller otomatik yüklenmez; dış bağlantı açma yalnız
kullanıcının HTTPS bağlantısına tıklamasıyla gerçekleşir.

Run generation ve event sıra numarası, gecikmiş bir snapshot'ın daha yeni
çalışmayı eski durumuna çevirmesini engeller. Onaylar run kimliğine bağlıdır;
iptal, tamamlanma ve bağlantı kaybında kapatılır. Backend koparsa mevcut run'lar
UI'da hata durumuna geçer; otomatik yeniden gönderim/yan etki tekrarı yapılmaz.

Kapanış önce backend'in normal cleanup'ını ister. Süre sınırı aşılırsa Windows'ta
backend süreç ağacı, POSIX'te backend'in özel süreç grubu sonlandırılır. Stdout yalnız JSON-RPC'ye
ayrılır; backend/plugin print çıktıları stderr'e yönlendirilir. Ham stderr
renderer'a taşınmaz; veri dizini kilit hatası sabit bir hata koduyla bildirilir.

## Tarayıcı sınırı

Python'daki model araçları, session/tool filter, approval ve journal akışı yerinde
kalır. `RemoteBrowserManager`, kanonik `BrowserConfig` limitlerini ve host kimliği /
güncel grant'i Main'e gönderir; Main özel MessagePort ile utilityProcess worker'a
iletir. Renderer yalnız doğrulanan kullanıcı komutlarını sunar. Chromium sayfası
Electron uygulamasının preload'una veya Node erişimine sahip değildir.

Panel, aynı Playwright sayfasının yaklaşık 1,5 saniyede yenilenen PNG önizlemesidir.
Görüntü arayüze binary IPC ile gider; stdio, transcript ve model girdisine eklenmez.
Kullanıcı önizlemesinde parola/OTP/kart alanları; model screenshot artifact'larında
normal metin alanları da maskelenir, child frame'ler dahil edilir. Elle kontrol
ajan gözlemini/etkileşimini durdurur; host geri verdiğinde yeni gözlem alınır.
Gerçek `WebContentsView` gömmesi bu dilimde uygulanmadı.

İzin değişikliği bekleyen işlem jetonlarını geçersiz kılar. Sohbet değiştirmek
frame'leri temizler; izin ve oturumlar diğer sohbete taşınmaz. Backend kopması veya
worker timeout/exit sonrası yeni işlemler engellenir, belirsiz etkiler otomatik
tekrarlanmaz. Uygulama yeniden açıldığında tarayıcı yetkisi geri yüklenmez.

Ağ koruması uygulama düzeyinde URL/DNS/IP ve her redirect/istek denetimidir;
OS egress izolasyonu veya DNS bağlantısının IP'ye sabitlenmesi değildir.
`strict_network_isolation` isteği desteklenmediği için reddedilir. Upload/download,
kalıcı kullanıcı profili ve renderer/model üzerinden genel CDP erişimi açılmaz.

## Doğrulama

```sh
npm run desktop:build
npm run desktop:check
npm run desktop:test
npm run desktop:browser:install
npm run desktop:test:browser
.venv/bin/python -m pytest -q tests/test_desktop_rpc.py
npm run desktop:smoke
```

Smoke testi gerçek Electron penceresi, gerçek Python Agent ve loopback üzerinde
yerel sahte FIRIK sağlayıcısı kullanır. Canlı FIRIK çağrısı veya model kotası
kullanmaz; kullanıcının geçmiş/ayar/anahtarlarını değiştirmez. Akış, model/ayar,
iptal, geçmiş, yeniden açılış, renderer izolasyonu ve ekran boyutuna ek olarak
gerçek Chromium navigasyonu, elle yazılan sayfanın Python ajanı tarafından
okunması, tek kullanımlık onaylı tıklama, sekme/izin/sohbet yalıtımını doğrular. Görüntüler/sonuçlar `test-results/hybrid-desktop/` altına
yazılır. `FIRIK_DESKTOP_EVIDENCE` çıktı dizinini değiştirebilir.

Paketlenmiş uygulamayı aynı testle sınamak için
`FIRIK_DESKTOP_PACKAGED_EXECUTABLE` paket içindeki yürütülebilir dosyaya ayarlanır.
Bu modda Python sidecar ve Chromium uygulama paketinin içinden çalışır; test
sistem Python yolunu ve global browser cache yolunu bilerek kullanılamaz yapar.

macOS/Windows eski GUI regresyonları proje standardına göre her Tk testini ayrı
süreçte çalıştırır:

```sh
.venv/bin/python scripts/test_desktop.py
```

Yeni CI işi ortak şemaları yeniden üretip fark denetler, TypeScript derler ve
Python/TypeScript sınır testlerini çalıştırır. Ayrı macOS işi eşleşen Chromium
kurup `desktop:test:browser` senaryolarını çalıştırır; standart unit işi bunları
bilinçli atlar. Chromium sandbox'ı test için kapatılmaz. Yerel doğrulama sonucu, uzak CI
çalışmasının geçtiği anlamına gelmez.

[10 Kasım browser doğrulaması](Evidence/HybridDesktop/20261110/verification.md).
[9 Kasım ilk dilimin geçmiş raporu](Evidence/HybridDesktop/20261109/verification.md)
o günkü test ve paket kapsamını gösterir.

## Geçişin durumu ve kalan işler

Aşama 3'ün worker, Python adaptörü ve önizleme paneli eklendi. Gerçek Chromium ve
Electron üzerindeki macOS arm64 kapsamı tarihli kanıtla izlenir; bu sonuç diğer
platformlar, canlı sağlayıcı veya tüm özelliklerin taşındığı anlamına gelmez.

Coding cockpit, Monaco, mevcut komut çıktıları/Git akışı, MCP sunucusu yönetimi,
ses/OCR/rerank/kota ve improvement ekranları sonraki aşamadadır. Etkileşimli
terminal için xterm.js yanında mevcut izinli çalışma sınırına bağlı PTY gerekir.
Bu ekranlar ve platform/imza kapıları geçilmeden `firik gui` / `firik-gui`
varsayılanları değişmez. Tk sürümü mevcut ortak Python servisleriyle çalışır.

Katmanlar ve diğer kabul kapıları: [mimari plan](HYBRID_DESKTOP_ARCHITECTURE.md).
