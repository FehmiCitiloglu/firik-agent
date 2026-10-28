# Phase 3: bağlam motoru ve çalışma profilleri

Bu faz mevcut `ProjectCodingService`, `ProjectContextBuilder`, `CodingToolFilter`,
`Agent`, SQLite oturum kaydı ve masaüstü Context Inspector akışını genişletir.
Kaynakları seçmek veya araç şemasını göstermek çalıştırma izni vermez.

## Mimari denetim ve entegrasyon

| Mevcut bileşen | Son davranış |
| --- | --- |
| `projects/indexer.py` | Güvenli dosya taraması, içerik hashleri, değişen dosyaların yeniden ayrıştırılması, Python AST ve açıkça etiketlenmiş TS/JS lexical fallback |
| `projects/retrieval.py` | Amaç sınıflandırma, aday bulma, bağımlılık ve test genişletme, nedenleri görülebilen deterministik sıralama |
| `projects/coding_service.py` | Her istekte güncel dosyalar; kalıcı dışlamalar; sabitlenen/her zaman eklenen/seçilen/özetlenen kaynaklar; ilgili hafıza ve ADR sıralaması |
| `projects/context_builder.py` | Ağırlıklı bütçe dağıtımı, satır numaralı sınırlı alıntılar, dosya düzeyinde dahil edilme kanıtı |
| `projects/context_inspector.py` | Gerçek istek mesajları ve filtrelenmiş şemalar, kaynak nedenleri, profil, yetenek güveni, eksik bağlam uyarıları |
| `projects/skill_selection.py` | Mevcut açık beceri seçimi korunur; kaynak verisinden yeni beceri/izin etkinleştirilmez |
| `projects/coding_runtime.py` | Her araç çağrısında bağımsız proje, yol, hash, mod, onay ve izin denetimi |
| `core/compaction.py` | Tam arşivden ayrı özet görünümü, deterministik koruma kaydı ve özet kökeni |
| `providers/registry.py` | Tam sağlayıcı/model kimliğine bağlı kanıtlı veya bilinmeyen yetenek kaydı |
| `projects/lsp.py` | Açıkça onaylanan kurulu sunucu için isteğe bağlı sınırlı stdio adapter |

Pipeline sırası `intent_classification → candidate_retrieval → dependency_expansion
→ relevance_scoring → budget_allocation → context_assembly → inspection_metadata`.
Skor faktörleri kullanıcı sabitlemesi/seçimi, dosya yolu, semboller, lexical içerik,
sembol referansları, bağımlılık uzaklığı, ilişkili test, güncel hash ile doğrulanan
değişiklik ve güncel derleyici tanısıdır. Hash tek başına ilgisiz dosyayı bağlama eklemez.
Embedding yapılandırması bulunmadığından semantic index yoktur ve varmış gibi raporlanmaz.

Tarama varsayılan olarak en fazla 1500 güvenli dosya/16 MiB kaynakla sınırlıdır.
Eksik tarama Inspector'a uyarı olarak taşınır. Değişmemiş dosyaların ayrıştırma sonucu
yeniden kullanılır; aynı boyut/mtime altında değişikliği kaçırmamak için byte hashleri
yeniden hesaplanır. Model bağlamı her zaman güncel dosyadan okunur. Silinen güvenli
dosyaların eski kalıcı indeks kayıtları temizlenir. Git farkı yalnız seçilmiş güvenli
kaynakların literal yollarıyla istenir; ilgisiz dosyaların farkı eklenmez.

## Güven sınırları ve geçmiş

Model isteğinde host talimatları `system`, proje içeriği ve üretilmiş görev/alt ajan
verisi ayrı `user` mesajlarıdır. Kaynak içindeki komutlar, dosya adları, kabul edilmiş
kararlar ve alt ajan sonuçları sistem yetkisi kazanmaz. `system_prompt` alanı eski
çağıranlar için birleşik önizleme olarak korunur; gerçek yürütücü
`trusted_system_prompt` ve `project_context` parçalarını kullanır.

Kalıcı dışlamalar oturum için verilen dışlamalarla birleşir. Sabitleme, dosya/klasör
referansı, bağımlılık, tanı veya araç keşfi dışlamayı kaldıramaz. Hassas yollar,
sembolik bağlantılar, proje dışına kaçışlar ve credential içerikleri mevcut ortak
güvenlik filtresinden geçer. Çıktı içindeki secret örüntüleri maskelenir.

Tam konuşma ve araç yürütme günlüğü SQLite'ta korunur. Sıkıştırma arşivi değiştirmez.
Tekrarlanan araç çıktıları özet isteğinde hash ve mesaj konumuyla tekilleştirilir;
büyük çıktının baş/son alıntısı ve çıkarılan karakter sayısı açıkça kaydedilir.
Araç çağrısı ve sonuçları işlem bütünlüğü içinde kalır. Gereksinimler, çözülmemiş
hatalar, kararlar ve dosya referansları model özetinden bağımsız koruma kaydındadır.
Özetin kaynak hashleri, cursor ve provenance kaydı yeniden yüklenir. Bölünmez çağrı
argümanları veya zorunlu veri bütçeye sığmazsa açık hata üretilir.

## Profil ve yetenek yapılandırması

Profiller: `minimal`, `coding`, `research`, `deep-debug`, `multi-agent`, `local-efficient`.
Bağlam, çıktı, iterasyon, süre, paralellik ve alt ajan başına toplam bütçeyi belirlerler.
Öncelik açık çalışma/oturum tercihi, proje tercihi, kullanıcı yapılandırmasıdır.
Sayısal kullanıcı override'ları korunur. Yetkiler ayrı mevcut izin ayarlarındadır.

```yaml
agent:
  harness_profile: local-efficient
  harness_overrides:
    context_tokens: 4000
    max_output_tokens: 1536
providers:
  local:
    type: openai_compatible
    base_url: http://127.0.0.1:1234/v1
    default_model: my-explicit-model-id
    model_capabilities:
      my-explicit-model-id:
        source: verified_config
        context_window: 8192
        supports_tools: true
        # Yalnız gerçekten doğrulanan alanları belirtin.
```

Proje tercihleri `coding_preferences.harness_profile` ve `harness_overrides`
alanlarını kullanır. `prepare_coding_prompt(..., harness_profile=...)` ile oturum
override'ı verilebilir. Masaüstünde Görev panelindeki profil seçicisi proje tercihini
kaydeder. CLI sohbetinde `--harness-profile local-efficient` oturum seçimini uygular.

Yetenek kaydı kanıt kaynağı, varsa doğrulama zamanı/URL'si, doğrulama durumu,
context window, tools/streaming/structured output/vision/reasoning, usage ve native
cache desteği ile tokenizer kullanılabilirliğini ayırır. Model adından özellik
türetilmez. Belirtilmeyen veya iptal edilmiş bilgiler `null` kalır. Native cache
metadata'sı kaydetmek sağlayıcı isteğine desteklenmeyen alan eklemez.

Bu kurulumda doğrulanmış tokenizer kullanılmadığında kaynak ve Inspector sayıları
karakter/4 tahminidir; runtime overflow kontrolü daha muhafazakâr UTF-8/3 hesabı
kullanır. Bunlar sağlayıcının bildirdiği usage değildir. Tokenizer kütüphanesinin
bulunması encoding'in kullanılabildiğini veya request framing sayımının kesinliğini
kanıtlamaz. Kullanım/fiyat eksikse gerçek token ve maliyet sıfır gösterilmez.

## Araç seçimi ve yedek davranış

Şema seçimi mevcut izin filtresinden **sonra** çalışır. Review/araştırma/planlama,
düzeltme ve derleme amaçları farklı küçük envanterler kullanır. `discover_tools`
yalnız o anda izinli araçların ad/açıklamasını bulur; sonraki isteğe şemalarını ekler.
MCP için açık araç ve sunucu seçimi, çalışma modu ve yürütme izni birlikte gereklidir.
İzin sonradan daraltılırsa keşfedilmiş araç da çalıştırılamaz.

Modelin araç seçimi yetersizse host `harness_overrides.tool_selection: all_permitted`
seçebilir veya model `discover_tools(query="*")` kullanabilir. Genel bir oturumda
keşif aracı bulunmuyorsa zaten izinli olan envanter korunur ve fallback nedeni
raporlanır. Bu yedek davranış envantere yeni yetenek veya izin eklemez.

## İsteğe bağlı LSP

```python
adapter = LSPAdapter(root, ["pyright-langserver", "--stdio"],
                     approved=True, excluded=is_excluded)
await adapter.start()
service.register_lsp_adapter(project_id, adapter)
await adapter.open_document("src/main.py")
definitions = await adapter.definitions("src/main.py", line=0, character=4)
references = await adapter.references("src/main.py", line=0, character=4)
await adapter.close()
```

API konumları LSP standardındaki gibi sıfır tabanlıdır. Servis yalnız güvenli,
güncel dosya hashine bağlı tanı snapshot'larını retrieval sinyali olarak kullanır.
Adapter subprocess, initialize/initialized, JSON-RPC framing, diagnostics,
definitions, references, timeout/cancel ve shutdown/exit akışını yönetir. Mesaj,
belge, tanı ve process kaynak sınırları uygulanır. Sunucu yoksa AST/arama sürer.
Kurucu komutları ve shell launcher reddedilir; otomatik sunucu kurulmaz. Yerel LSP
bir OS sandboxı değildir; yalnız host tarafından onaylanmış kurulu executable kullanılır.

Python için mevcut AST yeterli olduğundan tree-sitter zorunlu bağımlılık yapılmadı.
TS/JS lexical desteği tam dil semantiği iddiası taşımaz; ihtiyaç halinde isteğe bağlı
LSP adapter definitions/references sağlar.

## İnceleme ve doğrulama

```sh
firik harness profiles
firik harness context --root . --request 'invoice rounding fix' --profile local-efficient
firik harness context --project-id PROJECT --database /path/projects.db --request 'compiler error'
firik-agent --harness-profile local-efficient --prompt 'Kaynakları incele'
```

`harness context` mevcut masaüstü hazırlık yolunu kullanır, kaynak ve araç metadata'sını
JSON verir; API çağrısı ve araç yürütmesi yapmaz. Araç önizlemesi `ask` modundadır.
Mevcut veritabanı belirtilirse hafıza ve tercihler de hesaba katılır. Yeni root
önizlemesi geçici bir metadata veritabanı kullanır.

Regresyonlar kaynak uygunluğu, sabitleme, dışlamalar/secret filtreleri, aynı mtime
altında değişiklik, yeni/silinen dosya, import döngüleri, büyük repository sınırı,
overflow, prompt injection, şema keşfi, izinlerin daraltılması, local capability
belirsizliği, tam geçmiş ve gerçek stdio LSP süreçlerini kapsar. Native Tk testi
profil seçimini, veri mesajı sınırını ve Inspector'u gerçek widget'larla doğrular.

Tekrarlanabilir karşılaştırma, kapsam ve sayısal sonuçlar
[Phase 3 kanıt raporundadır](Evidence/Phase3/README.md).
