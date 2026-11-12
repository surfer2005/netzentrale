"""Baut 'Netzentrale.app' (Script-Bundle, richtet beim 1. Start eine venv ein).

    python3 tools/build_app.py dist/
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from netzentrale import APP_NAME, __version__  # noqa: E402

PLIST = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>{APP_NAME}</string>
    <key>CFBundleDisplayName</key><string>{APP_NAME}</string>
    <key>CFBundleIdentifier</key><string>de.peterbode.netzentrale</string>
    <key>CFBundleVersion</key><string>{__version__}</string>
    <key>CFBundleShortVersionString</key><string>{__version__}</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleExecutable</key><string>{APP_NAME}</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSLocalNetworkUsageDescription</key><string>{APP_NAME} durchsucht das lokale Netzwerk und prüft, ob Geräte erreichbar sind.</string>
</dict>
</plist>
"""

LAUNCHER = r'''#!/bin/bash
# Startprogramm: richtet beim ersten Start eine Python-Umgebung ein und startet die App.
NAME="__NAME__"
APP_RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
SUPPORT="$HOME/Library/Application Support/$NAME"
VENV="$SUPPORT/venv"
LOG="$HOME/Library/Logs/$NAME.log"
mkdir -p "$SUPPORT" "$(dirname "$LOG")"
export PATH="/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/Current/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

dialog() {
  osascript -e "display dialog \"$1\" with title \"$NAME\" buttons {\"OK\"} default button \"OK\" with icon ${2:-note}" >/dev/null 2>&1
}

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3 /usr/bin/python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3,9))' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
if [ -z "$PY" ]; then
  xcode-select --install >/dev/null 2>&1
  dialog "Für $NAME wird Python 3 benötigt.\n\nBitte die angebotenen Befehlszeilen-Entwicklerwerkzeuge installieren (oder Python von python.org) und die App danach erneut starten." caution
  exit 1
fi

REQ_HASH="$(cat "$APP_RES/requirements.txt" "$PY" 2>/dev/null | shasum | cut -c1-16)"
if [ ! -x "$VENV/bin/python" ] || [ "$(cat "$SUPPORT/.req" 2>/dev/null)" != "$REQ_HASH" ]; then
  osascript -e "display notification \"Erster Start: Komponenten werden geladen (ca. 1–2 Minuten) …\" with title \"$NAME\"" >/dev/null 2>&1
  {
    echo "=== Einrichtung $(date) mit $PY ==="
    rm -rf "$VENV"
    "$PY" -m venv "$VENV" &&
    "$VENV/bin/python" -m pip install --upgrade pip -q &&
    "$VENV/bin/python" -m pip install -q -r "$APP_RES/requirements.txt"
  } >>"$LOG" 2>&1
  if [ $? -ne 0 ]; then
    dialog "Die Einrichtung ist fehlgeschlagen (Internetverbindung?).\n\nDetails: ~/Library/Logs/$NAME.log" stop
    exit 1
  fi
  echo "$REQ_HASH" > "$SUPPORT/.req"
fi

cd "$APP_RES"
echo "=== Start $(date) ===" >>"$LOG"
exec "$VENV/bin/python" -m netzentrale >>"$LOG" 2>&1
'''


def build(dist: Path):
    app = dist / f"{APP_NAME}.app"
    shutil.rmtree(app, ignore_errors=True)
    macos, res = app / "Contents/MacOS", app / "Contents/Resources"
    macos.mkdir(parents=True)
    res.mkdir(parents=True)
    (app / "Contents/Info.plist").write_text(PLIST, encoding="utf-8")
    exe = macos / APP_NAME
    exe.write_text(LAUNCHER.replace("__NAME__", APP_NAME), encoding="utf-8")
    exe.chmod(0o755)
    subprocess.run([sys.executable, str(ROOT / "tools/make_icon.py"), str(res / "AppIcon")], check=True)
    shutil.copytree(ROOT / "netzentrale", res / "netzentrale",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.move(str(res / "AppIcon.png"), res / "netzentrale/icon.png")
    shutil.copy(ROOT / "requirements.txt", res)
    print(f"Gebaut: {app}")
    return app


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "dist"))
