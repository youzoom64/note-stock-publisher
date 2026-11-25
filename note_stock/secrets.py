"""Windows user-bound encryption for native article settings (never credentials in logs)."""
import base64
import ctypes
from ctypes import wintypes
import json


class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]


def _apis():
    crypt=ctypes.windll.crypt32
    crypt.CryptProtectData.argtypes=[ctypes.POINTER(Blob),wintypes.LPCWSTR,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    crypt.CryptProtectData.restype=wintypes.BOOL
    crypt.CryptUnprotectData.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    crypt.CryptUnprotectData.restype=wintypes.BOOL
    ctypes.windll.kernel32.LocalFree.argtypes=[ctypes.c_void_p]
    ctypes.windll.kernel32.LocalFree.restype=ctypes.c_void_p
    return crypt


def protect(value):
    crypt=_apis()
    raw=json.dumps(value,ensure_ascii=False).encode('utf-8')
    buffer=ctypes.create_string_buffer(raw)
    source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    target=Blob()
    if not crypt.CryptProtectData(ctypes.byref(source),'NoteStock native article',None,None,None,1,ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        return base64.b64encode(ctypes.string_at(target.data,target.size)).decode('ascii')
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


def unprotect(value):
    crypt=_apis()
    raw=base64.b64decode(value)
    buffer=ctypes.create_string_buffer(raw)
    source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    target=Blob()
    if not crypt.CryptUnprotectData(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        return json.loads(ctypes.string_at(target.data,target.size).decode('utf-8'))
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)
