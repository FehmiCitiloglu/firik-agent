# Phase 3 doğrulama kanıtı

Tarih: 27 Ekim 2026. Başlangıç commit'i:
`d40230ea0b69cb1442aa4011aab7274b2d1eefc7`. Aday çalışma ağacıdır;
iki uygulamanın Python kaynak fingerprint'leri JSON raporlarındadır.

## Kontrollü retrieval karşılaştırması

`scripts/benchmark_context.py`, aynı altı görevi üç kez gerçek
`ProjectCodingService.prepare_coding_prompt` yolundan çalıştırır. Her deneme ayrı
geçici workspace ve SQLite metadata veritabanı kullanır. 350 ilgisiz gürültü dosyası,
Python/TS kaynakları, ilişkili test, pin, excluded/secret dosya ve indeksleme sonrası
oluşturulan dosya vardır. Her soğuk hazırlığın ardından aynı servisle sıcak hazırlık
da ölçülür. Eski kod `git archive` ile ayrı bir klasöre çıkarılmıştır. Son eşleştirilmiş
ölçüm sırasında başka regresyon veya benchmark çalıştırılmamıştır.

| Ölçüm, 18 denemenin toplamı/medyanı | Başlangıç | Phase 3 |
| --- | ---: | ---: |
| Beklenen kaynakları bulma ve koruma oracle'ı | 15/18 | 18/18 |
| Seçilen ilgisiz gürültü dosyaları | 69 | 0 |
| Getirilen kod bağlamının UTF-8 byte hacmi | 12.474 | 7.494 |
| Getirilen kod token **tahmini** | 3.144 | 1.890 |
| Soğuk hazırlık medyanı | 156,70 ms | 249,63 ms |
| Sıcak hazırlık medyanı | 159,00 ms | 239,81 ms |
| Sağlayıcı çağrısı | 0 | 0 |
| Canlı model görev başarısı / gerçek maliyet | Bilinmiyor | Bilinmiyor |

Kaynak token tahmini %39,89 azaldı; beklenen kaynak kapsamı iyileşti. Hazırlık süresi
%59,31 arttı. Güncel dosya hashleri ve yeni/silinen kaynak doğrulaması ek işlem
gerektiriyor; sıcak hazırlık ayrıştırmayı tekrar kullanıyor fakat güvenli byte hashlerini
yeniden denetliyor. Redundant yol çözümlemeleri profiling ile azaltıldı. Bu ölçüm bir
hız kazanımı veya canlı model başarısı iddiası taşımaz. Pipeline Tk event loop'unda
çalıştırılmaz.

Görevler kontrollü fixture'lardır; `retrieval_success` gerçek modelin bir kodlama
görevini çözmesi değildir. Byte hacmi model bağlamına eklenen satır numaralı kaynak
bölümünün byte sayısıdır; token değerleri yerel karakter/4 tahminidir. Model kullanım
ve fiyat bilgisi bulunmadığından gerçek token ve maliyet alanları `null` bırakılmıştır.

- [Başlangıç ham verisi](baseline.json)
- [Aday ham verisi](candidate.json)
- [Eşleştirilmiş karşılaştırma](comparison.json)

Aynı `auto` izinleri ve "Fix invoice rounding bug" isteğinde gerçek
`CodingToolFilter` envanteri 18 şemadır. `coding` 16 şema/~1.308 token,
`local-efficient` 10 şema/~822 token seçti; tam envanter ~1.484 token'dır.
İzin snapshot'ları altı profil için değişmedi. Bu şema ölçümü MCP/alt ajan
envanteri eklenmeyen ayrı bir offline kontroldür.
[Profil başına ham şema ölçümü](tool-selection.json).

Yeniden üretim, her checkout için aynı benchmark script'iyle:

```sh
PYTHONPATH=/path/to/checkout /path/to/python scripts/benchmark_context.py \
  --output /path/to/new-report.json --label candidate --revision COMMIT_OR_WORKTREE
```

Dosya fixture ve görev fingerprint'leri eşleşmeden iki rapor karşılaştırılmamalıdır.
Küçük yerel süreler makine yüküne bağlıdır; uzun vadeli performans veya faturaya
genellenmez.

## Gerçek guarded runtime ile offline kontrol

```sh
firik harness run --suite offline-smoke --trials 3 --label phase3 \
  --output docs/Evidence/Phase3/harness
```

20 farklı senaryoda 60/60 objektif fixture kontrolü geçti; güvenlik ihlali 0.
30 görev doğrulanmış tamamlanma verdi. Diğer fixture'lar hata, iptal veya engelleme
davranışını sınar; 60/60 sayısı bütün kodlama görevlerinin tamamlandığı anlamına gelmez.
Sağlayıcı deterministik replay'dir. Bu ayrıca yeni retrieval sıralamasının canlı
model A/B testi değildir. [Ham rapor](harness/report.json),
[okunabilir rapor](harness/report.md).

## Regresyon ve masaüstü

Başlangıç çekirdek paketi: 846 başarılı, 1 atlanan test. İlk sandbox içindeki Tk
pencere oluşturma denemesi macOS abort üretti; grafik erişimi olan çalıştırmada
aynı yerel testler çalıştı. Son tam regresyonda **1021 test geçti, 1 test atlandı**
(136,98 saniye); başarısız test yoktur. Sonuç, kaynak fingerprint'i ve komutlar
[validation.json](validation.json) ve [pytest.log](pytest.log) dosyalarındadır.

Native Tk testleri yeni profil seçicisini, ayarın kaydedilmesini, gerçek model
isteğine taşınan veri rolünü ve Context Inspector kaynak kartlarını kapsar.
Python 3.10 sözdizimi ile 146 modül ayrıştırıldı; `git diff --check` temizdir.
Atlanan Windows cmd testinin bu macOS ortamında çalışması beklenmez.

LSP testleri gerçek yerel stdio subprocessleriyle initialize, diagnostics,
definitions/references, çökme/yeniden başlatma, timeout/cancellation ve kapanışı
doğrular. Test sunucuları kontrollü protokol fixture'larıdır; kurulu bir pyright veya
typescript-language-server ile proje çapında dil semantiği doğrulaması değildir.
TS/JS parser fallback'i lexical olarak raporlanır; semantic index ve doğrulanmış
sağlayıcı tokenizer ölçümü yapılandırılmamıştır.

Uygulama ve yapılandırma ayrıntıları: [Phase 3 mimarisi](../../PHASE3_CONTEXT_ENGINE.md).
