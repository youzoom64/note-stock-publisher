"""Check that the published and offline manuals keep their navigation intact."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]


class Page(HTMLParser):
    def __init__(self, path):
        super().__init__()
        self.ids = []
        self.references = []
        self.headings = 0
        self.feed(path.read_text(encoding="utf-8"))

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])
        if tag == "h1":
            self.headings += 1
        if tag in {"a", "link"} and values.get("href"):
            self.references.append(values["href"])
        if tag in {"img", "script"} and values.get("src"):
            self.references.append(values["src"])


def test_manual_pages_and_assets_have_no_broken_local_links():
    files = [ROOT / "manual.html", *sorted((ROOT / "guide").glob("*.html"))]
    pages = {path.resolve(): Page(path) for path in files}
    assert len(pages) >= 15  # Basic guide, detailed index, and the thirteen topics.
    for path, page in pages.items():
        assert len(page.ids) == len(set(page.ids)), f"Duplicate IDs: {path}"
        for reference in page.references:
            parts = urlsplit(reference)
            if parts.scheme or parts.netloc:
                continue
            target = (path.parent / unquote(parts.path)).resolve() if parts.path else path
            assert target.is_relative_to(ROOT), (path, reference)
            assert target.is_file(), (path, reference)
            if parts.fragment:
                parsed = pages.get(target) or Page(target)
                assert unquote(parts.fragment) in parsed.ids, (path, reference)


def test_all_detailed_topics_are_linked_from_the_index():
    index = ROOT / "guide" / "index.html"
    page = Page(index)
    topics = {path.name for path in index.parent.glob("*.html") if path != index}
    linked = {urlsplit(ref).path for ref in page.references}
    assert topics <= linked
    assert "guide/index.html" in Page(ROOT / "manual.html").references
    for name in topics:
        parsed = Page(index.parent / name)
        assert parsed.headings == 1, name
        assert len(parsed.ids) >= 5, name
