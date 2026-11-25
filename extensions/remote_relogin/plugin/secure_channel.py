"""Versioned authenticated encryption; relay never receives the pairing secret."""
import base64
import json
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PROTOCOL=b'note-remote-login-v1'


def encode(raw): return base64.b64encode(raw).decode('ascii')
def decode(value): return base64.b64decode(value,validate=True)
def nonce(number): return number.to_bytes(12,'big')


class Handshake:
    def __init__(self,recovery_id,code_hash):
        self.recovery_id=recovery_id
        self.salt=bytes.fromhex(code_hash)
        self.private=ec.generate_private_key(ec.SECP256R1())
        self.public=self.private.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)

    def accept(self,client_public,proof):
        raw=decode(client_public)
        if len(raw)!=65: raise ValueError('Invalid public key')
        peer=ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),raw)
        info=PROTOCOL+self.recovery_id.encode()+self.public+raw
        keys=HKDF(algorithm=hashes.SHA256(),length=64,salt=self.salt,info=info).derive(self.private.exchange(ec.ECDH(),peer))
        if AESGCM(keys[:32]).decrypt(nonce(0),decode(proof),info)!=b'pair':
            raise ValueError('Pairing rejected')
        return Channel(keys[:32],keys[32:],info)


class Channel:
    def __init__(self,receive_key,send_key,context):
        self.receive=AESGCM(receive_key); self.send=AESGCM(send_key)
        self.context=context; self.last=0

    def unpack(self,envelope):
        number=envelope['n']
        if type(number) is not int or not self.last<number<2**48:
            raise ValueError('Expired or replayed request')
        aad=self.context+b'rpc'+nonce(number)
        value=json.loads(self.receive.decrypt(nonce(number),decode(envelope['box']),aad))
        self.last=number
        return number,value

    def pack(self,number,value):
        return {'n':number,'box':encode(self.send.encrypt(nonce(number),json.dumps(value,ensure_ascii=False).encode(),self.context+b'rpc'+nonce(number)))}

    def welcome(self,value):
        return encode(self.send.encrypt(nonce(0),json.dumps(value).encode(),self.context))
