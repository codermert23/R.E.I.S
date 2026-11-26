from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit,
                               QPushButton, QVBoxLayout)


class ProjectDialog(QDialog):
    """Proje adı + rapor dosyası/klasörü."""

    def __init__(self, name="", path="", parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)  # kenar çubuğu hep üstte; diyalog arkada kalmasın
        self.setWindowTitle("Proje")
        self.name_edit = QLineEdit(name)
        self.path_edit = QLineEdit(path)
        b1 = QPushButton("Dosya")
        b1.clicked.connect(self._file)
        b2 = QPushButton("Klasör")
        b2.clicked.connect(self._dir)
        row = QHBoxLayout()
        row.addWidget(self.path_edit, 1)
        row.addWidget(b1)
        row.addWidget(b2)
        form = QFormLayout()
        form.addRow("Ad", self.name_edit)
        form.addRow("Rapor yolu", row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

    def _file(self):
        p, _ = QFileDialog.getOpenFileName(self, "Rapor dosyası", "", "Markdown (*.md *.markdown *.txt);;Tümü (*)")
        if p:
            self.path_edit.setText(p)
            if not self.name_edit.text():
                self.name_edit.setText(Path(p).parent.name or Path(p).stem)

    def _dir(self):
        p = QFileDialog.getExistingDirectory(self, "Proje klasörü")
        if p:
            self.path_edit.setText(p)
            if not self.name_edit.text():
                self.name_edit.setText(Path(p).name)

    def values(self):
        return self.name_edit.text().strip(), self.path_edit.text().strip()
