import json
from pathlib import Path
from types import SimpleNamespace
from telyincore import plugins
from qq_tool_catalog import snapshot


def test_toolbox_reads_published_table_without_plugin_hooks(monkeypatch):
    instance=object()
    monkeypatch.setattr(plugins,'installed',lambda *a,**k:{'plugins':[{'name':'core-infinity-qq','installed':True,'enabled':True,'loaded':True}]})
    monkeypatch.setattr(plugins,'_plugin_folder',lambda *a:(None,{'telyincore':{'toolGuide':[{'name':'qq_send_text','title':'发送文字','group':'消息'}]}}))
    monkeypatch.setattr(plugins,'_loaded',{'core-infinity-qq':instance})
    monkeypatch.setattr(plugins,'_same_setting_scope',lambda *a:True)
    monkeypatch.setattr(plugins,'_lifecycle_state',lambda *a:('running',''))
    def handler():raise AssertionError('must not invoke')
    handler.__core_plugin_owner__=('core-infinity-qq',instance)
    tool=SimpleNamespace(name='qq_send_text',description='send',parameters={'properties':{'text':{}},'required':['text']},handler=handler)
    result=snapshot(None,None,[tool]);assert result['tools'][0]['required']==['text']
    assert result['guide'][0]['title']=='发送文字'
    monkeypatch.setattr(plugins,'_loaded',{})
    assert snapshot(None,None,[tool])['tools']==[]


def test_qq_manifest_and_prefixes_match_released_092():
    root=Path(__file__).resolve().parents[1]/'builtin/infinity/core-infinity-qq'
    manifest=json.loads((root/'package.json').read_text())
    assert manifest['version']=='0.9.2' and manifest['author']=='Telyin'
    assert manifest['repository']['directory']=='core/builtin/infinity/core-infinity-qq'
    assert len(manifest['telyincore']['toolGuide'])==15
    assert (root/'channel/qq.md').is_file()
