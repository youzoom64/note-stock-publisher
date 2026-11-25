import copy
from note_stock.native_document import document_fingerprint


def test_embed_render_cache_changes_do_not_hide_real_document_edits():
    original={'type':'doc','content':[{'type':'embed','attrs':{'url':'https://note.com/info','htmlForEmbed':'loading'}},
                                     {'type':'paragraph','attrs':{'id':'stable'},'content':[{'type':'text','text':'本文'}]}]}
    rendered=copy.deepcopy(original)
    rendered['content'][0]['attrs']['htmlForEmbed']='<iframe src="https://note.com/info"></iframe>'
    assert document_fingerprint(rendered)==document_fingerprint(original)
    changed=copy.deepcopy(rendered)
    changed['content'][0]['attrs']['url']='https://note.com/other'
    assert document_fingerprint(changed)!=document_fingerprint(original)
    changed=copy.deepcopy(rendered)
    changed['content'][1]['content'][0]['text']='本文が変わった'
    assert document_fingerprint(changed)!=document_fingerprint(original)
    assert original['content'][0]['attrs']['htmlForEmbed']=='loading'


def test_only_known_generated_cache_fields_are_ignored():
    left={'type':'custom_html','attrs':{'htmlForEmbed':'original authored HTML'}}
    right={'type':'custom_html','attrs':{'htmlForEmbed':'changed authored HTML'}}
    assert document_fingerprint(left)!=document_fingerprint(right)
