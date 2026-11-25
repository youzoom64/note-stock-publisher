"""Dedicated browser profiles; never attach to the user's everyday browser."""
import json
import os
import re
from pathlib import Path
import subprocess
import time

import psutil


CHOICES={'auto','chrome','edge','chromium'}

class BrowserNotRunning(RuntimeError):
    pass


def installed_browser(kind):
    """Read installation locations only; never inspect personal browser profiles."""
    executable='chrome.exe' if kind=='chrome' else 'msedge.exe'
    candidates=[]
    if os.name=='nt':
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive,rf'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}',0,winreg.KEY_READ|view) as key:
                        candidates.append(Path(winreg.QueryValue(key,None).strip('"')))
                except OSError:
                    pass
    relative=Path('Google/Chrome/Application/chrome.exe') if kind=='chrome' else Path('Microsoft/Edge/Application/msedge.exe')
    for variable in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA'):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable])/relative)
    return next((str(path.resolve()) for path in candidates if path.is_file()),None)


def select_browser(playwright, preference='auto'):
    if preference not in CHOICES:
        raise ValueError('ブラウザーの設定が正しくありません。')
    for kind in (('chrome','edge','chromium') if preference=='auto' else (preference,)):
        executable=playwright.chromium.executable_path if kind=='chromium' else installed_browser(kind)
        if executable and Path(executable).is_file():
            return kind,executable
    raise RuntimeError('選択したブラウザーが見つかりません。設定で別のブラウザーを選んでください。')


def owned_process(record, profile, executable):
    try:
        process=psutil.Process(record['pid'])
        if abs(process.create_time()-record['created'])>0.1:
            return None
        if Path(process.exe()).resolve()!=Path(executable).resolve():
            return None
        argument='--user-data-dir='+str(profile.resolve())
        if argument not in process.cmdline():
            return None
        return process
    except (OSError,KeyError,psutil.Error):
        return None


def connect(playwright, root, preference='auto', *, activate=True, allow_launch=True):
    root=Path(root)
    kind,executable=select_browser(playwright,preference)
    profile=root/'browser-profiles'/kind
    profile.mkdir(parents=True,exist_ok=True)
    record_file=root/f'browser-owner-{kind}.json'
    record=json.loads(record_file.read_text(encoding='utf-8')) if record_file.exists() else {}
    process=owned_process(record,profile,executable)
    if process is not None and record.get('mode')=='interactive_login':
        raise RuntimeError('Googleログイン用の専用ブラウザーが開いています。ログイン後、そのウィンドウを閉じて接続確認を続けてください。')
    if process is None:
        if not allow_launch:
            raise BrowserNotRunning('専用ブラウザーは起動していません。')
        port_file=profile/'DevToolsActivePort'
        if port_file.exists():
            port_file.unlink()
        startup={}
        if not activate and os.name=='nt':
            info=subprocess.STARTUPINFO()
            info.dwFlags|=subprocess.STARTF_USESHOWWINDOW
            info.wShowWindow=4  # SW_SHOWNOACTIVATE: visible, without taking focus.
            startup['startupinfo']=info
        child=subprocess.Popen([executable,'--user-data-dir='+str(profile.resolve()),
            '--remote-debugging-port=0','--remote-debugging-address=127.0.0.1',
            '--no-first-run','--disable-crash-reporter','--disable-background-mode',
            '--disable-extensions','--disable-component-extensions-with-background-pages',
            '--lang=ja',*(['--start-maximized'] if activate else []),'about:blank'],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**startup)
        process=psutil.Process(child.pid)
        record={'pid':process.pid,'created':process.create_time(),'executable':executable,'profile':str(profile)}
        record_file.write_text(json.dumps(record),encoding='utf-8')
    deadline=time.monotonic()+25
    while time.monotonic()<deadline:
        if not process.is_running():
            raise RuntimeError('専用ブラウザーを起動できません。')
        port_file=profile/'DevToolsActivePort'
        if port_file.exists():
            lines=port_file.read_text(encoding='utf-8').splitlines()
            if lines and lines[0].isdigit():
                browser=playwright.chromium.connect_over_cdp('http://127.0.0.1:'+lines[0])
                return browser,browser.contexts[0]
        time.sleep(0.1)
    raise RuntimeError('専用ブラウザーへの接続がタイムアウトしました。')


def startup_blank_page(context):
    """Find an unused tab in the app-owned browser without replacing open work."""
    return next((page for page in context.pages if not page.is_closed() and page.url=='about:blank'),None)


def background_page(browser,context):
    """Create a normal, visible tab without selecting it or raising its window."""
    session=browser.new_browser_cdp_session()
    try:
        with context.expect_page() as created:
            session.send('Target.createTarget',{'url':'about:blank','background':True})
        return created.value
    finally:
        session.detach()


def shutdown(root):
    root=Path(root)
    records=list(root.glob('browser-owner-*.json'))
    if (root/'browser-owner.json').exists():
        records.append(root/'browser-owner.json')
    results=[]
    for record_file in records:
        try:
            results.append(shutdown_record(root,record_file))
        except Exception as exc:
            results.append({'closed':False,'reason':str(exc)})
    return {'closed':all(item['closed'] for item in results),'browsers':results}


def reset_note_login(playwright,root,preference='auto'):
    """An explicit re-login resets only note cookies in this app's profile."""
    if not shutdown(root)['closed']: raise RuntimeError('専用ブラウザーを終了できません。')
    try:
        _,context=connect(playwright,root,preference)
        context.clear_cookies(domain=re.compile(r'(^|\.)note\.com$'))
    finally:
        if not shutdown(root)['closed']: raise RuntimeError('ログイン準備用ブラウザーを終了できません。')


def interactive_login(playwright,root,preference='auto',app_mode=False):
    """Open the app's profile as a regular browser, with automation disconnected."""
    root=Path(root)
    closed=shutdown(root)
    if not closed['closed']:
        raise RuntimeError('専用ブラウザーを終了できません。Googleログインを開始できませんでした。')
    kind,executable=select_browser(playwright,preference)
    if kind=='chromium':
        raise RuntimeError('GoogleログインにはGoogle ChromeまたはMicrosoft Edgeを選択してください。')
    profile=root/'browser-profiles'/kind
    profile.mkdir(parents=True,exist_ok=True)
    target=['--app=https://note.com/login'] if app_mode else ['--new-window','https://note.com/login']
    child=subprocess.Popen([executable,'--user-data-dir='+str(profile.resolve()),
        '--no-first-run','--disable-background-mode','--lang=ja','--start-maximized',
        *target],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    process=psutil.Process(child.pid)
    record={'pid':process.pid,'created':process.create_time(),'executable':executable,
            'profile':str(profile),'mode':'interactive_login'}
    (root/f'browser-owner-{kind}.json').write_text(json.dumps(record),encoding='utf-8')
    return child


def shutdown_record(root,record_file):
    if not record_file.exists():
        return {'closed':True,'reason':'not_started'}
    record=json.loads(record_file.read_text(encoding='utf-8'))
    profile=Path(record['profile']).resolve()
    # Ownership records cannot authorize shutting down a profile outside app data.
    if not profile.is_relative_to(root.resolve()):
        raise RuntimeError('専用ブラウザーの保存先を確認できません。')
    process=owned_process(record,profile,record['executable'])
    if process is None:
        return {'closed':True,'reason':'owned_process_already_exited'}
    if record.get('mode')!='interactive_login':
        try:
            from playwright.sync_api import sync_playwright
            port=(profile/'DevToolsActivePort').read_text(encoding='utf-8').splitlines()[0]
            with sync_playwright() as p:
                browser=p.chromium.connect_over_cdp('http://127.0.0.1:'+port,timeout=5000)
                browser.new_browser_cdp_session().send('Browser.close')
        except Exception:
            pass
    try:
        process.wait(timeout=4)
    except psutil.TimeoutExpired:
        # Re-check ownership immediately before terminating our remaining process.
        process=owned_process(record,profile,record['executable'])
        if process is None:
            return {'closed':True,'reason':'exited_during_shutdown'}
        children=[(p,p.create_time(),p.exe()) for p in process.children(recursive=True)
                  if Path(p.exe()).parent==Path(record['executable']).parent]
        for child,created,exe in children:
            try:
                if child.create_time()==created and child.exe()==exe:
                    child.terminate()
            except psutil.Error:
                pass
        process.terminate()
        process.wait(timeout=4)
    return {'closed':True,'pid':record['pid']}
