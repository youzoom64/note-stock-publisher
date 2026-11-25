"""Final publication cannot leave a stock window without a live app decision."""
import uuid
import json
from urllib.parse import urlparse
from urllib.parse import urlparse


def install(page, handler):
    name='noteStockGate_'+uuid.uuid4().hex
    def trusted(source,data):
        if source.get('page')!=page or source.get('frame')!=page.main_frame or urlparse(source['frame'].url).hostname not in {'editor.note.com','note.com'}:
            raise RuntimeError('noteの編集画面以外からの操作は受け付けません。')
        return handler(source,data)
    page.expose_binding(name,trusted)
    script="""(binding)=>{
        window.__noteStockGate=data=>window[binding](data);
        if(window.__noteStockGateInstalled)return;
        window.__noteStockGateInstalled=true;
        const final=(method,url)=>{try {const u=new URL(url,location.href);
            return method.toUpperCase()==='PUT'&&u.hostname==='note.com'&&/^\\/api\\/v1\\/text_notes\\/\\d+$/.test(u.pathname);
        }catch{return false}};
        const authorize=data=>typeof window.__noteStockGate==='function' ? window.__noteStockGate(data) : Promise.reject(new Error('アプリに接続してください'));
        const open=XMLHttpRequest.prototype.open,send=XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.open=function(method,url,...rest){this.__noteStockRequest={method:String(method),url:new URL(url,location.href).href};return open.call(this,method,url,...rest)};
        XMLHttpRequest.prototype.send=function(body){const request=this.__noteStockRequest;
            if(!request||!final(request.method,request.url))return send.call(this,body);
            const xhr=this;
            Promise.resolve(authorize({...request,body:String(body)})).then(result=>{
                if(typeof result==='string')send.call(xhr,result);
                else {xhr.abort();xhr.dispatchEvent(new Event('abort'));}
            }).catch(()=>{xhr.abort();xhr.dispatchEvent(new Event('abort'));});
        };
        const fetch=window.fetch.bind(window);
        window.fetch=async function(input,init){const request=input instanceof Request?input:null;
            const url=request?request.url:new URL(input,location.href).href;
            const method=String(init?.method||request?.method||'GET');
            if(!final(method,url))return fetch(input,init);
            const body=init?.body!==undefined?String(init.body):(request?await request.clone().text():'');
            const approved=await authorize({url,method,body});
            if(typeof approved!=='string')throw new Error('公開せずストックへ保存しました');
            return request?fetch(new Request(input,{...init,body:approved})):fetch(input,{...init,body:approved});
        };
    }"""
    page.add_init_script('('+script+')('+json.dumps(name)+');')
    page.evaluate(script,name)
