# Firik Agent

Masaüstü uygulaması: `firik-gui`; agent: `firik-agent`; doğrudan API istemcisi: `firik`. Python paketi: `firik_agent`.

FIRIK'in **LLM Çıkarım API'sini terminalden kullanmak** için CLI ve Python istemcisi. Sohbetin yanında görsel anlama, OCR, ses çözümleme, embeddings, rerank, medya yükleme, kota/kredi ve istek durumu sorgularını destekler. Önceki agent, MCP, skill ve plugin özellikleri `firik-agent` komutuyla kullanılmaya devam eder.

**Kapsam:** 22 Eylül 2026 tarihinde okunan [resmî OpenAPI şemasındaki](https://evren-llmapi.ssyz.org.tr/openapi.json) **20 genel işlemin tamamı**. `internal` servis uçları kapsam dışıdır. [Uç nokta ve kapsam tablosu](docs/API.md).

FIRIK portalında **Platform**, **LLM Çıkarım** ve **Model Çıkarımı** farklı anahtar türleridir. Anahtar listesinde “Tümü” bir filtre, “Modeller: Tümü” ise LLM model yetkisidir. Bu istemci `firik_llm_...` biçimindeki **LLM Çıkarım** anahtarını kullanır. Veri seti/etiketleme, GPU rezervasyonu, eğitim işleri, organizasyon veya platform anahtarı yönetimi için doğrulanmış genel API sözleşmesi bulunamadığından bu işlemler eklenmemiştir. Portalın bütün MLOps özelliklerinin bu anahtarla kullanılabildiği varsayılmaz.

## Kurulum

Çevrimdışı **Harness Benchmark Lab**, gerçek Kodlama Ajanı döngüsünü geçici Git/SQLite projelerinde deterministik sağlayıcı yanıtlarıyla sınar: `firik harness doctor` ve `firik harness bench --suite offline-smoke --repeat 3 --output /tmp/firik-benchmark`. API anahtarı gerekmez. 20 hata/izin senaryosu, açık izin isteyen 40 görevlik kodlama kataloğu, eşleştirilmiş A/B karşılaştırması ve masaüstünde rapor özeti içerir. JSON/Markdown raporu, istek/araç/hata ölçümleri ve bilinmeyen token/maliyet kapsamını içerir; canlı model performansı iddia etmez. [Yöntem ve sınırlar](docs/HARNESS_BENCHMARKS.md), [başlangıç denetimi](docs/HARNESS_AUDIT.md).

**Adaptive Recovery (Phase 2)**, akışlı ve normal model isteklerinde güvenli olduğu kanıtlanan hatalara sınırlı kurtarma uygular; kısmi yanıtları ve sonucu belirsiz yan etkileri otomatik tekrarlamaz. Kodlama görevinin tamamlanma sınıfı güncel doğrulama kanıtı ve mevcut kalite kapılarıyla belirlenir. [Yapılandırma, olaylar ve sınırlar](docs/ADAPTIVE_RECOVERY.md).

**Context Engine (Phase 3)**, güncel hash tabanlı retrieval, açıklanabilir kaynak sıralaması, isteğe bağlı LSP ve altı çalışma profili ekler. `firik harness profiles` profilleri, `firik harness context --root . --request 'hata izini incele' --profile local-efficient` kaynak/şema önizlemesini gösterir. Masaüstünde profil seçimi ve Context Inspector aynı akışı kullanır; proje verileri ayrı güven rolündedir ve çalışma izinleri bağımsız denetlenir. [Mimari, yapılandırma ve sınırlar](docs/PHASE3_CONTEXT_ENGINE.md), [benchmark kanıtı](docs/Evidence/Phase3/README.md).

Masaüstü uygulamasındaki **Kodlama Ajanı**, dosya gezgini, plan, etkinlik, terminal, test, Git ve ajan inceleyicisini tek çalışma alanında toplar. Gerçek araç yürütümü proje izinlerinden geçer; kontrol noktaları, değişiklik incelemesi, doğrulama kanıtı ve oturum geçmişi kalıcı kaydedilir. Çoklu ajan sınırları, bağlam/model bilgisinin kaynakları ve 52 gereksinimin uygulama durumu [Kodlama cockpit dokümanında](docs/CODING_COCKPIT.md) açıklanır.

Python 3.10+:

### ⚡ Tek Komutla Hızlı Kurulum (Otomatik .venv ve paketler)

İşletim sisteminize göre tek bir komut çalıştırmanız yeterlidir:

```bash
# macOS / Linux:
./install.sh
# veya
make install

# Windows (PowerShell):
.\install.ps1

# Tüm Sistemler (Firiksel Python):
python install.py
```

Kurulum betiği `uv` (varsa) veya Python'un yerleşik `venv` + `pip` modülünü kullanarak sanal ortamı kurar ve bağımlılıkları yükler. `.env` oluşturmaz; mevcut dosyanıza dokunmaz.

**Venv'i aktive etmeniz gerekmez.** Kurulum, `firik` ve `firik-agent` komutlarını macOS/Linux'ta `~/.local/bin`, Windows'ta `%LOCALAPPDATA%\FIRIK\bin` üzerinden erişilebilir yapar ve kullanıcı PATH ayarını kaydeder. macOS/Linux'ta kullanılan kabuğun zsh, bash veya fish başlangıç dosyası güncellenir. Diğer kabuklarda `~/.profile` güncellenir; kabuğunuz bu dosyayı okumuyorsa PATH'i kendi başlangıç dosyanıza ekleyin.

İlk kurulumdan sonra **yeni bir terminal açın** veya kurulumun sonunda gösterilen PATH komutunu mevcut terminalde bir kez çalıştırın. Kurulum betiği, kendisini başlatan terminalin ortamını doğrudan değiştiremez. Sonrasında `firik --help` ve `firik-agent --commands` herhangi bir klasörden çalışır; bağımlılıklar projenin `.venv` ortamında kalır. Proje klasörünü veya `.venv` dizinini silmeyin; projeyi taşırsanız `.venv` ortamını yeniden oluşturup kurulumu tekrar çalıştırın. `config.yaml` gibi göreli dosya yolları komutu çalıştırdığınız klasöre göre değerlendirilir.

`install.sh` ve `install.ps1` aynı `install.py` kurulumunu çalıştırır; üç giriş noktası da aktivasyonsuz kullanımı ayarlar. Windows'ta `install.ps1` mevcut PowerShell oturumunun PATH'ini de yeniler; bu oturumda yeni terminal açmadan komutları kullanabilirsiniz.

<details>
<summary><b>Manuel Kurulum Adımları (İsteğe Bağlı)</b></summary>

```bash
# macOS / Linux (bash/zsh):
uv venv .venv
source .venv/bin/activate
uv pip install -e '.[dev]'

# Windows (PowerShell):
uv venv .venv
.\.venv\Scripts\Activate.ps1
uv pip install -e '.[dev]'

# Windows (Command Prompt - CMD):
uv venv .venv
.venv\Scripts\activate.bat
uv pip install -e '.[dev]'
```
</details>

Kurulumdan sonra `firik`, `firik-agent` veya anahtar gerektiren ilk komutu çalıştırın. Anahtar bulunamazsa terminalde **LLM Çıkarım** API anahtarınız gizli girişle sorulur. Anahtar proje dosyalarına yazılmaz; [keyring](https://keyring.readthedocs.io/en/latest/) aracılığıyla macOS Keychain, Windows Credential Locker veya Linux Secret Service/KWallet kasasında saklanır. Sonraki açılışlarda, farklı bir klasörden çalıştırsanız da otomatik okunur.

```bash
firik                              # İlk kurulum, ardından komut yardımı
firik login                        # Kaydedilmiş FIRIK anahtarını değiştir
firik login --provider llmtr        # İsteğe bağlı diğer sağlayıcı
firik login --provider openai
```

Anahtarlar sağlayıcı, API adresi ve `api_key_env` adına göre ayrı saklanır. Özel bir adres için `firik login --base-url https://evren.ssyz.org.tr/v1` veya `--config DOSYA` kullanın. `login` anahtarı kaydeder; geçerliliğini API'de sorgulamaz. `firik models` ile bağlantıyı kontrol edebilirsiniz.

Mevcut ortam değişkenleri ve `.env` dosyaları geriye dönük uyumluluk için desteklenir ve kasadaki anahtardan önce gelir. Yeni kaydı kullanmak için eski ortam/`.env` değerini kaldırın. Şablon anahtarlar gerçek anahtar sayılmaz. `--help`, `--commands`, `api-docs`, `health` ve MCP yönetimi anahtar istemez. Terminal dışı çalıştırmalarda (pipe/CI) giriş beklenmez; kayıtlı anahtar veya ortam değişkeni yoksa açıklayıcı hata döner.

İşletim sistemi kasası kullanılamıyorsa otomatik kurulumda girilen anahtar yalnızca o çalıştırma boyunca bellekte tutulur ve bu durum bildirilir. `firik login` kalıcı kaydı tamamlayamazsa hata verir. Linux'ta etkin Secret Service/KWallet gerekir; dosyaya düz metin kaydetme alternatifi kullanılmaz.

Kimlik doğrulama ve anahtar oluşturma [Sağlayıcı portalında](https://evren.ssyz.org.tr/api-keys) yapılır. CLI e-Devlet şifresi istemez. Anahtar komut satırı argümanı olarak alınmaz.

## İlk kullanım

```bash
firik --help
firik health
firik health --ready
firik terms status
firik terms text --text
# Yalnızca okuduğunuz ve kabul etmek istediğiniz sürümü belirtin:
firik terms accept 1
firik models
firik quota
firik chat -p 'Merhaba!' --text
```

Şartlar otomatik kabul edilmez. `accept 1` yalnızca örnektir; güncel sürümü `terms text` ve `terms status` ile okuyun. Anahtarınız şartları zaten kabul etmişse tekrar kabul etmeniz gerekmez.

Yeni komutlar şu biçimlerde de çalışır:

```bash
firik-agent api models
python -m firik_agent.api.cli models
# Agent REPL içinde:
# /api models
# /api quota
# /api --help
```

## Komutlar

| Komut | İşlev |
|---|---|
| `models` | Erişilebilir modeller, görev, modalite ve güncel fiyat bilgisi |
| `chat` | Sohbet, görsel girdiler, araç çağrıları, yapılandırılmış çıktı, SSE |
| `completions` | Klasik metin tamamlama; tek/çoklu prompt ve SSE |
| `responses` | Responses API; yapılandırılmış girdi, araçlar ve türlenmiş SSE olayları |
| `embeddings` | Tek veya çoklu metnin vektör temsili |
| `rerank` | Sorguya göre belge sıralama |
| `ocr` | Görselden metin çıkarma |
| `transcribe` | Ses → metin, JSON, SRT, verbose/diarized JSON |
| `media upload` | Tek/çok parçalı yükleme ve tamamlama; isteğe bağlı finalize |
| `media initiate/status/complete/finalize/abort` | Medyanın bütün genel API işlemleri |
| `quota` | Token kullanımı, kota penceresi, yenilenme, rezerve/kalan kredi |
| `request-status ID` | İsteğin yürütme ve ücretlendirme durumu; eski yanıt gövdesini döndürmez |
| `terms status/text/accept` | Şartları okuma, durum ve açık sürüm kabulü |
| `health [--ready]` | Anahtarsız servis durumu |
| `request METHOD PATH` | Belgelenmiş uçlara tam JSON gönderme |
| `api-docs` | Çevrimdışı endpoint listesi, `--schema` ile OpenAPI sözleşmesi |
| `gui` | Çapraz platform masaüstü grafiksel kullanıcı arayüzünü (`firik`) başlatır |

Her komutta `--help` bulunur. API komutları agent, plugin veya MCP sunucusu başlatmaz; LLMTR'ye otomatik yönlendirme yapmaz.

Agent sohbetlerinde (`firik-agent`, masaüstü sohbet ve Coding Agent) model, ilk yanıtından önce yerel çalışma ortamını sistem bağlamında alır: işletim sistemi/sürümü, mimari, komut aracının gerçekten kullandığı kabuk, çalışma dizini, Python çalışma zamanı, saat dilimi içeren yerel saat ve PATH üzerinde bulunan temel araçlar. Bu bilgiler her model isteğinde yenilenir; `get_environment_info` aracıyla da sorgulanabilir. Örneğin macOS/zsh ortamında komutların zsh sözdizimine göre yazılması yönlendirilir. Ortam değişkenlerinin tamamı veya API anahtarları bu bağlama eklenmez. Terminal penceresinin kabuğu ile komut aracının varsayılan kabuğu farklı olabilir; model komut aracının kabuğunu esas alır.

## Masaüstü Uygulaması (`firik`)

FIRIK LLM API'sini grafiksel kullanıcı arayüzü ile kullanmak için çapraz platform (macOS, Windows, Linux) ve tamamen Türkçe tasarlanmış modern masaüstü uygulamasıdır. Pencere başlığı ve uygulama adı **`firik-agent`** olarak tanımlanmıştır.

### Başlatma Seçenekleri:

```bash
# Terminalden:
firik gui
# veya
firik-gui
# veya
python -m firik_agent.ui
```

Grafik ortamda çift tıklayarak çalıştırmak için:
- **macOS:** `firik-agent.command`
- **Windows:** `firik-agent.bat`
- **Linux:** `firik-agent.desktop` (Sistem menüsüne eklemek için `~/.local/share/applications/` içine kopyalanabilir)

### Temel Özellikler:
- 💬 **Sohbet:** Gerçek zamanlı akışlı yanıtlar, görsel yükleme, model parametreleri, sağda kullanıcı / solda asistan mesajları, katlanabilir işlem ayrıntıları, otomatik kaydedilen sohbet geçmişi ve dışa aktarma (Markdown/JSON).
- 👁️ **Görsel & OCR:** Belgeleri, faturaları ve fotoğrafları önizleme, FIRIK OCR modelleriyle metin çıkarma, panoya kopyalama ve `.txt` olarak kaydetme.
- 🎙️ **Ses Çözümleme:** MP3, WAV, M4A, OGG ve FLAC ses kayıtlarını metne ve zaman damgalı SRT altyazılara dönüştürme.
- 🔍 **Yeniden Sıralama (Rerank):** Arama sorguları ve belge listelerini semantik alaka puanlarına göre sıralama.
- 📊 **Kota ve Durum:** Canlı bakiye ve token kotası sorgulama, sunucu sağlık/hazırlık (`/healthz`, `/readyz`) kontrolleri ve resmi Kullanım Şartları (Terms) metnini okuyup onaylama.
- 🔌 **Gelişmiş MCP Yöneticisi:** Yerel (`stdio`) ve uzak (`sse`/`streamable_http`) Model Context Protocol sunucularını görsel olarak ekleme, test etme, araç şemalarını inceleme, canlı logları izleme, işletim sistemi kasasında (`keyring`) güvenli token saklama ve her sohbet oturumuna özel bağımsız araç seçimi (per-chat MCP isolation).
- ⚙️ **Ayarlar:** API taban adresi yapılandırması, API anahtarını işletim sisteminin şifreli kasasında (macOS Keychain, Windows Credential Manager, Linux Secret Service) saklama, açık/koyu tema ve arayüz ölçeklendirme.

Sohbet ekranındaki aranabilir **Sohbetler** listesinden eski bir konuşmayı açıp devam edebilirsiniz. Bir yanıt yazılırken başka sohbete geçebilir, **+ Yeni Sohbet** ile farklı sorular sorabilirsiniz; yanıtlar kendi sohbetlerinde devam eder. Taslaklar ve kaydırma konumları korunur. Liste, **Yanıt yazılıyor** ve arka planda tamamlanan **Yeni yanıt** durumlarını gösterir. **Durdur** yalnızca seçili sohbeti durdurur; **Sohbeti sil** yalnızca seçili konuşmayı onay sonrası siler.

Model yanıtları Markdown olarak gösterilir: başlıklar, kalın/italik metin, listeler, alıntılar, bağlantılar, tablolar ve kod blokları biçimlendirilir. Metin seçilebilir; kod bloklarındaki **Kodu kopyala** düğmesi yalnızca kodu kopyalar. Geniş kod ve tablolar yatay kaydırılır. Mesaj kopyalama, dışa aktarma ve sohbet geçmişi orijinal Markdown metnini korur.

Kullanıcı mesajı gönderildiğinde bir kez, model yanıtı tamamlandığında (veya durdurulduğunda) yeniden kayıt yapılır. Akış sırasında kelime başına veya zamanlayıcıyla kayıt yapılmaz. Araç çağrılarında deneme kimliği yürütümden önce, sonuç ve belirsizlik durumu yürütümden sonra ayrıca kaydedilir. Uygulama normal şekilde kapatılırken yarım kalan yanıtlar da korunur; eski araç çağrıları tekrar çalıştırılmaz. Mesajlar, ekli görseller, işlem ayrıntıları ve model ayarları yerel `chats.db` dosyasında tutulur: kaynak koddan çalıştırıldığında `~/.firik/`, paketlenmiş uygulamada kullanıcıya ait `firik/config.yaml` dosyasının bulunduğu dizin.

**Computer-use MCP** varsayılan olarak eklenir ve yeni sohbetlerde seçilir. Yerleşik sunucu uygulama içinde çalışır; harici Python veya MCP sunucusu kurulumu gerekmez. MCP yöneticisinden kapatılabilir veya kaldırılabilir; bu tercih sonraki açılışlarda korunur. **MCP** düğmesiyle yalnızca ilgili sohbetten de çıkarılabilir. Ekran, klavye ve fare işlemleri mevcut işletim sistemi izinlerini kullanır.

### Projeler: Kodlama Ajanı

Projenin **Kodlama Ajanı** sekmesinde model seçimi proje bazında kaydedilir. **Değişiklikler**, her çalışma başlangıcındaki dosyalarla karşılaştırarak eklenen, değişen ve silinen dosyaları; yeşil/kırmızı satır farklarını gösterir. Araç işlemlerinden sonra ve çalışma sonunda otomatik güncellenir. Git deposu olmayan projelerde de çalışır. **İşlemler** sekmesinden araç parametreleri, komut çıktısı, çalışma süresi ve hatalar incelenebilir. **Dosyalar** alt klasörlerdeki kaynak dosyaları listeler ve satır numaralı önizleme açar.

**Editör** alanından VS Code, Cursor, Zed, sistem varsayılanı veya özel bir uygulama seçin; **Projeyi Aç** ile proje klasörünü, fark görünümündeki veya önizlemedeki açma düğmesiyle ilgili dosyayı açın. Editör tercihi kaydedilir. Özel editör için çalıştırılabilir uygulama dosyasını seçin.

Karşılaştırma son çalışma süresindeki kaynak dosya değişikliklerini gösterir; aynı sırada dışarıdan yapılan düzenlemeler de bu karşılaştırmaya dahildir. Gizli dosyalar, bağımlılık/derleme klasörleri ve ikili dosyalar taramaya dahil edilmez. Tarama 2000 dosya, dosya başına 1 MB ve toplam 20 MB ile sınırlıdır; eksik karşılaştırmalar arayüzde belirtilir. İşlem görünümü son çalışmanın en son 100 araç çağrısını tutar.

**Git God**, proje gezinmesinden veya Kodlama Ajanı içindeki **Git** sekmesinden açılır. Katkı sıralaması, aktivite haritası, dosya değişim yoğunluğu, commit grafiği, satır yazarı (blame), yeniden adlandırmaları takip eden dosya geçmişi ve dal karşılaştırmaları gerçek yerel Git verilerinden hazırlanır. Değişiklikleri inceleyip stage/unstage/commit işlemleri yapabilir; gelişmiş komutları açıklamaları ve tam komut önizlemesiyle çalıştırabilirsiniz. Reflog, stash, worktree ve etiketler kurtarma ekranındadır. Katkı raporları JSON/CSV olarak dışa aktarılabilir. [Git God kullanım kılavuzu](docs/GIT_GOD.md).

Kodlama ajanının yerleşik `git_inspect`, `git_tools` ve `git_prepare` araçları aynı Git servislerini kullanır. Okuma araçları arka planda çalışır; değişiklik yapan araçlar ajan için işlem önizlemesi döndürür ve Git panelinden onaylanır. Git kurulu olmalıdır; ayrıca sunucu veya grafik kütüphanesi kurulumu gerekmez.

### Masaüstü MCP Yöneticisi (Desktop MCP Manager)

FIRIK Masaüstü uygulaması, sol menüdeki **🔌 MCP** sekmesi üzerinden tam teşekküllü bir MCP sunucu yönetim merkezi sunar:

1. **Sunucu Ekleme & Düzenleme:**
   - **Yerel (stdio):** `npx`, `uvx`, `python`, `node` gibi komutlar, argümanlar, çalışma dizini (`cwd`) ve ortam değişkenleri tanımlanabilir.
   - **Uzak (HTTP/SSE):** Uzak MCP sunucusu URL'si, taşıma protokolü (`sse` / `streamable_http`), özel HTTP başlıkları (headers) ve kimlik doğrulama ayarları.
2. **İşletim Sistemi Kasasında Güvenli Secret Saklama:**
   - API tokenları ve gizli ortam değişkenleri düz metin `config.yaml` içine **asla** yazılmaz.
   - Değerler işletim sisteminin güvenli kasasında saklanır (`keyring://mcp/<sunucu>/<anahtar>`). Arayüzde `••••••••` olarak maskelenir; loglarda ve araç çağrılarında otomatik olarak sansürlenir (redaction).
3. **Tanılama ve Bağlantı Testi:**
   - Sunucuyu kaydetmeden önce veya sonra **Sına (Test)** butonuyla JSON-RPC el sıkışması, sunucu sürümü, protokol sürümü, yetenekler (capabilities), gecikme süresi (ms) ve keşfedilen araç listesi tek ekranda doğrulanabilir.
4. **Araç Gezgini & Canlı Loglar:**
   - **Araçlar:** Sunucunun sunduğu tüm araçlar, açıklamaları ve parametre şemaları (JSON Schema) incelenebilir.
   - **Loglar:** Subprocess stdout/stderr akışı ile JSON-RPC hata ve bildirimleri gerçek zamanlı izlenebilir.
5. **Sohbet Başına MCP Seçimi (Per-Chat Isolation):**
   - Sohbet ekranının üst araç çubuğundaki **🔌 MCP (X)** butonuna tıklanarak mevcut sohbet oturumuna istenen sunucular eklenip çıkarılabilir.
   - Giriş alanının üzerinde aktif sunucular rozet (chip) olarak listelenir; `×` ile yalnızca o sohbetten kaldırılır (global konfigürasyon korunur).
   - Global araç havuzu körü körüne mutasyona uğramaz (`SessionToolFilter`); her sohbet bağımsız bir çalışma oturumu (`ChatSession`) yürütür.
6. **Katlanabilir Araç Çağrısı Kartları:**
   - Model bir MCP aracını çağırdığında arayüzde donma olmaz; çağrı durumu (çalışıyor / tamamlandı / hata), çalışma süresi, argümanlar ve önizleme katlanabilir kart içinde gösterilir.


## Sohbet, görsel anlama ve akış

```bash
firik chat -m glm-5.3 -p 'Türkiye hakkında üç cümle yaz.' --text
firik chat -p 'Kısa bir öykü yaz.' --stream --text
firik chat -m qwen3-vl-30b --image ./foto.png -p 'Görseli açıkla.' --text
firik chat --system 'Kısa cevap ver.' --prompt @soru.txt --max-tokens 512
cat soru.txt | firik chat --prompt - --text

firik completions --prompt 'Bir zamanlar' --max-tokens 128
firik responses --prompt 'Bu konuyu açıkla' --stream
# Sunucu tarafında canlı veri araçlarına açık katılım:
firik chat -p 'Güncel bilgiyi kontrol et.' --firik-tools
```

`--image` tekrar edilebilir. `--image-url` uzak veya data URL girdileri içindir. Modellerin modaliteleri ve erişim durumu değişebilir; `firik models` yanıtını esas alın. `--firik-tools` sunucunun özelliğidir; model/gateway desteğini sunucu doğrular.

## Embeddings ve rerank

```bash
firik embeddings -m qwen3-embedding-8b --input 'Birinci metin' --input @belge.txt -o vectors.json
firik rerank -m qwen3-reranker-8b --query 'yapay zekâ' \
  --document 'Makine öğrenmesi üzerine bir belge' --document 'Bahçecilik rehberi'
firik rerank -m qwen3-reranker-8b --query 'arama metni' --documents @belgeler.json
```

`belgeler.json` bir metin dizisidir: `["Belge 1", "Belge 2"]`. Bu komutlarda `--model` veya JSON gövdesinde `model` gereklidir. Yukarıdaki modeller 22 Eylül 2026 canlı katalog kontrolünde görüldü; örnekler hesabınızın gelecekteki erişimini garanti etmez.

## OCR ve ses

```bash
firik ocr --file ./taranan.png --model dots-ocr --text -o metin.txt
firik ocr --file ./taranan.jpg --model deepseek-ocr-2
firik transcribe ./kayit.wav --language tr --response-format text -o kayit.txt
firik transcribe ./kayit.wav --response-format srt -o altyazi.srt
firik transcribe ./kayit.wav --response-format diarized_json -o konusmacilar.json
```

OCR görsel girdisini resmî `image` data URL alanına dönüştürür; PDF ayrıştırıcısı değildir. Ses dosyaları `multipart/form-data` gönderilir. Desteklenen ses çıktı seçenekleri: `json`, `text`, `srt`, `verbose_json`, `diarized_json`. Metin ve SRT çıktıları JSON tırnakları eklenmeden yazılır.

## Medya

```bash
firik media upload ./video.mp4
# Yükleme + complete + finalize:
firik media upload ./video.mp4 --finalize
# Yukarıdaki yanıttaki media_id değerini kullanın:
firik media status MEDIA_UUID
firik media finalize MEDIA_UUID
firik media abort MEDIA_UUID

# Yüklemeyi başka bir araçla yapmak için:
firik media initiate --content-type video/mp4 --size-bytes 123456
# İmzalı URL'lere PUT yaptıktan sonra:
firik media complete MEDIA_UUID
# Çok parçalı yüklemede her PUT yanıtının ETag değerini kullanın:
firik media complete MEDIA_UUID --parts @parts.json
```

`parts.json`: `[{"part_number":1,"etag":"\"storage-etag\""}]`.

`upload` sunucunun verdiği tek/çok parçalı planı izler; dosyayı belleğe bütünüyle almaz ve storage adresine FIRIK anahtarı göndermez. Tamamlama öncesi hata veya iptalde `abort` dener. Finalize başarısızsa tamamlanmış medyayı otomatik silmez; durumunu kontrol edip finalize komutunu tekrar kullanabilirsiniz. `initiate` çıktısındaki imzalı URL'ler geçici yükleme yetkisi taşır.

## Tam API parametreleri ve otomasyon

`--body` inline JSON, `@dosya.json` veya stdin için `-` kabul eder. `--set ALAN=JSON` parametresi tekrar edilebilir; `tools`, `tool_choice`, `response_format`, `reasoning_effort`, `stop`, `seed`, `top_k` gibi ek alanlar korunur. CLI seçenekleri JSON gövdesindeki ilgili alanları geçersiz kılar; `messages` ile `--prompt/--image` çakışması hata verir.

```bash
firik chat --body @conversation.json --set 'reasoning_effort="high"'
firik responses --body @request.json -o response.json
firik request POST /v1/chat/completions --body @conversation.json
firik request GET /v1/quota --metadata
firik request-status REQUEST_UUID
firik api-docs --schema -o firik-openapi.json
firik api-docs --path /v1/media
```

`request` yalnızca kaydedilmiş genel uçlara erişir; `internal` servis yollarını veya başka siteleri kabul etmez. Yeni uç noktalar için şemanın ve kapsam testinin güncellenmesi gerekir. Ses için dosya seçeneği olan `transcribe` komutunu kullanın.

Varsayılan stdout çıktısı JSON'dur. `--text` metin çıktısını seçer. Akışta varsayılan çıktı her satırda `{ "event": ..., "data": ... }` içeren **JSONL**'dır; reasoning, usage, tool-call ve Responses olayları korunur. `--stream --text` yalnızca metin deltalarını gösterir; araç çağrısı işlemek isteyenler JSONL kullanmalıdır. Doğrudan API komutları modelin döndürdüğü araç çağrılarını kendiliğinden çalıştırmaz; otonom araç döngüsü `firik-agent` içindedir.

- `--metadata`: istek kimliği, `X-Evren-*`, hız/kota ve `Retry-After` başlıklarını stderr'e yazar.
- `--output DOSYA`: başarılı yanıtı atomik olarak yazar; hata olursa eski dosyayı korur.
- `--timeout SANİYE`: varsayılan 180; POST işlemleri otomatik tekrar edilmez.
- `--config DOSYA`: `providers.firik` içindeki adres, model ve `api_key_env` ayarlarını okur.
- `--base-url URL`: doğrudan API adresini değiştirir. `FIRIK_BASE_URL` de desteklenir. Öncelik CLI → ortam → config → varsayılan.
- Çıkış kodları: başarı `0`, istek/girdi hatası `1`, argüman hatası `2`, Ctrl-C `130`.

HTTP 401/402/403/429/503 hataları sunucunun açıklamasıyla gösterilir. İstek kimliği ve hata içindeki `firik` alanları korunur. Kullanım/kota hatalarında `firik quota`, şart hatalarında `firik terms status` kullanın. CLI yeni anahtar üretmez veya şart kabulünü sessizce değiştirmez.

## Python istemcisi

```python
import asyncio
import os
from firik_agent.api import FirikAPI

async def main():
    async with FirikAPI(os.environ["FIRIK_API_KEY"]) as api:
        print(await api.models())
        print(await api.quota())
        result = await api.embeddings("qwen3-embedding-8b", ["Merhaba", "Dünya"])
        print(result)
        async for event in api.stream("/v1/responses", {
            "model": "glm-5.3", "input": "Merhaba"
        }):
            print(event)

asyncio.run(main())
```

İstemci `chat`, `completions`, `responses`, `embeddings`, `rerank`, `ocr`, `transcribe`, `upload_media`, medya yaşam döngüsü, şartlar ve durum sorguları için metotlar sunar. Akış için `stream` kullanın; nesneyi `async with` ile veya `await api.close()` çağrısıyla kapatın.

## Agent ve MCP modu

Claude Code ve Codex CLI benzeri MCP sunucusu ekleme, listeleme ve kaldırma:

```bash
# MCP sunucusu ekleme ve config.yaml'a otomatik kaydetme:
firik-agent mcp add git npx -y @modelcontextprotocol/server-git
firik-agent mcp add sqlite uvx mcp-server-sqlite --db-path ./app.db
firik-agent mcp add github -e GITHUB_PERSONAL_ACCESS_TOKEN=ghp_xxx npx -y @modelcontextprotocol/server-github
firik-agent mcp add remote --url http://localhost:8000/sse

# Sunucuları listeleme ve kaldırma:
firik-agent mcp list
firik-agent mcp list --test
firik-agent mcp remove git    # veya: firik-agent mcp rm git

# (Aynı komutlar 'firik mcp add ...' olarak da çalışır)
```

İnteraktif agent ve testler:

```bash
firik-agent
firik-agent -p 'Bu soruyu araçlarla çöz'
firik-agent --provider llmtr --model evren/glm-5.3-fp8
pytest -q
```

Agent içinde `!ls -la`, `!git status` veya `/shell pwd` ile yerel terminal komutu
çalıştırabilirsiniz. Komut doğrudan çalışır, çıktısı sohbet bağlamına eklenir.
`Ctrl+C` komutu durdurup oturuma döner; varsayılan zaman aşımı 60 saniyedir.
Tek komut için `firik-agent -p '!ls -la'` kullanın; API anahtarı gerekmez.
Her komut yeni bir shell açar; dizin değiştirmek için `!cd dizin && komut` yazın.

Agent'ın tool çağrıları, argümanları, sonuçları ve Thinking metinleri varsayılan
olarak katlıdır; sohbet alanında kısa bir işlem özeti görünür. İşlem sürerken
`Ctrl+O` veya özet satırına tıklama ayrıntı panelini açar/kapatır. Bir satırı
tıklayarak ya da ok tuşlarıyla seçip `Enter`/`Space` kullanarak içeriğini açın.
Uzun sonuçlarda ok tuşları, `PageUp`/`PageDown` ve fare tekerleğiyle gezinin.

Yanıttan sonra `Ctrl+O`, alt çubuğa tıklama veya `/details`, oturumdaki geçmiş
çağrıların ayrıntılarını açar; `Esc`/`Ctrl+O` sohbete döner ve yazmakta olduğunuz
mesaj korunur. `/clear` bu ayrıntıları da temizler. Katlama yalnızca görünümü
etkiler: modele iletilen sonuçlar değişmez ve ayrıntılarda eski 200 karakterlik
önizleme sınırı yoktur. Etkileşimli terminal bulunmadığında `/details` ayrıntıları
düz metin olarak yazdırır; fare desteği terminale bağlıdır.

[Detaylı MCP, skill, plugin, computer-use ve agent kullanım kılavuzu](docs/AGENT.md). Bilgisayar denetimi sistem izinleri ve yetenek teşhisi için `firik-agent computer-use doctor` komutunu çalıştırabilirsiniz. Agent REPL'inde `/api ...` için doğrudan `firik` sağlayıcısını seçin. Birim/kontrat testleri sahte HTTP transport kullanır; gerçek anahtara veya ücretli API çağrılarına ihtiyaç duymaz.


## Masaüstü uygulamasını indirme

Kurulum dosyaları yayımlandığında [GitHub Releases](https://github.com/FehmiCitiloglu/firik-agent/releases/latest) sayfasında yer alacaktır:

| Sistem | Dosya |
| --- | --- |
| Windows x64 | `firik-agent-VERSION-windows-x64-setup.exe` |
| macOS 15+ Apple Silicon | `firik-agent-VERSION-macos-arm64.dmg` |
| macOS 15+ Intel | `firik-agent-VERSION-macos-x64.dmg` |
| Ubuntu 22.04+ / Debian 12+ | `firik-agent_VERSION_amd64.deb` |
| Fedora / openSUSE (glibc 2.35+) | `firik-agent-VERSION-1.x86_64.rpm` |
| Diğer Linux x86_64 (glibc 2.35+, X11/XWayland) | `firik-agent-VERSION-linux-x86_64.tar.gz` |

Windows kurucusu kullanıcı hesabına yükler; macOS'ta DMG içinden uygulamayı Applications'a sürükleyin. Python kurulumu gerekmez. ZIP dosyaları taşınabilir Windows/macOS alternatifleridir. Linux arşivinde `usr/bin/firik-agent` çalıştırılır. Alpine/musl desteklenmez. Linux'ta anahtar kaydetmek için Secret Service uyumlu, açık bir anahtarlık gerekir.

Windows paketleri henüz geliştirici sertifikasıyla imzalanmadığından SmartScreen onay isteyebilir. Yeni macOS release akışı Developer ID imzası ve Apple noter onayı gerektirir; daha önce yayımlanan imzasız paketler Gatekeeper onayı isteyebilir. Dosya bütünlüğü `SHA256SUMS.txt` ile kontrol edilebilir. [macOS imzalama ve GitHub Actions secret kurulumu](docs/MACOS_SIGNING.md).

Paketli uygulamanın ayarları Windows'ta `%APPDATA%/firik`, macOS'ta `~/Library/Application Support/firik`, Linux'ta `$XDG_CONFIG_HOME/firik` (varsayılan `~/.config/firik`) altında saklanır.

### Yeni sürüm yayınlama

1. İlk imzalı macOS release öncesinde [Apple sertifikasını ve Actions secret'larını](docs/MACOS_SIGNING.md#github-releases) hazırlayın. `pyproject.toml` sürümünü güncelleyin ve workflow/script değişiklikleriyle birlikte GitHub'a gönderin.
2. Aynı sürümün etiketini gönderin: örneğin `git tag v0.2.1` ve `git push origin v0.2.1`.
3. **Desktop Release** her işletim sisteminde testleri, PyInstaller derlemesini ve paketli uygulamanın açılış kontrolünü çalıştırır. Tüm kurulum dosyaları hazır olduğunda release yayınlanır.

Yeniden denemek için Actions → Desktop Release → Run workflow alanında **mevcut etiketi** girin. Manuel çalıştırma da etiketin kaynak kodunu derler; başka bir commit'i o sürümmüş gibi yayınlamaz.
