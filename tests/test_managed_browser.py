from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from note_stock import managed_browser as managed


def test_selection_falls_back_and_explicit_choice_does_not(tmp_path,monkeypatch):
    monkeypatch.delenv('NOTE_STOCK_BROWSER_EXE',raising=False)
    bundled=tmp_path/'chromium.exe'
    bundled.touch()
    edge=tmp_path/'edge.exe'
    edge.touch()
    pw=SimpleNamespace(chromium=SimpleNamespace(executable_path=str(bundled)))
    monkeypatch.setattr(managed,'installed_browser',lambda kind:str(edge) if kind=='edge' else None)
    assert managed.select_browser(pw)==('edge',str(edge))
    with pytest.raises(RuntimeError):
        managed.select_browser(pw,'chromium')
    with pytest.raises(RuntimeError):
        managed.select_browser(pw,'chrome')
    with pytest.raises(ValueError):
        managed.select_browser(pw,'invalid')
    monkeypatch.setattr(managed,'installed_browser',lambda kind:str(bundled) if kind=='chrome' else None)
    assert managed.select_browser(pw,'chromium')==('chromium',str(bundled))


def test_reconnect_keeps_same_owned_browser_and_shutdown_leaves_other_browser(tmp_path,monkeypatch):
    monkeypatch.setenv('NOTE_STOCK_BROWSER_EXE',str(tmp_path/'not-a-browser.exe'))
    monkeypatch.setenv('NOTE_STOCK_TEST_CDP_FILE',str(tmp_path/'not-a-connection.json'))
    with sync_playwright() as pw:
        unrelated=pw.chromium.launch(headless=False,executable_path=managed.installed_browser('chrome'))
        unrelated_page=unrelated.new_page()
        unrelated_page.set_content('<title>Other browser</title>')
        try:
            browser,context=managed.connect(pw,tmp_path,'chromium')
            page=context.new_page()
            page.goto('data:text/html,<title>App tab retained</title>')
            record_path=tmp_path/'browser-owner-chromium.json'
            before=record_path.read_text()
            again,retained=managed.connect(pw,tmp_path,'chromium')
            assert record_path.read_text()==before
            assert any(p.title()=='App tab retained' for p in retained.pages)
            assert managed.shutdown(tmp_path)['closed']
            assert unrelated_page.title()=='Other browser'
        finally:
            managed.shutdown(tmp_path)
            unrelated.close()


def test_shutdown_rejects_profile_outside_app_data(tmp_path):
    import json
    record=tmp_path/'browser-owner-chrome.json'
    record.write_text(json.dumps({'profile':str(tmp_path.parent/'other-profile'),'executable':'unused'}))
    result=managed.shutdown(tmp_path)
    assert not result['closed']
    assert '保存先' in result['browsers'][0]['reason']


def test_developer_environment_cannot_redirect_browser_selection(tmp_path,monkeypatch):
    monkeypatch.setenv('NOTE_STOCK_BROWSER_EXE',str(tmp_path/'not-a-browser.exe'))
    monkeypatch.setenv('NOTE_STOCK_TEST_CDP_FILE',str(tmp_path/'not-a-connection.json'))
    browser=tmp_path/'chromium.exe'
    browser.touch()
    pw=SimpleNamespace(chromium=SimpleNamespace(executable_path=str(browser)))
    private=tmp_path/'private-x86.exe'
    private.touch()
    monkeypatch.setattr(managed,'installed_browser',lambda kind:str(private) if kind=='chrome' else None)
    assert managed.select_browser(pw,'chromium')==('chromium',str(private))
