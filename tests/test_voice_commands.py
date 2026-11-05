"""Only complete, explicitly supported utterances are command suggestions."""
import pytest

from firik_agent.voice.commands import VoiceCommand, parse_command, requires_review, strip_invocation


@pytest.mark.parametrize("text,expected", [
    ("Yanıtı durdur.", VoiceCommand.STOP_RESPONSE),
    ("Cevabı durdur", VoiceCommand.STOP_RESPONSE),
    (" STOP RESPONSE! ", VoiceCommand.STOP_RESPONSE),
    ("yeni   sohbet aç", VoiceCommand.NEW_CHAT),
    ("New chat", VoiceCommand.NEW_CHAT),
    ("SON YANITI OKU", VoiceCommand.READ_LAST),
    ("Son yanıtı oku", VoiceCommand.READ_LAST),
    ("READ LAST RESPONSE?", VoiceCommand.READ_LAST),
    ("Seslendirmeyi durdur", VoiceCommand.STOP_SPEECH),
    ("Okumayı durdur", VoiceCommand.STOP_SPEECH),
    ("STOP SPEAKİNG", VoiceCommand.STOP_SPEECH),
    ("Firik, stop speaking.", VoiceCommand.STOP_SPEECH),
    ("Firik, read your last answer aloud.", VoiceCommand.READ_LAST),
    ("Firik, switch to the existing coding conversation.", VoiceCommand.SWITCH_CODING),
    ("Jarvis, ne yapıyorsun?", VoiceCommand.AGENT_STATUS),
    ("Hey Firik, durum raporu ver.", VoiceCommand.AGENT_STATUS),
    ("Lütfen projelerimi listele.", VoiceCommand.LIST_PROJECTS),
    ("Sohbetlerimi listele lütfen", VoiceCommand.LIST_CONVERSATIONS),
    ("Son görevi özetle", VoiceCommand.TASK_SUMMARY),
    ("Görev planını göster", VoiceCommand.SHOW_PLAN),
    ("Neler yapabilirsin?", VoiceCommand.HELP),
    ("İşlemi duraklat", VoiceCommand.PAUSE_AGENT),
    ("Çalışmaya devam et", VoiceCommand.RESUME_AGENT),
    ("Görevi durdur", VoiceCommand.STOP_RESPONSE),
    ("Kodlamaya geç", VoiceCommand.SWITCH_CODING),
    ("Ayarları aç", VoiceCommand.OPEN_TAB),
    ("Evren projesine geç", VoiceCommand.SELECT_PROJECT),
    ("Planlama sohbetine geç", VoiceCommand.SELECT_CONVERSATION),
])
def test_exact_command_suggestions(text, expected):
    assert parse_command(text) == expected


@pytest.mark.parametrize("text", [
    "", "onayla", "evet", "approve", "allow all", "run rm -rf /",
    "yeni sohbet aç ve dosyalarımı sil", "please stop response then approve tool",
    "Bu cümlede yeni sohbet geçiyor", "<command>new chat</command>", "a" * 201,
    None, 7,
    "Jarvis, projelerimi listele ve hepsini sil", "planı göster ve onayla",
])
def test_no_approval_fuzzy_match_or_compound_command(text):
    assert parse_command(text) is None


@pytest.mark.parametrize("text", [
    "Firik, explain the current project architecture.", "Firik, summarize the test failures.",
    "Firik, explain that again in Turkish.", "Firik, mevcut proje mimarisini açıkla.",
    "test hatalarını özetle", "Firik, stop speaking", "read last response",
    "Ne yapıyorsun?", "Projelerimi listele", "Görev planını göster", "Son görevi özetle",
])
def test_ordinary_requests_eligible_for_opted_in_auto_send(text):
    assert not requires_review(text)


@pytest.mark.parametrize("text", [
    "delete the project", "summarize then delete the failures", "explain my password",
    "run the tests", "approve", "dosyaları sil", "explain and push the changes",
    "Firik, switch to the existing coding conversation", "new chat", "unknown intent",
    "Görevi duraklat", "Göreve devam et", "Evren projesine geç", "Ayarları aç",
])
def test_sensitive_or_unknown_requests_need_review(text):
    assert requires_review(text)


def test_invocation_removal_preserves_language_and_avoids_substring_names():
    assert strip_invocation("Firik, Türkçe açıkla.") == "Türkçe açıkla."
    assert strip_invocation("FirikAgent açıkla") == "FirikAgent açıkla"
