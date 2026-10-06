"""Discoverable, previewable Git tools with explicit mutation approval.

Commands always use argument arrays. Paths are literal and remain inside the
selected repository; commit references are resolved before they reach a command.
"""
from __future__ import annotations

import copy
import os
import re
import shlex
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


def _field(name: str, label: str, default: Any = "", required: bool = False) -> dict[str, Any]:
    return {"name": name, "label": label, "default": default, "required": required}


def _tool(tool_id: str, title: str, category: str, description: str,
          fields: list[dict[str, Any]] | None = None, risk: str = "read",
          example: str = "") -> dict[str, Any]:
    return {"id": tool_id, "title": title, "category": category,
            "description": description, "fields": fields or [],
            "mutates": risk != "read", "risk": risk, "example": example}


_PATH = _field("path", "Dosya yolu (isteğe bağlı)")
_REF = _field("ref", "Commit veya referans", "HEAD")
_LIMIT = _field("limit", "Sonuç sınırı", 100)
_REMOTE = _field("remote", "Uzak depo", "origin")
_BRANCH = _field("branch", "Dal adı", "", True)
_STASH = _field("stash", "Saklanan değişiklik", "stash@{0}")
_DIFF_FIELDS = [_PATH, _field("staged", "Hazırlanan değişiklikler (true / false)", False)]

_TOOLS = [
    _tool("status", "Çalışma ağacının durumu", "Çalışma alanı", "Hazırlanan, değişen ve yeni dosyaları ve dal takibini gösterir."),
    _tool("diff", "Değişiklikleri incele", "Çalışma alanı", "Dosyanın veya çalışma ağacının gerçek diff çıktısını gösterir.", _DIFF_FIELDS),
    _tool("stage", "Seçili dosyaları hazırla", "Çalışma alanı", "Yalnızca seçtiğiniz yolları sonraki commit için hazırlar.", [_field("paths", "Dosya yolları (her satırda bir yol)", "", True)], "write", "src/app.py\nREADME.md"),
    _tool("unstage", "Seçili dosyaları hazırlıktan çıkar", "Çalışma alanı", "Dosya içeriğine dokunmadan seçilen yolları index'ten çıkarır.", [_field("paths", "Dosya yolları (her satırda bir yol)", "", True)], "write"),
    _tool("commit", "Hazırlanan değişiklikleri kaydet", "Çalışma alanı", "Yalnızca index'teki değişikliklerden commit oluşturur. Git hook'ları çalışabilir.", [_field("message", "Commit mesajı", "", True)], "write"),
    _tool("branch_create", "Yeni dal oluştur", "Dallar", "Seçtiğiniz commit'ten dal oluşturur; çalışma dalını değiştirmez.", [_BRANCH, _REF], "write"),
    _tool("branch_switch", "Dala geç", "Dallar", "Var olan yerel dala geçer; Git, çakışan yerel değişiklikleri korur.", [_BRANCH], "write"),
    _tool("branch_delete", "Birleşmiş yerel dalı kaldır", "Dallar", "Yalnızca tamamen birleşmiş dalı Git'in güvenli -d kontrolüyle kaldırır.", [_BRANCH], "destructive"),
    _tool("merge", "Dalı mevcut dala birleştir", "Dallar", "Temiz çalışma ağacında seçilen referansı birleştirir; çakışmaları çözmek için işlemi açık bırakır.", [_field("ref", "Birleştirilecek dal veya commit", "", True)], "write"),
    _tool("merge_continue", "Birleşmeyi tamamla", "Dallar", "Çakışmalar çözüldükten ve dosyalar hazırlandıktan sonra devam eden merge'ü tamamlar.", risk="write"),
    _tool("merge_abort", "Birleşmeyi iptal et", "Kurtarma", "Devam eden merge'ü iptal edip başlangıç durumuna döner.", risk="destructive"),
    _tool("rebase", "Commit'leri yeni tabana taşı", "Dallar", "Temiz çalışma ağacında mevcut dalın commit'lerini seçilen taban üzerine yeniden uygular; yerel commit kimlikleri değişir.", [_field("ref", "Yeni taban dalı veya commit", "", True)], "write"),
    _tool("rebase_continue", "Taban değişimini sürdür", "Dallar", "Çakışmalar çözülüp dosyalar hazırlandıktan sonra rebase'ü sürdürür.", risk="write"),
    _tool("rebase_abort", "Taban değişimini iptal et", "Kurtarma", "Devam eden rebase'ü iptal ederek eski dala ve commit'lere döner.", risk="destructive"),
    _tool("rebase_skip", "Bu commit'i taban değişiminde atla", "Kurtarma", "Rebase sırasında durulan commit'in değişikliklerini atlayıp sonraki commit'e geçer; atlanan değişiklikler kaybolabilir.", risk="destructive"),
    _tool("branch_compare", "İki dalı karşılaştır", "Dallar", "İki referans arasındaki dosya ve satır değişimlerini gösterir.", [_field("base", "Başlangıç", "HEAD~1"), _field("target", "Hedef", "HEAD")]),
    _tool("branch_merged", "Birleşmiş dalları bul", "Dallar", "Belirtilen commit'e tamamen alınmış yerel dalları listeler.", [_REF]),
    _tool("merge_base", "Ortak atayı bul", "Dallar", "İki dalın ayrıldığı ortak commit'i bulur.", [_field("base", "İlk referans", "HEAD"), _field("target", "İkinci referans", "", True)]),
    _tool("cherry", "Henüz taşınmamış commit'ler", "Dallar", "Diğer dalda eşdeğer yaması bulunan ve bulunmayan commit'leri ayırır.", [_field("upstream", "Karşılaştırılacak dal", "", True), _REF]),
    _tool("range_diff", "İki commit dizisini karşılaştır", "Dallar", "Rebase öncesi ve sonrası commit dizilerindeki değişiklikleri gösterir.", [_field("old_range", "Önceki dizi (A..B)", "", True), _field("new_range", "Yeni dizi (C..D)", "", True)], example="main..feature-old / main..feature"),
    _tool("merge_preview", "Birleşme sonucunu önizle", "Dallar", "Ortak ata üzerinden iki dalın birleşmesini çalışma ağacına dokunmadan gösterir.", [_field("base", "İlk dal", "HEAD"), _field("target", "İkinci dal", "", True)]),
    _tool("fetch", "Uzak değişiklikleri getir", "Uzak depo", "Yapılandırılmış uzak depodan commit ve referansları getirir.", [_REMOTE], "network"),
    _tool("pull_ff", "Dalını güvenle güncelle", "Uzak depo", "Yalnızca fast-forward yapılabiliyorsa günceller; otomatik merge veya rebase oluşturmaz.", [_REMOTE, _BRANCH], "network"),
    _tool("push", "Dalı uzak depoya gönder", "Uzak depo", "Yerel dalı aynı adlı uzak dala normal push ile gönderir.", [_REMOTE, _BRANCH], "network"),
    _tool("remotes", "Uzak depo adresleri", "Uzak depo", "Tanımlı uzak depoların fetch ve push adreslerini gösterir."),
    _tool("stash_save", "Değişiklikleri sakla", "Kurtarma", "İzlenen ve yeni dosyalardaki değişiklikleri isim vererek saklar.", [_field("message", "Saklama açıklaması", "Firik çalışma kaydı")], "write"),
    _tool("stash_list", "Saklanan çalışmaları gör", "Kurtarma", "Stash kayıtlarını tarihleri ve açıklamalarıyla listeler."),
    _tool("stash_show", "Saklanan çalışmayı incele", "Kurtarma", "Seçilen stash'in izlenen ve yeni dosyalarına ait yamayı gösterir.", [_STASH]),
    _tool("stash_apply", "Saklanan çalışmayı uygula", "Kurtarma", "Stash kaydını silmeden değişikliklerini temiz çalışma ağacına uygular.", [_STASH], "write"),
    _tool("stash_pop", "Saklanan çalışmayı geri al", "Kurtarma", "Temiz çalışma ağacına uygular; başarılı olursa seçilen stash kaydını kaldırır.", [_STASH], "destructive"),
    _tool("reflog", "Yerel hareket geçmişi", "Kurtarma", "Dal geçişleri, reset ve rebase gibi yerel hareketlerden önceki commit'leri gösterir.", [_LIMIT]),
    _tool("rescue_branch", "Kaybolan commit'i kurtar", "Kurtarma", "Reflog veya commit kimliğinden yeni dal oluşturur; mevcut dalı değiştirmez.", [_BRANCH, _field("ref", "Kurtarılacak commit veya reflog", "HEAD@{1}", True)], "write"),
    _tool("restore_file", "Dosyanın yerel değişikliklerini geri al", "Kurtarma", "Tek dosyanın çalışma kopyasını seçilen commit ile değiştirir; index korunur. Yerel içerik kaybolabilir.", [_field("path", "Tek dosya yolu", "", True), _REF], "destructive"),
    _tool("revert", "Commit'i ters commit ile geri al", "Kurtarma", "Temiz çalışma ağacında seçilen commit'in tersini yeni commit olarak uygular.", [_field("ref", "Geri alınacak commit", "", True)], "write"),
    _tool("revert_abort", "Geri alma işlemini iptal et", "Kurtarma", "Devam eden revert işlemini iptal edip başlangıç durumuna döner.", risk="destructive"),
    _tool("revert_continue", "Commit geri almayı tamamla", "Kurtarma", "Çakışmalar çözüldükten ve dosyalar hazırlandıktan sonra revert'ü sürdürür.", risk="write"),
    _tool("cherry_pick", "Commit'i bu dala taşı", "Dallar", "Temiz çalışma ağacında seçtiğiniz commit'i mevcut dala uygular.", [_field("ref", "Taşınacak commit", "", True)], "write"),
    _tool("cherry_pick_abort", "Commit taşıma işlemini iptal et", "Kurtarma", "Devam eden cherry-pick işlemini iptal eder.", risk="destructive"),
    _tool("cherry_pick_continue", "Commit taşımayı tamamla", "Dallar", "Çakışmalar çözüldükten ve dosyalar hazırlandıktan sonra cherry-pick'i sürdürür.", risk="write"),
    _tool("tag_create", "Sürüm etiketi oluştur", "Sürümler", "Seçilen commit'e açıklamalı yerel etiket ekler; uzak depoya göndermez.", [_field("tag", "Etiket adı", "", True), _REF, _field("message", "Etiket açıklaması", "", True)], "write"),
    _tool("describe", "En yakın sürümü bul", "Sürümler", "Commit'in en yakın etikete uzaklığını ve kısa kimliğini gösterir.", [_REF]),
    _tool("worktree_list", "Paralel çalışma alanları", "Çalışma alanı", "Bu depoya bağlı worktree'leri, dalları ve commit'leri listeler."),
    _tool("worktree_add", "Paralel çalışma alanı aç", "Çalışma alanı", "Depo içindeki yeni bir klasöre bağımsız worktree ve yeni dal oluşturur.", [_field("directory", "Yeni klasör (depo içinde)", "", True), _BRANCH, _REF], "write"),
    _tool("bisect_start", "Hatayı getiren commit'i ara", "Hata avı", "Temiz ağaçta bilinen iyi ve kötü commit arasında ikili aramayı başlatır; commit'ler arası geçiş yapar.", [_field("bad", "Hatalı commit", "HEAD"), _field("good", "Çalışan commit", "", True)], "write"),
    _tool("bisect_good", "Bu commit çalışıyor", "Hata avı", "Mevcut commit'i iyi olarak işaretler ve sıradaki deneme commit'ine geçer.", risk="write"),
    _tool("bisect_bad", "Bu commit hatalı", "Hata avı", "Mevcut commit'i kötü olarak işaretler ve aramayı daraltır.", risk="write"),
    _tool("bisect_reset", "Hata aramasını bitir", "Hata avı", "Bisect oturumunu bitirip başlangıç dalına geri döner.", risk="write"),
    _tool("bisect_status", "Hata aramasının kaydı", "Hata avı", "Bisect sırasında verilen iyi / kötü kararlarının yeniden oynatılabilir kaydını gösterir."),
    _tool("pickaxe", "Bir metin ne zaman değişti", "Geçmiş", "Belirttiğiniz metnin eklenip çıkarıldığı commit'leri yamasıyla bulur.", [_field("text", "Aranacak metin", "", True), _PATH, _LIMIT], example="DEFAULT_RERANK_MODEL"),
    _tool("regex_history", "Desenin değişim geçmişi", "Geçmiş", "Regex ile eşleşen satırları değiştiren commit'leri yamasıyla bulur.", [_field("pattern", "Git regex deseni", "", True), _PATH, _LIMIT]),
    _tool("line_history", "Satırların evrimini izle", "Geçmiş", "Dosyanın seçilen satır aralığının commit'ler boyunca nasıl değiştiğini gösterir.", [_field("path", "Dosya yolu", "", True), _field("start", "İlk satır", 1), _field("end", "Son satır", 20), _REF, _LIMIT]),
    _tool("file_history", "Dosyanın tüm geçmişi", "Geçmiş", "Dosyanın isim değişikliklerini takip ederek kimin ne değiştirdiğini gösterir.", [_field("path", "Dosya yolu", "", True), _LIMIT]),
    _tool("blame_moved", "Satırı son kim değiştirdi", "Geçmiş", "Dosya içi taşımaları ve dosyalar arası kopyalamaları izleyerek satırların yazarını bulur.", [_field("path", "Dosya yolu", "", True), _field("start", "İlk satır (0 = tüm dosya)", 0), _field("end", "Son satır (0 = tüm dosya)", 0), _REF]),
    _tool("log_search", "Commit mesajında ara", "Geçmiş", "Commit açıklamalarında büyük / küçük harften bağımsız düz metin arar.", [_field("text", "Mesajda aranacak metin", "", True), _LIMIT]),
    _tool("commit_show", "Commit'in tam değişikliği", "Geçmiş", "Seçilen commit'in bilgilerini, dosya özetini ve yamasını gösterir.", [_REF]),
    _tool("word_diff", "Kelime kelime karşılaştır", "İnceleme", "Satır içindeki eklenen ve çıkarılan kelimeleri okunur biçimde gösterir.", _DIFF_FIELDS),
    _tool("moved_diff", "Taşınan kodu bul", "İnceleme", "Taşınan kodu tespit eden Git renk işaretleriyle diff çıktısını gösterir.", _DIFF_FIELDS),
    _tool("check_ignore", "Bu dosya neden yok sayılıyor", "İnceleme", "Dosyayı yok sayan ignore kuralını, kaynak dosyasını ve satırını gösterir.", [_field("path", "Dosya yolu", "", True)]),
    _tool("attributes", "Dosyanın Git özellikleri", "İnceleme", "Dosyanın LFS, satır sonu ve diff davranışını belirleyen attributes kurallarını gösterir.", [_field("path", "Dosya yolu", "", True)]),
    _tool("tracked_files", "Git'in izlediği dosyalar", "İnceleme", "İzlenen dosyaları index durumlarıyla listeler.", [_PATH]),
    _tool("conflict_stages", "Çakışmanın üç sürümü", "İnceleme", "Çakışan dosyaların ortak ata, bizim ve karşı tarafın index sürümlerini gösterir.", [_PATH]),
    _tool("rerere_status", "Kaydedilen çakışma çözümleri", "İnceleme", "Git rerere'nin izlediği çakışma çözümü kayıtlarını listeler."),
    _tool("rerere_remaining", "Elle çözülmesi gereken çakışmalar", "İnceleme", "Rerere ile henüz otomatik çözülemeyen dosyaları gösterir."),
    _tool("rerere_diff", "Çakışma çözümünün farkı", "İnceleme", "Çakışmanın ilk hali ile mevcut çözüm arasındaki farkı gösterir."),
    _tool("diff_check", "Yama kalitesini kontrol et", "İnceleme", "Boşluk hatalarını ve kalan çakışma işaretlerini değişikliklerde arar.", [_field("staged", "Hazırlanan değişiklikler (true / false)", False)]),
    _tool("submodule_status", "Alt depo sürümleri", "İnceleme", "Submodule commit'lerini ve beklenen sürümden sapmaları gösterir."),
    _tool("sparse_checkout_status", "Kısmi checkout yolları", "İnceleme", "Sparse checkout açıksa çalışma ağacına alınan klasörleri gösterir."),
    _tool("clean_preview", "Temizlenecek dosyaları önizle", "İnceleme", "Git'in izlemediği dosya ve klasörleri kuru çalıştırmada listeler; hiçbirini silmez."),
    _tool("fsck", "Nesne bütünlüğünü incele", "Depo sağlığı", "Commit bağlantılarını ve reflog dışında kalan ulaşılmaz nesneleri kontrol eder."),
    _tool("health", "Depo boyutu ve nesne sağlığı", "Depo sağlığı", "Gevşek ve paketlenmiş nesnelerin sayısını, disk boyutunu ve atık dosyaları gösterir."),
    _tool("bundle_create", "Taşınabilir Git yedeği", "Dışa aktarma", "Tüm referansları içeren ve başka bilgisayarda clone edilebilen yeni bundle üretir.", [_field("output", "Yeni .bundle dosyası (depo içinde)", "firik-backup.bundle", True)], "write"),
    _tool("archive_zip", "Kaynakları ZIP olarak dışa aktar", "Dışa aktarma", "Seçilen commit'teki izlenen dosyaları Git geçmişi olmadan yeni ZIP'e aktarır.", [_field("output", "Yeni .zip dosyası (depo içinde)", "firik-source.zip", True), _REF], "write"),
]


class GitOperationsMixin:
    """Tool catalogue used by both the desktop Git workbench and coding agent."""

    _OPS_OUTPUT_LIMIT = 192 * 1024
    _OPS_SPOOL_LIMIT = 16 * 1024 * 1024
    _OPS_GLOBAL_ARGS = ["--no-pager", "-c", "color.ui=false"]

    @classmethod
    def _ops_global_args(cls, args: list[str]) -> list[str]:
        # check-ignore does not support literal pathspec magic. Stash also calls
        # check-ignore/clean internally, so propagating this switch breaks cleanup.
        paths_commands = {"add", "rm", "restore", "diff", "log", "show", "ls-files", "blame"}
        return cls._OPS_GLOBAL_ARGS + (["--literal-pathspecs"] if args and args[0] in paths_commands else [])

    @classmethod
    def get_tool_catalog(cls) -> list[dict[str, Any]]:
        return copy.deepcopy(_TOOLS)

    @staticmethod
    def _ops_redact(text: str) -> str:
        """Prevent credentials embedded in remote URLs or Git output reaching chat."""
        text = re.sub(r"(?i)\b((?:https?|ssh|git|ftp)://)[^\s/@]+@", r"\1***@", text)
        text = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})\b", "[REDACTED_TOKEN]", text)
        return re.sub(r"\bsk-[A-Za-z0-9_-]{20,}\b", "[REDACTED_TOKEN]", text)

    @classmethod
    def _ops_run(cls, path: str | Path, args: list[str], timeout: float = 30,
                 output_limit: int | None = None) -> dict[str, Any]:
        """Bound returned output and execution time without buffering full output."""
        env = os.environ.copy()
        for key in list(env):
            if key in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                       "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
                       "GIT_PREFIX", "GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS",
                       "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS"} or key.startswith("GIT_CONFIG_"):
                env.pop(key, None)
        env.update({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
                    "GIT_PAGER": "cat", "GIT_EDITOR": ":", "GIT_ASKPASS": ""})
        timed_out = False
        overflow = False
        try:
            with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
                proc = subprocess.Popen(["git", *cls._ops_global_args(args), *args], cwd=str(path),
                                        env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                        start_new_session=os.name != "nt")
                deadline = time.monotonic() + timeout
                while proc.poll() is None:
                    overflow = (os.fstat(out.fileno()).st_size > cls._OPS_SPOOL_LIMIT
                                or os.fstat(err.fileno()).st_size > cls._OPS_SPOOL_LIMIT)
                    timed_out = time.monotonic() >= deadline
                    if not overflow and not timed_out:
                        try:
                            proc.wait(timeout=min(0.1, max(0.001, deadline - time.monotonic())))
                        except subprocess.TimeoutExpired:
                            pass
                        continue
                    if os.name != "nt":
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        proc.kill()
                    proc.wait(timeout=5)
                    break
                overflow = overflow or (os.fstat(out.fileno()).st_size > cls._OPS_SPOOL_LIMIT
                                        or os.fstat(err.fileno()).st_size > cls._OPS_SPOOL_LIMIT)
                out.seek(0)
                err.seek(0)
                output_limit = cls._OPS_OUTPUT_LIMIT if output_limit is None else output_limit
                stdout = out.read(output_limit + 1)
                stderr = err.read(output_limit + 1)
                truncated = len(stdout) > output_limit or len(stderr) > output_limit
                stdout_text = stdout[:output_limit].decode("utf-8", errors="replace")
                stderr_text = stderr[:output_limit].decode("utf-8", errors="replace")
                if timed_out:
                    stderr_text += f"\nGit işlemi {timeout:g} saniyelik süre sınırına ulaştı."
                if overflow:
                    stderr_text += "\nGit çıktısı güvenli dosya boyutu sınırını aştı; sonuçlar kısaltıldı."
                return {"ok": proc.returncode == 0 and not timed_out and not overflow,
                        "returncode": 124 if timed_out else (125 if overflow else proc.returncode),
                        "stdout": stdout_text, "stderr": stderr_text, "truncated": truncated}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "returncode": 1, "stdout": "", "stderr": str(exc), "truncated": False}

    @classmethod
    def _ops_repo(cls, path: str | Path) -> Path:
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("Çalışma klasörü bulunamadı.")
        result = cls._ops_run(root, ["rev-parse", "--show-toplevel"], output_limit=16_384)
        if not result["ok"] or not result["stdout"].strip():
            raise ValueError("Seçilen klasör bir Git çalışma ağacı değil.")
        return Path(result["stdout"].strip()).resolve()

    @staticmethod
    def _ops_text(value: Any, label: str, required: bool = True) -> str:
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise ValueError(f"{label}: metin bekleniyor.")
        value = str(value)
        if "\x00" in value or len(value) > 16_384:
            raise ValueError(f"{label}: geçersiz veya çok uzun değer.")
        if required and not value.strip():
            raise ValueError(f"{label} gerekli.")
        return value

    @classmethod
    def _ops_ref(cls, root: Path, value: Any) -> str:
        ref = cls._ops_text(value, "Referans").strip()
        if ref.startswith("-") or any(c in ref for c in "\n\r\x00"):
            raise ValueError("Geçersiz Git referansı.")
        result = cls._ops_run(root, ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"], output_limit=256)
        commit = result["stdout"].strip()
        if not result["ok"] or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            raise ValueError(f"Commit referansı çözülemedi: {ref}")
        return commit

    @classmethod
    def _ops_branch(cls, root: Path, value: Any, *, tag: bool = False) -> str:
        branch = cls._ops_text(value, "Etiket" if tag else "Dal").strip()
        if branch.startswith("-") or "\n" in branch or "\r" in branch:
            raise ValueError("Geçersiz dal veya etiket adı.")
        args = ["check-ref-format", f"refs/tags/{branch}"] if tag else ["check-ref-format", "--branch", branch]
        if not cls._ops_run(root, args)["ok"] or branch == "HEAD" or branch.startswith("@"):
            raise ValueError("Geçersiz dal veya etiket adı.")
        return branch

    @classmethod
    def _ops_path(cls, root: Path, value: Any, *, required: bool = True,
                  new: bool = False, single_file: bool = False) -> str:
        raw = cls._ops_text(value, "Dosya yolu", required=required)
        if not raw and not required:
            return ""
        candidate = Path(raw).expanduser()
        candidate = candidate if candidate.is_absolute() else root / candidate
        resolved = candidate.resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("Dosya yolu seçilen deponun içinde olmalı.") from exc
        if ".git" in relative.parts:
            raise ValueError("Git yönetim dosyaları araçlarla değiştirilemez.")
        # .git may be a symlink or a linked-worktree pointer to an unusually
        # named metadata directory. Protect its real target and common directory.
        metadata = (root / ".git").resolve()
        if metadata.is_file():
            with metadata.open("r", encoding="utf-8", errors="replace") as pointer:
                gitdir = pointer.read(16_384).strip()
            if gitdir.startswith("gitdir:"):
                target = Path(gitdir[7:].strip())
                metadata = (target if target.is_absolute() else root / target).resolve()
        reserved = [metadata]
        common_pointer = metadata / "commondir"
        if metadata.is_dir() and common_pointer.is_file():
            with common_pointer.open("r", encoding="utf-8", errors="replace") as pointer:
                common = Path(pointer.read(16_384).strip())
            reserved.append((common if common.is_absolute() else metadata / common).resolve())
        if any(resolved == directory or directory in resolved.parents for directory in reserved):
            raise ValueError("Git yönetim dosyaları araçlarla değiştirilemez.")
        if single_file and (resolved.is_dir() or not relative.parts):
            raise ValueError("Bu işlem için tek bir dosya yolu seçin.")
        if new and (candidate.exists() or candidate.is_symlink()):
            raise ValueError("Çıktı yolu zaten var; mevcut dosya veya klasörün üzerine yazılmaz.")
        if new and not candidate.parent.is_dir():
            raise ValueError("Çıktının üst klasörü mevcut olmalı.")
        # Preserve symlink names inside the repository, while rejecting escapes.
        lexical = Path(os.path.abspath(str(candidate)))
        try:
            lexical_relative = lexical.relative_to(root)
            if ".git" in lexical_relative.parts:
                raise ValueError("Git yönetim dosyaları araçlarla değiştirilemez.")
            return lexical_relative.as_posix() or "."
        except ValueError as exc:
            raise ValueError("Dosya yolu seçilen deponun içinde olmalı.") from exc

    @classmethod
    def _ops_paths(cls, root: Path, value: Any) -> list[str]:
        paths = value.splitlines() if isinstance(value, str) else value
        if not isinstance(paths, (list, tuple)) or not paths or len(paths) > 500:
            raise ValueError("En az bir dosya yolu gerekli (en fazla 500).")
        return list(dict.fromkeys(cls._ops_path(root, item) for item in paths))

    @staticmethod
    def _ops_integer(value: Any, label: str, minimum: int = 1, maximum: int = 1000) -> int:
        try:
            number = int(str(value))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{label}: tam sayı bekleniyor.") from exc
        if not minimum <= number <= maximum:
            raise ValueError(f"{label}: {minimum} ile {maximum} arasında olmalı.")
        return number

    @staticmethod
    def _ops_bool(value: Any) -> bool:
        if value in (True, 1, "1", "true", "True", "yes", "evet"):
            return True
        if value in (False, 0, "0", "false", "False", "no", "hayır", ""):
            return False
        raise ValueError("true veya false kullanın.")

    @classmethod
    def _ops_range(cls, root: Path, value: Any) -> str:
        raw = cls._ops_text(value, "Commit dizisi").strip()
        if raw.count("..") != 1 or "..." in raw:
            raise ValueError("Commit dizisini A..B biçiminde yazın.")
        start, end = raw.split("..")
        return f"{cls._ops_ref(root, start)}..{cls._ops_ref(root, end)}"

    @classmethod
    def _ops_stash(cls, root: Path, value: Any) -> str:
        stash = cls._ops_text(value, "Stash").strip()
        if not re.fullmatch(r"stash@\{\d{1,5}\}", stash):
            raise ValueError("Stash kaydını stash@{0} biçiminde seçin.")
        cls._ops_ref(root, stash)
        return stash

    @classmethod
    def _ops_remote(cls, root: Path, value: Any) -> str:
        remote = cls._ops_text(value, "Uzak depo").strip()
        result = cls._ops_run(root, ["remote"])
        if remote.startswith("-") or remote not in result["stdout"].splitlines():
            raise ValueError("Önceden yapılandırılmış bir uzak depo seçin.")
        return remote

    @classmethod
    def _ops_args(cls, root: Path, tool_id: str, p: dict[str, Any]) -> list[str]:
        path = lambda: cls._ops_path(root, p.get("path", ""), required=False)
        with_path = lambda args: args + (["--", path()] if path() else [])
        limit = lambda: cls._ops_integer(p.get("limit", 100), "Sonuç sınırı")
        ref = lambda: cls._ops_ref(root, p.get("ref", "HEAD"))
        if tool_id == "status":
            return ["status", "--short", "--branch", "--untracked-files=normal"]
        if tool_id == "stage":
            return ["add", "--", *cls._ops_paths(root, p["paths"])]
        if tool_id == "unstage":
            paths = cls._ops_paths(root, p["paths"])
            if cls._ops_run(root, ["rev-parse", "--verify", "HEAD"])["ok"]:
                return ["restore", "--staged", "--", *paths]
            return ["rm", "--cached", "--", *paths]
        if tool_id == "commit":
            return ["commit", "-m", cls._ops_text(p["message"], "Commit mesajı")]
        if tool_id in {"branch_create", "rescue_branch"}:
            return ["branch", cls._ops_branch(root, p["branch"]), ref()]
        if tool_id == "branch_switch":
            branch = cls._ops_branch(root, p["branch"])
            check = cls._ops_run(root, ["show-ref", "--verify", f"refs/heads/{branch}"])
            if not check["ok"]:
                raise ValueError("Geçiş yapılacak yerel dal bulunamadı.")
            return ["switch", "--", branch]
        if tool_id == "branch_delete":
            return ["branch", "-d", "--", cls._ops_branch(root, p["branch"])]
        if tool_id == "merge":
            return ["merge", "--no-edit", ref()]
        if tool_id in {"merge_continue", "merge_abort"}:
            return ["merge", "--continue" if tool_id == "merge_continue" else "--abort"]
        if tool_id == "rebase":
            return ["rebase", "--", ref()]
        if tool_id in {"rebase_continue", "rebase_abort", "rebase_skip"}:
            return ["rebase", "--" + tool_id.split("_", 1)[1]]
        if tool_id == "branch_compare":
            return ["diff", "--no-ext-diff", "--no-textconv", "--stat", cls._ops_ref(root, p["base"]), cls._ops_ref(root, p["target"]), "--"]
        if tool_id == "branch_merged":
            return ["branch", "--merged", ref()]
        if tool_id == "merge_base":
            return ["merge-base", cls._ops_ref(root, p["base"]), cls._ops_ref(root, p["target"])]
        if tool_id == "cherry":
            return ["cherry", "-v", cls._ops_ref(root, p["upstream"]), ref()]
        if tool_id == "range_diff":
            return ["range-diff", "--no-color", cls._ops_range(root, p["old_range"]), cls._ops_range(root, p["new_range"])]
        if tool_id == "merge_preview":
            first, second = cls._ops_ref(root, p["base"]), cls._ops_ref(root, p["target"])
            base = cls._ops_run(root, ["merge-base", first, second])
            if not base["ok"]:
                raise ValueError("İki referans arasında ortak ata bulunamadı.")
            return ["merge-tree", base["stdout"].strip().splitlines()[0], first, second]
        if tool_id == "fetch":
            return ["fetch", "--", cls._ops_remote(root, p["remote"])]
        if tool_id == "pull_ff":
            return ["pull", "--ff-only", "--no-rebase", "--", cls._ops_remote(root, p["remote"]), cls._ops_branch(root, p["branch"])]
        if tool_id == "push":
            branch = cls._ops_branch(root, p["branch"])
            if not cls._ops_run(root, ["show-ref", "--verify", f"refs/heads/{branch}"])["ok"]:
                raise ValueError("Gönderilecek yerel dal bulunamadı.")
            return ["push", "--", cls._ops_remote(root, p["remote"]), f"refs/heads/{branch}:refs/heads/{branch}"]
        if tool_id == "remotes":
            return ["remote", "-v"]
        if tool_id == "stash_save":
            return ["stash", "push", "--include-untracked", "-m", cls._ops_text(p["message"], "Saklama açıklaması")]
        if tool_id == "stash_list":
            return ["stash", "list", "--date=iso-local"]
        if tool_id == "stash_show":
            return ["stash", "show", "--include-untracked", "--no-ext-diff", "--no-textconv", "--patch", cls._ops_stash(root, p["stash"])]
        if tool_id in {"stash_apply", "stash_pop"}:
            return ["stash", "apply" if tool_id == "stash_apply" else "pop", cls._ops_stash(root, p["stash"])]
        if tool_id == "reflog":
            return ["reflog", "show", "--date=iso", f"--max-count={limit()}"]
        if tool_id == "restore_file":
            return ["restore", "--worktree", f"--source={ref()}", "--", cls._ops_path(root, p["path"], single_file=True)]
        if tool_id in {"revert", "cherry_pick"}:
            return ["revert" if tool_id == "revert" else "cherry-pick", "--no-edit", ref()]
        if tool_id in {"revert_abort", "cherry_pick_abort", "revert_continue", "cherry_pick_continue"}:
            return ["revert" if tool_id.startswith("revert_") else "cherry-pick", "--" + tool_id.rsplit("_", 1)[1]]
        if tool_id == "tag_create":
            return ["tag", "--annotate", "--message", cls._ops_text(p["message"], "Etiket açıklaması"), "--", cls._ops_branch(root, p["tag"], tag=True), ref()]
        if tool_id == "describe":
            return ["describe", "--tags", "--always", "--long", ref()]
        if tool_id == "worktree_list":
            return ["worktree", "list", "--porcelain"]
        if tool_id == "worktree_add":
            directory = cls._ops_path(root, p["directory"], new=True)
            return ["worktree", "add", "-b", cls._ops_branch(root, p["branch"]), "--", str(root / directory), ref()]
        if tool_id == "bisect_start":
            bad, good = cls._ops_ref(root, p["bad"]), cls._ops_ref(root, p["good"])
            if bad == good or not cls._ops_run(root, ["merge-base", "--is-ancestor", good, bad])["ok"]:
                raise ValueError("İyi commit, kötü commit'in farklı ve önceki bir atası olmalı.")
            return ["bisect", "start", bad, good]
        if tool_id in {"bisect_good", "bisect_bad", "bisect_reset", "bisect_status"}:
            return ["bisect", {"bisect_good": "good", "bisect_bad": "bad", "bisect_reset": "reset", "bisect_status": "log"}[tool_id]]
        if tool_id in {"pickaxe", "regex_history"}:
            option = "-S" if tool_id == "pickaxe" else "-G"
            term = cls._ops_text(p["text" if tool_id == "pickaxe" else "pattern"], "Arama deseni")
            return with_path(["log", f"--max-count={limit()}", "--no-ext-diff", "--no-textconv", "--format=fuller", "--patch", option, term])
        if tool_id == "line_history":
            filename = cls._ops_path(root, p["path"], single_file=True)
            start = cls._ops_integer(p["start"], "İlk satır", maximum=10_000_000)
            end = cls._ops_integer(p["end"], "Son satır", minimum=start, maximum=10_000_000)
            return ["log", f"--max-count={limit()}", "--no-ext-diff", "--no-textconv", "--format=fuller", "-L", f"{start},{end}:{filename}", ref()]
        if tool_id == "file_history":
            return ["log", "--follow", f"--max-count={limit()}", "--format=fuller", "--name-status", "--", cls._ops_path(root, p["path"], single_file=True)]
        if tool_id == "blame_moved":
            start = cls._ops_integer(p["start"], "İlk satır", minimum=0, maximum=10_000_000)
            end = cls._ops_integer(p["end"], "Son satır", minimum=0, maximum=10_000_000)
            if (start == 0) != (end == 0) or (start and end < start):
                raise ValueError("Satır aralığını birlikte girin; son satır ilk satırdan küçük olamaz.")
            return ["blame", "--no-textconv", "-M", "-C", "--date=iso", *(["-L", f"{start},{end}"] if start else []), ref(), "--", cls._ops_path(root, p["path"], single_file=True)]
        if tool_id == "log_search":
            return ["log", "--all", "--fixed-strings", "--regexp-ignore-case", "--format=fuller", f"--max-count={limit()}", "--grep", cls._ops_text(p["text"], "Arama metni")]
        if tool_id == "commit_show":
            return ["show", "--no-ext-diff", "--no-textconv", "--format=fuller", "--stat", "--patch", ref(), "--"]
        if tool_id in {"diff", "word_diff", "moved_diff"}:
            args = ["diff", "--no-ext-diff", "--no-textconv"]
            if cls._ops_bool(p.get("staged", False)):
                args.append("--cached")
            if tool_id == "word_diff":
                args.append("--word-diff=plain")
            if tool_id == "moved_diff":
                args.extend(["--color=always", "--color-moved=zebra", "--color-moved-ws=allow-indentation-change"])
            return with_path(args)
        if tool_id == "check_ignore":
            return ["check-ignore", "--verbose", "--no-index", "--", cls._ops_path(root, p["path"])]
        if tool_id == "attributes":
            return ["check-attr", "--all", "--", cls._ops_path(root, p["path"])]
        if tool_id == "tracked_files":
            return with_path(["ls-files", "--stage"])
        if tool_id == "conflict_stages":
            return with_path(["ls-files", "--unmerged"])
        if tool_id in {"rerere_status", "rerere_remaining", "rerere_diff"}:
            return ["rerere", tool_id.split("_", 1)[1]]
        if tool_id == "diff_check":
            return ["diff", "--no-ext-diff", "--no-textconv", "--check", *(["--cached"] if cls._ops_bool(p.get("staged", False)) else [])]
        if tool_id == "submodule_status":
            return ["submodule", "status", "--recursive"]
        if tool_id == "sparse_checkout_status":
            return ["sparse-checkout", "list"]
        if tool_id == "clean_preview":
            return ["clean", "--dry-run", "-d"]
        if tool_id == "fsck":
            return ["fsck", "--no-reflogs", "--connectivity-only", "--unreachable", "--no-progress"]
        if tool_id == "health":
            return ["count-objects", "-vH"]
        if tool_id == "bundle_create":
            output = cls._ops_path(root, p["output"], new=True, single_file=True)
            return ["bundle", "create", str(root / output), "--all"]
        if tool_id == "archive_zip":
            output = cls._ops_path(root, p["output"], new=True, single_file=True)
            return ["archive", "--format=zip", f"--output={root / output}", ref()]
        raise ValueError("Bilinmeyen Git aracı.")

    @classmethod
    def _ops_plan(cls, path: str | Path, tool_id: str,
                  params: dict[str, Any] | None) -> tuple[Path, dict[str, Any]]:
        tool = next((item for item in _TOOLS if item["id"] == tool_id), None)
        if tool is None:
            raise ValueError("Bilinmeyen Git aracı.")
        root = cls._ops_repo(path)
        if params is not None and not isinstance(params, dict):
            raise ValueError("Araç parametreleri bir nesne olmalı.")
        params = params or {}
        fields = {item["name"]: item for item in tool["fields"]}
        if set(params) - set(fields):
            raise ValueError("Araç tanımında bulunmayan parametre kullanıldı.")
        values = {name: params.get(name, field["default"]) for name, field in fields.items()}
        for name, field in fields.items():
            if field["required"] and (values[name] is None or values[name] == "" or values[name] == []):
                raise ValueError(f"{field['label']} gerekli.")
        args = cls._ops_args(root, tool_id, values)
        targets = {}
        if tool_id in {"stash_show", "stash_apply", "stash_pop"}:
            targets["stash"] = cls._ops_ref(root, values["stash"])
        warnings = []
        if tool["risk"] == "destructive":
            warnings.append("Bu işlem yerel içerik veya kurtarma kaydını kaldırabilir; komutu ve hedefi inceleyin.")
        if tool["risk"] == "network":
            warnings.append("Uzak depoyla iletişim kurar; yerel veya uzak referanslar güncellenebilir.")
        if tool_id in {"revert", "cherry_pick", "merge", "rebase", "stash_apply", "stash_pop", "bisect_start", "pull_ff"}:
            warnings.append("İşlemden önce çalışma ağacı ve index temiz olmalı.")
        if tool_id in {"bisect_start", "bisect_good", "bisect_bad", "bisect_reset"}:
            warnings.append("Bu işlem incelenen commit'i ve çalışma ağacını değiştirebilir.")
        if tool_id == "moved_diff":
            warnings.append("Taşınan blok işaretleri ANSI renk kodlarıyla döndürülür.")
        command = shlex.join(["git", "-C", str(root), *cls._ops_global_args(args), *args])
        return root, {"id": tool_id, "title": tool["title"], "description": tool["description"],
                      "command": command, "args": args, "mutates": tool["mutates"],
                      "risk": tool["risk"], "requires_confirmation": tool["mutates"], "warnings": warnings,
                      "targets": targets}

    @classmethod
    def preview_tool(cls, path: str | Path, tool_id: str,
                     params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Validate the request and return exactly what an execution would run."""
        try:
            _, plan = cls._ops_plan(path, tool_id, params)
            return {"ok": True, **plan}
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return {"ok": False, "id": tool_id, "error": cls._ops_redact(str(exc)), "command": "", "args": [],
                    "warnings": [], "requires_confirmation": False}

    @classmethod
    def _ops_conflicts(cls, root: Path) -> list[str]:
        result = cls._ops_run(root, ["diff", "--name-only", "--diff-filter=U", "-z", "--"])
        return [name for name in result["stdout"].split("\x00") if name] if result["ok"] else []

    @classmethod
    def execute_tool(cls, path: str | Path, tool_id: str, params: dict[str, Any] | None = None,
                     confirmed: bool = False, expected_command: str | None = None,
                     expected_targets: dict[str, str] | None = None) -> dict[str, Any]:
        """Run a validated tool; every mutation needs explicit application approval."""
        command = ""
        root = None
        try:
            root, plan = cls._ops_plan(path, tool_id, params)
            command = plan["command"]
            if plan["requires_confirmation"] and confirmed is not True:
                raise ValueError("Bu Git işlemi için komut önizlemesinden sonra açık onay gerekli.")
            if expected_command is not None and expected_command != command:
                raise ValueError("Onaylanan komut değişti. Referans veya hedef güncellenmiş olabilir; yeni önizlemeyi inceleyip tekrar onaylayın.")
            if expected_targets is not None and expected_targets != plan["targets"]:
                raise ValueError("Onaylanan hedef değişti. Stash sırası güncellenmiş olabilir; yeni önizlemeyi inceleyip tekrar onaylayın.")
            if tool_id in {"revert", "cherry_pick", "merge", "rebase", "stash_apply", "stash_pop", "bisect_start", "bisect_good", "bisect_bad", "bisect_reset", "pull_ff"}:
                state = cls._ops_run(root, ["status", "--porcelain=v1", "-z", "--untracked-files=normal"])
                if not state["ok"]:
                    raise ValueError(state["stderr"] or "Çalışma ağacının durumu okunamadı.")
                if state["stdout"]:
                    raise ValueError("Çalışma ağacı temiz değil. Önce değişiklikleri commit veya stash ile koruyun.")
            if tool_id == "commit":
                staged = cls._ops_run(root, ["diff", "--cached", "--quiet", "--exit-code", "--"])
                if staged["returncode"] == 0:
                    raise ValueError("Commit için hazırlanan değişiklik yok.")
                if staged["returncode"] != 1:
                    raise ValueError(staged["stderr"] or "Index okunamadı.")
            if tool_id in {"revert", "cherry_pick", "branch_switch", "bisect_start", "stash_apply", "stash_pop", "pull_ff", "merge", "rebase", "merge_continue", "rebase_continue", "cherry_pick_continue", "revert_continue"}:
                if cls._ops_conflicts(root):
                    raise ValueError("Çözülmemiş çakışmalar var; önce mevcut işlemi tamamlayın veya iptal edin.")
            result = cls._ops_run(root, plan["args"], timeout=60 if plan["risk"] == "network" else 30)
            # Exit 1 from check-ignore is a normal negative match, not an error.
            if tool_id == "check_ignore" and result["returncode"] == 1 and not result["stderr"]:
                result["ok"] = True
                result["stdout"] = "Bu yol hiçbir ignore kuralıyla eşleşmiyor.\n"
            if tool_id == "sparse_checkout_status" and not result["ok"] and "not sparse" in result["stderr"].lower():
                result["ok"] = True
                result["returncode"] = 0
                result["stderr"] = ""
                result["stdout"] = "Bu depoda sparse checkout etkin değil.\n"
            result["stdout"] = cls._ops_redact(result["stdout"])
            result["stderr"] = cls._ops_redact(result["stderr"])
            return {**result, "command": command, "conflicts": cls._ops_conflicts(root),
                    "requires_confirmation": False}
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return {"ok": False, "command": command, "stdout": "", "stderr": cls._ops_redact(str(exc)), "returncode": 1,
                    "conflicts": cls._ops_conflicts(root) if root is not None else [], "truncated": False,
                    "requires_confirmation": "açık onay" in str(exc)}
