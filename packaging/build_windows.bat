@echo off
rem Netzentrale von Hand unter Windows bauen (Alternative zum GitHub-Ablauf).
rem Voraussetzung: Python 3.12 von python.org, optional Inno Setup 6 für den Installer.
cd /d "%~dp0\.."
python -m venv .venv || goto :fehler
call .venv\Scripts\activate.bat
pip install -r requirements.txt pyinstaller pillow || goto :fehler
python tools\make_icon.py build\AppIcon || goto :fehler
python packaging\make_version_info.py || goto :fehler
pyinstaller packaging\netzentrale.spec --noconfirm || goto :fehler
for /f %%v in ('python -c "import netzentrale; print(netzentrale.__version__)"') do set VERSION=%%v
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
  "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" /DAppVersion=%VERSION% installer\netzentrale.iss
) else (
  echo Inno Setup nicht gefunden – Programm liegt in dist\Netzentrale\Netzentrale.exe
)
echo Fertig.
exit /b 0
:fehler
echo Build fehlgeschlagen.
exit /b 1
