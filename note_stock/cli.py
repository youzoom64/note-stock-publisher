import json
from pathlib import Path
import sys

from .qt import QCoreApplication, QLockFile

from .commands import Commands
from .ipc import request_running
from .store import Store


def run_cli(args, data):
    application = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    result = None
    try:
        payload = json.loads(args.input.read_text(encoding='utf-8-sig')) if args.input else {}
        request = {'command':args.cli,'id':args.id,'data':payload,'request_key':getattr(args,'request_key',None)}
        result = request_running(data,request)
        if result is None:
            lock = QLockFile(str(data/'instance.lock'))
            if not lock.tryLock(0):
                raise RuntimeError('アプリは起動中ですが応答がありません。自動で再実行しないでください。')
            try:
                store=Store(data)
                if args.cli not in {'status','list','get','export','job'}:
                    store.recover()
                result = Commands(store).handle(request)
            finally:
                lock.unlock()
    except Exception as exc:
        result = {'ok':False,'error':str(exc)}
    text=json.dumps(result,ensure_ascii=False,indent=2)
    # Windowed Windows executables have no stdout. The default result file makes
    # the same CLI usable from PowerShell without adding a console to the GUI.
    output = args.output or (data/'cli-result.json' if sys.stdout is None else None)
    if output:
        Path(output).write_text(text,encoding='utf-8')
    if sys.stdout is not None:
        print(text)
    return 0 if result.get('ok') else 1
