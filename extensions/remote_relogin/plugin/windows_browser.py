"""Visible, app-owned Chrome/Edge window only. No arbitrary desktop target API."""
import ctypes
from ctypes import wintypes
import json
import os
import threading
from PySide6.QtCore import QBuffer,QByteArray,QIODevice
from PySide6.QtGui import QImage
from note_stock.managed_browser import owned_process


def matches_recovery_process(state,record):
    """A dedicated profile alone is insufficient: this exact launch must own it."""
    return (state.get('pid')==record.get('pid') and isinstance(state.get('created'),(int,float)) and
        isinstance(record.get('created'),(int,float)) and abs(state['created']-record['created'])<.1)


class BrowserWindow:
    def __init__(self,store):
        self.store=store; self.lock=threading.RLock(); self.pressed=False
        self.binding=None; self.recovery_id=None

    def target(self):
        if os.name!='nt': raise RuntimeError('Windowsでのみ使用できます。')
        state=self.store.setting('remote_relogin_window',{})
        if not state.get('ready') or not state.get('recovery_id') or not state.get('pid') or not state.get('created'):
            raise RuntimeError('今回の再ログイン用ブラウザーを確認できません。接続を開始し直してください。')
        user=ctypes.windll.user32
        user.GetForegroundWindow.restype=wintypes.HWND
        user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
        user.IsWindowVisible.argtypes=[wintypes.HWND]
        user.IsIconic.argtypes=[wintypes.HWND]
        user.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
        user.GetWindow.argtypes=[wintypes.HWND,wintypes.UINT]
        user.GetWindow.restype=wintypes.HWND
        user.GetClassNameW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
        processes={}
        for path in self.store.root.glob('browser-owner-*.json'):
            try:
                record=json.loads(path.read_text(encoding='utf-8'))
                profile=self.store.root/'browser-profiles'/path.stem.removeprefix('browser-owner-')
                process=owned_process(record,profile,record['executable'])
                if process and record.get('mode')=='interactive_login' and matches_recovery_process(state,record):
                    processes[process.pid]=process.create_time()
            except (ValueError,OSError,KeyError): continue
        found=[]
        callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
        def visit(hwnd,_):
            pid=wintypes.DWORD(); user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            if pid.value in processes and user.IsWindowVisible(hwnd) and not user.IsIconic(hwnd):
                name=ctypes.create_unicode_buffer(256)
                user.GetClassNameW(hwnd,name,len(name))
                if name.value!='Chrome_WidgetWin_1' or user.GetWindow(hwnd,4): return True
                rect=wintypes.RECT()
                if user.GetWindowRect(hwnd,ctypes.byref(rect)):
                    found.append((hwnd,pid.value,processes[pid.value]))
            return True
        user.EnumWindows(callback_type(visit),0)
        if not found: raise RuntimeError('専用ブラウザーのログイン画面を待っています。')
        recovery_id=state['recovery_id']
        if self.recovery_id!=recovery_id:
            self.binding=None; self.recovery_id=recovery_id
        if self.binding is None:
            if len(found)!=1: raise RuntimeError('専用ブラウザー本体を一意に特定できません。別の窓へは接続しません。')
            self.binding=found[0]
        if self.binding not in found: raise RuntimeError('接続対象のブラウザーが閉じられたか変更されました。')
        hwnd=self.binding[0]
        rect=wintypes.RECT(); user.GetWindowRect(hwnd,ctypes.byref(rect))
        width,height=rect.right-rect.left,rect.bottom-rect.top
        if width<100 or height<100 or width>8000 or height>8000: raise RuntimeError('専用ブラウザーの表示範囲を確認できません。')
        return hwnd,rect,width,height


    def frame(self):
        with self.lock:
            hwnd,rect,width,height=self.target()
            user,gdi=ctypes.windll.user32,ctypes.windll.gdi32
            user.GetWindowDC.restype=wintypes.HDC; user.GetWindowDC.argtypes=[wintypes.HWND]
            gdi.CreateCompatibleDC.restype=wintypes.HDC; gdi.CreateCompatibleDC.argtypes=[wintypes.HDC]
            gdi.CreateCompatibleBitmap.restype=wintypes.HBITMAP; gdi.CreateCompatibleBitmap.argtypes=[wintypes.HDC,ctypes.c_int,ctypes.c_int]
            gdi.SelectObject.restype=wintypes.HGDIOBJ; gdi.SelectObject.argtypes=[wintypes.HDC,wintypes.HGDIOBJ]
            source=user.GetWindowDC(hwnd); dc=gdi.CreateCompatibleDC(source); bitmap=gdi.CreateCompatibleBitmap(source,width,height)
            previous=gdi.SelectObject(dc,bitmap)
            class Header(ctypes.Structure):
                _fields_=[('size',wintypes.DWORD),('width',wintypes.LONG),('height',wintypes.LONG),('planes',wintypes.WORD),
                          ('bits',wintypes.WORD),('compression',wintypes.DWORD),('image_size',wintypes.DWORD),
                          ('x',wintypes.LONG),('y',wintypes.LONG),('used',wintypes.DWORD),('important',wintypes.DWORD)]
            header=Header(ctypes.sizeof(Header),width,-height,1,32,0,width*height*4,0,0,0,0)
            pixels=ctypes.create_string_buffer(width*height*4)
            user.PrintWindow.argtypes=[wintypes.HWND,wintypes.HDC,wintypes.UINT]
            gdi.GetDIBits.argtypes=[wintypes.HDC,wintypes.HBITMAP,wintypes.UINT,wintypes.UINT,ctypes.c_void_p,ctypes.c_void_p,wintypes.UINT]
            try:
                if not user.PrintWindow(hwnd,dc,2) or not gdi.GetDIBits(dc,bitmap,0,height,pixels,ctypes.byref(header),0):
                    raise RuntimeError('専用ブラウザーの画面を取得できません。')
                image=QImage(pixels.raw,width,height,width*4,QImage.Format.Format_RGB32).copy()
                if image.width()>1440: image=image.scaledToWidth(1440)
                raw=QByteArray(); output=QBuffer(raw); output.open(QIODevice.OpenModeFlag.WriteOnly)
                if not image.save(output,'JPEG',68): raise RuntimeError('画面の変換に失敗しました。')
                return bytes(raw),str(hwnd)+':'+str(width)+':'+str(height),width,height
            finally:
                gdi.SelectObject(dc,previous)
                gdi.DeleteObject.argtypes=[wintypes.HGDIOBJ]; gdi.DeleteObject(bitmap)
                gdi.DeleteDC.argtypes=[wintypes.HDC]; gdi.DeleteDC(dc)
                user.ReleaseDC.argtypes=[wintypes.HWND,wintypes.HDC]; user.ReleaseDC(hwnd,source)

    def focus(self,frame):
        hwnd,rect,width,height=self.target()
        if frame!=str(hwnd)+':'+str(width)+':'+str(height): raise ValueError('画面が変わりました。画面更新を待ってください。')
        user=ctypes.windll.user32
        user.SetForegroundWindow.argtypes=[wintypes.HWND]
        user.SetForegroundWindow(hwnd)
        if user.GetForegroundWindow()!=hwnd: raise RuntimeError('専用ブラウザーに入力を渡せませんでした。')
        return rect,width,height

    def pointer(self,frame,x,y,phase):
        if type(x) not in (float,int) or type(y) not in (float,int) or not 0<=x<=1 or not 0<=y<=1 or phase not in ('down','up','move'):
            raise ValueError('Invalid pointer')
        with self.lock:
            rect,width,height=self.focus(frame)
            user=ctypes.windll.user32
            user.SetCursorPos(rect.left+min(width-1,int(x*width)),rect.top+min(height-1,int(y*height)))
            if phase=='down': user.mouse_event(2,0,0,0,0); self.pressed=True
            elif phase=='up': user.mouse_event(4,0,0,0,0); self.pressed=False

    def scroll(self,frame,delta):
        if type(delta) is not int or abs(delta)>1200: raise ValueError('Invalid scroll')
        with self.lock:
            self.focus(frame); ctypes.windll.user32.mouse_event(0x800,0,0,delta,0)

    def text(self,frame,value):
        if not isinstance(value,str) or len(value)>2000 or any(ord(c)<32 for c in value): raise ValueError('Invalid text')
        with self.lock:
            self.focus(frame)
            class Key(ctypes.Structure):
                _fields_=[('vk',wintypes.WORD),('scan',wintypes.WORD),('flags',wintypes.DWORD),('time',wintypes.DWORD),('extra',ctypes.c_size_t)]
            class Mouse(ctypes.Structure):
                _fields_=[('x',wintypes.LONG),('y',wintypes.LONG),('data',wintypes.DWORD),('flags',wintypes.DWORD),('time',wintypes.DWORD),('extra',ctypes.c_size_t)]
            class Union(ctypes.Union): _fields_=[('key',Key),('mouse',Mouse)]
            class Input(ctypes.Structure): _fields_=[('type',wintypes.DWORD),('data',Union)]
            inputs=[]; encoded=value.encode('utf-16-le')
            for i in range(0,len(encoded),2):
                unit=int.from_bytes(encoded[i:i+2],'little')
                for flag in (4,6):
                    item=Input(); item.type=1; item.data.key=Key(0,unit,flag,0,0); inputs.append(item)
            if inputs:
                array=(Input*len(inputs))(*inputs)
                if ctypes.windll.user32.SendInput(len(inputs),array,ctypes.sizeof(Input))!=len(inputs): raise RuntimeError('文字入力に失敗しました。')

    def key(self,frame,key):
        keys={'ENTER':13,'TAB':9,'BACKSPACE':8,'ESCAPE':27,'DELETE':46,'LEFT':37,'UP':38,'RIGHT':39,'DOWN':40}
        if key not in keys: raise ValueError('Unsupported key')
        with self.lock:
            self.focus(frame)
            user=ctypes.windll.user32
            user.keybd_event(keys[key],0,0,0); user.keybd_event(keys[key],0,2,0)

    def release(self):
        if self.pressed and os.name=='nt': ctypes.windll.user32.mouse_event(4,0,0,0,0)
        self.pressed=False

    def finish_login(self):
        # Google sign-in intentionally uses a normal dedicated browser. Closing
        # its own window lets the base app reconnect and verify the note account.
        with self.lock:
            hwnd,_,_,_=self.target()
            ctypes.windll.user32.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
            ctypes.windll.user32.PostMessageW(hwnd,0x10,0,0)
