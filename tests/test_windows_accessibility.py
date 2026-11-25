import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows UI Automation")
def test_first_visible_window_initializes_uia_without_com_exception(tmp_path):
    script = tmp_path / "check_uia.py"
    script.write_text(r'''
import ctypes
import faulthandler
import sys
from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import QDialog, QVBoxLayout, QPushButton
from note_stock.windows_accessibility import create_application

faulthandler.enable()
kernel = ctypes.WinDLL("kernel32")
callback_type = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))
exceptions = []
@callback_type
def handler(info):
    code = ctypes.cast(info[0], ctypes.POINTER(ctypes.c_ulong))[0]
    if code == 0x8001010D:
        exceptions.append(code)
    return 0
kernel.AddVectoredExceptionHandler.restype = ctypes.c_void_p
kernel.RemoveVectoredExceptionHandler.argtypes = [ctypes.c_void_p]
registration = kernel.AddVectoredExceptionHandler(1, handler)
app = create_application([])
window = QDialog()
window.setWindowTitle("UI Automation verification")
layout = QVBoxLayout(window)
layout.addWidget(QPushButton("Verify accessible button"))
window.show()
process = QProcess()
command = (
    "Add-Type -AssemblyName UIAutomationClient; "
    "Add-Type -AssemblyName UIAutomationTypes; "
    "$element=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]"
    + str(int(window.winId())) + "); "
    "Write-Output $element.Current.Name; "
    "$buttons=$element.FindAll("
    "[System.Windows.Automation.TreeScope]::Descendants,"
    "[System.Windows.Automation.Condition]::TrueCondition); "
    "foreach($button in $buttons){Write-Output $button.Current.Name}"
)
process.finished.connect(app.quit)
process.start("powershell", ["-NoProfile", "-Command", command])
QTimer.singleShot(15000, app.quit)
app.exec()
assert process.state() == QProcess.ProcessState.NotRunning, "UIA query timed out"
output = bytes(process.readAllStandardOutput()).decode(errors="replace")
assert process.exitCode() == 0, bytes(process.readAllStandardError())
assert "UI Automation verification" in output, output
assert "Verify accessible button" in output, output
kernel.RemoveVectoredExceptionHandler(registration)
assert not exceptions, exceptions
print("UIA query succeeded; COM exception count 0")
''', encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, "-B", str(script)], env=env,
        capture_output=True, text=True, timeout=25,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "COM exception count 0" in result.stdout
    assert "Windows fatal exception" not in result.stderr
