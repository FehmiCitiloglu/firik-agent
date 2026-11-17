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

17 Kasım 2026 güncellemesi: Kodlama alanına yerleşik komut terminali eklendi.
Kullanıcının komutları ve ajanın mevcut onaylı komut araçları aynı proje geçmişinde
görünür; komut yürütmek için ek bir MCP sunucusu gerekmez.

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

### Yerleşik komut terminali

Kodlama alanının altındaki **Terminal**, **Ajan / GitGod** sekmeleri arasında
korunan, açılıp kapatılabilen bir paneldir. Geniş görünümde yüksekliği fareyle veya
klavyeyle ayarlanabilir; dar görünümde çekmece olarak açılır. Panel kapanınca komut
çalışmaya devam eder. **Komutu çalıştır** veya Cmd/Ctrl+Enter kullanıcı komutunu
başlatır; **Klasör** her komutun proje içindeki başlangıç klasörünü, **Süre** ise
projenin izin verdiği zaman aşımını seçer.

- Geçmişte **Siz / Ajan** kaynağı, komut, çalışma klasörü, yerel/Docker yürütme
  hedefi, durum, çıkış kodu ve süre gösterilir. stdout ve stderr ayrı ayrı canlı
  güncellenir. Her akışta en fazla 32.768 karakter tutulur; kısaltma bildirilir.
  Geçmiş proje başına bu uygulama oturumundaki son 10 komutla sınırlıdır;
  uygulama genelindeki kayıt sayısı ve iletişim boyutu da sınırlandırılır.
- **Komutu durdur** kullanıcının komutunu alt süreçleriyle durdurur. Ajanın
  komutları kodlama çalışmasının mevcut **Durdur** denetimiyle iptal edilir.
  Kaynak temizliği bitene kadar komut meşgul kalır; uygulama kapanışı da sahip
  olduğu kullanıcı komutlarını durdurup temizliği bekler.
- **Çıktıyı ajana ekle**, seçili sonucu aktif kodlama oturumunun mevcut taslağına
  ekler ve Ajan sekmesine döner. İstek kendiliğinden gönderilmez; çıktı taslağa
  eklenirken ayrıca sınırlanır. Komut tekrar çalıştırılmadan düzenlenebilir veya
  sonuç kopyalanabilir.
- Ajanın yerleşik `run_command`, `run_tests` ve kalite kapısı komutları mevcut
  çalışma modu, proje politikası ve işlem onayları üzerinden yürür. Terminal
  paneli bu araçların gerçek başlangıç/çıktı/sonuç olaylarını gösterir; ek MCP
  veya paralel bir ajan komut yetkisi oluşturmaz. Kullanıcının **Komutu çalıştır**
  eylemi doğrudan komut isteğidir; kapalı proje izinlerini veya politika
  engellerini aşmaz.
- Aynı projede kullanıcı komutu ve kodlama çalışması eşzamanlı başlatılmaz.
  Kullanıcı komutu sürerken yeni ajan çalışması, değişiklik yapan Git işlemleri
  ve proje izinlerini değiştirme engellenir. Başka projelerde en fazla dört
  kullanıcı komutu eşzamanlı yürüyebilir.

`terminal.list`, ilk komuttan önce seçili yürütme hedefini ve zaman sınırını
bildirir. Mevcut `execution_config` kullanılır; Docker seçiliyse hazırlanamadığında
yerel makinede yeniden denenmez. **Yerel makine** komutları kullanıcının normal
süreç yetkileriyle çalışır; başlangıç klasörünü projede seçmek OS izolasyonu sağlamaz.
**Docker** kullanıcı komutu her seferinde geçici proje kopyasında çalışır; dosya
değişiklikleri, kurulan bağımlılıklar ve üretilen dosyalar çalışma kopyasına uygulanmaz.

Bu panel komut terminalidir. Her komut yeni, etkileşimsiz bir shell sürecidir;
`cd`, ortam değişkenleri ve shell durumu sonraki komuta taşınmaz. stdin/TTY,
parola istemleri, `vim` gibi TUI programları ve kalıcı PTY oturumları bu dilimde
desteklenmez. Tam etkileşimli terminal ayrı sonraki kapsamdır.

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
GitGod renderer'ı ham Git/shell komutu göndermez. Yürütme sırasında mevcut Git servisi
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

17 Kasım dilimi `terminal.list`, `terminal.start` ve `terminal.cancel` yöntemlerini
ekler. Renderer süreç kimliği, shell, yürütme backend'i veya izin enjekte edemez;
iptal proje ve sunucunun oluşturduğu komut kimliğiyle eşleşir. Terminal olayları
çıktıyı taşımayan tipli yenileme bildirimleridir; sınırlı ve maskelenmiş durum
ayrı sorgulanır. Komut süreçleri mevcut ortak execution backend'inde çalışır;
Python arayüzüne yeni bir terminal ekranı eklenmez.

## Doğrulama

```sh
npm run desktop:check
npm run desktop:test
npm run desktop:build
.venv/bin/python -m pytest -q tests/test_desktop_rpc.py tests/test_desktop_workspaces.py tests/test_desktop_coding_tools.py tests/test_desktop_terminal.py
npm run desktop:smoke:workspaces
npm run desktop:smoke:terminal
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

**17 Kasım terminal doğrulaması:** TypeScript kontrolü ve üretim derlemesi başarılı;
50 masaüstü testi ile 79 backend/yürütme testi geçti. Standart masaüstü komutunda
5 isteğe bağlı tarayıcı testi atlandı. Güncel gerçek Electron terminal smoke testi
12 kontrol grubunu geçti: kullanıcı komutu, stdout/stderr, hata çıkışı, canlı çıktı,
çalışma klasörü, süreç grubuyla iptal, taslağa çıktı ekleme, proje izolasyonu,
komut öncesi yürütme hedefi/zaman sınırı, klavyeyle boyutlandırma, konuşma alanının
korunması ve onaydan sonra gerçek ajan komutu doğrulandı. Backend testleri kapanış
sırasında eşzamanlı tamamlanmayı ve temizlik sürerken iptali de kapsar. Mevcut
çalışma alanı smoke testi 10, sohbet/tarayıcı smoke testi 13 kontrolü yeniden geçti.
Sonuçlar ve görüntüler `test-results/hybrid-terminal/` altındadır. Gerçek model
veya kullanıcı verisi kullanılmadı; Windows/Linux ve gerçek Docker kabulü ayrıca
yapılmalıdır.

## Sonraki geçiş dilimleri

Tam dosya düzenleyicisi ve satır numaralı diff/IDE'ye açma; kalıcı etkileşimli PTY terminali;
bağlam bütçesi/kaynak ayrıntıları; checkpoint/recovery ve harness/alt ajan panellerinin
tam eşliği; görev/planlama yaşam döngüsü; geçmiş sayfalaması; kodlama içinde
ses yönlendirmesi ve sesle gezinme komutları; ses aygıtı seçimi/tanılama; MCP sunucusu
yönetimi; OCR/rerank/kota/improvement ekranları. Kaynaksız paket doğrulaması,
macOS imza/noter ve diğer platform testleri ayrı kabul kapılarıdır.

Bu dilim kurulumun varsayılan başlatıcısını değiştirmez. Electron'un varsayılan
kurulum/başlatıcı olması paket doğrulaması ve geçiş kabulünün ardından tamamlanır;
Python arayüzü için yeni geliştirme planlanmaz.
