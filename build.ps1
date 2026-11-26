# Reis'i PyInstaller (onedir) ile paketler. Çıktı: dist\Reis\Reis.exe
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install pyinstaller | Out-Null
& $py -m PyInstaller reis.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller basarisiz" }
$size = (Get-ChildItem dist\Reis -Recurse | Measure-Object Length -Sum).Sum / 1MB
"Tamam: dist\Reis\Reis.exe  ({0:N0} MB)" -f $size
# Dagitim zip'i icin:  Compress-Archive dist\Reis Reis-win64.zip
