from note_stock.public_page import PublicArticle


def test_public_metadata_reads_encoded_tags_and_nested_heading():
    page=PublicArticle('<h1>記事 <strong>A &amp; B</strong></h1><a href="https://note.com/hashtag/%E6%97%A5%E8%A8%98">#日記</a>')
    assert page.headings==['記事 A & B'] and page.tags=={'日記'}


def test_external_tag_links_and_json_only_headings_do_not_count():
    page=PublicArticle('<script>{"title":"記事"}</script><a href="https://other.example/hashtag/日記">#日記</a>')
    assert not page.headings and not page.tags
