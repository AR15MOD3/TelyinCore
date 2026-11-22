"""Offline acceptance against a temporary Bot, using the installed public code."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from uuid import uuid4


def check_documents(core: Path) -> dict:
    core = Path(core).resolve()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from launch import (submit_persona_proposal, adopt_a_proposal,
                        sync_bot_search_index, the_self_account_handlers)
    from telyincore import memory, prefix, search, web
    from telyincore.dispatch import Dispatcher
    from telyincore.stream import ToolCall
    from telyincore.tools import DECLARED_TOOLS, build_table

    checks = []
    report = dict(schema='document-storage-acceptance-v1',
                  at=datetime.now().astimezone().isoformat(),
                  scope='temporary Bot copied from bundled public template',
                  official_bot_data_modified=False, model_requests=0, checks=checks,
                  semantic_vectors='not tested: no embedding model invoked')

    @contextmanager
    def check(name):
        try:
            yield
        except Exception as error:
            checks.append(dict(name=name, ok=False, error_type=type(error).__name__,
                               error=str(error)[:300]))
            raise
        else:
            checks.append(dict(name=name, ok=True))

    def require(condition, message):
        if not condition:
            raise RuntimeError(message)

    try:
        with tempfile.TemporaryDirectory(prefix='telyincore-documents-') as folder:
            bot = Path(folder) / 'Fixture'
            with check('prepare_temporary_bot'):
                shutil.copytree(core / 'templates/bot-default', bot)
                home = bot / 'memory'
                (home / '知识').mkdir(parents=True, exist_ok=True)
                (home / '做法').mkdir(parents=True, exist_ok=True)
                runtime = Path(folder) / 'runtime'
                runtime.mkdir()
                port = web.PersonaPort(bot / 'persona')
                initial = (bot / 'persona/宪法.md').read_text(encoding='utf-8')
                role_initial = (bot / 'persona/角色设定.md').read_bytes()

            svc = web.WebService(port=0, persona=port,
                adopt_proposal=lambda **kw: adopt_a_proposal(home, port, **kw))
            svc.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def http(route, payload=None):
                data = None if payload is None else json.dumps(payload).encode('utf-8')
                request = urllib.request.Request(svc.url().rstrip('/') + route, data=data,
                    headers={'Content-Type': 'application/json'} if data else {})
                try:
                    response = opener.open(request, timeout=5)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    return response.status, json.loads(response.read())

            try:
                with check('persona_http_section_save_and_reload'):
                    status, result = http('/persona')
                    require(status == 200 and result['order'] == ['宪法', '角色设定'],
                            'modern persona layout did not load')
                    info = port.file_info('宪法.md')
                    section = next(s['body'] for s in info['sections'] if s['id'] == 'evidence')
                    revised = section + '\n验收标记：只修改依据章节。'
                    payload = dict(name='宪法.md', section='evidence', text=revised,
                                   expect=info['revision'], stamp='20261108-100001')
                    status, saved = http('/persona', payload)
                    require(status == 200, 'section save failed')
                    fresh = web.PersonaPort(bot / 'persona').file_info('宪法.md')
                    require(fresh['body'] == initial.replace(section, revised),
                            'section save changed surrounding content')
                    require((bot / 'persona/角色设定.md').read_bytes() == role_initial,
                            'saving constitution changed role settings')

                with check('persona_conflict_and_history'):
                    status, _ = http('/persona', payload)
                    require(status == 409, 'stale edit was not rejected')
                    histories = list((bot / 'persona/.history-宪法').glob('*.md'))
                    require(any(p.read_text(encoding='utf-8') == initial for p in histories),
                            'previous persona version missing')

                with check('bot_proposal_and_autonomous_decision'):
                    original = port.file_info('角色设定.md')
                    body = next(s['body'] for s in original['sections'] if s['id'] == 'identity')
                    proposal_body = body + '\n验收角色名：测试角色。'
                    receipt = submit_persona_proposal(home, name='角色设定.md', section='identity',
                        text=proposal_body, why='隔离验收章节提议', ts='20261108-100002')
                    require('已记录待讨论提议 ID=' in receipt
                            and (bot / 'persona/角色设定.md').read_bytes() == role_initial,
                            'proposal did not remain pending')
                    pending = memory.pending_of(home, proposed='persona')
                    require(len(pending) == 1, 'persona proposal missing')
                    from persona_deliberation import decide
                    decision_receipt = decide(home, proposal_id=pending[0].proposal['id'],
                                              decision='apply', reason='临时Bot的隔离验收决定。')
                    require('修订该章节' in decision_receipt, 'autonomous decision failed')
                    require(proposal_body in port.read_text('角色设定.md'), 'decided section missing')
                    require(not memory.pending_of(home, proposed='persona'), 'decided proposal still pending')
                    require((home / '修订记录.jsonl').is_file(), 'decision journal missing')

                stamps = iter(('20261108-100004', '20261108-100005'))
                handlers = memory.handlers(home)
                handlers.update(the_self_account_handlers(home, clock=lambda: next(stamps), port=port))
                names = {'self_account_add', 'kb_search', 'kb_read'}
                dispatcher = Dispatcher(build_table(
                    [tool for tool in DECLARED_TOOLS if tool.name in names],
                    {name: handlers[name] for name in names}))

                def tool(tool_name, **arguments):
                    result = dispatcher.run([ToolCall(uuid4().hex, tool_name,
                        json.dumps(arguments, ensure_ascii=False))])
                    require(len(result.receipts) == 1, 'tool receipt missing')
                    receipt = result.receipts[0].text
                    require(not receipt.startswith('[tool failed]'), 'tool failed: ' + receipt[:160])
                    return receipt

                with check('self_account_tool_timestamp_newest_first_and_reopen'):
                    tool('self_account_add', text='第一条隔离自述。')
                    tool('self_account_add', text='第二条隔离自述。')
                    self_path = home / '自我/自述.md'
                    written = self_path.read_text(encoding='utf-8')
                    require(written.startswith('### 2026-11-08 10:00:05'), 'system timestamp missing')
                    require(written.index('第二条') < written.index('第一条'), 'latest entry was not first')
                    require(web.PersonaPort(bot / 'persona').read_text(web.HER_FILES[0]) == written,
                            'self-account did not survive reopening reader')
                    require((bot / 'persona/角色设定.md').read_text(encoding='utf-8') == original['body'].replace(body, proposal_body),
                            'self-account write changed persona')

                with check('empty_knowledge_index_and_tool_search'):
                    sync_bot_search_index(home, runtime)
                    require(search.the_keys(runtime / 'search') == [], 'empty index has unexpected data')
                    tool('kb_search', query='隔离灯塔')

                with check('knowledge_write_index_tool_search_and_read'):
                    note = memory.remember_knowledge(home, '验收/隔离灯塔', '隔离灯塔的识别码是 LANTERN742。\n')
                    sync_bot_search_index(home, runtime)
                    require(any('隔离灯塔' in k for k in search.the_keys(runtime / 'search')), 'new note not indexed')
                    require('LANTERN742' in tool('kb_search', query='隔离灯塔'), 'tool search missed note')
                    require('LANTERN742' in tool('kb_read', name='验收/隔离灯塔'), 'tool read missed note')

                with check('knowledge_replace_rename_delete_index_and_reopen'):
                    memory.remember_knowledge(home, '验收/隔离灯塔', '新版识别码是 LANTERN953。\n', overwrite=True)
                    sync_bot_search_index(home, runtime)
                    require(any('LANTERN953' in text for _, text in search.the_pairs(runtime / 'search')),
                            'replaced content not indexed')
                    require(not any('LANTERN742' in text for _, text in search.the_pairs(runtime / 'search')),
                            'old knowledge content remained indexed')
                    renamed = note.with_name('隔离新灯塔.md')
                    note.rename(renamed)
                    sync_bot_search_index(home, runtime)
                    require(not any(k.endswith('/隔离灯塔') or k.endswith('/隔离灯塔.md')
                                    for k in search.the_keys(runtime / 'search')), 'old path remained indexed')
                    require(any('隔离新灯塔' in k for k in search.the_keys(runtime / 'search')), 'renamed note missing')
                    renamed.unlink()
                    sync_bot_search_index(home, runtime)
                    require(search.the_pairs(runtime / 'search') == [], 'deleted note remained indexed')
                    require(not memory.knowledge_of(home), 'deleted note remained visible')

                with check('persona_prefix_uses_saved_sections'):
                    rendered = prefix.assemble(bot / 'persona', include_channel=False)[0].text
                    require('只修改依据章节' in rendered and '测试角色' in rendered,
                            'saved persona sections missing from prefix')
            finally:
                require(svc.stop(), 'temporary HTTP service did not stop')
    except Exception as error:
        if not checks or checks[-1]['ok']:
            checks.append(dict(name='fixture_or_cleanup', ok=False, error_type=type(error).__name__,
                               error=str(error)[:300]))
    report['ok'] = bool(checks) and all(row['ok'] for row in checks)
    return report


def main(core: Path | None = None) -> int:
    core = Path(core or Path(__file__).resolve().parents[1])
    report = check_documents(core)
    directory = core / 'data/acceptance'
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:6]
    target = directory / f'documents-{stamp}.json'
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    target.write_text(text, encoding='utf-8')
    (directory / 'documents-latest.json').write_text(text, encoding='utf-8')
    for row in report['checks']:
        print(('PASS ' if row['ok'] else 'FAIL ') + row['name'], flush=True)
        if not row['ok']:
            print(row.get('error', ''), flush=True)
    print('Report: ' + str(target), flush=True)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
