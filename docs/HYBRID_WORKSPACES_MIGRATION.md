# Kodlama, GitGod ve sesli asistan: Electron çalışma alanları

15 Kasım 2026. Bu değişiklik hibrit mimarinin 4. aşamasını başlatır. React/TypeScript
ekranları mevcut Python uygulama servislerini özel stdio JSON-RPC üzerinden çağırır.
Ajan döngüsü, Git doğrulaması, ses motorları ve SQLite verileri ortak serviste kalır.
Python çekirdeğinin tamamının TypeScript'e çevrildiği veya native arayüzle tam
özellik eşliği sağlandığı anlamına gelmez.

16 Kasım 2026 güncellemesi: GitGod kodlama alanının geliştirme araçlarına alındı;
yerel klasör seçimi, dosya bağlamı, etkili proje izinleri ve günlük Git işlemleri
eklendi. Aşağıdaki kullanım bu güncel davranışı, ilk dilime ait kanıtlar ise kendi
tarihindeki kapsamı anlatır.

**Arayüz yönü:** Yeni arayüz geliştirmelerinin hedefi tamamen Electron + React +
TypeScript'tir. Python/CustomTkinter arayüzü için yeni geliştirme veya eşlik işi
planlanmaz. Ortak Python backend servisi kullanılmaya devam eder. Güncel kapsam,
ilerleme ve sonraki dilimler [Electron UX yol haritasında](SOFTWARE_DEVELOPMENT_UX_AUDIT_20261115.md)
izlenir.

## Kullanım

```sh
npm run desktop:build
npm run desktop:start
```

Sol menüden **Kodlama ajanı** veya **Sesli asistan** seçilir. GitGod'a kodlama alanının
**GitGod** araç sekmesinden ulaşılır; **Ajan** sekmesi aynı projedeki kodlama oturumunu
gösterir. Mevcut projeler ortak veritabanından gelir. **Proje ekle → Klasör seç**,
işletim sisteminin yerel klasör seçicisini açar ve klasör adından proje adı önerir.
İsim değiştirilebilir; yol elle de girilebilir. Seçimi iptal etmek proje oluşturmaz.
Aynı klasör ikinci kez eklenirse mevcut proje seçilir.

### Kodlama ajanı

- Gerçek proje bağlamını hazırlama, modele akışlı istek, yeni oturum ve son 50
  çalışma içinden konuşma geçmişini geri açma.
- Kodlama alanında projeye ait aranabilir oturum listesi; Cmd/Ctrl+N ile
  aynı projede yeni kodlama oturumu. Çalışma sürerken yeni oturum isteği reddedilir.
- **Ajan / GitGod** geçişinde ajan bileşeni bağlı kalır; oturum, taslak ve seçili
  bağlam korunur. Çalışma sürerken GitGod içinde de **Durdur** denetimi bulunur.
- Projeye sor, planla, onaylı uygula ve yardımlı uygula modları; model seçimi.
  Model kataloğunun desteklediği seviyeler varsa **Düşünme** seçimi sunulur ve
  backend seçilen seviyeyi doğrular.
- Sabit görev çubuğunda model/mod açıklaması, durum ve aktif çalışma denetimleri.
  Cmd/Ctrl+Enter gönderir, Enter yeni satır ekler; uzun konuşmada sona takip ve
  yeni çıktıya gitme bulunur.
- Mevcut runtime üzerinden duraklatma, devam ettirme, iptal ve tam işlem onayı.
  Kontroller proje/oturum/çalışma kimliğine bağlıdır; eski onay yeni çalışmaya uygulanmaz.
- Plan, dosya değişiklikleri/diff ve test kanıtları gösterilir. Arayüz kendi
  doğrulama sonucunu üretmez; kaydedilen servis sonucunu gösterir.
- Test sunumu eski/engellenmiş/çalıştırılmamış/bilinmeyen kanıtları ayırır;
  stdout ve stderr birlikte korunur. İnceleme sekmeleri klavyeyle kullanılabilir;
  dar görünümde panel açılır/kapanır çekmeceye dönüşür.
- Kayıtlı proje izinleri, dışlanan dosyalar, işlem politikası, kalite kapıları ve
  execution/sandbox ayarı korunur. Kodlama oturumları sohbet listesine karışmaz.
- Üst çubuk etkili okuma/düzenleme/komut/test izinlerini gösterir. **İnceleme →
  İzinler** bu dört tercihi proje ayarlarına kaydeder; backend proje politikasını
  uyguladıktan sonraki etkili değerleri döndürür. Aktif çalışma sırasında izin
  değiştirilemez; komut/test izinleri işlem onayını kaldırmaz.
- **İnceleme → Bağlam**, klasör gezintisi, proje genelinde dosya adı/yolu arama,
  yenileme ve metin önizlemesi sunar. Listeleme en fazla 300, arama en fazla 100
  sonuçla sınırlanır; sınır bilgisi arayüze iletilir. Önizleme en fazla 64 KiB okur.
  Okuma izni, görünürlük, dışlanan yollar ve proje kökü denetimleri backend'dedir.
- **Dosya ekle** ile Bağlam açılır; oturum için en fazla 24 dosya/klasör seçimi
  sonraki isteğe `context_paths` olarak gönderilir. Bu seçim, mevcut bağlam
  oluşturma ve bütçeleme kuralları içinde kullanılır; tam dosya içeriğinin her
  isteğe sığacağı garanti edilmez. Girişteki seçimler ayrı ayrı çıkarılabilir.
- **Projeyi tanı**, **Özellik geliştir**, **Hata gider**, **Test ekle** ve
  **Değişiklikleri incele** başlangıçları modu ve düzenlenebilir istek taslağını
  doldurur; çalışmayı kullanıcı gönderir.
- Aynı proje klasöründe bir kodlama çalışması yürütülür; başka projeler bağımsızdır.
  Bağlam hazırlanırken de iptal işlenir. İptalden sonra servis kalıcı kaydı ve
  kaynak temizliğini tamamlayana kadar çalışma meşgul kalır.

Bu dilimde kodlama MCP/browser/alt ajanları etkinleştiren ayar ekranları henüz
taşınmadığından bu yetkiler kapalı başlar. Mevcut native ayarların bu yetkileri
kendiliğinden açması engellenir. İleride taşınan denetimler aynı izin sınırını kullanmalıdır.

### Kodlama ajanı içindeki GitGod

GitGod, kodlama ajanının seçili projesinde çalışır. **Değişen dosyalar** listesinde
dosyalar fareyle seçilir; **Seçilenleri hazırla** ve **Hazırlıktan çıkar** seçili
yolları stage/unstage formuna doldurur. Dosya satırındaki **Diff** ilgili dosyayı
forma taşır; **Commit oluştur** commit formunu açar. Kullanıcı önce **Komutu
önizle**, sonra gereken onayı verip **Çalıştır** adımını uygular. Bu kısayollar
kendiliğinden Git komutu yürütmez.

Sık kullanılan durum/diff/stage/unstage/commit araçları başlangıçta görünür.
**Tüm Git araçları** mevcut `GitService.get_tool_catalog()` kataloğunun tamamını
açar; katalogda arama yapılabilir. Durum, diff ve son commitler okunabilir;
tüm araçlar önce komut önizlemesi üretir. Değişiklik yapan araçlar için ayrıca
görünür bir onay kutusu gerekir.

Sunucuda tutulan önizleme jetonu proje/kök dizin, araç parametreleri, çözülmüş
komut ve hedeflere bağlıdır; beş dakika geçerlidir ve bir kez çalıştırılır.
Renderer ham Git/shell komutu gönderemez. Yürütme sırasında mevcut Git servisi
onaylanan komutu ve hedefi yeniden karşılaştırır. Çalışan kodlama ajanıyla aynı
projeyi değiştirecek Git işlemi, ajan bitene/durdurulana kadar reddedilir.

### Sesli asistan

- Seçili sohbet için mevcut `VoiceController` ile mikrofon kaydı, görüşme/VAD,
  dinlemeyi duraklatma/devam ettirme, sesi durdurma ve son yanıtı okuma.
- Transkript ekranda düzenlenir ve **İnceledim, sohbete gönder** ile açıkça gönderilir.
  `VoiceRequestRouter` ve ses kaynaklı istek/araç onayı kısıtları kullanılır.
- Ses sağlayıcısı, konuşma dili, yanıt okuma ve oynatma hızı ortak config'e kaydedilir.
  Diğer native ses ayarları korunur. Kaydedilmiş otomatik gönderim tercihi bu ilk
  dilimde transkript incelemesini atlatmaz.
- Sohbet değişimi eski mikrofon/controller neslini kapatır. Kapsam ve inceleme
  kimlikleri, gecikmiş transkriptin başka sohbete gönderilmesini engeller.
- Ekrandan çıkma sesi kapatır; ayrı **Ajan yanıtını durdur** denetimi çalışmayı iptal eder.
  Ham mikrofon baytları RPC, arayüz veya olay kayıtlarına taşınmaz.

Ses aygıtları ekran açılırken başlatılmaz. Ses kullanımı mevcut isteğe bağlı Python
ses bağımlılıklarını ve işletim sistemi mikrofon iznini gerektirir. macOS uygulama
manifestine mikrofon kullanım açıklaması eklendi; paketlenmiş/imzalı uygulamada
izin akışı ayrıca doğrulanmalıdır.

## İletişim ve yaşam döngüsü

İlk dilimde 13 yeni yöntem Pydantic'ten ortak JSON Schema/TypeScript sözleşmelerine
üretildi. 16 Kasım dilimi `projects.files`, `projects.read`, `coding.preferences`
ve `coding.preferences.update` yöntemlerini, kodlama isteğine `context_paths` ve
`reasoning_effort` alanlarını ekler. Yerel klasör seçici Python RPC kullanmaz;
Electron main sürecinde yalnız sabit `firik:desktop.selectProjectDirectory` IPC kanalı
üzerinden açılır. Seçim girdileri doğrulanır ve dialog ana pencereye bağlanır.
Preload ve main yalnız sabit IPC yöntemlerini sunar; mevcut pencere/frame/origin
denetimi uygulanır. Bağlam, Git ve aygıt işleri stdio okuyucusunu bloke etmez.
İptal/onay denetimlerinin ayrı, sınırlı kuyruğu vardır; dört bağlam işçisi meşgulken
de iptal ulaşır. Kopan bağlantıda işlemler otomatik gönderilmez/tekrarlanmaz.

## Doğrulama

```sh
npm run desktop:check
npm run desktop:test
npm run desktop:build
.venv/bin/python -m pytest -q tests/test_desktop_rpc.py tests/test_desktop_workspaces.py tests/test_desktop_coding_tools.py
npm run desktop:smoke:workspaces
npm run desktop:smoke
```

Python testleri gerçek servis, SQLite, ajan döngüsü ve geçici Git depolarını
kullanır. Ses testi kayıt/ASR aygıtlarını enjekte eder; gerçek controller,
yönlendirici ve ajan akışı çalışır. Kapsam/çalışma izolasyonu, dosya onayı,
hazırlık sırasında iptal, dolu işçi kuyruğunda iptal ve Git jetonlarının tek
kullanımlılığı sınanır.

Yeni Electron smoke testi izole geçici veri dizini ve loopback test sağlayıcısıyla
proje kaydı, kodlama/oturum geçmişi, Git index değişikliği, ses ayarları ve dar
pencerede gezinmeyi sınar. Görüntüler/sonuçlar `test-results/hybrid-workspaces/`
altına yazılır. Mevcut sohbet/tarayıcı smoke testi ayrıca çalıştırılır. Canlı
model kotası veya kullanıcının kayıtlı verileri kullanılmaz. Gerçek mikrofon,
hoparlör ve canlı ASR/TTS sağlayıcısı bu testlerde sınanmadı.

**16 Kasım doğrulaması:** TypeScript kontrolü, üretim derlemesi, 45 masaüstü testi
ve 34 Python backend testi geçti; standart komutta 5 isteğe bağlı tarayıcı testi
atlandı. Electron çalışma alanı smoke testi 10 kontrol grubunu, genel sohbet/tarayıcı
smoke testi 13 kontrolü geçti. Klasör seçicinin IPC akışı (OS penceresi test dublörü),
iptal/boşluklu yollar, bağlam seçimi, okuma iznini kapatıp açma, oturum izolasyonu,
Git formu kalıcılığı, dışarıdan Git değişikliğini yenileme, gerçek stage işlemi ve
dar pencere/kart boyutları doğrulandı. Sonuçlar `test-results/hybrid-workspaces/`
altındadır. Gerçek Windows/Linux yerel dosya seçici etkileşimi henüz sınanmadı.

## Sonraki geçiş dilimleri

Tam dosya düzenleyicisi ve satır numaralı diff/IDE'ye açma; bağımsız komut/PTY paneli;
bağlam bütçesi/kaynak ayrıntıları; checkpoint/recovery ve harness/alt ajan panellerinin
tam eşliği; görev/planlama yaşam döngüsü; geçmiş sayfalaması; kodlama içinde
ses yönlendirmesi ve sesle gezinme komutları; ses aygıtı seçimi/tanılama; MCP sunucusu
yönetimi; OCR/rerank/kota/improvement ekranları. Kaynaksız paket doğrulaması,
macOS imza/noter ve diğer platform testleri ayrı kabul kapılarıdır.

Bu dilim kurulumun varsayılan başlatıcısını değiştirmez. Electron'un varsayılan
kurulum/başlatıcı olması paket doğrulaması ve geçiş kabulünün ardından tamamlanır;
Python arayüzü için yeni geliştirme planlanmaz.
