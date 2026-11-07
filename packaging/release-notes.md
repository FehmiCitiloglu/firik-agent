## v0.2.50 değişiklikleri

- Gömülü tarayıcının klavye girdi testi, sentetik tıklama öncesinde yerel pencereyi etkinleştirecek şekilde düzeltildi.
- Geniş pencere yerleşimi testi, küçük CI ekranlarında Tk pencere boyutu sınırının testi yanlışlıkla dar yerleşimde tutmasını önleyecek şekilde güncellendi.
- v0.2.49 için hazırlanan gömülü tarayıcı, genel HTTPS izinleri ve araç hatası sonrası sohbet devamı iyileştirmelerinin tamamı bu yayına dahildir.

## v0.2.49 değişiklikleri

- Sohbet ve kodlama panellerine gömülü Chromium görünümü, gezinme kontrolleri ve tarayıcı girdisi eklendi; dar pencere yerleşimi ve elle kontrol akışı geliştirildi.
- Genel HTTPS sayfalarında gezinme ve yerleşik web arama/okuma için kapsamı doğrulanan izinler eklendi; HTTP, özel ağ ve güvenli olmayan yönlendirme kısıtları korunur.
- Tarayıcı gezinme ve gözlem hatalarından sonra sohbetin önceki kanıtlarla ve bağımsız kaynaklarla devam etmesi sağlandı. Sonucu belirsiz işlemlerin tekrar gönderilmesi engellenir.
- Yerel tarayıcı araçlarının seçimi, oturum durumu ve hata sınıflandırması iyileştirildi; gerçek Chromium, izin, kurtarma ve masaüstü arayüz regresyonları genişletildi.
- Tarayıcı mimarisi ve kullanım belgeleri ile yerel doğrulama raporları güncellendi.

## v0.2.48 değişiklikleri

- Windows yayınını engelleyen POSIX kabuk sözdizimli süreç iptal testi, platformun gerçek kabuğunda çalışan Python süreç ağacı ve hazır olma sinyaliyle düzeltildi; hızlı Windows regresyon akışına eklendi.
- v0.2.47 için hazırlanan entegre Chromium tarayıcısı, sohbet/kodlama panelleri, denetimli geliştirme sunucuları ve paketlenmiş tarayıcı doğrulaması bu yayına dahildir.

## v0.2.47 değişiklikleri

- Yerel ajan döngüsüne gerçek Chromium tarayıcı araçları, sohbet ve Kodlama Ajanı için Tarayıcı paneli ve isteğe bağlı canlı önizleme eklendi.
- Oturum ve köken bazlı tarayıcı izinleri, işlem önizlemesine bağlı onaylar, giriş/MFA için elle kontrol ve özel ekran görüntüsü izinleri eklendi.
- Kodlama görevlerinde denetimli yerel geliştirme sunucuları ve güncel kaynak durumuna bağlı tarayıcı doğrulama kanıtları eklendi.
- Tarayıcı gözlemlerinin saklanması, hassas veri temizliği, sağlayıcı görüntü aktarımı ve kesintilerden sonra belirsiz işlemlerin kurtarılması geliştirildi.
- Masaüstü paketlerine Playwright sürücüsü dahil edildi; yayın akışı paketlenmiş uygulamadan Chromium başlatmayı da doğrular. Chromium ayrıca açıkça kurulmalıdır; uygulama açılışında indirilmez ve tarayıcı varsayılan olarak kapalıdır.
- Tarayıcı mimarisi, kullanım kılavuzu, yerel doğrulama kanıtları ve isteğe bağlı çapraz platform Chromium test akışı eklendi.

## v0.2.46 değişiklikleri

- Harici harness oturum başlatma el sıkışması (`get_state`) zaman aşımı sabit 10 saniyeye ayarlanarak, alt-saniye görev zaman aşımlı testlerde ve yavaş macOS koşucularında erken oturum iptali (`startup_timeout`) önlendi.
- Linux, macOS ve Windows platformlarında masaüstü paketleme ve regresyon testleri tam uyumlu hale getirildi.
- v0.2.42-v0.2.45 sürümlerinde hazırlanan tüm MCP ve arayüz iyileştirmeleri bu yayına dahildir.

## v0.2.45 değişiklikleri

- Windows headless test ortamında CTkToplevel ve Tcl native widget odaklanma ve olay işleme (`update_idletasks` ve toplevel pencere senkronizasyonu) güvenceye alındı.
- Linux, macOS ve Windows platformlarında masaüstü paketleme ve regresyon testleri tam uyumlu hale getirildi.
- v0.2.42-v0.2.44 sürümlerinde hazırlanan tüm MCP ve arayüz iyileştirmeleri bu yayına dahildir.

## v0.2.44 değişiklikleri

- Windows ortamında metin düzenleme kısayol testleri (`Control` değiştirici tuşu ve Tk Text `\n` kırpma) platforma uygun hale getirildi.
- Linux, macOS ve Windows genelinde tüm test ve masaüstü paketleme adımları uyumlu kılındı.
- v0.2.42 ve v0.2.43 için hazırlanan tüm MCP ve arayüz geliştirmeleri bu yayına dahildir.

## v0.2.43 değişiklikleri

- Linux X11 ortamlarında yerel Tk tümünü seçme tuş kombinasyonu (`<Control-Key-slash>`) ve pano temizleme regresyonu düzeltildi.
- v0.2.42 için hazırlanan MCP hata kurtarma, güvenli sentez yanıtı ve yerel metin düzenleme kısayollarının tamamı bu yayına dahildir.

## v0.2.42 değişiklikleri

- MCP çağrı hataları sonrası sohbetin kesilmeden devam edebilmesi sağlandı; sunucu ve taşıma hataları araç sonucu olarak döndürülür ve model eldeki başarılı sonuçlarla veya bağımsız araçlarla devam eder.
- Tekrarlanan işlem hataları ve araç sınırı aşıldığında güvenli sentez yanıtı mekanizması eklendi; sonucu belirsiz yan etkili işlemlerin kontrolsüz tekrarı engellendi.
- Masaüstü arayüzünde (FirikApp) çapraz platform ve işletim sistemine özgü (Aqua/macOS vs. Windows/Linux) metin düzenleme kısayolları ve seçim kontrolleri eklendi; yerel kısayol çakışmaları giderildi.
- Kodlama çalışma alanında sekme tamamlama (Tab) ve öneri seçimi için niteleyici tuş filtreleme (Shift/Ctrl/Cmd/Alt) eklendi.
- Phase 2 scripted benchmark senaryoları ve MCP continuation/results testleri eklendi.

## v0.2.41 değişiklikleri

- Windows'ta ses konsolu kurulurken ve native başlık çubuğu yenilenirken mikrofonun yanlışlıkla duraklatılmasına yol açan olay sırası düzeltildi.
- Gerçek pencere gizlenmesinde mikrofonun durması korunur; konsolun açılması ve tema yenilenmesi yeni kayıt başlatmaz.
- Açık/koyu tema, klavye erişimi ve mikrofon yaşam döngüsü için kısa Windows regresyon akışı eklendi; eşzamanlı benchmark testi yavaş CI ortamlarında kararlı hale getirildi.
- Önceki yayın denemelerinde hazırlanan sesli asistan konsolu ve tüm düzeltmeler bu yayına dahildir.

## v0.2.40 değişiklikleri

- Windows'ta tema yenilenirken açılan ses konsolunun geçici gizli pencere durumunu devralması düzeltildi.
- Tema testleri, gerçek ana pencere yeniden görünür olduktan sonra ses yakalamayı başlatır; pencere yenilemesinin yeni kayıt başlatmadığı da doğrulanır.
- v0.2.39 için hazırlanan sesli asistan konsolu ve kapanış düzeltmeleri bu yayına dahildir.

## v0.2.39 değişiklikleri

- Ses penceresi; gerçek mikrofon düzeyini ve görev durumunu gösteren, hareketi azaltılabilen bir asistan konsoluna dönüştürüldü.
- Mevcut proje/sohbetlere sesle geçiş, görev durumu ve planını sorgulama, incelenmiş duraklatma/devam/durdurma komutları eklendi. Araç ve plan onayları mevcut onay akışında kalır.
- Mikrofon, seslendirme ve ajan görevi için bağımsız kontroller; gerçek işlem ilerlemesi ve onay bekleme göstergeleri eklendi.
- Konsol açılırken gelen mikrofon düzeyi korunur; kapatılan ses ayarlarının bellekten serbest bırakılması düzeltildi.
- Entegre tarayıcı için tasarım önerisi belgelendi; bu sürümde tarayıcı uygulaması bulunmaz.

## v0.2.38 değişiklikleri

- Apple CloudKit bilet servisindeki geçici bağlantı kesintileri için kademeli, sınırlı yeniden deneme süresi artırıldı.
- Noter onayı, bilet doğrulaması ve Gatekeeper kontrolleri zorunlu kalır; diğer hata türleri yeniden denenmez.
- Önceki yayın denemelerindeki tüm ses, öz iyileştirme ve masaüstü düzeltmeleri bu yayına dahildir.

## v0.2.37 değişiklikleri

- Windows'ta ses penceresi kurulumu sırasında gelen mikrofon hatalarının kaybolması ve tema değişiminde ses penceresinin gizli kalması düzeltildi.
- Windows'un yerel başlık çubuğu yenilemesi sırasında geçici gizlenmenin mikrofonu yanlışlıkla durdurması önlendi.
- Platform testleri Canvas koordinatları, gerçek odak olayları ve Windows'ta güvenli kod uygulama sınırlarıyla uyumlu hale getirildi.
- v0.2.35 ve v0.2.36 için hazırlanan tüm ses, öz iyileştirme ve masaüstü düzeltmeleri bu yayına dahildir.

## v0.2.36 değişiklikleri

- Dar kodlama pencerelerinde fark önizlemesinin görünür yüksekliği korundu.
- macOS arayüz testlerinde Tcl/Tk süreç izolasyonu; Linux sanal ekran testlerinde pencere yöneticisi eklendi.
- Windows testlerinde Git satır sonları, odak geçişleri ve yavaş CI bekleme sınırları düzeltildi.
- v0.2.35 için hazırlanan ses ve öz iyileştirme özellikleri bu yayına dahildir.

## v0.2.35 değişiklikleri

- Sesli giriş/çıkış, Evren ASR/TTS yönlendirmesi, cihaz ayarları ve tanılama geliştirildi.
- Masaüstü paketlerinde ses bağımlılıkları ve paketlenmiş uygulama kontrolleri güncellendi.
- Kanıta dayalı deneyim kaydı, bellek/öneri inceleme, sürüm bazlı onay ve geri alma içeren öz iyileştirme altyapısı eklendi.
- Öz iyileştirme için CLI komutları ve masaüstü inceleme görünümü eklendi; aday deneyleri mevcut yalıtım ve izin sınırlarını kullanır.
- Kodlama oturumları, bağlam toplama, eklentiler ve ilgili regresyon testleri güncellendi.

## v0.2.34 değişiklikleri

- Yavaş CI ortamlarında kodlama geçmişi ve kısmi yanıt testlerinin bekleme sınırları artırıldı.

## v0.2.33 değişiklikleri

- Yavaş Windows CI ortamlarında kodlama oturumu testinin bekleme süresi artırıldı.

## v0.2.32 değişiklikleri

- macOS DMG oluşturulurken geçici kaynak meşgul hataları için sınırlı tekrar denemesi eklendi.

## v0.2.31 değişiklikleri

- Yavaş sistemlerde harici süreç tanılama testinin zamanlama kararlılığı iyileştirildi.

## v0.2.30 değişiklikleri

- Windows çalışma alanı testi satır sonu uyumluluğu düzeltildi.

## v0.2.29 değişiklikleri

- Yerleşik web arama ve sayfa okuma araçları eklendi.
- Model bağlam kapasiteleri, bağlam sıkıştırma ve kurtarma davranışı iyileştirildi.
- Proje çalışma alanı taraması, yok sayılan dosyalar ve eksik tarama durumları daha doğru ele alınacak şekilde geliştirildi.
- Kodlama çalışma alanı ve işlem geçmişi güncellendi; ilgili testler genişletildi.

## v0.2.28 değişiklikleri

- Windows CI üzerinde uzun süren entegrasyon testleri ve Git çalışma ağacı fixture izinleri düzeltildi.

## v0.2.27 değişiklikleri

- Windows üzerinde güvenli entegrasyon sınırları ve platforma özgü benchmark doğrulamaları düzeltildi.

## v0.2.26 değişiklikleri

- Windows test ve geçici çalışma alanı temizleme uyumluluğu iyileştirildi.
- Python 3.10 test uyumluluğu ve macOS noter servisindeki geçici bağlantı hatalarına karşı tekrar denemesi eklendi.

## v0.2.24 değişiklikleri

- Windows üzerinde LSP dosya adresleri ve platformlar arası test uyumluluğu düzeltildi.

## v0.2.23 değişiklikleri

- Python 3.10 üzerinde araç zaman aşımı sonrası belirsiz işlemlerin güvenli şekilde durdurulması düzeltildi.

## v0.2.22 değişiklikleri

- Kodlama ajanına gelişmiş bağlam toplama, arama, araç seçimi ve LSP desteği eklendi.
- Bağlam sıkıştırma, uyarlanabilir çalışma profilleri ve kurtarma gözlemlenebilirliği geliştirildi.
- Yeni bağlam hattı için testler, benchmark araçları ve teknik dokümantasyon eklendi.

## v0.2.21 değişiklikleri

- Kodlama Ajanı için araştırma ve inceleme alt ajanları eklendi. Seçenek proje bazında açılır; ana ajan bağımsız görevleri dağıtır, sonuçları bekler ve birleştirir. Alt ajanlar yalnızca dosya okuma ve inceleme araçlarını kullanabilir.
- Alt ajanların görevleri, durumları, süreleri ve sonuçları İşlemler sekmesinde gösterilir. Ayrı mesaj geçmişleri ve sonuçları kalıcı oturum kaydında korunur; ana görev durdurulunca açık alt ajanlar da durur.
- Aynı anda iki, çalışma boyunca dört alt ajan sınırı uygulanır. Her alt ajan için süre, model turu ve çıktı sınırları vardır; ana ajanın izinleri aşılmaz.
- Kodlama çalışma alanına Türkçe modlar, dosya gezgini, bağlam seçimi, görev planı, işlem onayları, değişiklik farkları ve kalıcı çalışma geçmişi eklendi. Dosya işlemleri gerçek izin kapısından ve dosya sürümü kontrolünden geçer.
- Git çalışma alanı gerçek depo durumunu, farkları ve Git işlemlerinin önizlemelerini gösterir; değişiklik yapan işlemler onay akışına bağlanır.
- Windows checkout işlemini engelleyen, yanlışlıkla izlenen `nul` dosyası release kaynaklarından çıkarıldı.
- Dar pencerelerde sohbet araçlarının taşması ve kodlama fark görünümünün küçülmesi düzeltildi; düşünme ve alt ajan kontrollerinin yerleşimi düzenlendi.

- Windows dosya güncellemelerindeki izin ve geçici dosya kilidi hatası giderildi. Git satır sonları ve çalışma ağacı yolları platforma uygun gösterilir; testlerin UTF-8 ve komut kabuğu uyumluluğu düzeltildi.

- Python 3.10 için Git UTC tarih ayrıştırması düzeltildi. Yerel arayüz testleri sabit gecikme yerine gerçek pencere yerleşiminin tamamlanmasını bekler.

- Sohbet tamamlanırken kaydırma takibi son metin çizilmeden önce korunur; Windows Python 3.10 dâhil yerel yerleşim farkları takip durumunu kaybettirmez. Düşünme kontrolü arayüz testleri Windows’ta ayrı Tk süreçlerinde çalışır.

## v0.2.16 değişiklikleri

- Model, ilk yanıtından önce işletim sistemi/sürümü, mimari, komut kabuğu, çalışma dizini, Python çalışma zamanı, yerel saat ve bulunan temel araçları öğrenir. Ortam bilgileri her model isteğinde yenilenir ve `get_environment_info` aracıyla sorgulanabilir.
- Yerel komutlar için gerçek kabuğun sözdizimi esas alınır; macOS, Linux, CMD ve PowerShell arasındaki farklar sistem bağlamında belirtilir.
- Sohbet sırasında modelin yanıt bekleme, düşünme, MCP bağlantısı ve komut çalıştırma aşamaları canlı olarak gösterilir. Komut önizlemesi ve geçen süre ayrıntılar kapalıyken de görünür.
- Açıkça seçilen model yeni sohbetler ve uygulama yeniden başlatıldığında korunur.
- İç içe token kullanım verileri içeren akış yanıtlarının araçları yeniden çalıştırmasına yol açan doğrulama hatası düzeltildi.

## v0.2.15 değişiklikleri

- Apple Silicon ve Intel macOS kurulumları Developer ID sertifikasıyla imzalanır ve Apple noter onayından geçer.
- Apple onay bileti uygulamaya ve DMG paketine eklenir; paketler yayınlanmadan önce Gatekeeper ve imza kontrolleri yapılır.
- Release akışı geçici imzalama anahtarlığı kullanır ve işlem sonunda kimlik bilgilerini temizler.

## v0.2.14 değişiklikleri

- Model yanıtları Markdown olarak gösterilir: başlıklar, kalın/italik metin, listeler, alıntılar ve bağlantılar biçimlendirilir.
- Kod bloklarında dil etiketi ve ayrı kopyalama düğmesi bulunur; geniş kod ve tablolar yatay kaydırılabilir.
- Akış sırasında görünüm güncellemeleri birleştirilir; yanıt bittiğinde son biçimlendirme hemen uygulanır.
- Sohbet geçmişi ve mesaj kopyalama orijinal Markdown metnini korur; yeniden açılan sohbetler aynı biçimde gösterilir.
- Arka plan döngüsü tamamen başlamadan ilk MCP görevinin gönderilmesine yol açabilen açılış yarışı düzeltildi.

## v0.2.10 değişiklikleri

- Sohbet geçmişi aranabilir bir yan listeye taşındı; taslaklar ve kaydırma konumları korunur.
- Bir sohbet yanıt yazarken başka sohbetlerde soru sorulabilir; yanıtlar arka planda bağımsız devam eder.
- Yanıt yazılan ve yeni yanıt gelen sohbetler listede işaretlenir. Durdur düğmesi yalnızca seçili sohbeti durdurur.
- Akış sırasında ara kayıtlar ve sürekli “Kaydediliyor” göstergesi kaldırıldı. Yanıt tamamlandığında veya durdurulduğunda kayıt yapılır.
- Her sohbetin model, araç işleyicileri ve çalışma dizini ayrı tutulur; diğer sohbetlerin ayarlarıyla karışmaz.
- Computer-use MCP varsayılan olarak eklenir ve yeni sohbetlerde seçilir. Yerleşik sunucu harici Python gerektirmez; kapatma ve kaldırma tercihleri korunur.

## Masaüstü kurulumu

- **Windows x64:** `windows-x64-setup.exe` dosyasını açın. Kullanıcı hesabınıza kurulur; Python gerekmez. ZIP taşınabilir sürümdür.
- **macOS Apple Silicon:** `macos-arm64.dmg` dosyasını açıp firik'i Applications'a sürükleyin.
- **macOS Intel:** `macos-x64.dmg` dosyasını kullanın. macOS paketleri macOS 15 ve üzeri için derlenir.
- **Ubuntu 22.04+ / Debian 12+:** `sudo apt install ./firik_*_amd64.deb`
- **Fedora / openSUSE:** `.rpm` paketini dağıtımınızın paket yöneticisiyle kurun. glibc 2.35+ ve x86_64 gerekir.
- **Diğer glibc tabanlı Linux dağıtımları:** `linux-x86_64.tar.gz` arşivini açıp `usr/bin/firik` dosyasını çalıştırın. X11/XWayland ve glibc 2.35+ gerekir. Alpine/musl desteklenmez.

Linux'ta API anahtarını kaydetmek için çalışan bir Secret Service anahtarlığı (ör. GNOME Keyring veya uyumlu KWallet) gerekir.

Windows paketleri geliştirici sertifikasıyla imzalanmadığından SmartScreen ek onay isteyebilir. macOS paketleri Developer ID Application sertifikasıyla imzalanır, Apple noter onayından geçer ve onay bileti paketlere eklenir. Kurulum dosyaları için SHA256 özetleri `SHA256SUMS.txt` içindedir.
## v0.2.25 değişiklikleri

- Kodlama ajanına yapılandırılabilir harness altyapısı, yalıtılmış çalışma alanları ve Docker tabanlı isteğe bağlı yürütme eklendi.
- Görev yürütme günlüğü, güvenli devam ettirme ve kurtarma araçları eklendi.
- Uzun istem kapasitesi model kataloğundan doğru okunur; UI ve CLI bağlam sınırlarını buna göre uygular.
- Masaüstü kurulum betikleri uygulamayı derleyip kullanıcı hesabına başlatıcı ekler.
