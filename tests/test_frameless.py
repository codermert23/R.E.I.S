import os
import struct

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QIcon, QImage, QShortcut

from reis.assets import asset_path
from reis.config.settings import Settings
from reis.core.state import StateBus
from reis.ui import frameless as fl
from reis.ui.appicon import ICON_SIZES, app_icon, render_icon

W, H, B = 1000, 700, 6
BAR = (0, 0, W, 34)


# ---------------------------------------------------------------- saf mantık
def test_hit_region_edges_and_corners():
    hr = lambda x, y, **k: fl.hit_region(x, y, W, H, B, BAR, **k)
    assert hr(0, 0) == "topleft" and hr(W - 1, 0) == "topright"
    assert hr(0, H - 1) == "bottomleft" and hr(W - 1, H - 1) == "bottomright"
    assert hr(500, 0) == "top" and hr(500, H - 1) == "bottom"
    assert hr(0, 300) == "left" and hr(W - 1, 300) == "right"
    assert hr(10, 3) == "topleft"          # köşe bölgesi kenar şeridinden geniş
    assert hr(500, 20) == "caption"
    assert hr(500, 20, over_interactive=True) == "client"
    assert hr(500, 400) == "client"
    assert hr(500, 3, over_interactive=True) == "top"  # kenar, düğmeden önceliklidir


def test_hit_region_maximized_and_fixed():
    assert fl.hit_region(0, 0, W, H, B, BAR, maximized=True) == "caption"
    assert fl.hit_region(500, 400, W, H, B, BAR, maximized=True) == "client"
    assert fl.hit_region(0, 300, W, H, B, BAR, resizable=False) == "client"
    assert fl.hit_region(-1, 5, W, H, B, BAR) == "client"
    assert fl.hit_region(500, 20, W, H, B, None) == "client"
    assert all(k in fl.HT_CODES for k in ("caption", "left", "bottomright"))
    assert fl.HT_CODES["caption"] == 2 and fl.HT_CODES["topleft"] == 13


def test_maximize_geometry_respects_taskbar_and_monitor_origin():
    g = fl.maximize_geometry((0, 0, 1920, 1080), (0, 0, 1920, 1032))
    assert (g.pos_x, g.pos_y, g.width, g.height) == (0, 0, 1920, 1032)
    g = fl.maximize_geometry((1920, 0, 3840, 1080), (1920, 40, 3840, 1080))  # görev çubuğu üstte, ikinci monitör
    assert (g.pos_x, g.pos_y, g.width, g.height) == (0, 40, 1920, 1040)


def test_choose_screen_rect_picks_largest_overlap():
    a, b = (0, 0, 1920, 1032), (1920, 0, 1920, 1032)
    assert fl.choose_screen_rect((1800, 100, 800, 600), [a, b]) == b
    assert fl.choose_screen_rect((100, 100, 800, 600), [a, b]) == a
    assert fl.choose_screen_rect((0, 0, 1, 1), []) is None


def test_app_user_model_id_is_safe():
    assert fl.set_app_user_model_id("Reis.Assistant.Test") in (True, False)
    assert fl.set_app_user_model_id(None) in (True, False)  # str(None) bile patlamaz


# ---------------------------------------------------------------- simge
def test_icon_files_and_ico_sizes(qapp):
    png, ico = asset_path("icons", "reis.png"), asset_path("icons", "reis.ico")
    assert os.path.getsize(png) > 1000 and os.path.getsize(ico) > 1000
    img = QImage(png)
    assert img.width() == 256 and img.height() == 256 and img.hasAlphaChannel()
    data = open(ico, "rb").read()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1)
    got = sorted((struct.unpack("<B", data[6 + 16 * i:7 + 16 * i])[0] or 256) for i in range(count))
    assert got == sorted(ICON_SIZES)
    icon = QIcon(ico)
    assert not icon.isNull()
    avail = {s.width() for s in icon.availableSizes()}
    assert set(ICON_SIZES) <= avail


def test_rendered_icon_not_blank_at_16(qapp):
    for sz in ICON_SIZES:
        im = render_icon(sz)
        assert im.width() == sz
        opaque = sum(1 for x in range(sz) for y in range(sz) if im.pixelColor(x, y).alpha() > 200)
        assert opaque > sz * sz * 0.25
    pm = app_icon().pixmap(16, 16).toImage()
    assert any(pm.pixelColor(x, y).alpha() > 0 for x in range(pm.width()) for y in range(pm.height()))


# ---------------------------------------------------------------- düğmeler / pencereler
def test_window_buttons_signals(qapp):
    wb = fl.WindowButtons()
    got = []
    wb.minimize_clicked.connect(lambda: got.append("min"))
    wb.maximize_clicked.connect(lambda: got.append("max"))
    wb.close_clicked.connect(lambda: got.append("close"))
    for b in (wb.btn_minimize, wb.btn_maximize, wb.btn_close):
        b.click()
    assert got == ["min", "max", "close"]
    wb.set_maximized(True)
    assert wb.btn_maximize.kind == "restore"
    wb.set_maximized(False)
    assert wb.btn_maximize.kind == "max"


def test_briefing_and_viewer_chrome(qapp, tmp_path):
    from reis.core.documents import load_project
    from reis.ui.docs_window import DocsWindow
    from reis.ui.viewer import MarkdownViewer

    d = tmp_path / "Proj"
    d.mkdir()
    (d / "asistannote.md").write_text("# Proj\n- [ ] a\n", encoding="utf-8")
    b = DocsWindow()
    f = b.windowFlags()
    assert f & Qt.FramelessWindowHint and f & Qt.WindowStaysOnTopHint
    assert b.title_bar.title_label.text() == "Notlar"
    b.set_sources(Settings(projects=[{"name": "Proj", "path": str(d)}]))
    b.open_centered()
    b.title_bar.buttons.btn_close.click()
    assert not b.isVisible()

    v = MarkdownViewer()
    v.set_loader(lambda: load_project("Proj", str(d), "asistannote.md"))
    v.open_big()
    win = v._big_win
    assert win.windowFlags() & Qt.FramelessWindowHint and win.windowFlags() & Qt.WindowStaysOnTopHint
    assert win.title_bar.title_label.text() == win.windowTitle() != ""
    sc = [x for x in win.findChildren(QShortcut) if x.key().toString() == "Esc"]
    assert sc  # Esc kapatır (offscreen'de odak olmadığından doğrudan tetikle)
    sc[0].activated.emit()
    assert not win.isVisible()
