# Evren Kodlama Ajanı 2.0

Bu kayıt, mevcut CustomTkinter masaüstü uygulamasını koruyarak yapılan geliştirmeyi ve doğrulanması gereken sınırları açıklar. Kodlama çalışma alanının dili Türkçedir; API adları, olay türleri ve veri tabanı değerleri geriye dönük uyumluluk için korunur.

## Başlangıçta mevcut sistem

| Soru | İncelenen gerçek yapı ve başlangıç bulgusu |
| --- | --- |
| 1. Bir istek alındığında ne çalışıyordu? | `ui/views/projects_view.py` içindeki `_run_coding_prompt`, `ui/service.py` üzerinden `Agent.run_stream` döngüsüne bağlanıyordu. Akış olayları zaten vardı; görev yaşam döngüsü ve kalıcı çalışma kimliği bulunmuyordu. |
| 2. Proje dosyalarına nasıl erişiliyordu? | `projects/indexer.py` sembol indeksini oluşturuyordu; `projects/workspace.py` sınırlı dosya anlık görüntüleri ve önce/sonra farkları sağlıyordu. Ajanın genel meta dosya araçları, kodlama çalışma alanına özel bir yürütme sınırı uygulamıyordu. |
| 3. Araçları kim seçiyordu? | `core/session.py` içindeki `SessionToolFilter`, seçili araç ve MCP sunucularını filtreliyordu. Yerleşik/meta/plugin araçlarına geniş varsayılan erişim vardı; proje çalışma modu dosya yürütücüsünde uygulanmıyordu. |
| 4. Hafıza ve konuşmalar nasıl korunuyordu? | `projects/db.py` SQLite üzerinde proje kuralları, hafıza, ADR ve geçmiş kodlama özetlerini saklıyordu. `ChatSession.messages` çalışan sohbeti bellekte tutuyordu. Araç olaylarının ilerleme halinde kaydedilmesi ve kesintiden güvenli geri yükleme eksikti. |
| 5. Düzenleme, hata ve test süreci nasıldı? | Ajan döngüsünde araç çağrıları ve hata olayları vardı. `workspace.py` gerçek dosya farkını çıkarıyordu. Beklenen dosya sürümüyle hedefli yama, ajana ait güvenli geri alma ve gerçek test kanıtına bağlı plan tamamlama yoktu. |
| 6. İzin sınırları neydi? | Proje ayarlarında izin sözlüğü vardı, ancak kodlama arayüzünden genel araçlara giderken bu sözlük ortak bir yürütme kapısı oluşturmuyordu. Task prompt hazırlanırken proje/görev kimliği ve göreli yol/symlink kontrolleri eksikti. |
| 7. Ne yeniden kullanıldı? | Sağlayıcı kayıt sistemi, asenkron çalışma zamanı, mevcut Agent akış döngüsü, MCP bağlantı yönetimi, proje SQLite kayıtları, mevcut Git ekranı, editör açma ve workspace farkları korundu. Kodlama görünümü ayrı mixin/bileşenlere, yürütme izinleri ayrı kodlama araç filtresine taşındı. |

## Uygulama aşamaları ve kod karşılıkları

| Aşama | Uygulanan yapı | Sınırı |
| --- | --- | --- |
| 1. Temel güvenilirlik | `projects/coding_runtime.py`: ask/plan/approved/auto modları, run/session/project/task kimlikleri, gerçek iptal ve kontrol noktası duraklatma, onay kararları, aynı başarısız işlemi sınırlama, sınırlı dosya araçları. `projects/coding_sessions.py` ve `projects/db.py`: mevcut tabloya eklemeli geçiş, olay kayıtları, kesinti işaretleme, açık kullanıcı isteğiyle devam. | Terminal ve MCP ayrı süreçlerdir; işletim sistemi düzeyinde sandbox oluşturulmaz. |
| 2. Arayüz | `ui/coding_workspace.py`, `ui/components/coding_conversation.py`, `ui/components/coding_explorer.py`: üç panel, kademeli ağaç, kaynak araması, Markdown/kod kartları, kopyalama, işlem/onay kartları, çok satırlı giriş, durdurma/devam, model seçimi, dışa aktarma, kalıcı tercihler. `ui/coding_strings.py` merkezi mod/durum/izin metinleri. | Gerçek ekran boyutları ve tema/klavye davranışı GUI testleriyle ayrıca kontrol edilir. |
| 3. Bağlam ve doğrulama | `projects/context_builder.py`, `projects/coding_service.py`, `projects/indexer.py`: her tur güncel kurallar ve dosyalar, sembol/anahtar sözcük seçimi, doğrudan Python bağımlılıkları, görev kabul kriterleri, onaylı/pinli hafıza ve kabul edilmiş ADR, oturum kararları/test sonuçları. `coding_runtime.py`: tam konuşma korunurken eski turları yerel bütçeyle sıkıştırma, gerçek test kanıtıyla kriter kaydı. | Token sayımı karakter hesabına dayalı yerel tahmindir. Bilinmeyen model penceresi veya fiyat kesin değer olarak sunulmaz. Anlamsal arama eklenmemiştir. |
| 4. Araçlar ve proje entegrasyonu | Hedefli `edit_file`/tek dosyalı `apply_patch`, SHA-256 beklenen sürüm, eşzamanlı kullanıcı değişikliğini koruyan geri alma, test komutu ve gerçek çıkış kodu, Git durum/farkı, proje/oturum MCP seçimi, metadata katalogu ve yalnız seçilen beceri talimatları. Mevcut Git çalışma alanı korunur; Git olmayan projede anlık görüntü farkı çalışır. | Açık onay olmadan commit/push yapan kodlama aracı yoktur. Ajanın test başarısı, tek başına bütün görev kriterlerinin sağlandığı anlamına gelmez. |
| 5. İleri yetenek temeli | `projects/specialists.py`: rol ve izin kesişimi, gerçek araç kapısı ve dosya kilitleri. `projects/subagents.py`: gerçek sağlayıcıyla salt okunur araştırma/inceleme alt ajanları. | Alt ajanlar proje seçeneğiyle etkinleşir; varsayılan kapalıdır. Ayrı checkout ve dosya düzenleyen alt ajanlar sonraki aşamadır. |

## Güvenlik ve veri davranışı

- Kaynak okuma/yazma proje köküyle sınırlandırılır. Proje dışına çıkan yollar, sembolik bağlantılar, hassas dosyalar ve korumalı ayar klasörleri reddedilir. Destekleyen işletim sistemlerinde symlink yarışlarına karşı `O_NOFOLLOW` ve klasör dosya tanımlayıcıları kullanılır; çok bağlantılı dosyalar kaynak bağlamına alınmaz.
- Yazma, güncel dosyanın beklenen özetiyle eşleşmesini gerektirir. Ajanın önceki değişikliğinden sonra dosya kullanıcı tarafından değiştirilmişse geri alma durur. Yeniden açılışta journal hash'leri yeniden incelenir; geçmiş çalışma otomatik yürütülmez.
- Soru Sor ve Planla modlarında kaynak değiştiren veya terminal çalıştıran araçlar sunulmaz. Onaylı Uygulama modunda düzenleme planı ve işlem onayı gerekir. Otomatik Uygulama da silme, terminal ve MCP onay kapısını korur.
- Terminal komutu onaylandıktan sonra yerel kullanıcı yetkileriyle çalışır; `cwd` proje içinde olsa da komutun bütün yan etkileri proje içinde tutulmuş olmaz. MCP çağrısının dış etkisini sunucu belirler. Bu yüzden bu işlemlerde her çağrı ve parametre ayrı onaylanır.
- Dosya içeriği, belge, hafıza, ADR ve beceri içerikleri analiz edilecek veridir. Bunların içindeki komutlar kullanıcı izinlerini veya sistem güvenlik politikasını değiştiremez.
- Ortam anahtarları, private key blokları, belirgin token/parola atamaları ve hassas Git fark bölümleri model bağlamı/kayıt/dışa aktarımdan süzülür. Yapılandırılmış bilinen sağlayıcı sırları kayıt katmanında ayrıca maskelenir. Desen süzgeci, her olası serbest metin sırrının otomatik tanınacağını garanti etmez.
- Aynı uygulama çalışmasında canlı journal, ajanın tam önce/sonra içeriği üzerinden güvenli geri almayı sağlar. Yeniden açılışta kalıcı journal güvenlik için süzülmüş metadata içerir; eski dosya içeriğini yeniden üretip otomatik geri alma yapılmaz. Bu durumda Git farkı ve dosya hash kontrolüyle kullanıcı incelemesi gerekir.
- Tamamlanan konuşma çalışması ile tamamlanan proje görevi ayrı kavramlardır. Kod üretilmesi görev durumunu otomatik `Done` yapmaz. Plan adımının `completed` olması için kabul kriteri, doğrulama yöntemi ve gerçek başarılı araç kanıtı gereklidir.
- Uzman dosya kilitleri bu uygulama sürecindeki kaynak düzenlemelerini kapsar. Harici editörler ve shell komutları kilide tabi değildir; gerçek dosya sürümü kontrolü ve ana yürütücü izinleri yine gereklidir. Kayıtlı uzman yürütücü uygulamanın güvenilir kodudur; modele sadece daraltılmış araç şeması sunulmalıdır.

## Model ve beceri bilgisi

`projects/model_capabilities.py` yalnız açıkça sağlayıcı verisi veya doğrulanmış kontrol olarak işaretlenen yetenekleri kabul eder. Eski `ModelInfo` varsayılanı, model isminden yapılan çıkarım veya OpenAI uyumlu `/models` listesindeki bir isim, bağlam kapasitesini ispatlamaz. Araç desteği açıkça `False` ise çalışma araçsız devam eder. Bildirilmeyen maliyet/fiyat/usage alanları bilinmiyor olarak kalır.

`providers/registry.py` mevcut sağlayıcıları korurken yapılandırma üzerinden Ollama, LM Studio ve diğer OpenAI uyumlu uç noktaları ekleyebilir. Yerel sağlayıcıların yapılandırma testi, makinede gerçek bir model sunucusunun çalıştırıldığı anlamına gelmez.

`projects/skill_selection.py` katalogda ad, açıklama, tetikleme ve kaynak metadata'sını gösterir; callable talimat yükleyicileri yalnız seçilen beceriler için çağrılır. Kodlama çalışması genel aktif becerilerin tüm talimatlarını veya genel sınırsız meta araçlarını miras almaz.

## Doğrulama kaydı

3 Ekim 2026 tarihinde bu alt bölüm için gerçek komut:

```text
.venv/bin/python -m pytest tests/test_coding_context.py tests/test_coding_specialists.py tests/test_projects.py -q
29 passed in 0.49s
```

Bu sonuç; bütçe normalleştirme, zorunlu güvenlik politikası, kaynak/görev/proje ayrımı, hafıza/ADR onayı, güncel tur bağlamı, hassas veri ve Git süzme, dış yol/symlink engeli, lazy gezgin, gerçek satır araması, bilinmeyen model verisi, progressive beceri yükleme, uzman izin kesişimi, gerçek ebeveyn dosya düzenleme kapısı, paralel düzenleme çakışması, iptal ve mevcut proje API uyumluluğunu kapsar. Uygulamanın tüm testleri ve gerçek GUI/model/terminal testleri bu yerel alt test sonucundan ayrı raporlanır.

## Kabul senaryoları ve kanıt kapsamı

Aşağıdaki test dosyaları istenen senaryoları kapsar. Test altyapısındaki kontrollü model yanıtı, gerçek dış sağlayıcı çağrısı veya işletim sisteminin bütün platformlarında GUI testi anlamına gelmez.

| Senaryo | Test ve kanıt sınırı |
| --- | --- |
| 1. Yeni proje ve ajan başlatma | `test_projects.py`, `test_ui_projects.py`: gerçek proje kayıtları ve gerçek masaüstü görünümü; kontrollü akış sağlayıcısı. |
| 2. Değişiklik yapmadan planlama | `test_coding_runtime.py::test_read_only_modes_block_mutations_even_when_called_directly`: ask/plan yürütücüden doğrudan yazma/terminal engeli. |
| 3. Plan ve dosya değişikliği onayı | `test_approved_plan_and_each_mutation_await_ui_decision`: gerçek asenkron onay bekleme ve dosya işlemi. |
| 4. Doğru fark görünümü | `test_ui_projects.py::test_coding_changes_activity_preview_and_editor`: gerçek önce/sonra kaynak farkı, eklenen/silinen satır etiketleri. |
| 5. Gerçek test sonucu | `test_actual_commands_cwd_tests_exit_failure_and_verification_evidence`: yerel subprocess, gerçek çıkış kodu ve kanıt kimliği. |
| 6. Çalışırken durdurma | `test_actual_process_cancellation_and_timeout_cleanup`: subprocess iptal/zaman aşımı temizliği; `test_pause_resume_and_cancel_approval`: bekleyen onayın iptali. |
| 7. Kesinti ve yeniden açılış | `test_coding_sessions.py::test_interrupted_work_reopens_without_executing_or_auto_resuming`: aynı SQLite kaydını yeniden açma, çalışmayı otomatik başlatmama. |
| 8. Güvenli devam | Oturum journal kaydı ve bridge dosya hash yeniden doğrulaması; eski redakte journal'dan dosya içeriği yeniden üretilmez. Gerçek GUI üzerinden kesinti/yeniden açılış/harici değişiklik devamı ayrıca uçtan uca sınanmalıdır. |
| 9. İki proje ayrımı | `test_project_task_session_and_event_identity_are_enforced`, `test_latest_history_and_terminal_state_remain_project_scoped`, UI project identity testi. |
| 10. Proje dışı erişim engeli | Runtime path testi; context outside/symlink/hardlink ve check-open arasında klasör değiştirme testleri. |
| 11. Önceden/eşzamanlı kullanıcı değişiklikleri | `test_targeted_edits_revision_conflicts_and_safe_undo`: beklenen sürüm çakışması ve kullanıcı değişikliğinde geri alma reddi. |
| 12. Ağ/model/araç hataları | `test_partial_model_stream_failure_does_not_repeat_request`, subprocess timeout testi: kontrollü kesik akış, tekrar göndermeme ve temiz hata. Gerçek internet kesintisi ayrı ağ koşulu testidir. |
| 13. Desteklenmeyen model özellikleri | `test_provider_without_tools_receives_no_schemas_and_cannot_execute`; context testinde doğrulanmamış kapasite/fiyatın bilinmiyor kalması. |
| 14. Türkçe karakterler ve arayüz | Kaynak satır arama/context/GUI testlerinde Türkçe içerik; mevcut Türkçe mod/durum/izin etiketleri. Tüm işletim sistemi fontlarının incelendiği iddia edilmez. |
| 15. Büyük proje/uzun sohbet | Lazy çocuk listesi kaynak okumaz; arama dosya/bayt/sonuçla sınırlıdır; `test_panel_preferences_and_long_conversation_are_bounded` kart sınırını ve panel tercihlerini doğrular. Çok büyük gerçek repo performans benchmarkı ayrıca yapılmalıdır. |

## Açık kalan işler

- İşletim sistemi düzeyinde terminal/ağ sandboxı ve sunucu tarafında MCP sandboxı bu değişikliğin parçası değildir.
- Native dosya sürükle/bırak için platforma özel Tk entegrasyonu eklenmemiştir; dosya ekleme düğmesi, gezginden bağlam seçimi ve sohbet kodu ekleme mevcuttur.
- Embedding tabanlı anlamsal arama, dosya düzenleyen uzman sağlayıcı yürütücüleri ve otomatik worktree oluşturma ayrı ilerleme aşamalarıdır.
- GUI kontrolleri ve doğrulama kayıtları görev sistemiyle bağlanmıştır; otomatik alt görev veya hafıza/ADR kesinleştirme yapılmaz. Kullanıcı onayıyla öneri inceleme akışı ayrıca genişletilebilir.
- Gerçek model sunucusu/provider testi ve paketlenmiş masaüstü dağıtımı ayrıca doğrulanmalıdır; kaynak veya birim test değişikliği yayımlanmış uygulama paketini güncellemez.

## Araştırma ve inceleme alt ajanları — 5 Ekim 2026

Kodlama ekranındaki **Araştırma / inceleme alt ajanları** seçeneği proje tercihine kaydedilir ve varsayılan kapalıdır. Etkinleştirildiğinde ana modele `spawn_subagent`, `wait_subagent` ve `cancel_subagent` araçları sunulur. Bağımsız görevlerde ana ajan araştırma (`research`) veya inceleme (`review`) rolü seçer ve sonuçları toplar.

`projects/subagents.py` mevcut `SpecialistCoordinator` ve gerçek `Agent.run_stream` döngüsünü kullanır. Çocuklar ana ajanın sağlayıcısını, modelini ve düşünme seviyesini kullanır; mesaj geçmişleri ayrıdır. Her çocuğun ayrı `agent_id` kimliği vardır. Yalnız mevcut ana izin kapısından geçen okuma araçları görünür; terminal, dosya düzenleme, MCP, plan/doğrulama değiştirme ve başka alt ajan oluşturma araçları sunulmaz.

Sınırlar: aynı anda iki, çalışma boyunca dört alt ajan; çocuk başına 120 saniye, sekiz model turu ve en fazla 1024 çıktı tokenı. Bu yerel sınırlar kesin parasal bütçe değildir. Ana çalışma bittiğinde veya durdurulduğunda açık çocuklar iptal edilir. Alt ajan hatası ana modele başarısız araç sonucu olarak iletilir; başarılı inceleme gibi gösterilmez.

Alt ajan olayları ve terminal mesaj geçmişleri ana çalışmanın SQLite kaydında `agent_id` ve `parent_run_id` ile tutulur. Model kullanım bilgisi ana ve çocuk istekleri birlikte sayılarak kaydedilir. **İşlemler** sekmesinde görev, rol, durum, geçen süre ve sonuç gösterilir; kaydedilmiş kartlar geçmiş açılırken pasif olarak geri yüklenir.

Bu sürüm dosya düzenleyen alt ajan, ayrı Git worktree veya otomatik birleştirme içermez. Kontrollü sağlayıcı testleri gerçek ajan döngüsü, araç yürütücüsü ve SQLite kaydını doğrular; dış sağlayıcı kanıtı ayrıca raporlanır.

5 Ekim doğrulaması:

- `test_subagents.py`, `test_coding_specialists.py`, `test_coding_bridge.py`, `test_coding_runtime.py`: **67 test geçti**. İzin daraltma, eşzamanlılık/toplam sınırı, zaman aşımı, ana görev iptali, ayrı geçmiş, SQLite olay/sonuç kaydı ve toplam model kullanımı doğrulandı.
- `test_ui_coding_workspace.py`, `test_ui_coding_history.py`: gerçek macOS Tk pencerelerinde **11 test geçti**. Yeni seçenek, ayrı ilerleme kartı, ana sohbetin erken kapanmaması ve geçmiş kartının pasif geri yüklenmesi doğrulandı. Test ortamına mevcut Tcl/Tk kitaplık yolları verildi.
- Gerçek EVREN `mimo-v2.6-pro` sağlayıcısıyla geçici `sample.py` üzerinde ana model iki `spawn_subagent` ve iki `wait_subagent` çağrısı yaptı. Araştırma ve inceleme alt ajanları ayrı gerçek `read_file` çağrılarıyla `return a - b` davranışını buldu; ana model sonuçları birleştirdi. İki çocuk da tamamlandı, örnek dosya değişmedi. İlk ana model denemesi zaman aşımına uğradı; sonraki uçtan uca deneme geçti.
- Paketlenmiş masaüstü uygulaması bu kaynak değişikliğiyle yeniden oluşturulmadı.

Release öncesi izole v0.2.17 checkout doğrulaması: `scripts/test_desktop.py` gerçek macOS grafik ortamında **512 başarılı, 1 atlanan** testle tamamlandı. Aynı klasörde devam eden bağlam sıkıştırma çalışması bu release snapshotına alınmadı.
