# PyInstaller-Bauplan für Netzentrale – derselbe für Windows und macOS.
#   pyinstaller packaging/netzentrale.spec --noconfirm
# Vorher: python tools/make_icon.py build/AppIcon   (erzeugt .png/.icns/.ico)
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))
from netzentrale import APP_NAME, __version__  # noqa: E402

datas, binaries, hidden = [], [], []
for pkg in ("zeroconf", "paho"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hidden += h
hidden += collect_submodules("keyring.backends")          # Schlüsselbund / Windows-Anmeldeinformationen
hidden += collect_submodules("netzentrale")
datas += [(str(ROOT / "netzentrale" / "icon.png"), "netzentrale")]          # Fenster-/Taskleistensymbol
datas += [(str(ROOT / "netzentrale" / "throughput.py"), "gegenstelle")]   # für den Pi mitliefern

a = Analysis([str(ROOT / "packaging" / "run_netzentrale.py")], pathex=[str(ROOT)], binaries=binaries,
             datas=datas, hiddenimports=hidden, excludes=["tkinter", "PySide6.Qt3DCore", "PySide6.QtWebEngineCore",
                                                         "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtPdf"],
             noarchive=False)
pyz = PYZ(a.pure)

is_win = sys.platform.startswith("win")
icon = str(ROOT / "build" / ("AppIcon.ico" if is_win else "AppIcon.icns"))
version_file = str(ROOT / "build" / "version_info.txt") if is_win and (ROOT / "build" / "version_info.txt").exists() else None

exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=APP_NAME, console=False, icon=icon,
          version=version_file, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME, upx=False)

if sys.platform == "darwin":
    app = BUNDLE(coll, name=f"{APP_NAME}.app", icon=icon, bundle_identifier="de.peterbode.netzentrale",
                 info_plist={
                     "CFBundleShortVersionString": __version__, "CFBundleVersion": __version__,
                     "NSHighResolutionCapable": True, "LSMinimumSystemVersion": "11.0",
                     "NSLocalNetworkUsageDescription":
                         f"{APP_NAME} durchsucht das lokale Netzwerk und prüft, ob Geräte erreichbar sind.",
                     "NSLocationWhenInUseUsageDescription":
                         "macOS gibt WLAN-Namen nur mit Standortfreigabe heraus.",
                 })
