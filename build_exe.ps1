# Build ytuploader.exe - a single-file Windows app (no console window).
#
#   .\build_exe.ps1
#
# The exe looks for config.toml, the DB, token.json etc. next to itself, so it
# is copied into this folder when the build finishes.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = ".venv\Scripts\python.exe"
& $python -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

New-Item -ItemType Directory -Force build | Out-Null
& $python -m ytuploader.gui.icon build\icon.ico
if ($LASTEXITCODE -ne 0) { throw "icon generation failed" }

& $python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name ytuploader `
    --icon build\icon.ico `
    --add-data "config.example.toml;." `
    --paths . `
    packaging\ytuploader_app.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Copy-Item dist\ytuploader.exe . -Force
Write-Host "`nBuilt: $PSScriptRoot\ytuploader.exe"
