import argparse
import os
import sys
from pathlib import Path

from note_stock.platform import app_folder, prepare_environment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--tray", action="store_true")
    parser.add_argument('--setup-discord',action='store_true',help='Discord Bot作成ウィザードを開く')
    parser.add_argument("--smoke-test", action="store_true", help="起動のみを検証して終了（投稿しない）")
    parser.add_argument("--browser-smoke", action="store_true", help="専用ブラウザーのオフライン起動を検証")
    from note_stock.commands import COMMANDS
    parser.add_argument('--cli',choices=COMMANDS)
    parser.add_argument("--input", type=Path, help="CLI入力JSONファイル")
    parser.add_argument("--id", help="CLIの原稿ID")
    parser.add_argument('--request-key',help='同じ操作の重複受付を防ぐ任意の識別子')
    parser.add_argument("--output", type=Path, help="CLI結果JSONファイル")
    parser.add_argument('--close-browser',action='store_true',help='このアプリが所有する専用ブラウザーだけを終了')
    args = parser.parse_args()
    data = (args.data_dir or app_folder() / "data").resolve()
    # A portable onedir bundle includes Chromium next to its other runtime libraries.
    if getattr(sys, "frozen", False):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(sys._MEIPASS) / "browsers")
    preparation_error = None
    try:
        prepare_environment(data)
    except OSError as exc:
        preparation_error = exc
    if args.cli:
        from note_stock.cli import run_cli
        return run_cli(args,data)
    if args.close_browser:
        import json
        from note_stock.managed_browser import shutdown
        result=shutdown(data)
        discord_result=shutdown(data/'discord-browser')
        result['discord']=discord_result
        result['closed']=result['closed'] and discord_result['closed']
        (data/'browser-close.json').write_text(json.dumps(result),encoding='utf-8')
        return 0 if result['closed'] else 1
    if args.browser_smoke:
        from playwright.sync_api import sync_playwright
        import json
        from note_stock.managed_browser import connect,shutdown
        try:
            with sync_playwright() as pw:
                browser,context = connect(pw,data,'auto')
                page = context.new_page()
                page.set_content('<h1>portable browser OK</h1>')
                assert page.locator('h1').inner_text() == 'portable browser OK'
                again,retained=connect(pw,data,'auto')
                assert any(p.locator('h1').count() and p.locator('h1').inner_text()=='portable browser OK' for p in retained.pages)
                public=context.browser.new_context()
                public.new_page().set_content('<h1>public check OK</h1>')
                public.close()
            assert shutdown(data)['closed']
            result={'result':'passed','network':'not used'}
        except Exception as exc:
            result={'result':'failed','error':str(exc)}
        finally:
            shutdown(data)
        (data / 'browser-smoke.json').write_text(json.dumps(result),encoding='utf-8')
        return 0 if result['result']=='passed' else 1
    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication(sys.argv[:1])
    app.setApplicationName("NoteStockPublisher")
    app.setQuitOnLastWindowClosed(False)
    try:
        if preparation_error:
            raise preparation_error
        prepare_environment(data)
        lock = QLockFile(str(data / "instance.lock"))
        if not lock.tryLock(0):
            raise RuntimeError("このデータフォルダーを使うnoteストックは、すでに起動しています。")
        from note_stock.store import Store
        from note_stock.ui import MainWindow, STYLE
        app.setStyleSheet(STYLE)
        store = Store(data)
        window = MainWindow(store,offline=args.smoke_test)
        if not args.smoke_test and (args.setup_discord or (
                store.setting('discord_onboarding',None) is None and not store.setting('discord_token',''))):
            window.tabs.setCurrentIndex(2)
            QTimer.singleShot(500,window.discord_panel.setup)
        from note_stock.diagnostics import Diagnostics
        warning = Diagnostics(store).record('application_start')
        if warning:
            window.notify(warning, True)
        if args.smoke_test:
            window.timer.stop()
            (data / 'gui-smoke.txt').write_text('GUI startup passed', encoding='utf-8')
            QTimer.singleShot(1200, app.quit)
        if not args.tray or not window.tray.isVisible():
            window.show()
        result = app.exec()
        lock.unlock()
        return result
    except Exception as exc:
        QMessageBox.critical(None, "noteストックを起動できません", str(exc) + "\n書き込み可能なフォルダーへ展開して起動してください。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
