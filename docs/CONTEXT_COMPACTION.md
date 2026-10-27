# Otomatik bağlam sıkıştırma

FIRIK sohbet ve Kodlama Ajanı, her model isteğinden önce bağlam bütçesini kontrol eder. Varsayılan olarak kullanılabilir konuşma bütçesinin yaklaşık %75'ine ulaşıldığında eski mesajları seçili modelle özetler; yaklaşık %50'ye inmeyi hedefler. Komut girmeniz gerekmez. Arayüzde “Bağlam sıkıştırılıyor” durumu görünür, ardından aynı görev sürer.

Sistem talimatları ve araç şemaları bütçeden düşülür. Sağlayıcının doğrulanmış kapasitesi varsa yanıt ve güvenlik payı ayrılır. Kapasite bilinmiyorsa konuşma için 6.000 tahmini tokenlık yerel bütçe kullanılır; sistem ve araç şemaları ayrıca hesaba katılır. Bu değer modelin gerçek kapasitesi değildir. Tahmin UTF-8 boyutuna dayanır; sağlayıcının token sayacı değildir.

Tam mesaj geçmişi silinmez. Modele gönderilen görünüm, eski mesajların özeti ve yakın geçmişten oluşur. Son kullanıcı isteği aynen korunur. Araç çağrısı ve sonuçları bölünmez. Uzun bir görevde tamamlanmış eski araç işlemleri de özetlenebilir. Büyük eski geçmiş, bütçeye sığan parçalarla özetlenir; sonraki sıkıştırmada önceki özet güncellenir. Özet yeni bir sistem talimatı veya izin sayılmaz. Kodlama çalışmasının kayıtlı planı, kararları ve doğrulama kanıtları ayrıca gönderilir; yürütücü izinleri geçerliliğini korur.

Sohbet özeti ve konumu kalıcı sohbet kaydına yazılır. Kodlama geçmişi tam mesajları ve sıkıştırma olaylarını saklar; farklı bir oturumdan yeniden açıldığında özet gerektiğinde yeniden oluşturulur. Geçmiş değişirse eski özet önbelleği geçersiz olur. Sohbet temizlenirse özet de temizlenir.

Özetleme başarısız olursa veya iptal edilirse eski geçmiş korunur. Sığmayan tek bir istek/araç işlemi sessizce kesilmez; açıklayıcı hata verilir. Model özetleri ayrıntı kaybedebilir; dosya veya test sonucu gerektiğinde tekrar doğrulanmalıdır. Özetleme ek model istekleri ve süre gerektirir.

Streaming ajan akışında her özetleme çağrısı ayrı `request_id` ve `request_kind=compaction` ile ölçülür. Sağlayıcı kullanım bildirirse token ve doğrulanmış fiyatlarla tahmini maliyet kayda girer. Eksik kullanım sıfır sayılmaz. Başarısız veya iptal çağrılarının süresi de kaydedilir. Olaylar özetleme sınırından sonra aktarılır; süreç bu sırada çökerse eksiksiz ücret kaydı garanti edilmez. Ayrıntılar: [Harness Benchmark Lab](HARNESS_BENCHMARKS.md).

`config.yaml` içinde isteğe bağlı ayarlar:

```yaml
agent:
  context_compaction:
    enabled: true
    trigger_ratio: 0.75
    target_ratio: 0.50
    fallback_input_tokens: 6000
    summary_max_tokens: 1000
    timeout_seconds: 90
```

Ayarlar verilmediğinde otomatik sıkıştırma varsayılan olarak açıktır. `enabled: false`, modelle özetlemeyi kapatır; Kodlama Ajanı'nın mevcut bütçe koruması yine uygulanır. `summary_max_tokens` üst sınırdır; küçük bütçelerde özet için ayrılan çıktı azaltılır.
