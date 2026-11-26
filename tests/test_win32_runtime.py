import struct
import asyncio
import sys
from pathlib import Path

from note_stock.managed_browser import is_x86_executable, installed_browser


def test_machine_header_rejects_x64_and_malformed_files(tmp_path):
    executable=tmp_path/'sample.exe'
    for machine,expected in ((0x14c,True),(0x8664,False)):
        header=bytearray(70)
        header[:2]=b'MZ'
        struct.pack_into('<I',header,60,64)
        header[64:68]=b'PE\0\0'
        struct.pack_into('<H',header,68,machine)
        executable.write_bytes(header)
        assert is_x86_executable(executable) is expected
    executable.write_bytes(b'MZ')
    assert not is_x86_executable(executable)
    assert not is_x86_executable(tmp_path/'missing.exe')


def test_private_browser_has_priority_over_installed_browser(tmp_path,monkeypatch):
    monkeypatch.setattr('note_stock.platform.app_folder',lambda:tmp_path)
    private=tmp_path/'runtime32'/'chrome-win32'/'chrome.exe'
    private.parent.mkdir(parents=True)
    private.touch()
    monkeypatch.setattr('note_stock.managed_browser.is_x86_executable',lambda p:Path(p)==private)
    assert Path(installed_browser('chrome'))==private


def test_discord_locks_are_created_in_the_running_loop(tmp_path):
    from note_stock.store import Store
    from note_stock.discord_bot import NoteClient
    from note_stock.discord_flow import flow
    from note_stock.schedule_activity import ScheduleActivity
    client=NoteClient(Store(tmp_path))
    screen=flow(client)
    activity=ScheduleActivity(client.store)
    assert client.recovery_ui_lock is None
    assert screen._lock is None and activity._auth_lock is None
    async def check():
        async with screen.lock:
            if sys.version_info<(3,10):assert screen.lock._loop is asyncio.get_running_loop()
        async with activity.auth_lock:
            if sys.version_info<(3,10):assert activity.auth_lock._loop is asyncio.get_running_loop()
        await client.close()
    asyncio.run(check())
