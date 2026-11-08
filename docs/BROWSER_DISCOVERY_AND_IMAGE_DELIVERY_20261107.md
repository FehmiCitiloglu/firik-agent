# Yandaki tarayıcıyı bulma ve görüntü aktarımı — 7 Kasım 2026

Kullanıcının ekran görüntüsü ve ilgili kaydedilmiş sohbet, modelin yandaki
Firik tarayıcısı yerine computer-use MCP ile ekran görüntüsü ve masaüstü
pencereleri istediğini gösterdi. Seçili model `deepseek-v4.1-flash` idi.
Kurulu uygulama 0.2.50 olarak doğrulandı; sorun yalnız eski sürüm varsayımıyla
açıklanmadı.

## Bulgular ve düzeltmeler

1. **Görüntü yer tutucusu, görüntü teslimi sanılıyordu.** MCP görüntü baytları
   geçmişte korunuyordu ancak bu modelin sağlayıcı isteğine görüntü eklenmiyordu.
   Yalnız `[MCP Image: image/jpeg]` gibi bir etiket görünüyordu. Sağlayıcı artık
   gönderilmeyen görüntüyü açıkça bildiriyor; modelin görüntüyü gördüğünü iddia
   etmemesini ve aynı görüntüyü tekrar istememesini söylüyor. Açık, güvenilir
   vision metadata'sı eski model adı tahminlerinden önce uygulanıyor. Geçmişteki
   görüntü korunuyor; başka bir modele geçiş için silinmiyor. Yerel Browser'ın
   ayrı görüntü izni ve doğrulanmış taşıma koşulları korunuyor.
2. **Panel keşfi ile OS penceresi keşfi karıştırılıyordu.** Talimat artık
   “yandaki tarayıcı”nın aynı sohbetin gömülü paneli olduğunu, headless Chromium'un
   ayrı bir OS penceresi olarak görünmeyebileceğini açıklıyor. Model yalnız son
   izinli envanterdeki durum, sekme ve DOM gözlem araçlarına yönlendiriliyor.
   Açık sistem tarayıcısı/profil tercihi korunuyor. Ek olarak sohbet panelini
   açma hareketi kapalı sohbetin tarayıcı/önizleme iznini uyguluyor; etkin sohbetin
   panelini yeniden odaklamak veya açmak kapatılmış önizleme tercihini koruyor.
   Panel açmak tek
   başına Chromium başlatmıyor veya gezinmiyor. Kodlama sekmesinin izin davranışı
   değişmiyor. Bu panel açılış açığı, kaydedilmiş çalışmadaki bütün davranışın
   tek nedeni olarak yorumlanmadı: o çalışma daha sonra `browser_status` çağırmıştı.
3. **Gerçek başlatma hatası vardı.** Sohbet daha sonra `browser_status` ile
   `uninitialized`, `browser_start` ile `browser_launch_failed` almıştı. Chromium
   ve eşleşen Playwright 1.64.1 bileşenleri diskteydi. Kurulu paketin Node
   sürüm komutu çalıştı, JavaScript çalıştırması ise `Failed to reserve virtual
   memory for CodeRange` ile 133 döndürdü. Aynı ikilinin geçici kopyasını yalnız
   `com.apple.security.cs.allow-jit` ile imzalamak sorunu giderdi; kurulu uygulama
   bu geçici Node üzerinden gerçek Chromium'u başlattı. PyInstaller paketi artık
   BUNDLE sonrasında yalnız bilinen Playwright Node ikilisini bu izinle imzalıyor,
   ardından ana paketi çocuk imzalarını değiştirmeden mühürlüyor. Ana uygulamanın
   mikrofon izin dosyası aynı kalıyor. Hazır paket kullanımı da Node iznini
   notarizasyon öncesi doğruluyor. Apple'ın [JIT açıklaması](https://developer.apple.com/documentation/apple-silicon/porting-just-in-time-compilers-to-apple-silicon)
   bu runtime koşulunu belgeliyor.

## Doğrulama

- Geniş, GUI/live/Docker dışı regresyon: **2.544 geçti, 28 atlandı, 65 seçilmedi**.
- Görüntü/MCP/computer-use/sağlayıcı odaklı kontrol: **86 geçti**.
- Tarayıcı yönlendirmesi: **27 geçti**; native panel: **12 geçti**. Son native
  kontrol önizlemeyi kapatma tercihinin odaklama ve yeniden açma sonrasında
  korunmasını da kapsar.
- macOS imzalama/paketleme odaklı kontrol: **80 geçti**.
- Bu gruplar kısmen örtüşür; toplam benzersiz test sayısı olarak toplanmamalıdır.
- `git diff --check` başarılı.
- [Yerel paket derlemesi](Evidence/Browser/20261107/discovery-image-delivery/build.log)
  ve [frozen smoke](Evidence/Browser/20261107/discovery-image-delivery/frozen-smoke.log)
  başarılı. Yeni paketin UI/PortAudio keşfi ve gerçek headless Chromium başlangıcı
  doğrulandı; Chromium 156.0.8078.4, `state=ready`. İç içe strict imza kontrolü geçti.
- Komutlar, sayımlar ve paket hash'leri:
  [verification.json](Evidence/Browser/20261107/discovery-image-delivery/verification.json).

Doğrulanan yerel test paketi:
`build/browser-diagnostics/dist/firik-agent.app`. Bu paket yerel ad hoc imzalıdır;
bu çalışma notarizasyon veya yayın yapmadı, `/Applications/firik-agent.app`
kurulumunu değiştirmedi. Canlı model çağrısı, gerçek modelin artık hangi aracı
seçeceği, Intel macOS, Windows ve Linux bu doğrulamada denenmedi.
