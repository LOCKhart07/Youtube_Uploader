# Build the Windows installer: dist\ytuploader-setup-<version>.exe
#
#   .\build_installer.ps1
#
# 1. PyInstaller one-folder build into dist\ytuploader\ (starts instantly,
#    unlike the one-file exe that unpacks itself on every launch).
# 2. Inno Setup (ISCC.exe) packages it per-user, see packaging\installer.iss.
#    Install Inno Setup 6 once with:  winget install JRSoftware.InnoSetup
#
# For the portable single-file exe, use .\build_exe.ps1 instead.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = ".venv\Scripts\python.exe"
& $python -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

$version = (& $python -c "import ytuploader; print(ytuploader.__version__)").Trim()
if ($LASTEXITCODE -ne 0) { throw "couldn't read ytuploader.__version__" }

New-Item -ItemType Directory -Force build | Out-Null
& $python -m ytuploader.gui.icon build\icon.ico
if ($LASTEXITCODE -ne 0) { throw "icon generation failed" }

& $python -m PyInstaller --noconfirm --clean --onedir --windowed `
    --name ytuploader `
    --icon build\icon.ico `
    --add-data "config.example.toml;." `
    --paths . `
    packaging\ytuploader_app.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$iscc = @(
    (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source,
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found - winget install JRSoftware.InnoSetup" }

& $iscc /Qp "/DAppVersion=$version" packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

Write-Host "`nBuilt: $PSScriptRoot\dist\ytuploader-setup-$version.exe"
