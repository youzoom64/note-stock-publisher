import json
import faulthandler
import logging
import sys
import threading
import traceback
from datetime import datetime, timezone

_write_lock = threading.RLock()
DEFAULT_ENABLED = True

class Diagnostics:
    """Session diagnostics; the operational history lives separately in SQLite."""
    def __init__(self, store):
        self.store = store

    def enabled(self):
        session = getattr(self.store, 'diagnostic_session', None)
        return session.enabled if session else self.store.setting('diagnostics', DEFAULT_ENABLED)

    def record(self, event, **fields):
        try:
            with _write_lock:
                if not self.enabled():
                    return ""
                folder = self.store.root / "logs"
                folder.mkdir(exist_ok=True)
                row = {"at": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
                with (folder / "diagnostics.jsonl").open("a", encoding="utf-8") as out:
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
            return ""
        except Exception as exc:
            return "診断ログを保存できません: " + str(exc)

    def error(self, event, exc=None, **fields):
        try:
            enabled = self.enabled()
        except Exception:
            return
        if not enabled:
            return
        if isinstance(exc, BaseException):
            fields['exception'] = type(exc).__name__
            fields['stack'] = [f'{frame.filename}:{frame.lineno}:{frame.name}'
                               for frame in traceback.extract_tb(exc.__traceback__, limit=-12)]
        self.record(event, level='error', **fields)


class ErrorHandler(logging.Handler):
    def __init__(self, diagnostics):
        super().__init__(logging.ERROR)
        self.diagnostics = diagnostics

    def emit(self, record):
        # Library messages can contain HTTP bodies or credentials.
        self.diagnostics.error('library_error', record.exc_info[1] if record.exc_info else None,
                               logger=record.name, source=record.funcName, line=record.lineno)


class DiagnosticSession:
    """Enable error hooks for a single GUI session and reset logs once."""
    def __init__(self, store):
        self.store = store
        self.enabled = False
        self.started = False
        store.diagnostic_session = self
        self.log = Diagnostics(store)

    def configure(self, enabled):
        from PySide6.QtCore import qInstallMessageHandler

        with _write_lock:
            if enabled == self.enabled:
                return
            if not enabled:
                self.enabled = False
                for logger in self.loggers:
                    logger.removeHandler(self.handler)
                sys.excepthook = self.previous_hook
                threading.excepthook = self.previous_thread_hook
                qInstallMessageHandler(self.previous_qt_handler)
                faulthandler.disable()
                self.fatal.close()
                return
            folder = self.store.root / 'logs'
            folder.mkdir(exist_ok=True)
            if not self.started:
                (folder/'diagnostics.jsonl').write_text('',encoding='utf-8')
                (self.store.root/'startup.log').unlink(missing_ok=True)
            self.fatal = (folder/'fatal.log').open('a' if self.started else 'w',encoding='utf-8')
            faulthandler.enable(self.fatal, all_threads=True)
            self.previous_hook = sys.excepthook
            self.previous_thread_hook = threading.excepthook
            sys.excepthook = lambda _kind, error, _trace: self.log.error('unhandled_exception',error)
            threading.excepthook = lambda args: self.log.error('thread_exception',args.exc_value,thread=getattr(args.thread,'name',None))
            self.previous_qt_handler = qInstallMessageHandler(self.qt_message)
            self.handler = ErrorHandler(self.log)
            self.loggers = [logging.getLogger(name) for name in ('discord','aiohttp','asyncio')]
            for logger in self.loggers:
                logger.addHandler(self.handler)
            self.enabled = True
            self.started = True

    def qt_message(self, kind, context, _message):
        if kind.name in {'QtCriticalMsg','QtFatalMsg'}:
            self.log.error('qt_error', severity=kind.name, source=context.file, line=context.line)
