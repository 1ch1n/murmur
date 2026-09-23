"""Capsule widget: geometry, HiDPI crispness, motion, position, prefs."""
from __future__ import annotations

import json

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtGui import QImage

from murmur.overlay.pill.prefs import PillPrefs
from murmur.overlay.pill.state import PillState


# --- pure models (no Qt app needed) ---------------------------------------

def test_trace_scrolls_real_audio_and_rests_flat():
    from murmur.overlay.waveform import Trace

    t = Trace(24)
    t.push(np.zeros(1600, dtype=np.float32))
    assert t.level == 0.0
    for _ in range(30):
        t.tick(1 / 60)
    assert float(np.abs(t.buf).max()) == 0.0

    loud = (np.random.default_rng(1).standard_normal(1600) * 0.3).astype(np.float32)
    t.push(loud)
    assert t.level > 0.5 and len(t.pending) > 0
    for _ in range(8):                             # ~0.13 s: points enter, none leave yet
        t.tick(1 / 60)
    assert float(np.abs(t.buf).max()) > 0.2       # speech reached the line
    assert t.active()
    t.clear()
    for _ in range(200):
        t.tick(1 / 60)
    assert not t.active()                          # scrolled back to flat


def test_resolve_position_rules():
    from murmur.overlay.pill.widget import ScreenInfo, resolve_position

    a = ScreenInfo("\\\\.\\DISPLAY1", QRect(0, 0, 1920, 1040))
    b = ScreenInfo("\\\\.\\DISPLAY2", QRect(1920, 0, 2560, 1400))
    size = QSize(260, 52)

    p = resolve_position(PillPrefs(x=4300, y=1390, screen=b.name), [a, b], a, size)
    assert b.avail.contains(QRect(p, size))
    assert p == QPoint(4480 - 260, 1400 - 52)

    p = resolve_position(PillPrefs(x=100, y=100, screen="gone"), [a, b], a, size)
    assert p == QPoint(100, 100)

    p = resolve_position(PillPrefs(x=99999, y=99999, screen="gone"), [a, b], a, size)
    assert p == QPoint((1920 - 260) // 2, 1040 - 52 - 80)

    p = resolve_position(PillPrefs(), [a, b], a, size)
    assert p == QPoint((1920 - 260) // 2, 1040 - 52 - 80)


def test_settings_roundtrip_with_widget_fields(tmp_path, monkeypatch):
    import murmur.config as cfg

    monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "MODELS_DIR", tmp_path / "models")

    s = cfg.Settings(pill_x=100, pill_y=200, pill_screen="\\\\.\\DISPLAY2", pill_scale="l", pill_opacity=0.8)
    cfg.save_settings(s)
    assert cfg.load_settings() == s

    old = {"model": "small.en", "pill_width": 280, "pill_height": 56, "pill_bottom_margin": 80}
    (tmp_path / "config.json").write_text(json.dumps(old), encoding="utf-8")
    loaded = cfg.load_settings()
    assert loaded.pill_scale == "m" and loaded.pill_x is None


def test_prefs_from_settings_and_sizes():
    from murmur.config import Settings
    from murmur.overlay.pill.prefs import CAPSULE_SIZES, widget_size_for

    p = PillPrefs.from_settings(Settings(pill_scale="L", pill_opacity=2.0))
    assert p.scale == "l" and p.capsule_size == CAPSULE_SIZES["l"] and p.opacity == 1.0
    assert p.size == widget_size_for(CAPSULE_SIZES["l"])
    assert PillPrefs.from_settings(Settings(pill_scale="weird")).capsule_size == CAPSULE_SIZES["m"]


def test_sentence_splitter():
    from murmur.tts import split_sentences

    text = "Hey there. This is a test! Does it work?\n\nNew paragraph here... and more. x"
    spans = split_sentences(text)
    sents = [text[a:b] for a, b, _ in spans]
    assert sents[0] == "Hey there."
    assert sents[1] == "This is a test!"
    assert sents[2] == "Does it work?"
    assert sents[3].startswith("New paragraph here...")
    assert all(s.strip() for s in sents)
    assert split_sentences("") == []


# --- rendering (offscreen QApplication) -----------------------------------

@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.0])
def test_capsule_rim_is_exactly_one_device_pixel(qapp, dpr):
    from murmur.overlay.pill.bezel import render_bezel
    from murmur.overlay.pill.theme import Layout

    w, h = PillPrefs().size
    L = Layout.compute(w, h, dpr)
    pm = render_bezel(L)
    assert pm.width() == round(w * dpr) and pm.height() == round(h * dpr)

    img = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    x = img.width() // 2
    col = [img.pixelColor(x, y) for y in range(img.height() // 2)]
    # The rim is the first row that is both bright (a warm line over the
    # dark fill) and opaque. It must be exactly one device pixel tall: the
    # row above is shadow (faint), the row below is the plain dark fill.
    rim = next(i for i, c in enumerate(col) if c.alpha() > 200 and c.lightness() > 20)
    assert col[rim - 1].alpha() < 90, col[rim - 1].alpha()
    assert col[rim + 2].alpha() > 200 and col[rim + 2].lightness() < col[rim].lightness() - 6


def test_widget_renders_every_state_at_two_dprs(qapp):
    from murmur.overlay.pill import Pill

    pill = Pill(PillPrefs())
    pill.set_caption("RALT")
    for st in (PillState.IDLE, PillState.LISTENING, PillState.PROCESSING, PillState.DONE, PillState.ERROR):
        pill.set_state(st)
        if st == PillState.LISTENING:
            pill.push_samples((np.random.default_rng(2).standard_normal(1600) * 0.2).astype(np.float32))
        if st == PillState.PROCESSING:
            pill.set_progress(42, "tail")
        for _ in range(30):
            pill._tick(1 / 60)
        for dpr in (1.0, 2.0):
            img = QImage(int(pill.width() * dpr), int(pill.height() * dpr), QImage.Format.Format_ARGB32)
            img.setDevicePixelRatio(dpr)
            img.fill(0)
            pill.render(img)
            assert any(img.pixelColor(x, img.height() // 2).alpha() > 0 for x in range(0, img.width(), 7))
    pill.set_state(PillState.HIDDEN)
    assert not pill.isVisible()


def test_click_opens_panel_and_drag_emits_position(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QSignalSpy, QTest

    from murmur.overlay.pill import Pill

    pill = Pill(PillPrefs(x=200, y=200, screen=""))
    pill.set_state(PillState.IDLE)
    pill._save_timer.setInterval(0)
    spy = QSignalSpy(pill.position_changed)

    QTest.mouseClick(pill, Qt.MouseButton.LeftButton, pos=pill.rect().center())
    assert pill.panel_open()
    pill.collapse_panel(restore_focus=False)

    start = pill.pos()
    QTest.mousePress(pill, Qt.MouseButton.LeftButton, pos=pill.rect().center())
    QTest.mouseMove(pill, pos=pill.rect().center() + QPoint(60, 25))
    QTest.mouseMove(pill, pos=pill.rect().center() + QPoint(80, 40))
    QTest.mouseRelease(pill, Qt.MouseButton.LeftButton, pos=pill.rect().center() + QPoint(80, 40))
    assert pill.pos() != start
    qapp.processEvents()
    QTest.qWait(20)
    assert spy.count() == 1
    x, y, _name = spy.at(0)
    assert (x, y) == (pill.pos().x(), pill.pos().y())
    pill.set_state(PillState.HIDDEN)


def test_fonts_and_qss(qapp):
    from murmur.overlay.fonts import font_family, is_bundled
    from murmur.overlay.styles import build_qss

    assert font_family("ui") and font_family("mono")
    qss = build_qss("settings")
    assert font_family("ui") in qss
    if is_bundled("ui"):
        assert "Geist" in font_family("ui")
