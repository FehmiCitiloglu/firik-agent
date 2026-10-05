# Modele göre düşünme düzeyi

Sohbet ve Kodlama Ajanı ekranlarında modelin yanında düşünme düzeyi seçilebilir.
Destek ve seviyeler aktif EVREN sunucusunun `/v1/models` yanıtındaki
`capabilities.reasoning_effort`, `reasoning_effort_values` ve
`reasoning_effort_aliases` alanlarından alınır. Model adından destek tahmin edilmez.

- `Varsayılan`: istekten `reasoning_effort` çıkarılır; sunucu kendi varsayılanını kullanır.
- `Kapalı (none)`: yalnızca model açıkça `none` seviyesini bildiriyorsa seçilebilir.
- Destek yoksa veya bilinmiyorsa seçici kapalıdır ve effort gönderilmez.
- Destek `true`, seviye listesi `null` ise standart `low`, `medium`, `high`
  seçenekleri kullanılır; sunucunun bildirdiği alias varsa gönderilmeden uygulanır.
- Açık bir seviye listesi varsa yalnızca o liste gösterilir. Geçersiz eski seçim
  varsayılana döner. Yanıt hazırlanırken seçim kilitlenir.

Sohbet seçimi sohbet geçmişinde, kodlama seçimi proje tercihlerinde saklanır.
Sunucu adresi değişirse eski sunucunun yetenekleri kullanılmaz. Çevrimdışı ilk
başlatmada destek bilinmez; model listesi yüklendiğinde seçici güncellenir.
