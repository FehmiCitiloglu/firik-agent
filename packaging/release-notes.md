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
