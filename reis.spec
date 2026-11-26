# -*- mode: python ; coding: utf-8 -*-
# Derleme: .\build.ps1   (ya da: .\.venv\Scripts\python.exe -m PyInstaller reis.spec --noconfirm)
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("reis/ui/theme.qss", "reis/ui"),
         ("reis/assets/icons", "reis/assets/icons"),
         ("THIRD_PARTY.md", "."),
         ("reis/core/tts/yazgi_worker.py", "reis/core/tts")]
binaries = []
hiddenimports = ["keyring.backends.Windows", "win32ctypes.core"]

# Veri dosyaları / yerel DLL'ler gerektiren paketler
for pkg in ("faster_whisper", "ctranslate2", "piper", "onnxruntime", "sounddevice", "_sounddevice_data",
            "webrtcvad", "google.genai", "tokenizers", "edge_tts", "av", "certifi", "aiohttp", "winrt"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("keyring.backends")

# Kullanılmayan büyük Qt/Python parçaları dışarıda
excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtQuick", "PySide6.QtQml",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtDesigner",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtNetwork", "PySide6.QtOpenGL",
    "tkinter", "matplotlib", "pytest", "IPython", "torch",
]

a = Analysis(
    ["run_reis.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Reis",
    console=False,  # pencere yok; hata ayıklamak için True yap
    upx=False,
    icon="reis/assets/icons/reis.ico",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Reis")
