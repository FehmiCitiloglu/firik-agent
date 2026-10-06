# Firik Agent — önceki agent modu kılavuzu

Doğrudan FIRIK API komutları için [ana kılavuza](../README.md) bakın. Bu dosya agent/MCP/skill/plugin kullanımını anlatır.

An extensible, AI Agent framework designed with **Model Context Protocol (MCP)**, **modular Skills**, and **lifecycle Plugins**, with native first-class integration for **FIRIK** (Savunma Sanayii Başkanlığı / SAYZEK) and **LLMTR Gateway**, as well as OpenAI-compatible providers.

---

## 🌟 Highlights

- 🇹🇷 **Native FIRIK & LLMTR Integration**:
  - Direct FIRIK platform: `https://evren-llmapi.ssyz.org.tr/v1`
  - LLMTR Gateway routing: `https://llmtr.com/v1` (documentation: [llmtr.com/docs/gateway/firik](https://llmtr.com/docs/gateway/firik/))
  - Full support for models: `glm-5.3`, `deepseek-v4-flash`, `qwen3.8-flash-next`, `gemma-4-31b`, `qwen3-vl-30b`, `auto` (and `evren/glm-5.3-fp8`, `evren/deepseek-v4-flash-tr` via LLMTR).
  - Real-time **reasoning stream extraction** (`delta.reasoning` / `delta.reasoning_content`) for thinking models.
  - Explicit Terms of Service status, text, and version acceptance (`/v1/terms/status`, `/v1/terms/text`, `/v1/terms/accept`).
- 🔌 **Model Context Protocol (MCP)**:
  - Connect to standard stdio and HTTP/SSE MCP servers dynamically.
  - Automatic JSON-RPC 2.0 handshake and tool discovery.
  - Prefixing and collision prevention across servers.
  - The agent itself can dynamically connect new MCP servers during its reasoning loop!
- 🧠 **Modular Skills System**:
  - Clean `SKILL.md` format with YAML frontmatter.
  - Active skills automatically inject specialized instructions into system prompts.
  - Create and activate skills on the fly via CLI or Python API.
- 🧩 **Extensible Plugins**:
  - Full lifecycle hooks: `on_user_message`, `on_model_request`, `on_model_response`, `on_tool_call`, `on_tool_result`, `on_error`.
  - Plugins can inject custom tools directly into the agent's tool registry.
  - Built-in plugins: `logger`, `calculator`, `web_search`.
- 💻 **Interactive CLI & REPL**:
  - Rich terminal formatting with syntax highlighting, thinking panels, and tool call audit trails.
  - Full slash commands: `/provider`, `/model`, `/mcp`, `/skill`, `/plugin`, `/tools`, `/terms`, `/clear`.

---

## 📦 Installation

```bash
# Tek komutla kurulum (İşletim sisteminize göre):
./install.sh         # macOS / Linux
# veya
make install         # macOS / Linux (Make ile)

.\install.ps1        # Windows (PowerShell)

python install.py    # Tüm Sistemler (Firiksel Python)
```

<details>
<summary><b>Manuel Kurulum</b></summary>

```bash
uv venv .venv
source .venv/bin/activate       # Windows PowerShell: .\.venv\Scripts\Activate.ps1
uv pip install -e .
```
</details>

---

## ⚙️ Configuration

Run `firik-agent` after installation. When the selected provider has no key, it
prompts with hidden input and stores the key in the OS credential store. Future
launches read it automatically, from any working directory. Switching to a
provider without a key using `/provider` also prompts before switching.

To save or replace a key explicitly:

```bash
firik login
firik login --provider llmtr
firik login --provider openai
```

Existing environment variables and optional legacy `.env` files remain supported
and take precedence over stored keys. Remove an old value to use a newly saved
key. No `.env` file is created by the installer. Without a usable OS credential
store, automatic onboarding keeps the key only for that run and reports this;
`firik login` fails if it cannot save. Non-interactive runs require an existing
stored key or environment variable and never wait for input.

You can customize defaults in `config.yaml`.

---

## 🖥️ Interactive CLI

Launch the interactive terminal session:

```bash
firik-agent
```

Or pass a single prompt directly:

```bash
firik-agent -p "Summarize the latest developments in Turkish AI"
```

Switch providers and models on startup:

```bash
firik-agent --provider llmtr --model evren/glm-5.3-fp8
```

### Slash Commands in REPL

> 💡 **Autocomplete:** Press **Tab** or start typing `/` for instant autocomplete across commands, subcommands, models, providers, and skills.

| Command | Category | Description & Practical Example |
|---|---|---|
| `/help` | General | Show the categorized beneficial commands reference guide |
| `/commands` / `/list` | General | List all available slash commands with syntax and optional category filter (`/list skills`) |
| `/status` | Diagnostics | Display full agent diagnostics (provider, model, tools, skills, message count) |
| `/provider [name]` | Providers | Switch or list providers (`/provider firik`, `/provider llmtr`, `/provider openai`) |
| `/model [name]` | Models | Switch active model (`/model glm-5.3`, `/model evren/glm-5.3-fp8`) or list models |
| `/mcp list` | MCP | Show connected MCP servers and their exposed tools (`/mcp list` or `/mcp ls`) |
| `/mcp add <name> <cmd> [args]` | MCP | Connect and persist a new MCP server (`/mcp add git npx -y @modelcontextprotocol/server-git`) |
| `/mcp remove <name>` | MCP | Disconnect and remove an MCP server from configuration (`/mcp remove git` or `/mcp rm git`) |
| `/skill list` | Skills | View available skills and their active status |
| `/skill on <name>` / `/skill off` | Skills | Toggle skill prompt augmentation (`/skill on computer-use`, `/skill on code_assistant`) |
| `/skill info <name>` | Skills | Inspect full prompt instructions and tags of a skill |
| `/skill add` | Skills | Interactive wizard to create and persist a new skill with `SKILL.md` |
| `/plugin list` | Plugins | View loaded plugins, versions, and injected tools |
| `/plugin add <file.py>` | Plugins | Load a Python plugin dynamically (`/plugin add plugins/weather_plugin.py`) |
| `/tools` | Tools | List all registered tools, their parameters, and sources (`builtin`, `mcp`, `skill`, `plugin`) |
| `/shell <command>` / `!<command>` | Terminal | Run a local command directly and keep its output in context (`!ls -la`, `!git status`) |
| `/api <command>` | FIRIK | All direct API commands; `/api --help`, `/api quota`, `/api ocr --file scan.png` |
| `/terms text` | FIRIK | Read the current terms before explicit acceptance |
| `/terms` | FIRIK | Check FIRIK direct endpoint terms of service status |
| `/terms accept <version>` | FIRIK | Accept FIRIK terms of service directly |
| `/history` | Session | View conversation turns summary and context stats |
| `/export [file.md]` | Session | Export conversation transcript with reasoning to Markdown |
| `/clear` | Session | Clear chat history and reset context window |
| `/exit`, `/quit` | Session | Safely disconnect MCP servers and exit the session |

### Terminal komutları

Agent oturumunda mesajı `!` ile başlatarak komutu doğrudan çalıştırabilirsiniz:

```text
You > !ls -la
You > !git status
You > !cd src && ls -la
You > /shell pwd
You > Bu çıktıya göre proje yapısını açıkla.
```

Komut yürütülürken modele istek gönderilmez. Komut, çıkış kodu, stdout ve stderr
sohbet geçmişine eklenir; sonraki normal mesajda model bunları görebilir.
Çıktı Markdown olarak yorumlanmadan terminal panelinde gösterilir.

`Ctrl+C` çalışan komutu durdurur ve oturuma geri döner. Varsayılan zaman aşımı
60 saniyedir. Her çıktı akışının ilk 32 KiB'ı tutulur; daha uzun çıktılar kesildi
işaretiyle gösterilir. Komutlar yerel kullanıcı yetkileriyle çalışır; bir sandbox
oluşturulmaz. İnteraktif programlara stdin verilmez. Her komut yeni bir shell
açar; `cd` ve `export` sonraki komuta taşınmaz. Dizin seçmek için aynı komutta
`cd dizin && komut` kullanın.

macOS/Linux'ta `$SHELL` (yoksa `/bin/sh`), Windows'ta `COMSPEC`/CMD kullanılır.
Modelin `run_command` aracı ayrıca `shell`, `cwd` ve `timeout` parametrelerini
destekler. Asenkron Python kullanımı: `await agent.run_command_async("ls -la")`.

Tek komutluk kullanım da desteklenir ve API anahtarı gerektirmez:

```bash
firik-agent -p '!ls -la'
```

---

## 🔌 Model Context Protocol (MCP) CLI (Codex & Claude Benzeri)

Firik Agent, Claude Code ve Codex CLI benzeri pratik bir `mcp` komut satırı arayüzü sunar. Eklenen sunucular doğrudan `config.yaml` dosyasına kaydedilir ve sonraki agent oturumlarında otomatik olarak ayağa kalkar.

### 1. Terminalden MCP Sunucusu Ekleme (`mcp add`)

```bash
# Standart stdio MCP sunucusu ekleme (npx, uvx, python vb.):
firik-agent mcp add git npx -y @modelcontextprotocol/server-git
firik-agent mcp add fs npx -y @modelcontextprotocol/server-filesystem /Users/me/Documents
firik-agent mcp add sqlite uvx mcp-server-sqlite --db-path ./app.db

# Ortam değişkeni (Environment variable) ile ekleme:
firik-agent mcp add github -e GITHUB_PERSONAL_ACCESS_TOKEN=ghp_xxx npx -y @modelcontextprotocol/server-github

# Uzak HTTP/SSE MCP sunucusu ekleme:
firik-agent mcp add remote --url http://localhost:8000/sse

# Bağlantı testini atlayarak doğrudan kaydetme:
firik-agent mcp add my-server python /path/to/server.py --no-test

# Aynı komutlar 'firik mcp add ...' olarak da çalışır:
firik mcp add git npx -y @modelcontextprotocol/server-git
```

`mcp add` komutu varsayılan olarak sunucuya bağlanır, araçları (tools) listeler, testi başarılı olursa `config.yaml` dosyasına yazar.

### 2. Yapılandırılmış Sunucuları Listeleme (`mcp list`)

```bash
firik-agent mcp list
# veya canlı bağlantı ve araç durumunu kontrol ederek:
firik-agent mcp list --test
```

### 3. MCP Sunucusunu Kaldırma (`mcp remove` / `mcp rm`)

```bash
firik-agent mcp remove git
# veya kısa takma ad:
firik-agent mcp rm git
```

### 4. REPL İçinde Dinamik Ekleyip Çıkarma

İnteraktif REPL oturumunda da aynı komutlar geçerlidir ve `config.yaml` otomatik güncellenir:
```
You > /mcp add git npx -y @modelcontextprotocol/server-git
You > /mcp list
You > /mcp rm git
```

---

## 🖥️ Desktop MCP Manager & Per-Chat Architecture

FIRIK Masaüstü uygulaması (`firik gui`), CLI'daki MCP altyapısını görsel bir yönetim merkezine ve oturum bazlı (per-chat) araç izolasyonuna kavuşturur.

### 1. Üç Ayrı Durum Modeli (Lifecycle States)
Sistemde her MCP sunucusu için 3 bağımsız durum katmanı bulunur:
- **Yapılandırılmış (Configured):** Sunucu tanımı `config.yaml` içinde kayıtlıdır.
- **Bağlı (Connected):** Sunucu süreci (subprocess) veya HTTP/SSE bağlantısı aktif ve JSON-RPC el sıkışması tamamlanmıştır.
- **Mevcut Sohbet İçin Etkin (Enabled for current chat):** Sunucunun araçları yalnızca o anki sohbet oturumunun (`ChatSession`) dil modeline iletilir. Bir sunucuyu mevcut sohbetten kaldırmak (`×`), sunucunun bağlantısını koparmaz veya genel konfigürasyonundan silmez.

### 2. Kalıcı Asenkron Çalışma Zamanı (`AsyncRuntime`)
Masaüstü Tkinter ana iş parçacığını dondurmamak ve `MCPClient` bağlantılarını geçici döngülere (ephemeral event loops) taşımamak için arka planda uzun ömürlü bir daemon iş parçacığı (`AsyncRuntime`) çalışır:
- Tek ve kalıcı bir `asyncio` event loop barındırır.
- MCP subprocess'leri, HTTP bağlantıları ve otonom `Agent` akış döngüleri bu runtime içinde yaşar.
- Arayüz (Tkinter) yalnızca `root.after()` veya event dispatch ile güvenli şekilde bilgilendirilir. Uygulama kapanırken `runtime.shutdown()` ile tüm alt süreçler zarifçe (graceful termination: SIGTERM -> timeout -> SIGKILL) sonlandırılır.

### 3. Oturum İzolasyonu (`ChatSession` & `SessionToolFilter`)
- Her sohbet bir `ChatSession` örneğidir ve kendi `active_mcp_servers` kümesine sahiptir.
- Global `ToolRegistry` körü körüne mutasyona uğramaz. `SessionToolFilter` katmanı, modele yalnızca:
  1. Yerleşik araçları (builtin),
  2. Aktif eklenti araçlarını (plugins),
  3. O sohbet oturumu için seçilmiş MCP sunucularının araçlarını sunar.
- Model izin verilmeyen bir MCP aracını çağırmaya çalışırsa `SessionToolFilter.execute()` tarafından güvenli şekilde engellenir.

### 4. Bağlantı Havuzu & Referans Sayımı (`MCPConnectionManager`)
- MCP süreçleri her mesaj gönderildiğinde yeniden başlatılmaz.
- `MCPConnectionManager` havuzunda çalışan bağlantılar referans sayımı (`acquire(session_id)` / `release(session_id)`) ile yönetilir.
- Birden fazla sohbet aynı sunucuyu paylaştığında tek bir canlı süreç üzerinden hizmet verilir.

### 5. Güvenli Secret ve Keyring Yönetimi
- Hassas API tokenları (örn. `GITHUB_TOKEN`, Bearer anahtarları) `config.yaml` içine düz metin olarak yazılmaz.
- İşletim sistemi kasasında güvenle saklanır:
  - macOS: Keychain
  - Windows: Credential Manager
  - Linux: Secret Service / KWallet
- `config.yaml` içinde referans URI tutulur (`keyring://mcp/<server>/<key>`).
- Arayüzde `••••••••` şeklinde maskelenir ve loglarda/araç çıktılarında `redact_secrets()` ile sansürlenir.

### 6. Otonom Araç Akışı & Katlanabilir Kartlar (`ToolActivityBubble`)
- Desktop Chat doğrudan FIRIK API çağrısı yerine `FirikService.chat_agent_stream_async()` köprüsünü kullanır.
- Dil modeli araç çağırdığında `tool_call_started`, `tool_call_result`, `tool_call_error` gibi türlenmiş `AgentEvent` olayları fırlatılır.
- Sohbet akışında katlanabilir `ToolActivityBubble` kartları üretilir; parametreler, çalışma süresi ve önizleme kullanıcıya açık ve anlaşılır şekilde sunulur.

### 7. Sorun Giderme (Troubleshooting)
- **Süreç Başlatılamadı (Process failed / FileNotFoundError):**
  Komutun (örn. `npx`, `uvx`) sistem PATH'inde olduğundan ve çalışma dizininin erişilebilir olduğundan emin olun. Gerekirse tam yolu (örn. `/usr/local/bin/npx`) belirtin.
- **Bağlantı Zaman Aşımı (Timeout):**
  Sunucu başlatma betiği uzun sürüyorsa veya ilk indirmeyi yapıyorsa zaman aşımı sürebilir. `MCPView` içindeki **Sına** butonunu kullanarak detaylı hata ve süre analizini görüntüleyin.
- **Taşıma Hataları (Transport error / BrokenPipe):**
  Uzak HTTP/SSE sunucusunun çalıştığını, URL'in doğru olduğunu ve gerekli header/token bilgilerinin girildiğini kontrol edin.
- **Kasa Erişimi (Keyring unavailable):**
  Başsız (headless) veya kilitli anahtarlık durumlarında oturum boyunca geçici güvenli bellek saklama alanı devreye girer.

---

## 🧩 Architecture Overview

```
firik/
├── firik_agent/
│   ├── core/
│   │   ├── agent.py          # Autonomous execution loop & meta-tools
│   │   ├── tools.py          # ToolRegistry & @tool decorator
│   │   └── types.py          # Message, ToolCall, StreamChunk types
│   ├── providers/
│   │   ├── base.py           # BaseProvider abstract class
│   │   ├── firik.py          # Direct FIRIK & LLMTR Gateway provider
│   │   ├── openai_provider.py# OpenAI-compatible provider
│   │   └── registry.py       # ProviderRegistry for dynamic switching
│   ├── mcp/
│   │   ├── client.py         # MCP JSON-RPC 2.0 stdio & HTTP client
│   │   ├── manager.py        # Dynamic MCP server manager & tool mapper
│   │   └── protocol.py       # MCP schemas
│   ├── skills/
│   │   ├── skill.py          # Skill parser & directory serializer
│   │   └── manager.py        # Discovery, activation & prompt injection
│   └── plugins/
│       ├── base.py           # BasePlugin & lifecycle hooks
│       ├── manager.py        # Dynamic loader & hook dispatcher
│       └── builtins/         # logger, calculator, web_search
├── skills/                   # User custom skills
├── plugins/                  # User custom plugins
├── examples/                 # Python scripts & mock MCP server
└── tests/                    # Unit & integration test suite
```

---

## 🐍 Python SDK Usage

### 1. Basic Chat with FIRIK

```python
import asyncio
from firik_agent import Agent

async def main():
    agent = Agent()
    await agent.initialize()

    response = await agent.run("Hello! What capabilities do you have?")
    print(response)

    await agent.close()

asyncio.run(main())
```

### 2. Dynamically Adding an MCP Server

```python
import asyncio
import sys
from firik_agent import Agent

async def main():
    agent = Agent()
    await agent.initialize()

    # Connect an MCP server (e.g. mock server or any stdio/npx tool)
    tools = await agent.mcp.add_server(
        name="system_tools",
        command=sys.executable,
        args=["examples/mock_mcp_server.py"],
    )
    print("Registered MCP tools:", tools)

    # Now the agent can autonomously call text_transformer or get_system_time!
    response = await agent.run("What is the current system time according to system_tools?")
    print(response)

    await agent.close()

asyncio.run(main())
```

### 3. Creating & Activating a Skill Dynamically

```python
import asyncio
from firik_agent import Agent

async def main():
    agent = Agent()
    await agent.initialize()

    # Add a specialized skill
    agent.skills.add_skill(
        name="defense_analyst",
        description="Turkish defense industry analysis specialist",
        instructions="Focus on unmanned systems (UAVs, USVs), sensor fusion, and sovereign algorithms.",
        tags=["defense", "sayzek"],
        activate=True,
    )

    response = await agent.run("Assess the significance of sovereign H200 AI clusters.")
    print(response)

    await agent.close()

asyncio.run(main())
```

### 4. Writing a Custom Plugin with Tools & Hooks

```python
from firik_agent.plugins.base import BasePlugin
from firik_agent.core.types import ToolDefinition

class TranslationPlugin(BasePlugin):
    name = "translator"
    description = "Provides language translation helper"

    def get_tools(self):
        tool_def = ToolDefinition(
            name="translate_phrase",
            description="Translate text to target language",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "target_lang": {"type": "string"},
                },
                "required": ["text", "target_lang"],
            },
            source="plugin:translator",
        )
        return [(tool_def, lambda text, target_lang: f"[{target_lang}] {text}")]

    async def on_user_message(self, text: str):
        print(f"[Audit] User input: {text}")
        return text
```


---

## 🖥️ Cross-Platform Computer Use (GUI Otomasyonu)

Firik Agent, macOS, Windows ve Linux üzerinde gerçek masaüstü kontrolü ve görsel gözlem yeteneklerine sahiptir.

### Mimari

```text
               Computer Use Engine (ComputerUseService)
                               ↓
                        Platform Backend
             ┌─────────────────┬─────────────────┬─────────────────┐
             │      macOS      │     Windows     │      Linux      │
             │ Quartz / AppKit │ Win32 / UIA / DP│ X11 / Wayland   │
             └─────────────────┴─────────────────┴─────────────────┘
                               ↓
                       Tool Adapter Layer
             ┌───────────────────────────────────┬─────────────────┐
             │       Native Agent Tools          │   MCP Server    │
             └───────────────────────────────────┴─────────────────┘
                               ↓
                          Firik Agent
                               ↓
                       Computer Use Skill (Observe → Plan → Act → Verify)
```

### 16 Standart Araç

1. `computer_environment`: İşletim sistemi, ekran sunucusu, ölçek faktörü, izinler ve yetenekler.
2. `computer_get_displays`: Bağlı tüm monitörler, çözünürlükler, ofsetler ve birincil ekran.
3. `computer_screenshot`: Tam ekran, monitör, pencere veya bölge yakalama (Retina/HiDPI ölçeklemeli).
4. `computer_list_windows`: Açık pencereler, başlıklar, koordinatlar ve uygulama adları.
5. `computer_focus_window`: Belirtilen pencereyi öne getirme ve odaklama.
6. `computer_move_pointer`: Fare imlecini mantıksal `(x, y)` koordinatına taşıma.
7. `computer_click`: Tıklama (`left`, `right`, `middle`, `single`, `double`, `triple`).
8. `computer_drag`: Sürükleyip bırakma.
9. `computer_scroll`: Dikey ve yatay tekerlek kaydırma.
10. `computer_type_text`: Odaklanmış alana Unicode metin yazma.
11. `computer_key`: Tekil tuş basma (`Return`, `Escape`, `Tab`, `BackSpace`, vb.).
12. `computer_hotkey`: Kısayol kombinasyonları (`['Command', 'c']` veya `['Ctrl', 'Shift', 'p']`).
13. `computer_get_clipboard`: Sistem panosundan metin okuma.
14. `computer_set_clipboard`: Sistem panosuna metin yazma.
15. `computer_wait`: GUI animasyonları veya sayfa yüklemeleri için bekleme (maks. 30 sn).
16. `computer_accessibility_snapshot`: Erişilebilirlik ve UI öğe ağacı dökümü.

### Multimodal Görsel Akış

- **Vision Modelleri** (örn. `qwen3-vl-30b`, `gpt-4o`): Ekran görüntüleri otomatik olarak Base64 `image_url` formatına dönüştürülüp modelin görsel algısına sunulur.
- **Metin Modelleri** (örn. `glm-5.3`): Bağlam patlamasını önlemek için yalnızca insan tarafından okunabilir metin özeti aktarılır.

### Güvenlik ve Acil Durdurma

- **Hız Sınırı**: Tur başına işlem sayısı (`max_actions_per_turn`) sınırlandırılmıştır.
- **Yasaklı Uygulamalar**: Terminal, Sistem Ayarları, Anahtarlık ve Kayıt Defteri gibi kritik sistem uygulamaları varsayılan olarak engellidir.
- **Tehlikeli İşlem Koruması**: Hesap silme, disk biçimlendirme ve yetkisiz ödeme tetikleyicileri algılanır.
- **Denetim İzi & Gizleme**: Şifreler ve API anahtarları loglarda otomatik olarak `[REDACTED]` ile gizlenir.
- **Acil Durdurma (Emergency STOP)**: Arayüzdeki **ACİL DURDUR** butonu veya `/computer-use stop` komutu tüm işlemleri anında keser.

### CLI Teşhis & Doctor

```bash
# Sistem izinlerini ve yetenek matrisini denetle:
firik-agent computer-use doctor

# Standalone stdio MCP sunucusu başlat:
firik-agent computer-use mcp

# Teşhis ekran görüntüsü kaydet:
firik-agent computer-use screenshot -o test.png
```

Agent REPL içinde:
```text
/computer-use doctor   # Sistem teşhisini göster
/computer-use enable   # Bilgisayar denetimi araçlarını bağla
/computer-use stop     # Acil durdurma tetikle
/computer-use reset    # Durdurma durumunu sıfırla
```

---

## 🧪 Testing

Run the test suite with pytest:

```bash
uv pip install -e . pytest-asyncio
pytest tests/ -v
```

