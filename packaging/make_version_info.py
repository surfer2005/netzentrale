"""Erzeugt die Windows-Versionsinfo (Eigenschaften → Details der .exe)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from netzentrale import APP_NAME, __version__  # noqa: E402

nums = (__version__.split(".") + ["0", "0", "0"])[:4]
t = ", ".join(n if n.isdigit() else "0" for n in nums)
(ROOT / "build").mkdir(exist_ok=True)
(ROOT / "build" / "version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({t}), prodvers=({t}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
  kids=[StringFileInfo([StringTable('040704b0', [
    StringStruct('CompanyName', 'Peter Bode'),
    StringStruct('FileDescription', '{APP_NAME} – Fernzugriff und Netzwerk-Werkzeuge'),
    StringStruct('FileVersion', '{__version__}'),
    StringStruct('ProductName', '{APP_NAME}'),
    StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [0x0407, 1200])])]
)
""", encoding="utf-8")
print("version_info.txt geschrieben")
