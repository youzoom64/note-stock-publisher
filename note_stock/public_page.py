"""Read public article metadata without login state or another browser context."""
from html.parser import HTMLParser
from urllib.parse import urlparse,unquote
from urllib.request import Request,urlopen


def load_public_html(url):
    with urlopen(Request(url,headers={'User-Agent':'NoteStockPublisher/1.1 article verification'}),timeout=25) as response:
        return response.status,response.url,response.read(8*1024*1024).decode('utf-8')


class PublicArticle(HTMLParser):
    def __init__(self,html):
        super().__init__()
        self.headings=[]
        self.tags=set()
        self.heading=None
        self.feed(html)

    def handle_starttag(self,tag,attrs):
        if tag=='h1':
            self.heading=[]
        if tag=='a':
            link=urlparse(dict(attrs).get('href',''))
            if link.hostname and link.hostname!='note.com':
                return
            for prefix in ('/hashtag/','/contest/','/keyword/'):
                if link.path.startswith(prefix):
                    self.tags.add(unquote(link.path[len(prefix):]).rstrip('/'))

    def handle_endtag(self,tag):
        if tag=='h1' and self.heading is not None:
            self.headings.append(''.join(self.heading).strip())
            self.heading=None

    def handle_data(self,text):
        if self.heading is not None:
            self.heading.append(text)
