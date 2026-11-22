"""Real local model integration acceptance; only writes a temporary fictional Bot."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
CORE=Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path: sys.path.insert(0,str(CORE))
from telyincore import memory, search
from telyincore.semantic_index import SemanticIndex
from telyincore.index_maintenance import IndexMaintenance
from launch import sync_bot_search_index


def verify(model=None):
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='core-semantic-') as folder:
        base=Path(folder);home=memory.the_home(base,who='fictional');memory.lay_out(home)
        runtime=base/'runtime'
        examples=[('借阅安排','周三傍晚，读者到图书馆北门的寄存柜领取预约的书籍。'),
                  ('饮食习惯','小岚不吃花生，点餐时需要避开花生油和坚果酱。'),
                  ('交通安排','雨天骑车不方便，阿禾会改乘地铁去办公室。'),
                  ('照料提醒','布丁是家里的猫，每周日需要给它梳毛。'),
                  ('联系方式','机器出现故障时，先断开电源，再联系维修人员。')]
        for i in range(8): examples.append((f'干扰资料{i}',f'第{i}号画框已经放入美术室的纸箱。'))
        for title,body in examples: memory.remember_knowledge(home,title,body)
        service=SemanticIndex(runtime,memory_home=home,**({'model_path':model} if model else {}))
        worker=IndexMaintenance(runtime,lambda:sync_bot_search_index(home,runtime),semantic=service.maintain,interval=.05)
        heartbeats=[]
        async def run():
            async with worker.running():
                deadline=time.monotonic()+300
                while time.monotonic()<deadline:
                    heartbeats.append(time.monotonic())
                    # Indexing must run outside the event loop.
                    await asyncio.sleep(.02)
                    if worker.status().get('semantic',{}).get('state')=='ready':return
                    if worker.status().get('semantic',{}).get('state') in ('off','retry'):
                        raise AssertionError(worker.status())
                raise TimeoutError('incremental semantic preparation')
        asyncio.run(run())
        assert len(heartbeats)>2
        handlers=memory.handlers(home,semantic=service.road,semantic_memory=service.rank_texts)
        cases=[('Where can I collect the volumes I reserved?','借阅安排'),
               ('Which ingredient must be excluded from her meals?','饮食习惯'),
               ('How does he commute when it is wet outside?','交通安排'),
               ('What grooming does the pet need?','照料提醒'),
               ('设备坏了应该如何处理？','联系方式')]
        results=[]
        for query,title in cases:
            literal=memory.search(home,query)
            assert not literal, (query,'must test zero lexical matches',literal)
            kb=handlers['kb_search'](query=query,k=1)
            recalled=handlers['memory_search'](query=query,k=1)
            assert title in kb,(query,kb)
            assert examples[[t for t,_ in examples].index(title)][1] in recalled,(query,recalled)
            assert 'memory_ref=' in recalled
            results.append(dict(query=query,expected=title,literal_hits=0,kb=kb,memory=recalled))
        before=worker.status()
        # Rewording the body must invalidate only that content, preserving the rest.
        memory.remember_knowledge(home,'借阅安排','预订的读物改为星期五上午在西侧服务台交付。',overwrite=True)
        memory.remember_knowledge(home,'新约定','下次聚会由小林带上温热的桂花茶。')
        for _ in range(5):worker.run_once()
        assert service.health()['state']=='work'
        revised=handlers['kb_search'](query='Where should I pick up the reserved volumes now?',k=1)
        read=handlers['kb_read'](name='借阅安排.md')
        assert '星期五上午' in revised or '星期五上午' in read,(revised,read)
        added=handlers['memory_search'](query='Who is bringing a warm beverage to the gathering?',k=1)
        assert '小林' in added,added
        # Deleted records can remain in the derived content cache, never candidates.
        target=home/memory.KNOWLEDGE_TREE/'借阅安排.md';assert target.is_file();target.unlink()
        worker.run_once()
        assert isinstance(service.road('reserved volumes',['借阅安排.md']),search.SemanticUnavailable)
        for title,body in examples[1:]:
            assert body in dict(memory.knowledge_of(home))[title+'.md']
        return dict(passed=True,model=service.model_path,paraphrases=results,
                    incremental_add=True,incremental_edit=True,deleted_excluded=True,
                    untouched_originals=True,event_loop_ticks=len(heartbeats),
                    max_event_loop_gap=max(b-a for a,b in zip(heartbeats,heartbeats[1:])),
                    elapsed_seconds=time.monotonic()-started)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--model');p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    result=verify(a.model);a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='paraphrases'},ensure_ascii=False))
