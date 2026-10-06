"""Markdown appearance, clipboard, streaming and persisted source regressions."""
import tkinter.font as tkfont

import customtkinter as ctk
import pytest

from firik_agent.core.events import AgentEventType
from firik_agent.ui.components.markdown_message import MarkdownMessage, _TextBlock
from firik_agent.ui.theme import THEME_COLORS
from test_ui_chat import chat, emit, messages, pump, send, wait_for_layout


def displayed(renderer):
    return "\n".join(block.get("1.0", "end-1c") for block in renderer.blocks)


def test_markdown_styles_lists_and_raw_clipboard(chat):
    root, view, service = chat
    source = "# Başlık\n\n**Kalın** ve *italik*, `inline()` ve ~~eski~~.\n\n> Alıntı\n\n3. Bir\n4. İki\n\n- Üst\n  - Alt"
    stream = send(view, service, "**Kullanıcının metni**")
    emit(root, stream, AgentEventType.DONE, content=source)
    user, bot = messages(view)
    renderer = bot.text_label
    assert isinstance(renderer, MarkdownMessage)
    assert user.text_label.cget("text") == "**Kullanıcının metni**"
    text = displayed(renderer)
    assert text.startswith("Başlık\n\nKalın ve italik, inline() ve eski.")
    assert "3. Bir\n4. İki" in text
    assert "• Üst\n  • Alt" in text
    block = renderer.blocks[0]
    for tag in ("h1", "strong", "em", "code", "s", "quote"):
        assert block._textbox.tag_ranges(tag), tag
    heading = tkfont.Font(root, font=block._textbox.tag_cget("h1", "font"))
    body = tkfont.Font(root, font=block._textbox.tag_cget("body", "font"))
    assert abs(heading.cget("size")) > abs(body.cget("size"))
    assert block.cget("state") == "disabled"
    block._textbox.tag_add("sel", "1.0", "1.end")
    assert block._textbox.get("sel.first", "sel.last") == "Başlık"
    bot._copy_to_clipboard()
    assert root.clipboard_get() == source
    ctk.set_appearance_mode("Light")
    pump(root)
    assert block._textbox.cget("background") == THEME_COLORS["light"]["bot_bubble"]
    assert block._textbox.tag_cget("body", "foreground") == THEME_COLORS["light"]["text_primary"]
    ctk.set_appearance_mode("Dark")


@pytest.mark.parametrize("mode,scale", [("Light", 1), ("Dark", 1.25)])
def test_code_and_wide_tables_fit_without_clipping_reply(chat, mode, scale):
    root, view, service = chat
    ctk.set_appearance_mode(mode)
    ctk.set_widget_scaling(scale)
    try:
        root.geometry("660x760")
        code = 'print("' + "uzun " * 70 + '")\nprint("Bitti")\n'
        source = "## Örnek\n\n```python\n" + code + "```\n\n| Alan | Değer |\n| --- | --- |\n| Model | " + "geniş " * 80 + " |\n\n**Sonuç görülebiliyor.**"
        stream = send(view, service)
        emit(root, stream, AgentEventType.DONE, content=source)
        pump(root, 0.25)
        bot = view.current_bot_bubble
        renderer = bot.text_label
        assert renderer.code_sources == [code]
        assert displayed(renderer).endswith("Sonuç görülebiliyor.")
        assert "```" not in displayed(renderer)
        fixed = [block for block in renderer.blocks if block.fixed_width]
        assert len(fixed) == 2
        for block in renderer.blocks:
            assert block.winfo_rootx() >= bot.winfo_rootx()
            assert block.winfo_rootx() + block.winfo_width() <= bot.winfo_rootx() + bot.winfo_width()
            assert block._textbox.yview()[1] > 0.99  # Every line is accessible through chat scrolling.
        assert fixed[0]._textbox.xview()[1] < 1
        assert fixed[1]._textbox.xview()[1] < 1
        assert "Model" in fixed[1].get("1.0", "end-1c")
        frame = fixed[0].master
        header = frame.winfo_children()[0]
        copy = next(w for w in header.winfo_children() if isinstance(w, ctk.CTkButton))
        copy.invoke()
        assert root.clipboard_get() == code
        pump(root, 0.1)
        last = renderer.blocks[-1]
        canvas = view.chat_scroll._parent_canvas
        wait_for_layout(root, lambda: last.winfo_rooty() + last.winfo_height() <= canvas.winfo_rooty() + canvas.winfo_height(), diagnostics=lambda: {
            "view": canvas.yview(), "region": canvas.cget("scrollregion"),
            "bbox": canvas.bbox("all"), "offset": canvas.canvasy(0),
            "chat": (view.chat_scroll.winfo_rooty(), view.chat_scroll.winfo_height()),
            "reply": (bot.winfo_rooty(), bot.winfo_height()),
            "blocks": [(b.winfo_rooty(), b.winfo_height()) for b in renderer.blocks],
        })
    finally:
        ctk.set_widget_scaling(1)
        ctk.set_appearance_mode("Dark")


def test_streaming_coalesces_updates_and_renders_unclosed_code(chat):
    root, view, service = chat
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args)
    stream = send(view, service)
    renderer = view.current_bot_bubble.text_label
    writes = service.writes
    # Batch without yielding to Tk: elapsed wall time must not make this test
    # depend on how quickly native window layout completes on the runner.
    renderer.set_text("ilk", streaming=True)
    job = renderer._render_job
    for index in range(100):
        renderer.set_text(f"güncelleme {index}", streaming=True)
        assert renderer._render_job == job
    renderer.set_text("Yanıt hazırlanıyor...")
    emit(root, stream, AgentEventType.TEXT_DELTA, content="# Akış\n\n```python\n")
    for chunk in ('print(', '"merhaba"', ')'):
        emit(root, stream, AgentEventType.TEXT_DELTA, content=chunk)
    assert renderer.source.endswith('print("merhaba")')
    pump(root, 0.16)
    assert renderer.code_sources == ['print("merhaba")']
    assert service.writes == writes
    emit(root, stream, AgentEventType.TEXT_DELTA, content="\n```\n\n**Tamamlandı**")
    emit(root, stream, AgentEventType.DONE)
    assert renderer._render_job is None
    assert displayed(renderer).endswith("Tamamlandı")
    assert service.writes == writes + 1
    pump(root, 0.25)
    assert not errors


def test_reopened_history_renders_original_markdown(chat):
    root, view, service = chat
    source = "## Geçmiş\n\n**Yanıt**\n\n```sh\necho merhaba\n```"
    stream = send(view, service)
    emit(root, stream, AgentEventType.DONE, content=source)
    session_id = view.session.session_id
    assert service.load_chat_transcript(session_id)[1]["content"] == source
    view.new_chat()
    view._restore_chat(session_id)
    pump(root)
    bot = messages(view)[1]
    assert bot.raw_content == source
    assert displayed(bot.text_label).startswith("Geçmiş\n\nYanıt")
    assert bot.text_label.code_sources == ["echo merhaba\n"]
    assert not view.is_streaming
    assert len(service.streams) == 1


def test_delayed_native_layout_keeps_last_markdown_line_visible(chat, monkeypatch):
    root, view, service = chat
    root.geometry("660x760")
    stream = send(view, service)
    pump(root, 0.16)

    def delayed_fit(block):
        if block._fit_job is None:
            block._fit_job = block.after(120, block._fit)

    monkeypatch.setattr(_TextBlock, "schedule_fit", delayed_fit)
    source = "## Geciken yerleşim\n\n```python\n" + "print('satır')\n" * 12 + "```\n\n**Son satır**"
    emit(root, stream, AgentEventType.DONE, content=source)
    pump(root, 0.55)
    renderer = view.current_bot_bubble.text_label
    assert displayed(renderer).endswith("Son satır")
    canvas = view.chat_scroll._parent_canvas
    last = renderer.blocks[-1]
    assert canvas.yview()[1] > 0.99
    assert last.winfo_rooty() + last.winfo_height() <= canvas.winfo_rooty() + canvas.winfo_height()


def test_immediate_reply_preserves_pending_prompt_scroll(chat):
    root, view, service = chat
    stream = send(view, service)
    canvas = view.chat_scroll._parent_canvas
    canvas.configure(scrollregion=(0, 0, 600, 1200))
    canvas.yview_moveto(0)
    assert view._scroll_force
    source = "```text\n" + "Tam yanıt\n" * 25 + "```\n\nSon satır"
    emit(root, stream, AgentEventType.DONE, content=source)
    renderer = view.current_bot_bubble.text_label
    assert renderer._scroll_anchor is not None
    pump(root, 0.25)
    wait_for_layout(root, lambda: canvas.yview()[1] > 0.99)
    last = renderer.blocks[-1]
    assert last.winfo_rooty() + last.winfo_height() <= canvas.winfo_rooty() + canvas.winfo_height()


def test_stream_keeps_following_after_viewport_shrinks(chat):
    root, view, service = chat
    stream = send(view, service)
    emit(root, stream, AgentEventType.TEXT_DELTA, content="Satır  \n" * 60)
    pump(root, 0.16)
    canvas = view.chat_scroll._parent_canvas
    wait_for_layout(root, lambda: canvas.yview()[1] > 0.99)
    root.geometry("660x660")
    pump(root, 0.16)
    emit(root, stream, AgentEventType.TEXT_DELTA, content="Yeni satır  \n" * 20)
    pump(root, 0.16)
    wait_for_layout(root, lambda: canvas.yview()[1] > 0.99)


def test_links_require_click_and_html_images_remain_text(chat, monkeypatch):
    root, view, service = chat
    opened = []
    monkeypatch.setattr("firik_agent.ui.components.markdown_message.webbrowser.open", opened.append)
    source = '[Belge](https://example.com/docs)\n\n<img src="https://example.com/image.png">\n\n![Örnek](https://example.com/image.png)\n\n[Dosya](file:///tmp/example)'
    stream = send(view, service)
    emit(root, stream, AgentEventType.DONE, content=source)
    pump(root, 0.16)
    renderer = view.current_bot_bubble.text_label
    assert not opened
    assert '<img src="https://example.com/image.png">' in displayed(renderer)
    assert "[Görsel: Örnek]" in displayed(renderer)
    block = renderer.blocks[0]
    link = next(tag for tag in block._textbox.tag_names("1.0") if tag.startswith("url-"))
    assert block._textbox.tk.call(block._textbox._w, "tag", "bind", link, "<Button-1>")
    box = block._textbox.bbox("1.1")
    assert box is not None
    block._textbox.event_generate("<Motion>", x=box[0] + 2, y=box[1] + 2)
    block._textbox.event_generate("<Button-1>", x=box[0] + 2, y=box[1] + 2)
    assert opened == ["https://example.com/docs"]
    assert "file:///" in displayed(renderer)  # Unsafe link targets are plain text.
