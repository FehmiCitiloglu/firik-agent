# FIRIK API kapsamı

Kaynaklar (22 Eylül 2026):

- [Resmî OpenAPI](https://evren-llmapi.ssyz.org.tr/openapi.json)
- [Resmî model/API kataloğu ve istek örnekleri](https://evren.ssyz.org.tr/llm/models)
- [Resmî kullanım kılavuzu](https://evren.ssyz.org.tr/guide)
- [API anahtarları](https://evren.ssyz.org.tr/api-keys)

OpenAPI'nin genel uçları ve gerekli şemaları `firik_agent/api/openapi.json` içinde kayıtlıdır. Kaynak ve indirme tarihi dosyada bulunur. Katalogdaki OCR örneği `{"model":"dots-ocr","image":"data:image/png;base64,..."}` kullanır; OpenAPI, OCR istek gövdesini ayrıntılı tanımlamadığından bu alan resmî portal örneğinden alınmıştır.

## Kapsam tablosu

| Yöntem | Yol | CLI |
|---|---|---|
| GET | `/healthz` | `health` |
| GET | `/readyz` | `health --ready` |
| POST | `/v1/chat/completions` | `chat` |
| POST | `/v1/completions` | `completions` |
| POST | `/v1/responses` | `responses` |
| POST | `/v1/embeddings` | `embeddings` |
| POST | `/v1/rerank` | `rerank` |
| GET | `/v1/models` | `models` |
| POST | `/v1/ocr` | `ocr` |
| POST | `/v1/media` | `media initiate`, `media upload` |
| POST | `/v1/media/{media_id}/complete` | `media complete`, `media upload` |
| POST | `/v1/media/{media_id}/abort` | `media abort` |
| POST | `/v1/media/{media_id}/finalize` | `media finalize`, `media upload --finalize` |
| GET | `/v1/media/{media_id}` | `media status` |
| POST | `/v1/audio/transcriptions` | `transcribe` |
| GET | `/v1/quota` | `quota` |
| GET | `/v1/terms/status` | `terms status` |
| GET | `/v1/terms/text` | `terms text` |
| POST | `/v1/terms/accept` | `terms accept VERSION` |
| GET | `/v1/requests/{request_id}` | `request-status` |

20/20 genel işlem için doğrudan komut ve HTTP kontrat testi bulunur. JSON yanıtları alan kaybı olmadan verilir; böylece fiyat, kredi, kullanım, araç çağrısı ve sunucu uzantıları görünür kalır. Chat/Completions SSE ile Responses'ın türlenmiş SSE akışı ayrı olay bilgileri korunarak ayrıştırılır. Model seçenekleri, yapılandırılmış mesajlar ve araç şemaları `--body`/`--set` ile iletilir; hangi özelliği hangi modelin desteklediğine API karar verir.

## Bilinen sınırlar

- `internal` uçları API anahtarının genel yüzeyi değildir; servis kimliği/delegation token gerektirir ve istemciye eklenmemiştir.
- Platform ve Model Çıkarımı anahtarları LLM anahtarından ayrı sınıflardır. Kullanıcının ekran görüntüsündeki anahtar LLM Çıkarım olarak etiketlidir; sağdaki açılır menü liste filtresidir.
- `api.ssyz.org.tr/openapi.json` ve `api.ssyz.org.tr/api/v1/openapi.json` kontrolleri 404 döndürdü. Portalın veri seti, etiketleme, GPU/eğitim, model yayımlama, organizasyon, kredi yönetimi ve anahtar yönetimi işlemlerinin bu LLM anahtarıyla kullanılabildiği doğrulanmadı. Bu alanlar **uygulanmış değildir**. Onaylı Platform API dokümantasyonu/sözleşmesi olmadan tarayıcının oturumla çalışan uçları genel API olarak sunulmaz.
- OCR görseller içindir; PDF sayfa dönüştürme eklenmemiştir. Medya yüklemesi tek başına video anlama desteği anlamına gelmez; modelin kabul ettiği mesaj yapısı kullanılmalıdır.
- `request-status` yalnızca yürütme ve ücretlendirme metaverisi döndürür; eski yanıtları indirme/yeniden oynatma değildir.
- Bağımsız bir moderasyon, fine-tuning, batch, model indirme, veri seti veya GPU endpoint'i genel LLM şemasında yoktur. Model destekliyorsa ilgili içerik üretme/sınıflandırma görevi `chat`/`responses` üzerinden yapılabilir.
- Otomatik POST tekrarı yoktur. Ağ hatasında aynı işi tekrar göndermeden önce eldeki istek kimliğinin durumunu kontrol edin.

## Doğrulama

Testler tüm genel uçlar için yöntem/yol/gövde/header eşleşmesini; görsel kodlama, ses multipart ve beş çıktı biçimini; bölünmüş Unicode/SSE, Responses olaylarını, usage ve hata olaylarını; tek/çok parçalı yükleme, ETag, storage kimlik ayrımı ve iptal temizliğini; anahtar redaksiyonunu ve hata durumunda mevcut dosyanın korunmasını denetler.

22 Eylül 2026 canlı, salt okunur kontrolde `/healthz`, `/readyz`, `/v1/terms/status`, `/v1/models` ve `/v1/quota` başarılı yanıt verdi. Şartlar mevcut anahtar için kabul edilmişti. 11 model döndü; embedding/rerank/OCR/ses görevleri katalogda görüldü. Test sırasında şart kabulü, kredi tüketen üretim veya gerçek dosya yüklemesi yapılmadı. Üretim ve medya iş akışları HTTP kontrat testleriyle doğrulandı; canlı uçtan uca denenmiş sayılmaz.
