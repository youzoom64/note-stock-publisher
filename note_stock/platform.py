import os
import sys
from pathlib import Path


def app_folder():
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


def prepare_environment(data):
    """Keep our caches and browser temporary data inside the chosen data directory."""
    cache = Path(data) / "temp"
    cache.mkdir(parents=True, exist_ok=True)
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = str(cache)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(Path(data)/'browsers')
    os.environ['PLAYWRIGHT_NODEJS_PATH']=str(app_folder()/'runtime32'/'node-v22.23.5-win-x86'/'node.exe')


def set_autostart(enabled):
    if sys.platform != "win32":
        raise ValueError("自動起動はWindowsで利用できます。")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            if getattr(sys, "frozen", False):
                command = f'"{sys.executable}"'
            else:
                command = f'"{sys.executable}" "{app_folder() / "main.py"}"'
            winreg.SetValueEx(key, "NoteStockPublisher32", 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, "NoteStockPublisher32")
            except FileNotFoundError:
                pass


def autostart_enabled():
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            return bool(winreg.QueryValueEx(key, "NoteStockPublisher32")[0])
    except FileNotFoundError:
        return False
