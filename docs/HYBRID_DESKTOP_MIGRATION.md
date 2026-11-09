# Hibrit desktop: ilk çalışan geçiş

9 Kasım 2026. Kapsam: mimari planın 1–2. aşamalarındaki sohbet ve backend
bağlantısı. Mevcut native masaüstü ve CLI giriş noktaları korunur.

## Çalışan parçalar

- Electron penceresi, React/TypeScript arayüz, Vite derlemesi, Tailwind,
  Radix/shadcn yaklaşımındaki bileşenler, Zustand UI durumu ve TanStack Query.
- Mevcut Python Agent üzerinden akışlı sohbet, konuşma başına çalışma/iptal,
  bağımsız taslaklar, sohbetler arası geçiş ve SQLite geçmişi.
- API adresi, varsayılan model, zaman aşımı, mevcut keyring servisi üzerinden
  anahtar yazma; UI'ya kayıtlı anahtar değeri döndürülmez.
- Model kataloğunu yenileme, doğrulanmış reasoning seviyeleri, mevcut MCP'leri
  konuşma başına seçme ve mevcut ajan onay callback'lerini cevaplama.
- Özel stdio üzerinde sürümlü JSON-RPC; Pydantic'ten üretilen ortak JSON Schema,
  TypeScript türleri ve IPC'nin iki tarafında çalışma zamanı doğrulaması.
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

Paketleme UI'yı, sözleşmeleri ve backend'i sırasıyla yeniden derler; hata alan
aşamadan sonra devam etmez. Çıktılar `dist/hybrid-desktop/` altındadır. Bu ilk
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

## Doğrulama

```sh
npm run desktop:build
npm run desktop:check
npm run desktop:test
.venv/bin/python -m pytest -q tests/test_desktop_rpc.py
npm run desktop:smoke
```

Smoke testi gerçek Electron penceresi, gerçek Python Agent ve loopback üzerinde
yerel sahte FIRIK sağlayıcısı kullanır. Canlı FIRIK çağrısı veya model kotası
kullanmaz; kullanıcının geçmiş/ayar/anahtarlarını değiştirmez. Akış, model/ayar,
iptal, geçmiş, yeniden açılış, temel renderer izolasyonu ve ekran boyutu
kontrollerini yapar. Görüntüler/sonuçlar `test-results/hybrid-desktop/` altına
yazılır. `FIRIK_DESKTOP_EVIDENCE` çıktı dizinini değiştirebilir.

Paketlenmiş uygulamayı aynı testle sınamak için
`FIRIK_DESKTOP_PACKAGED_EXECUTABLE` paket içindeki yürütülebilir dosyaya ayarlanır.
Bu modda Python sidecar kurulum paketinin içinden çalışır.

macOS/Windows eski GUI regresyonları proje standardına göre her Tk testini ayrı
süreçte çalıştırır:

```sh
.venv/bin/python scripts/test_desktop.py
```

Yeni CI işi ortak şemaları yeniden üretip fark denetler, TypeScript derler ve
Python/TypeScript sınır testlerini çalıştırır. Yerel doğrulama sonucu, uzak CI
çalışmasının geçtiği anlamına gelmez.

[Yerel doğrulama raporu ve ekran görüntüleri](Evidence/HybridDesktop/20261109/verification.md).

## Sıradaki geçiş

TypeScript browser yürütücüsü, React browser paneli ve gerçek WebContentsView
bu ilk dilimde uygulanmadı. Browser mevcut Python/Tk sürümünde çalışmaya devam
eder. Browser geçişi mevcut izin, ref/revision, devralma, gizlilik, ağ politikası
ve belirsiz yan etki testlerinin TypeScript yürütücüde karşılığını gerektirir.

Coding cockpit, Monaco, etkileşimli terminal, MCP sunucusu ekleme/yönetme,
ses/OCR/rerank/kota ekranları ve improvement paneli de henüz Electron'a taşınmadı.
Bu özellikler için mevcut native UI kullanılabilir. Electron bütün temel
akışlarda eşlik sağlamadan varsayılan `firik gui` / `firik-gui` değiştirilmez.

Katmanlar ve diğer kabul kapıları: [mimari plan](HYBRID_DESKTOP_ARCHITECTURE.md).
