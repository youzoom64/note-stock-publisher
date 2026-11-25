import json

from playwright.sync_api import sync_playwright

from note_stock.native_gate import install


API='https://note.com/api/v1/text_notes/12345'


def test_stock_gate_blocks_reload_and_disconnected_binding_for_both_transports():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page()
        sent=[]
        captured=[]
        def route(route):
            if route.request.url==API:
                sent.append(route.request.post_data)
                route.fulfill(status=200,body='{}')
            else:
                route.fulfill(content_type='text/html',body='<title>Fixture editor</title>')
        page.route('**/*',route)
        page.goto('https://editor.note.com/notes/n123456789abc/edit/')
        install(page,lambda source,data:captured.append(data))
        for reload in (False,True):
            if reload:
                page.reload()
            page.evaluate('''async api=>{
                await fetch(api,{method:'PUT',body:'fetch-payload'}).catch(()=>{});
                const xhr=new XMLHttpRequest();xhr.open('PUT',api);xhr.send('xhr-payload');
            }''',API)
            page.wait_for_function('true')
            # Playwright pumps exposed binding callbacks while waiting.
            page.wait_for_timeout(100)
        assert [item['body'] for item in captured]==['fetch-payload','xhr-payload']*2
        page.evaluate("window.__noteStockGate=undefined")
        page.evaluate('''async api=>{
            await fetch(api,{method:'PUT',body:'disconnected-fetch'}).catch(()=>{});
            const xhr=new XMLHttpRequest();xhr.open('PUT',api);
            try{xhr.send('disconnected-xhr')}catch{}
        }''',API)
        page.wait_for_timeout(100)
        assert not sent
        assert len(captured)==4
        browser.close()


def test_embedded_frame_cannot_use_main_frame_binding():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page()
        page.route('**/*',lambda route:route.fulfill(content_type='text/html',body='<title>Fixture</title>'))
        page.goto('https://editor.note.com/notes/n123456789abc/edit/')
        captured=[]
        install(page,lambda source,data:captured.append(data))
        binding=page.evaluate("Object.keys(window).find(key=>key.startsWith('noteStockGate_'))")
        page.evaluate("document.body.innerHTML='<iframe src=\"https://note.com/embed\"></iframe>'")
        frame=page.frame_locator('iframe')
        frame.locator('body').wait_for()
        error=page.frames[1].evaluate('''async name=>{
            try{await window[name]({body:'untrusted'});return ''}catch(error){return String(error)}
        }''',binding)
        assert error
        assert not captured
        browser.close()
