# Electron / TypeScript: geliştirici deneyimi ve arayüz yol haritası

**Güncelleme:** 17 Kasım 2026; ilk denetim ve ilk dilim: 15 Kasım 2026.
**Ürün kararı:** Tüm yeni masaüstü arayüz çalışmaları Electron + React + TypeScript üzerinde yapılır. Python/CustomTkinter arayüzüne ilişkin geliştirme, tasarım düzeltmesi, özellik eşleme ve test işleri bu planın kapsamından çıkarılmıştır. Mevcut Python ajan/backend servisi ile stdio/IPC bağlantısı kullanılmaya devam eder; bu karar backend dilini değiştirmez.

**Kapsam:** Projeler, Kodlama Ajanı, oturumlar, değişiklik inceleme, test kanıtları, GitGod ve ileride eklenecek görev/planlama ekranları.

Temel hedef, proje → görev → ajan çalışması → değişiklik → doğrulama → Git akışını aynı çalışma bağlamında takip edilebilir kılmak. İlk geliştirme dilimi kanıt doğruluğu, proje oturumları, görünür çalışma kontrolleri ve klavye erişimine odaklandı. 16 Kasım dilimi proje seçimini, dosya bağlamını, etkili izinleri ve kodlama içindeki günlük Git akışını ekler. 17 Kasım dilimi kullanıcı ve ajan komutlarını aynı yerleşik komut terminalinde izlemeyi sağlar.

## Denetimin dayanağı

Electron kaynakları ve izole verilerle açılan gerçek Electron penceresi incelendi. Geçici Git deposu/veritabanı ve loopback test sağlayıcısı kullanıldı; gerçek model kotası veya kullanıcı verileri kullanılmadı. Kanıt bileşeni ayrıca gerçek React renderer'ında örnek kayıtlarla çalıştırıldı.

Önceki denetimin değişiklik öncesi Electron kanıtları:

- [Etkileşim ve pencere ölçümleri](Evidence/SoftwareDevelopment/20261115/verification.json)
- [Eski test sunumunun render örnekleri](Evidence/SoftwareDevelopment/20261115/evidence-rendering.json)
- [Kodlama — 1280 px](Evidence/SoftwareDevelopment/20261115/coding-1280.png)
- [Kodlama — 940 px](Evidence/SoftwareDevelopment/20261115/coding-940.png)
- [Git — 1280 px](Evidence/SoftwareDevelopment/20261115/git-1280.png)
- [Git — 940 px](Evidence/SoftwareDevelopment/20261115/git-940.png)

Bunlar önceki davranışın kanıtıdır; yeni uygulamanın ekran görüntüleri ve doğrulaması `test-results/hybrid-workspaces/` altında üretilir. 900 px pencere talebi, uygulamanın minimum genişliği nedeniyle 940 px gerçekleşir. Denetim kullanıcı araştırması, büyük depo performans testi veya ekran okuyucu uygunluk belgesi değildir.

## Bulgu ve iş listesi

P1, sonucun doğru anlaşılmasını veya temel çalışma kontrolünü etkiler. P2, günlük geliştirme akışındaki sürtünmeleri kapsar. Durumlar 17 Kasım itibarıyla uygulanan parçaları gösterir; bir başlığın ilk parçasının uygulanması bütün başlığın tamamlandığı anlamına gelmez. 15 ve 16 Kasım kanıtları kendi tarihindeki davranışı belgelendirir.

| No | Öncelik | Bulgu / ihtiyaç | Electron / TypeScript düzeltmesi | Durum |
| --- | --- | --- | --- | --- |
| 01 | P1 | Eski test başarılı; çalıştırılmamış test başarısız görünebiliyor. stdout varsa stderr kayboluyor. | Durum/güncellik/yürütme ayrımı, ayrı çıktı kanalları, gerçek kanıt metadata'sı. | İlk dilimde uygulandı; bileşen regresyon testleri var. |
| 02 | P1 | Model/mod ve görev kontrollerinin her boyutta erişilebilir olması gerekiyor. | Üst görev çubuğu; sabit model/mod/durum ve aktif çalışmanın durdurulması. | İlk dilimde uygulandı. |
| 03 | P1 | Kodlama alanında genel sohbet geçmişi ve genel sohbet açan Cmd/Ctrl+N bulunuyor. | Proje oturumları, proje içi arama ve bağlama göre yeni kodlama oturumu. | İlk dilimde uygulandı. |
| 04 | P1 | Değişiklik inceleme dosya adı ve ham diff ile sınırlı. | Dosya listesi, birleşik diff, satır numarası, satıra gitme ve IDE'ye açma. | Sıradaki ana dilim; uzun diff satırlarının bölünmesi ilk dilimde kaldırıldı. |
| 05 | P1 | Ajanın dosya/araç/komut düzeyindeki işi görünmüyor. | Gerçek runtime olaylarından etkinlik ve komut paneli; bekleme nedeni ve süre. | Komut başlangıcı/canlı stdout-stderr/sonuç ve kullanıcı–ajan kaynak ayrımı terminalde uygulandı. Dosya ve diğer araçlar için tam etkinlik paneli bekliyor. |
| 06 | P1 | Mod ve etkili izinler karar noktasında anlaşılmıyor. | Mod açıklamaları; backend'den gelen etkili dosya/komut/test izinleri ve proje içi ayarları. | Etkili dört izin özeti ve kalıcı düzenleme uygulandı; ayrıntılı sandbox/çalıştırma ortamı inceleyicisi bekliyor. |
| 07 | P2 | Proje eklemek için tam klasör yolu yazılıyor. | Electron klasör seçici, önerilen proje adı ve ilk kullanım eylemleri. | İşletim sisteminin klasör seçicisi, otomatik ad ve elle yol girme seçeneği uygulandı. |
| 08 | P2 | GitGod'da 72 araç tek listede; dosya yolları elle yazılıyor. | Değişen dosyalar → diff → stage → commit; ayrı gelişmiş katalog; mevcut önizleme/onayı koruma. | GitGod kodlama alanına alındı; seçili dosyalardan stage/unstage/diff ve commit formuna geçiş uygulandı. Gelişmiş katalog ve işlem onayları korundu. |
| 09 | P2 | Geçmiş küçük bir select; tarih/arama ve eski kayıtlara gezinme yetersiz. | Proje oturum listesi, tarih/durum, arama; sonra anlamlı başlık ve sayfalama. | Liste/arama/tarih uygulandı; son 50 sınırı açıkça belirtiliyor. Sayfalama bekliyor. |
| 10 | P2 | Gönderim kısayolu belirsiz; uzun yanıt takibi eksik. | Cmd/Ctrl+Enter gönderim; Enter satır; sona takip ve yeni çıktıya gitme. | İlk dilimde uygulandı. |
| 11 | P2 | Dar ekranda inceleyici konuşmanın altına taşınıyor; genişte kapatılamıyor. | Dar görünümde çekmece; aç/kapat denetimi; kontrol ve giriş alanlarını görünür tutma. | Temel çekmece uygulandı; yeniden boyutlama ve kalıcı düzen tercihi bekliyor. |
| 12 | P2 | Proje, görev, oturum ve kanıtın tek gezinme yapısına ihtiyacı var. | Global gezinme ile proje gezinmesini ayır; dosyalar/oturumlar/inceleyici için kalıcı yerler belirle. | Proje oturumları, Ajan/GitGod araç sekmeleri ve Bağlam dosya gezintisi uygulandı; görev–çalışma bağlantısı bekliyor. |
| 13 | P2 | Electron'da görev/planlama yaşam döngüsü henüz sunulmuyor. | Görev detay/durum düzenleme, görev–çalışma bağlantısı, kilometre taşı oluşturma. | Ayrı sonraki kapsam; mevcut ekran gibi sunulmamalı. |
| 14 | P2 | Renk/boyut/metin sabitleri dağınık; durumların ortak görsel dili eksik. | Tasarım token'ları, ortak durum/boş ekran/panel bileşenleri; tutarlı adlandırma. | Kanıt durumları ve görev çubuğu başlangıcı uygulandı; tam tasarım sistemi bekliyor. |
| 15 | P2 | İnceleme sekmelerinin seçili durum ve klavye semantiği eksik. | tablist/tab/tabpanel; ok/Home/End tuşları ve görünür odak. | İlk dilimde uygulandı; gerçek ekran okuyucu denemesi bekliyor. |
| 16 | P2 | Plan, değişiklik ve test için aynı boş metin gösteriliyor. | Moda/kayıt türüne göre boş durum; aramada sonuç yok; önceki kayıt biçimini koruma. | Kodlama kanıtları ve oturum araması düzeltildi; diğer ekranlar bekliyor. |

## İlk dilimin somut davranışı

- Eski kanıt, engellenmiş işlem, çalıştırılmamış test ve bilinmeyen eski kayıt güncel başarıya dönüştürülmez. Gerçek başarısızlık, süre aşımı ve çelişen çıkış kodu ayrıca ele alınır.
- Testte stdout, stderr ve farklı bir kaydedilmiş output birlikte korunur. Mevcutsa çıkış kodu, süre, kanıt kimliği ve güncellik gerekçesi gösterilir; eksik metadata üretilmez.
- Kodlama/Git alanında sol bölüm proje oturumlarını gösterir. Yeni oturum isteği aktif projeye bağlıdır; çalışan görev varken yeni oturum isteği beklemeye alınıp sonradan kendiliğinden uygulanmaz.
- Model/mod/durum ve aktif iş denetimleri konuşmadan ayrı üst çubuktadır. Başlatma RPC'si sürerken durum sorgusu devam eder; run kimliği geldiğinde kontrol kullanılabilir.
- Kodlama girişinde Cmd/Ctrl+Enter gönderir, Enter satır ekler. Kullanıcı yukarı kaydırınca otomatik takip durur; yeni çıktıya gitme eylemi sunulur.
- İnceleme paneli kapatılabilir; dar pencereye geçince kapanır ve istenirse çekmece olarak açılır. Plan/Değişiklikler/Testler gerçek erişilebilir sekmelerdir.

15 Kasım ilk diliminde etkili izin özeti, dosya gezgini ve günlük Git akışı yoktu. Bunların 16 Kasım'da eklenen sınırlı kapsamı aşağıda belirtilir; tam editör, satır numaralı diff, tipli etkinlik paneli ve geçmiş sayfalaması halen sonraki işlerdir.

## 16 Kasım: proje ve günlük geliştirme akışı

- **Kodlama ajanı** seçilen proje, oturumlar ve geliştirme araçları için ortak alandır. **Ajan** ve **GitGod**, bu alanın araç sekmeleridir. GitGod'a geçerken ajan bileşeni bağlı kalır; konuşma, taslak ve seçili bağlam korunur. Aktif ajan için GitGod'da da **Durdur** bulunur. Aynı projeyi değiştiren Git işlemleri çalışma bitene kadar backend tarafından engellenir.
- **Proje ekle → Klasör seç**, Electron'un sistem klasör seçicisini açar; macOS, Windows ve Linux kendi yerel dosya seçicisini kullanır. Seçilen klasör proje adını önerir; kullanıcı adı değiştirebilir veya yolu elle girebilir. İptal proje oluşturmaz. Aynı klasör yeniden kaydedilirse mevcut proje seçilir.
- **İnceleme → Bağlam** içinde klasörlere girme, üst klasöre/köke dönme, proje genelinde dosya yolu/adı arama, yenileme ve metin önizlemesi vardır. Arama içerik taraması değildir. Listeleme ve arama sınırlıdır; sınırda aramayı daraltma uyarısı gösterilir. Önizleme en fazla 64 KiB okur ve kesilmesini bildirir.
- **Dosya ekle**, Bağlam sekmesini açar. Oturuma ait en fazla 24 dosya veya klasör sonraki isteğin açık bağlamı olarak seçilebilir; seçimler giriş alanının üstünden çıkarılabilir. Backend seçilen yolları proje kökü, gizli/dışlanan dosya kuralları ve etkili okuma izniyle doğrular. Model ayrıca mevcut bağlam oluşturma/bütçeleme kuralları içinde çalışır; seçilen her dosyanın tamamının modele sığdığı garantisi verilmez.
- **İzinler**, **Dosyaları oku**, **Dosyaları düzenle**, **Komut çalıştır** ve **Test çalıştır** tercihlerini proje ayarlarına kaydeder. Üst çubuk backend'in döndürdüğü etkili değerleri gösterir. Proje politikası bu tercihleri daraltabilir; komut/test izni işlem onayını kaldırmaz. Aktif çalışma sırasında izin değiştirilmez.
- **Düşünme** seçimi, model kataloğunda desteklenen seviyeler olan modeller için görünür; seçilen değer istekle iletilir ve backend'de doğrulanır. **Projeyi tanı**, **Özellik geliştir**, **Hata gider**, **Test ekle** ve **Değişiklikleri incele** başlangıçları uygun modu ve düzenlenebilir istek taslağını doldurur; kullanıcı gönderir.
- GitGod'da **Değişen dosyalar** seçimi, **Seçilenleri hazırla** ve **Hazırlıktan çıkar** için yolları forma doldurur; dosya satırındaki **Diff** ilgili dosyayı seçer. **Commit oluştur** commit formunu açar. Bu eylemler doğrudan komut çalıştırmaz: **Komutu önizle**, gerekiyorsa görünür onay ve **Çalıştır** akışı kullanılır. **Tüm Git araçları** gelişmiş kataloğu açar. Sunucudaki tek kullanımlık, süreli önizleme jetonları korunur.

16 Kasım dilimi dosya okuma ve bağlam seçimi sağlar; komut terminali sonraki 17 Kasım diliminde eklendi. Tam IDE/editör, kalıcı etkileşimli PTY, kontrol noktası/geri alma, görev/planlama yaşam döngüsü ve ayrıntılı etkinlik paneli tamamlanmış özellikler değildir.

## 17 Kasım: yerleşik komut terminali

- Kodlama alanının altındaki **Terminal**, Ajan ve GitGod içinde aynı proje geçmişini gösterir. Panel açılır/kapanır; geniş görünümde yüksekliği fare veya klavyeyle ayarlanır, dar görünümde çekmece olur. Kullanıcı komutu **Komutu çalıştır** veya Cmd/Ctrl+Enter ile açıkça başlatır; klasör ve zaman aşımı her komut için seçilir.
- Kullanıcı ve ajanın komutları ayrı kaynak etiketleriyle aynı geçmişte görünür. stdout/stderr canlı ve ayrı gösterilir; durum, gerçek çıkış kodu, süre ve yürütme hedefi korunur. Her çıktı akışı en fazla 32.768 karakter, proje geçmişi bu uygulama oturumundaki son 10 komutla sınırlıdır. Bu geçmiş uygulama kapanınca kalıcı bir terminal oturumuna dönüşmez.
- **Komutu durdur** kullanıcı komutunun süreç grubunu temizler. Ajan komutunu durdurmak için mevcut kodlama **Durdur** denetimi kullanılır. İptal veya kapanış sırasında kaynak temizliği bitmeden aynı projede yeni çalışma başlatılmaz.
- **Çıktıyı ajana ekle**, sonucu aktif kodlama taslağına mevcut metni koruyarak ekler; isteği kendiliğinden göndermez. Ajanın mevcut `run_command`, `run_tests` ve kalite kapısı araçları gerçek yürütme olaylarını bu panele aktarır. Mevcut mod, izin ve işlem onayları geçerlidir; terminal için ek MCP gerekmez.
- Kullanıcı komutu ile aynı projedeki ajan çalışması çakıştırılmaz; aktif kullanıcı komutu kodlama başlangıcını, değişiklik yapan Git işlemlerini ve izin değişikliğini engeller. Başka projelerde en fazla dört kullanıcı komutu eşzamanlı yürür. Mevcut host/Docker seçimi korunur ve Docker hatasında host'a geçilmez; hedef ve proje zaman sınırı ilk komuttan önce görünür.
- Host komutları yerel kullanıcı yetkileriyle çalışır; proje içi başlangıç klasörü OS sandbox değildir. Docker kullanıcı komutları geçici proje kopyasında çalışır ve dosya/bağımlılık değişiklikleri sonraki komuta veya kullanıcının çalışma kopyasına uygulanmaz.

Bu teslimat **komut terminalidir**: her komut yeni, etkileşimsiz shell süreci kullanır; `cd` ve ortam değişkenleri sonraki komuta taşınmaz. stdin, parola istemleri, `vim` gibi TUI programları ve kalıcı PTY oturumları desteklenmez. Tam etkileşimli terminal ayrı sonraki iştir. Tüm yeni ekranlar Electron içindedir; Python tarafındaki ekler ortak servis ve tipli RPC ile sınırlıdır.

## Hedef çalışma alanı

```text
Proje / dal / görev                      Mod · Model · Durum · Durdur
───────────────────────────────────────────────────────────────────
Proje oturumları │ Görev ve ajan konuşması    │ Plan / Diff / Test
Dosyalar / arama │ İstek + bağlam ekleme      │ Kanıt ayrıntıları
───────────────────────────────────────────────────────────────────
Etkinlik / Komut çıktısı                   Gerçek durum ve kapsam
```

Dar görünümde ikincil paneller çekmeceye dönüşür. Tam IDE özellikleri ilk kapsam için gerekli değildir; dosya okuma, güvenilir diff ve IDE'ye açma önce gelir.

## Güncellenmiş yol haritası

Süreler bir frontend geliştirici, bir backend geliştirici ve kısmi tasarım desteği varsayımıyla iş günüdür. Backend işleri yalnız Electron'un ihtiyaç duyduğu veri/IPC yetenekleridir; Python arayüz işi yoktur. İlk plan temel kapsam için yaklaşık 5–6 hafta öngörüyordu; bu kesin teslim tarihi değildir. Aşağıdaki aşamalardaki klasör seçici, etkili izinler, temel dosya bağlamı ve günlük Git parçaları 16 Kasım'da uygulandı; kalan işler ve kabul kapsamı ayrıca tamamlanmalıdır.

| Aşama | Süre | Teslimat | Kabul |
| --- | --- | --- | --- |
| 0 — Electron ürün sözleşmesi | 2–3 gün | Hedef yerleşim, durum/mod sözlüğü, veri/IPC ihtiyaçları, önceliklendirilmiş backlog. | Yalnız Electron/TypeScript ekran işleri ve gerekli backend yetenekleri var. |
| 1 — Kanıt ve çalışma kontrolü | 4–5 gün | 01–03; 06'nın mod açıklamaları; 09–11 ve 15–16'nın temel parçaları. | Eski test başarı görünmüyor; stdout/stderr korunuyor; proje oturumu doğru; dar görünümde model/mod/durdurma/giriş erişilebilir. |
| 2 — Günlük kodlama akışı | 7–8 gün | Dosya listesi/diff/IDE'ye açma; gerçek etkinlik/komut görünümü; klasör seçici; etkili izin özeti; geçmiş sayfalama. | Proje aç → görev ver → değişen satırı ve güncel test kanıtını incele akışı aynı bağlamda tamamlanıyor. |
| 3 — Git ve düzen | 5–6 gün | Dosya seçimi/stage/commit, gelişmiş katalog, riske uygun önizleme, panel boyutlandırma/tercihler. | Dosya yolu yazmadan Git akışı; mevcut işlem onayları korunuyor. |
| 4 — Tutarlılık ve kabul | 3–4 gün | Ortak tasarım bileşenleri; boş/hata durumları; klavye/ekran okuyucu; uzun içerik; geliştiricilerle görev bazlı deneme. | Kritik akışlar tüm desteklenen boyutlarda ve klavyeyle tamamlanıyor; P1 açıkları kapanıyor. |
| Sonraki kapsam | Ayrı tahmin | Görev/planlama ekranları, kontrol noktası/geri alma, tam editör/PTY, kapsamlı context/harness/alt ajan panelleri. | Her dilim veri, izin, kalıcılık ve etkileşim kabulünü tamamlıyor. |

Bağımlılık sırası: kanıt doğruluğu → çalışma alanı/oturum kontrolü → dosya/diff ve gerçek olay verisi → Git akışı → kapsamlı planlama. Görsel kurallar başlangıçta tanımlanıp her dilimde uygulanır.

**Dağıtım geçişi:** Electron'un varsayılan kurulum ve başlatıcı olması, gerekli Electron ekranları ve bağımsız paket kabulü tamamlandığında ayrıca uygulanır. Bu ilk UX dilimi sistem kurulumunu veya varsayılan başlatıcıyı değiştirmez.

## Doğrulama

```sh
npm run desktop:check
npm run desktop:test
npm run desktop:build
npm run desktop:smoke:workspaces
npm run desktop:smoke:terminal
npm run desktop:smoke
```

Bileşen regresyonları eski başarılı kayıt, engellenmiş/çalıştırılmamış kayıt, stdout+stderr, bilinmeyen eski kayıt ve proje bağlamını kapsar. Electron smoke; gerçek IPC/backend ile klavye gönderimi, yeni oturum, kalıcı geçmiş, taslak koruma, aktif iş kontrolü, dar görünüm, sekmeler, Git önizleme/onay ve ses ayarlarını sınar. Sonuçlar ve görüntüler `test-results/hybrid-workspaces/` altındadır.

**İlk dilim sonucu:** TypeScript kontrolü ve üretim derlemesi başarılı. 40 test geçti; 5 isteğe bağlı tarayıcı entegrasyon testi standart test komutunda atlandı. Electron çalışma alanı smoke testi 7 kontrol grubunu, genel sohbet/tarayıcı smoke testi 13 kontrolü geçti. 940×620'de kritik kontroller, Esc ile inceleyiciyi kapatma/odak dönüşü, uzun çıktı takibi ve iki proje arasında geçmiş/taslak izolasyonu doğrulandı. Derlemede ana JavaScript paketi için 500 kB boyut uyarısı bulunuyor; bu bir derleme hatası değil.

**16 Kasım doğrulaması:** TypeScript kontrolü ve üretim derlemesi başarılı. 45 masaüstü testi ve 34 Python backend testi geçti; standart masaüstü komutunda 5 isteğe bağlı tarayıcı testi atlandı. Gerçek Electron çalışma alanı smoke testi 10 kontrol grubunu, genel sohbet/tarayıcı smoke testi 13 kontrolü geçti. Yeni kontroller klasör seçici IPC'sini (yalnız OS penceresi test dublörü), iptali, boşluklu yolları, dosya arama/önizleme/bağlamı, okuma iznini kapatıp açmayı, oturum izolasyonunu, Git formunun sekmeler arasında korunmasını, dışarıdan değişen Git durumunun yenilenmesini ve dosya seçimiyle gerçek stage işlemini kapsar. 940×620 görünüm ile Git kartlarının ve komut çıktısının kesilmemesi de sınandı. Sonuçlar [doğrulama kaydında](../test-results/hybrid-workspaces/verification.json); ekranlar aynı dizindedir. Gerçek model kotası ve kullanıcı verileri kullanılmadı. Yerel klasör seçicinin gerçek Windows/Linux etkileşimi bu değişiklikte doğrulanmış sayılmaz.

**17 Kasım terminal doğrulaması:** TypeScript kontrolü ve üretim derlemesi başarılı; 50 masaüstü testi ile 79 backend/yürütme testi geçti. Standart masaüstü komutunda 5 isteğe bağlı tarayıcı testi atlandı. Gerçek Electron terminal smoke testi 12 kontrol grubunu geçti. Kullanıcı komutu ve ajanın onaylı yerleşik komutu; stdout/stderr, hata kodu, canlı çıktı, çalışma klasörü, alt süreçlerle iptal, taslağa çıktı ekleme, proje izolasyonu, komut öncesi yürütme hedefi/zaman sınırı, klavyeyle panel boyutlandırma ve 940×620 görünüm [terminal kaydında](../test-results/hybrid-terminal/verification.json) doğrulandı. Terminal açıkken konuşma/çıktı alanı ve kullanıcı komutu sırasında ajan gönderiminin engellenmesi de sınandı. Backend regresyonları kapanış sırasında tamamlanan komutun diğer komutların temizliğini atlatmamasını ve temizlik sürerken iptali kapsar. Mevcut çalışma alanı smoke testi 10, sohbet/tarayıcı smoke testi 13 kontrolü yeniden geçti. Gerçek model ve kullanıcı verisi kullanılmadı; gerçek Docker/Windows/Linux kabulü bu kayıtla sağlanmış sayılmaz.

Gerçek model, büyük depo performansı, ekran okuyucu ve paketlenmiş Windows/Linux kabulü ayrıca yapılmalıdır. Eski denetimin kanıtları tarihsel kayıt olarak korunur; Python arayüzü için bir sonraki geliştirme işi oluşturmaz.
