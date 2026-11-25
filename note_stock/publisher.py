"""Browser UI adapter. Never calls undocumented note publishing APIs."""
from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import urlparse

from .store import normalize_body


class LoginRequired(RuntimeError):
    pass


class VerificationFailed(RuntimeError):
    pass


def account_id(text):
    text = text.strip().rstrip("/")
    if text.startswith("https://note.com/"):
        text = urlparse(text).path.strip("/")
    if not re.fullmatch(r"[a-z0-9_]{3,50}", text):
        raise ValueError("note IDまたは https://note.com/あなたのID を入力してください。")
    return text


def plain_text(locator):
    """Read paragraph/soft-break semantics without innerText's layout-only gaps."""
    return locator.evaluate("""(root) => {
        function inline(node) {
            if (node.nodeType === Node.TEXT_NODE) return node.nodeValue;
            if (node.nodeType !== Node.ELEMENT_NODE) return '';
            if (node.classList.contains('ProseMirror-trailingBreak')) return '';
            if (node.tagName === 'BR') return '\\n';
            return Array.from(node.childNodes).map(inline).join('');
        }
        let out = '', seen = false;
        for (const node of root.childNodes) {
            const block = node.nodeType === Node.ELEMENT_NODE && ['P','DIV'].includes(node.tagName);
            let value = inline(node);
            if (block) {
                if (seen) out += '\\n';
                if (value === '\\n') value = '';
                out += value; seen = true;
            } else {out += value; if (value) seen = true;}
        }
        return out;
    }""")


class NoteBrowser:
    CORE = "https://note.com"
    EDITOR = "https://editor.note.com"

    def __init__(self, store, progress=lambda message: None, cancelled=lambda: False):
        self.store = store
        self.progress = progress
        self.cancelled = cancelled

    def open(self, playwright):
        self.context = playwright.chromium.launch_persistent_context(
            str(self.store.root / "browser-profile"), headless=False,
            locale="ja-JP", timezone_id="Asia/Tokyo", viewport={"width": 1280, "height": 900},
            args=["--disable-crash-reporter", "--no-first-run", "--disable-background-mode"],
        )
        self.context.set_default_timeout(20000)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

    def first_visible(self, candidates, label, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.cancelled():
                raise RuntimeError("処理を中止しました。")
            for candidate in candidates:
                for i in range(min(candidate.count(), 20)):
                    loc = candidate.nth(i)
                    if loc.is_visible():
                        return loc
            self.page.wait_for_timeout(150)
        raise VerificationFailed(f"note画面の「{label}」を確認できません。画面変更または追加認証の可能性があります。")

    def button(self, name):
        return self.page.get_by_role("button", name=name, exact=True)

    def identity(self, wait=20):
        target = self.CORE + "/settings/account/note_id"
        self.page.goto(target, wait_until="domcontentloaded")
        end = time.monotonic() + wait
        while time.monotonic() < end:
            if self.cancelled():
                raise RuntimeError("接続確認を中止しました。")
            field = self.page.get_by_role("textbox", name="note ID", exact=True)
            if field.count() and field.first.is_visible():
                value = field.first.input_value()
                if value:
                    return account_id(value)
            if wait <= 25 and any(p in self.page.url for p in ("/login", "/signup")):
                break
            if wait > 25 and urlparse(self.page.url).path in {"/", "/settings/account"}:
                self.page.goto(target, wait_until="domcontentloaded")
            self.page.wait_for_timeout(500)
        raise LoginRequired("noteへのログインが必要です。設定から「ログイン・接続確認」を行ってください。")

    def login(self, expected):
        expected = account_id(expected)
        from playwright.sync_api import TimeoutError as BrowserTimeout

        login_url = self.CORE + '/login'
        try:
            self.page.goto(login_url, wait_until='domcontentloaded', timeout=20000)
        except BrowserTimeout as exc:
            raise VerificationFailed('noteのログインページを20秒以内に読み込めませんでした。ブラウザーで表示を確認してください。') from exc
        self.progress("専用ブラウザーでnoteにログインしてください。接続確認は最大15分待ちます。")
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            if self.cancelled():
                raise RuntimeError('接続確認を中止しました。')
            location = urlparse(self.page.url)
            section = location.path.strip('/').split('/',1)[0]
            if (location.scheme == 'https' and location.hostname == 'note.com' and
                    section not in {'login','signup','auth','oauth'}):
                actual = self.identity()
                break
            self.page.wait_for_timeout(500)
        else:
            raise LoginRequired('noteへのログインを確認できませんでした。ログイン・接続確認からやり直してください。')
        if actual != expected:
            raise LoginRequired(f"ログイン先が異なります（現在: {actual} / 設定: {expected}）。専用ブラウザーでログイン先を切り替えてください。")
        return actual

    def check_account(self):
        expected = self.store.setting("account", "")
        if not expected:
            raise LoginRequired("設定で投稿先のnote IDを登録してください。")
        actual = self.identity()
        if actual != expected:
            raise LoginRequired(f"投稿先とログイン先が一致しません（現在: {actual} / 設定: {expected}）。")
        return actual

    @staticmethod
    def draft_key(url):
        match = re.search(r"/(?:notes|n)/(n[a-z0-9]{12})(?:/|$|\?)", url)
        return match[1] if match else ""

    def wait_draft(self, post_id):
        end = time.monotonic() + 30
        while time.monotonic() < end:
            if self.cancelled():
                raise RuntimeError('処理を中止しました。')
            key = self.draft_key(self.page.url)
            if key:
                self.store.checkpoint(post_id, key=key)
                return key
            self.page.wait_for_timeout(250)
        raise VerificationFailed("note側の下書き保存を確認できませんでした。公開処理は実行していません。")

    def editor_fields(self):
        return (
            self.first_visible([
                self.page.get_by_placeholder("記事タイトル", exact=True),
                self.page.locator('textarea[placeholder*="タイトル"]'),
                self.page.get_by_role("textbox", name="タイトル", exact=True),
            ], "記事タイトル"),
            self.first_visible([self.page.locator('.ProseMirror[contenteditable="true"]')], "本文"),
        )

    def add_image(self, image):
        # A retry reuses the same plain-text draft. Replace any old cover explicitly.
        remove = self.page.get_by_role("button", name="削除", exact=True)
        visible = [remove.nth(i) for i in range(remove.count()) if remove.nth(i).is_visible()]
        if len(visible) > 1:
            raise VerificationFailed("見出し画像の削除ボタンを一意に確認できません。")
        if visible:
            visible[0].click()
        if not image:
            return
        self.progress("見出し画像を設定しています。")
        upload = self.first_visible([
            self.button("画像を追加"), self.button("見出し画像を追加"),
            self.page.get_by_role("button", name=re.compile("見出し画像|画像を追加")),
        ], "見出し画像")
        upload.click()
        select = self.page.get_by_role("button", name=re.compile("画像をアップロード|アップロード"))
        if select.count() and select.first.is_visible():
            with self.page.expect_file_chooser() as chooser:
                select.first.click()
            chooser.value.set_files(str(image))
        else:
            inputs = self.page.locator('input[type="file"][accept*="image"]')
            if inputs.count() != 1:
                raise VerificationFailed("見出し画像のアップロード欄を特定できません。")
            inputs.set_input_files(str(image))
        apply = self.first_visible([self.button("保存"), self.button("適用"), self.button("決定")], "画像の保存", timeout=30)
        apply.click()
        # The crop dialog must close before navigating away.
        apply.wait_for(state="hidden", timeout=30000)

    def set_tags(self, tags):
        field = self.first_visible([
            self.page.get_by_placeholder("タグ入力後、改行で追加", exact=True),
            self.page.get_by_placeholder(re.compile("ハッシュタグ|タグを入力|タグ入力")),
            self.page.get_by_role("textbox", name=re.compile("ハッシュタグ")),
        ], "ハッシュタグ")
        container = field.locator("..")
        remove = container.locator('[aria-label="削除"]')
        for _ in range(100):
            if not remove.count():
                break
            remove.first.locator("..").click()
        if remove.count():
            raise VerificationFailed("既存のハッシュタグを解除できませんでした。")
        for tag in tags:
            field.fill(tag)
            field.press("Enter")
            chip = container.get_by_text("#" + tag, exact=True)
            plain = container.get_by_text(tag, exact=True)
            self.first_visible([chip, plain], "追加したタグ「" + tag + "」", timeout=5)

    def publish(self, post):
        from playwright.sync_api import expect
        self.progress("ログイン先を確認しています。")
        account = self.check_account()
        key = post["draft_key"]
        self.page.goto(self.EDITOR + (f"/notes/{key}/edit/" if key else "/new"), wait_until="domcontentloaded")
        title, body = self.editor_fields()
        # Record the note key before any subsequent step can fail.
        key = self.wait_draft(post["id"])
        self.progress("タイトルと本文を入力しています。")
        title.fill(post["title"])
        body.fill(post["body"])
        expect(title).to_have_value(post["title"])
        # ProseMirror uses paragraphs; compare visible line breaks, never HTML strings.
        actual = normalize_body(plain_text(body))
        if actual != normalize_body(post["body"]):
            raise VerificationFailed("本文の入力結果が原稿と一致しません。公開を中止しました。")
        # The native "下書き保存" button leaves the editor. The publish-settings
        # button performs its own save; capture the already-created note key here.
        key = self.wait_draft(post["id"])
        self.add_image(self.store.image_path(post["image"]))
        self.first_visible([self.button("公開に進む"), self.button("公開設定")], "公開設定").click()
        self.set_tags(post["tags"])
        free = self.first_visible([self.page.get_by_role("radio", name="無料", exact=True),
                                   self.page.locator('input#free[name="is_paid"]')], "無料公開")
        free.check()
        expect(free).to_be_checked()
        final = self.first_visible([self.button("投稿する"), self.button("投稿"), self.button("公開する")], "投稿")
        if self.cancelled():
            raise RuntimeError("投稿を中止しました。")
        # Commit the irreversible boundary BEFORE clicking. A crash always recovers as unknown.
        self.store.checkpoint(post["id"], key=key, submitted=True)
        self.progress("公開しています。結果を確認するまで再送しません。")
        final.click()
        self.page.wait_for_timeout(1500)
        return self.verify(post, account, key)

    def verify(self, post, account=None, key=None):
        account = account or self.check_account()
        key = key or post["draft_key"]
        if not re.fullmatch(r"n[a-z0-9]{12}", key):
            raise VerificationFailed("note側の下書きIDが保存されていません。noteの記事一覧で公開結果を確認してください。")
        url = self.CORE + f"/{account}/n/{key}"
        # Public visibility must be checked without the author's session (draft previews excluded).
        public = self.context.browser.new_context(locale="ja-JP")
        try:
            page = public.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if not response or response.status != 200:
                raise VerificationFailed("公開記事を取得できません。公開結果は未確認です。")
            from playwright.sync_api import expect
            expect(page.get_by_role("heading", name=post["title"], exact=True).first).to_be_visible(timeout=20000)
            article_body = page.locator('.note-common-styles__textnote-body, .p-article__body, [data-testid="note-body"]')
            article_body.first.wait_for(state="visible", timeout=20000)
            if normalize_body(plain_text(article_body.first)) != normalize_body(post["body"]):
                raise VerificationFailed("公開された本文が原稿と一致しません。確認が必要です。")
            for tag in post["tags"]:
                expect(page.locator('a[href*="/hashtag/"], a[href*="/contest/"], a[href*="/topic/"], a[href*="/keyword/"]').filter(
                    has_text=re.compile(r"^#?" + re.escape(tag) + r"$")).first).to_be_visible(timeout=10000)
            if post["image"]:
                cover = page.locator('meta[property="og:image"]').get_attribute("content") or ""
                if not cover or "default" in cover or "ogp_default" in cover:
                    raise VerificationFailed("見出し画像の公開結果を確認できません。")
            if page.url.split("?")[0].rstrip("/") != url:
                raise VerificationFailed("公開先URLが一致しません。")
            return url
        finally:
            public.close()
