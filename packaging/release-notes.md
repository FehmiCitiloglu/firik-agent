## v0.2.18 değişiklikleri

- Kodlama Ajanı için araştırma ve inceleme alt ajanları eklendi. Seçenek proje bazında açılır; ana ajan bağımsız görevleri dağıtır, sonuçları bekler ve birleştirir. Alt ajanlar yalnızca dosya okuma ve inceleme araçlarını kullanabilir.
- Alt ajanların görevleri, durumları, süreleri ve sonuçları İşlemler sekmesinde gösterilir. Ayrı mesaj geçmişleri ve sonuçları kalıcı oturum kaydında korunur; ana görev durdurulunca açık alt ajanlar da durur.
- Aynı anda iki, çalışma boyunca dört alt ajan sınırı uygulanır. Her alt ajan için süre, model turu ve çıktı sınırları vardır; ana ajanın izinleri aşılmaz.
- Kodlama çalışma alanına Türkçe modlar, dosya gezgini, bağlam seçimi, görev planı, işlem onayları, değişiklik farkları ve kalıcı çalışma geçmişi eklendi. Dosya işlemleri gerçek izin kapısından ve dosya sürümü kontrolünden geçer.
- Git çalışma alanı gerçek depo durumunu, farkları ve Git işlemlerinin önizlemelerini gösterir; değişiklik yapan işlemler onay akışına bağlanır.
- Windows checkout işlemini engelleyen, yanlışlıkla izlenen `nul` dosyası release kaynaklarından çıkarıldı.
- Dar pencerelerde sohbet araçlarının taşması ve kodlama fark görünümünün küçülmesi düzeltildi; düşünme ve alt ajan kontrollerinin yerleşimi düzenlendi.

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
- **macOS Apple Silicon:** `macos-arm64.dmg` dosyasını açıp evren'i Applications'a sürükleyin.
- **macOS Intel:** `macos-x64.dmg` dosyasını kullanın. macOS paketleri macOS 15 ve üzeri için derlenir.
- **Ubuntu 22.04+ / Debian 12+:** `sudo apt install ./evren_*_amd64.deb`
- **Fedora / openSUSE:** `.rpm` paketini dağıtımınızın paket yöneticisiyle kurun. glibc 2.35+ ve x86_64 gerekir.
- **Diğer glibc tabanlı Linux dağıtımları:** `linux-x86_64.tar.gz` arşivini açıp `usr/bin/evren` dosyasını çalıştırın. X11/XWayland ve glibc 2.35+ gerekir. Alpine/musl desteklenmez.

Linux'ta API anahtarını kaydetmek için çalışan bir Secret Service anahtarlığı (ör. GNOME Keyring veya uyumlu KWallet) gerekir.

Windows paketleri geliştirici sertifikasıyla imzalanmadığından SmartScreen ek onay isteyebilir. macOS paketleri Developer ID Application sertifikasıyla imzalanır, Apple noter onayından geçer ve onay bileti paketlere eklenir. Kurulum dosyaları için SHA256 özetleri `SHA256SUMS.txt` içindedir.
