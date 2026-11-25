"""Restore a copied document through note's own ProseMirror schema and view."""
import hashlib
import json


def document_fingerprint(document):
    def normalize(value):
        if isinstance(value,list):
            return [normalize(child) for child in value]
        if not isinstance(value,dict):
            return value
        result={key:normalize(child) for key,child in value.items()}
        # note regenerates this display cache asynchronously for the same media ID.
        # Keep every identity, URL, caption, and user-authored field unchanged.
        if value.get('type') in {'embed','attachment'} and isinstance(result.get('attrs'),dict):
            result['attrs'].pop('htmlForEmbed',None)
        return result
    return hashlib.sha256(json.dumps(normalize(document),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


RESTORE_DOCUMENT=r'''(element,document)=>{
    let found=null;
    for(let dom=element;dom&&!found;dom=dom.parentElement){
        const key=Object.keys(dom).find(name=>name.startsWith('__reactFiber$'));
        let fiber=key?dom[key]:null;
        for(let level=0;fiber&&level<25&&!found;level++,fiber=fiber.return){
            let hook=fiber.memoizedState;
            for(let index=0;hook&&index<100&&!found;index++,hook=hook.next){
                const value=hook.memoizedState;
                const candidates=[value,value?.current,value?.view,value?.editorView,Array.isArray(value)?value[0]:null];
                found=candidates.find(view=>view?.dom===element&&view?.state?.doc&&typeof view.dispatch==='function');
            }
        }
        if(dom.tagName==='BODY')break;
    }
    if(!found)throw new Error('noteの本文編集機能へ接続できません。複製した原稿は公開しません。');
    const doc=found.state.doc.type.schema.nodeFromJSON(document);
    let transaction=found.state.tr.replaceWith(0,found.state.doc.content.size,doc.content);
    for(const [key,value] of Object.entries(doc.attrs||{}))transaction=transaction.setDocAttribute(key,value);
    found.dispatch(transaction);
    if(!found.state.doc.eq(doc))throw new Error('複製した本文構造が一致しません。公開を停止しました。');
    return true;
}'''
