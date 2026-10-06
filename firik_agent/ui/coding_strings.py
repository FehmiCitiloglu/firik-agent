"""Kodlama çalışma alanının ortak Türkçe etiketleri."""

MODES = {
    "Soru Sor": "ask", "Planla": "plan",
    "Onaylı Uygulama": "approved", "Otomatik Uygulama": "auto",
}
MODE_NAMES = {value: key for key, value in MODES.items()}
STATUS_NAMES = {
    "running": "Çalışıyor", "planning": "Planlanıyor", "paused": "Duraklatıldı",
    "waiting_approval": "Onay bekliyor", "interrupted": "Kesintiye uğradı",
    "completed": "Çalışma sona erdi", "failed": "Hata oluştu", "cancelled": "Durduruldu",
}
PERMISSION_NAMES = {
    "allow_reads": "Proje dosyalarını oku", "allow_deletes": "Dosya silme onayı iste",
    "allow_edits": "Kaynak dosyalarını düzenle", "allow_terminal": "Terminal kullan",
    "allow_tests": "Test çalıştır", "allow_mcp": "Seçili MCP araçlarını kullan",
}
WELCOME = "Bir görevi seçip ‘Ajan ile Başlat’ diyebilir veya aşağıya isteğinizi yazabilirsiniz."
