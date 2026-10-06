"""Kodlama çalışma alanının ortak Türkçe etiketleri."""

MODES = {
    "Soru Sor": "ask", "Planla": "plan",
    "Güvenli": "safe", "Yardımlı": "assisted", "Otonom": "autonomous", "Özel": "custom",
}
MODE_NAMES = {value: key for key, value in MODES.items()}
MODE_NAMES.update({"approved": "Güvenli", "auto": "Otonom"})
LEGACY_MODES = {"Onaylı Uygulama": "approved", "Otomatik Uygulama": "auto"}
WORKSPACE_VIEWS = ("Etkinlik", "Plan", "Değişiklikler", "Terminal", "Dosyalar", "Git", "Testler", "Sohbet")
QUICK_ACTIONS = (
    ("Projeyi analiz et", "Projenin mimarisini, çalışma akışını ve geliştirme komutlarını kaynaklara dayanarak incele."),
    ("Özellik geliştir", "Yeni özelliği planla. Önce gereksinimleri ve etkilenecek dosyaları incele: "),
    ("Hata düzelt", "Hatayı yeniden üret, nedenini bul, düzelt ve gerçek test sonuçlarını bildir: "),
    ("Testleri çalıştır", "Projedeki test, lint, typecheck ve build komutlarını keşfet; izinlerim kapsamında çalıştır ve sonuçlarını bildir."),
    ("Kodu açıkla", "Kod tabanının ana bileşenlerini ve aralarındaki ilişkileri dosya referanslarıyla açıkla."),
    ("İnceleme planla", "Mevcut değişiklikleri doğruluk ve test kapsamı açısından incele; gerekirse araştırma ve inceleme alt ajanlarına delege et."),
)
STATUS_NAMES = {
    "running": "Çalışıyor", "planning": "Planlanıyor", "paused": "Duraklatıldı",
    "waiting_approval": "Onay bekliyor", "interrupted": "Kesintiye uğradı",
    "completed": "Çalışma sona erdi", "failed": "Hata oluştu", "cancelled": "Durduruldu",
    "blocked": "Engellendi", "implementing": "Uygulanıyor", "applying": "Uygulanıyor",
    "testing": "Doğrulanıyor", "reviewing": "İnceleniyor", "queued": "Sırada",
}
PERMISSION_NAMES = {
    "allow_reads": "Proje dosyalarını oku", "allow_deletes": "Dosya silme onayı iste",
    "allow_edits": "Kaynak dosyalarını düzenle", "allow_terminal": "Terminal kullan",
    "allow_tests": "Test çalıştır", "allow_mcp": "Seçili MCP araçlarını kullan",
    "allow_subagents": "Alt ajan oluştur",
}
POLICY_NAMES = {
    "filesystem.read": "Dosyaları oku", "filesystem.write": "Dosyaları düzenle", "filesystem.delete": "Dosyaları sil",
    "shell.safe": "Shell komutları", "shell.network": "Ağ kullanan shell komutları", "shell.sudo": "Yönetici yetkisi",
    "network": "Ağ erişimi", "git.read": "Git verilerini oku", "git.commit": "Commit oluştur", "git.push": "Push",
    "git.forcePush": "Force push", "mcp": "MCP araçları", "secrets": "Gizli bilgiler", "spawn_agents": "Alt ajan oluştur",
}
POLICY_VALUES = {"İzin ver": "allow", "Sor": "ask", "Reddet": "deny"}
WELCOME = "Bir görevi seçip ‘Ajan ile Başlat’ diyebilir veya aşağıya isteğinizi yazabilirsiniz."
