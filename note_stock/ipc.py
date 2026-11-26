"""User-only named pipe for CLI control of the running GUI."""
import hashlib
import json

from .qt import QLocalServer, QLocalSocket


def pipe_name(root):
    return 'NoteStock_' + hashlib.sha256(str(root.resolve()).casefold().encode()).hexdigest()[:24]


class CommandServer:
    def __init__(self, store, handler, parent):
        self.server=QLocalServer(parent)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.handler=handler
        self.clients={}
        self.server.newConnection.connect(self.accept)
        name=pipe_name(store.root)
        QLocalServer.removeServer(name)
        if not self.server.listen(name):
            raise RuntimeError('CLI接続口を起動できません: '+self.server.errorString())

    def accept(self):
        while self.server.hasPendingConnections():
            client=self.server.nextPendingConnection()
            self.clients[client]=bytearray()
            client.readyRead.connect(lambda client=client:self.read(client))
            client.disconnected.connect(lambda client=client:self.remove(client))
            self.read(client)

    def remove(self, client):
        self.clients.pop(client,None)
        client.deleteLater()

    def read(self, client):
        if client not in self.clients:
            return
        data=self.clients[client]
        data.extend(bytes(client.readAll()))
        if len(data)>10*1024*1024:
            response={'ok':False,'error':'入力は10MB以内にしてください。'}
        elif b'\n' not in data:
            return
        else:
            try:
                request=json.loads(data.split(b'\n',1)[0])
                response=self.handler(request)
            except Exception as exc:
                response={'ok':False,'error':str(exc)}
        client.write((json.dumps(response,ensure_ascii=False)+'\n').encode())
        client.flush()
        client.disconnectFromServer()


def request_running(root, request):
    client=QLocalSocket()
    client.connectToServer(pipe_name(root))
    if not client.waitForConnected(500):
        return None
    client.write((json.dumps(request,ensure_ascii=False)+'\n').encode())
    client.flush()
    result=bytearray()
    while b'\n' not in result:
        if not client.bytesAvailable() and not client.waitForReadyRead(10000):
            raise RuntimeError('実行中アプリから応答がありません。コマンドを自動で再送しないでください。')
        result.extend(bytes(client.readAll()))
    client.disconnectFromServer()
    return json.loads(result.split(b'\n',1)[0])
