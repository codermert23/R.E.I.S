"""Şifre listesi: tek tıkla kopyala, ekle, sil. Dosya düz metin (`Ad: değer` satırları)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QMimeData, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..core import passwords
from ..core.dpapi import DpapiError
from .frameless import FramelessWindow, TitleBar

CLEAR_AFTER_MS = 30_000
MASK = "••••••••"


class PasswordsWindow(FramelessWindow):
    path_changed = Signal(str)

    def __init__(self, path: str = "", parent=None):
        super().__init__(parent, on_top=True)
        self._path = path
        self._entries: list[passwords.Entry] = []
        self._copied: str | None = None
        self.title_bar = TitleBar("Şifreler")
        self.path_label = QLabel("")
        self.path_label.setObjectName("PathLabel")
        pick = QPushButton("Dosya seç / oluştur")
        pick.clicked.connect(self._pick)
        self.show_pw = QCheckBox("Göster")
        self.show_pw.toggled.connect(lambda _c: self._render())
        self.lock_btn = QPushButton("Şifrele")
        self.lock_btn.setToolTip("Dosyayı Windows hesabına bağlı olarak şifreler (Not Defteri ile açılmaz)")
        self.lock_btn.clicked.connect(self._encrypt)
        export = QPushButton("Yedek (düz metin)")
        export.setToolTip("İçeriği düz metin olarak başka bir dosyaya kaydeder")
        export.clicked.connect(self._export)
        top = QHBoxLayout()
        top.addWidget(self.path_label, 1)
        top.addWidget(self.show_pw)
        top.addWidget(self.lock_btn)
        top.addWidget(export)
        top.addWidget(pick)

        self.rows = QVBoxLayout()
        self.rows.setSpacing(6)
        self.rows.setAlignment(Qt.AlignTop)
        inner = QWidget()
        inner.setLayout(self.rows)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(inner)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Ad (E-Devlet, LoL …)")
        self.value_edit = QLineEdit()
        self.value_edit.setPlaceholderText("Şifre")
        self.value_edit.setEchoMode(QLineEdit.Password)
        self.value_edit.returnPressed.connect(self._add)
        add = QPushButton("Ekle")
        add.clicked.connect(self._add)
        bar = QHBoxLayout()
        bar.addWidget(self.name_edit, 1)
        bar.addWidget(self.value_edit, 1)
        bar.addWidget(add)
        self.status = QLabel("")
        self.status.setObjectName("PathLabel")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        lay.addWidget(self.title_bar)
        body = QVBoxLayout()
        body.setContentsMargins(10, 8, 10, 10)
        body.setSpacing(8)
        body.addLayout(top)
        body.addWidget(scroll, 1)
        body.addLayout(bar)
        body.addWidget(self.status)
        lay.addLayout(body, 1)
        self.attach_title_bar(self.title_bar)
        self.setWindowTitle("Şifreler")
        QShortcut(QKeySequence("Esc"), self, activated=self.close)

        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.timeout.connect(self._clear_clipboard)

    # ---- liste
    def set_path(self, path: str) -> None:
        self._path = path
        self.reload()

    def reload(self) -> None:
        try:
            self._entries = passwords.load(self._path)
            err = ""
        except (DpapiError, UnicodeDecodeError):
            self._entries, err = [], "Şifreli dosya bu Windows hesabıyla çözülemedi."
        enc = bool(self._path) and passwords.is_encrypted(self._path)
        self.lock_btn.setVisible(bool(self._path) and not enc and Path(self._path).exists())
        self.path_label.setText((("🔒 " if enc else "") + self._path) if self._path else "Dosya seçilmedi")
        self.status.setText(err)
        self.path_label.setToolTip(self._path)
        self._render()

    def _render(self) -> None:
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not self._path:
            self.rows.addWidget(QLabel("Önce bir şifre dosyası seç (sağ üstteki düğme) ya da Ayarlar'dan yolu gir."))
            return
        if not self._entries:
            self.rows.addWidget(QLabel("Henüz kayıt yok. Aşağıdan ekle ya da dosyaya `Ad: şifre` satırları yaz."))
            return
        for e in self._entries:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            name = QLabel(e.name)
            name.setMinimumWidth(110)
            name.setStyleSheet("font-size: 15px; font-weight: bold;")
            val = QLabel(e.value if self.show_pw.isChecked() else MASK)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            val.setStyleSheet("font-family: Consolas, monospace; font-size: 14px;")
            cp = QPushButton("Kopyala")
            cp.clicked.connect(lambda _c=False, en=e: self._copy(en))
            rm = QPushButton("Sil")
            rm.clicked.connect(lambda _c=False, en=e: self._delete(en))
            h.addWidget(name)
            h.addWidget(val, 1)
            h.addWidget(cp)
            h.addWidget(rm)
            self.rows.addWidget(row)

    # ---- işlemler
    def _copy(self, e: passwords.Entry) -> None:
        md = QMimeData()
        md.setText(e.value)
        md.setData("ExcludeClipboardContentFromMonitorProcessing", QByteArray(b"1"))  # Win+V geçmişine girmesin
        QApplication.clipboard().setMimeData(md)
        self._copied = e.value
        self._clear_timer.start(CLEAR_AFTER_MS)
        self.status.setText(f"{e.name} kopyalandı (30 sn sonra panodan silinir).")

    def _clear_clipboard(self) -> None:
        cb = QApplication.clipboard()
        if self._copied is not None and cb.text() == self._copied:
            cb.clear()
        self._copied = None
        self.status.setText("Pano temizlendi.")

    def _add(self) -> None:
        if not self._path:
            self._pick()
            if not self._path:
                return
        if passwords.add(self._path, self.name_edit.text(), self.value_edit.text()):
            self.status.setText(f"{self.name_edit.text().strip()} eklendi.")
            self.name_edit.clear()
            self.value_edit.clear()
            self.reload()
        else:
            self.status.setText("Ad ve şifre boş olamaz; dosya da yazılabilir olmalı.")

    def _delete(self, e: passwords.Entry) -> None:
        if QMessageBox.question(self, "Reis", f"'{e.name}' silinsin mi?") != QMessageBox.Yes:
            return
        self.status.setText(f"{e.name} silindi." if passwords.delete(self._path, e)
                            else "Silinemedi: dosya bu arada değişmiş, listeyi yeniledim.")
        self.reload()

    def _encrypt(self) -> None:
        if passwords.encrypt_file(self._path):
            self.status.setText("Dosya şifrelendi: artık yalnızca bu Windows hesabında açılır.")
        else:
            self.status.setText("Şifrelenemedi; dosya olduğu gibi duruyor.")
        self.reload()

    def _export(self) -> None:
        if not self._path:
            return
        dest, _ = QFileDialog.getSaveFileName(self, "Düz metin yedek", str(Path.home() / "sifreler-yedek.txt"),
                                              "Metin (*.txt)")
        if dest:
            self.status.setText("Yedek yazıldı (güvenli bir yerde sakla)." if passwords.export_plain(self._path, dest)
                                else "Yedek yazılamadı.")

    def offer_encryption(self) -> None:
        """Dosya düz metinse bir kez şifrelemeyi önerir."""
        if not self._path or not Path(self._path).exists() or passwords.is_encrypted(self._path):
            return
        r = QMessageBox.question(
            self, "Reis", "Şifre dosyası düz metin. Windows hesabına bağlı olarak şifrelensin mi?\n\n"
            "• Başkası dosyayı açsa bile okuyamaz.\n• Not Defteri ile açılmaz, yalnızca Reis'ten düzenlenir.\n"
            "• Yedek için 'Yedek (düz metin)' düğmesi var.")
        if r == QMessageBox.Yes:
            self._encrypt()

    def _pick(self) -> None:
        p, _ = QFileDialog.getSaveFileName(self, "Şifre dosyası (var olanı seç ya da yeni ad yaz)",
                                           self._path or str(Path.home() / "sifreler.txt"),
                                           "Metin (*.txt);;Tümü (*)", options=QFileDialog.DontConfirmOverwrite)
        if p:
            self._path = p
            self.path_changed.emit(p)
            self.reload()

    # ---- pencere
    def open_centered(self) -> None:
        self.reload()
        screen = self.screen() or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        w, h = int(geo.width() * 0.4), int(geo.height() * 0.7)
        self.resize(max(w, 560), h)
        self.move(geo.center().x() - self.width() // 2, geo.center().y() - h // 2)
        self.show()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(200, self.offer_encryption)

    def closeEvent(self, e):
        self.show_pw.setChecked(False)
        self._entries = []
        self._render()  # açık metin şifreler ekranda/bellekte kalmasın
        super().closeEvent(e)
