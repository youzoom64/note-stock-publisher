"""Read the note-owned draft list through its live management UI responses."""
import re
import time

from .publisher import LoginRequired,VerificationFailed


def extract_notes(value):
    found={}
    def walk(item):
        if isinstance(item,list):
            for child in item:
                walk(child)
        elif isinstance(item,dict):
            key=item.get('key') or item.get('note_key') or item.get('noteKey')
            status=item.get('status')
            title=item.get('name') if 'name' in item else item.get('title')
            draft=item.get('noteDraft') or item.get('note_draft') or {}
            if isinstance(draft,dict) and status=='draft':
                title=draft.get('name',title)
            if title is None:
                title=''
            if isinstance(key,str) and re.fullmatch(r'n[a-z0-9]{12}',key) and isinstance(title,str) and isinstance(status,str):
                user=item.get('user') or item.get('creator') or {}
                found[key]={'key':key,'title':title,'status':status.lower(),
                            'owner':user.get('urlname','') if isinstance(user,dict) else '',
                            'updated':(draft.get('updatedAt') if isinstance(draft,dict) else '') or item.get('updated_at') or item.get('updatedAt') or item.get('publish_at') or ''}
            for child in item.values():
                if isinstance(child,(list,dict)):
                    walk(child)
    walk(value)
    return list(found.values())


def read_drafts(adapter):
    adapter.check_account()
    page=adapter.page
    notes={}
    responses=[]
    def receive(response):
        if 'note.com/api/' not in response.url or not response.ok:
            return
        content_type=response.headers.get('content-type','')
        if 'json' not in content_type:
            return
        try:
            data=response.json()
            rows=extract_notes(data)
            if rows:
                notes.update({item['key']:item for item in rows})
                responses.append(response.url)
        except Exception:
            pass
    page.on('response',receive)
    page.goto(adapter.CORE+'/notes',wait_until='domcontentloaded')
    adapter.progress('noteの記事管理画面から下書き一覧を取得しています。公開済み・予約済みの記事は除外します。')
    try:
        deadline=time.monotonic()+25
        filtered=False
        while time.monotonic()<deadline:
            if adapter.cancelled():
                raise RuntimeError('下書き一覧の取得を中止しました。')
            if '/login' in page.url:
                raise LoginRequired('noteの下書き一覧を取得するにはログインが必要です。')
            if notes:
                break
            for candidate in (page.get_by_role('tab',name=re.compile('^下書き')),
                              page.get_by_role('button',name=re.compile('^下書き'))):
                if candidate.count() and candidate.first.is_visible():
                    candidate.first.click()
                    filtered=True
                    break
            if filtered or notes:
                break
            page.wait_for_timeout(200)
        # Read successive UI pages; note's own UI owns pagination and request shapes.
        stable=0
        previous=len(notes)
        for _ in range(200):
            if adapter.cancelled():
                raise RuntimeError('下書き一覧の取得を中止しました。')
            more=page.get_by_role('button',name=re.compile('^(もっとみる|もっと見る|次へ)$'))
            if more.count() and more.first.is_visible() and more.first.is_enabled():
                more.first.click()
            else:
                page.mouse.wheel(0,1600)
            page.wait_for_timeout(500)
            count=len(notes)
            stable=stable+1 if count==previous else 0
            previous=count
            if stable>=3:
                break
        if not notes and not any(page.get_by_text(text,exact=False).count() for text in ('下書きはありません','記事がありません','まだ記事がありません')):
            raise VerificationFailed('noteの下書き一覧を読み取れたか確認できません。記事管理画面を確認してください。')
        return sorted((item for item in notes.values() if item['status']=='draft'),key=lambda item:str(item['updated']),reverse=True)
    finally:
        page.remove_listener('response',receive)


def read_catalog(adapter):
    """Collect all-status management responses; report completeness separately."""
    adapter.check_account()
    page=adapter.page
    notes={}
    observed={'response':False,'complete':False,'total':None,'pending':0}
    def receive(response):
        if not response.url.startswith('https://note.com/api/') or not response.ok:
            return
        try:
            payload=response.json()
            data=payload.get('data',{})
            if not isinstance(data,dict) or not isinstance(data.get('notes'),list):
                return
            observed['response']=True
            notes.update({r['key']:r for r in extract_notes(data['notes'])})
            for key in ('total_count','totalCount','total'):
                if type(data.get(key)) is int:
                    observed['total']=data[key]
            for key in ('isLastPage','is_last_page'):
                if data.get(key) is True:
                    observed['complete']=True
            for key in ('hasMore','has_more','has_next'):
                if data.get(key) is False:
                    observed['complete']=True
        except (ValueError,TypeError):
            pass
    page.on('response',receive)
    try:
        page.goto(adapter.CORE+'/notes',wait_until='domcontentloaded')
        adapter.progress('noteの全記事を同期しています。')
        # Do not click the draft filter; the management entry defaults to all.
        stable=0
        before=-1
        for _ in range(250):
            if adapter.cancelled():
                raise RuntimeError('記事同期を中止しました。')
            if '/login' in page.url:
                raise LoginRequired('記事同期にはnoteへのログインが必要です。')
            page.wait_for_timeout(400)
            more=page.get_by_role('button',name=re.compile('^(もっとみる|もっと見る|次へ)$'))
            if more.count() and more.first.is_visible() and more.first.is_enabled():
                more.first.click()
                stable=0
            else:
                page.mouse.wheel(0,1800)
                stable=stable+1 if before==len(notes) else 0
            before=len(notes)
            if observed['total'] is not None and len(notes)>=observed['total']:
                observed['complete']=True
            if stable>=6 and observed['response']:
                break
        if not observed['response']:
            raise VerificationFailed('記事一覧の取得応答を確認できませんでした。')
        return {'items':list(notes.values()),'complete':observed['complete']}
    finally:
        page.remove_listener('response',receive)
        if not page.is_closed():
            if len(page.context.pages)==1:
                page.context.new_page()
            page.close()
