"""Synthetic prefix-cache experiment. Building plans never calls a provider."""
from copy import deepcopy
import hashlib,json,tempfile
from pathlib import Path
from types import SimpleNamespace
from telyincore.console import Console
from telyincore.config import Config
from telyincore.note import Record
from telyincore.session import Session
from telyincore.session_view import ScopedSession
from telyincore.protocol import shape
from telyincore.generate import build_body

STAGES=('first','identical_repeat','retrieval_changed','changed_repeat')

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def common_prefix_bytes(a,b):
    left=json.dumps(a,ensure_ascii=False,separators=(',',':')).encode()
    right=json.dumps(b,ensure_ascii=False,separators=(',',':')).encode()
    return next((i for i,(x,y) in enumerate(zip(left,right)) if x!=y),min(len(left),len(right)))

def build_plans(nonce,history_rows=96,*,grow=False):
    if not isinstance(nonce,str) or len(nonce)<16 or not nonce.isalnum():
        raise ValueError('fresh alphanumeric nonce of at least 16 characters required')
    if not 4<=history_rows<=128:raise ValueError('bounded history size required')
    if type(grow) is not bool:raise ValueError("exact boolean grow required")
    stages=("round_1","round_2","round_3") if grow else STAGES
    plans=[];arms={}
    with tempfile.TemporaryDirectory(prefix='controlled-retrieval-cache-') as directory:
        for arm in ('old','tail'):
            s=Session(Path(directory)/(arm+'.jsonl'))
            s.append(Record(role='system',is_head=True,text=(
                f'RUN={nonce}/{arm}\nSynthetic cache benchmark. Return exactly a JSON object '
                'with one key code, using only CURRENT_REFERENCE in this request. '
                'Archived tickets are background, not current evidence. Do not call tools.')))
            for n in range(history_rows):
                s.append(Record(role='user',scope='private:fixture:one',text=(
                    f'Archived synthetic ticket {n:03d}: component=module-{n%11}; batch=B{n:04d}; '
                    f'expected_count={10+n}; observed_count={10+n}; reviewer=fixture; '
                    f'status=resolved; decision=retain audit evidence for iteration {n}. '
                    'This is fictional background, not a current reference.')))
                s.append(Record(role='assistant',scope='private:fixture:one',
                    text=f'Ticket {n:03d} archived. Counts matched; no external action was performed.'))
            s.append(Record(role='user',scope='private:fixture:two',text='OTHER_RECIPIENT_PRIVATE_SENTINEL'))
            s.append(Record(role='user',scope='private:fixture:one',text='Return the current reference code only as JSON.'))
            state={'code':'A'}
            def pull_in(said,state=state):
                assert 'OTHER_RECIPIENT_PRIVATE_SENTINEL' not in str(said)
                return [{'role':'system','content':'CURRENT_REFERENCE\n'+json.dumps({'code':state['code']},sort_keys=True)}]
            c=Console(write=lambda _:None)
            cfg=Config(model='fixture',base_url='https://api.deepseek.com/v1',thinking=True,tools=(),max_tokens=2048,retry_delays_ms=())
            c.assemble(root=Path(directory)/arm,session=s,config=cfg,transport=object(),read=lambda:None,
                head=[],prefix=(),linked_accounts=lambda _:(),pull_in=pull_in,retrieval_at_tail=arm=='tail')
            c._active_batch=[SimpleNamespace(origin='external',source='fixture',sender_key='fixture:one',meta={'conversation':'private:one'})]
            view=ScopedSession(s,lambda:'private:fixture:one',lambda _:())
            arms[arm]=(c,s,state,view,cfg)
        for index,stage in enumerate(stages):
            for arm in (('old','tail') if index%2==0 else ('tail','old')):
                c,s,state,view,cfg=arms[arm]
                if grow and index:
                    # Scripted, canonical previous answer keeps the two arms
                    # semantically matched; no private or live history is used.
                    s.append(Record(role='assistant',scope='private:fixture:one',
                                    text=json.dumps({'code':chr(64+index)},sort_keys=True)))
                    s.append(Record(role='user',scope='private:fixture:one',
                        text=f'Synthetic follow-up {index}: return the current reference code only as JSON.'))
                state['code']=chr(65+index) if grow else ('A' if index<2 else 'B')
                tail=s.the_tail() if callable(s.the_tail) else s.the_tail
                messages=view.outbound(c._head_cell(),tail)
                projected=shape('chat',build_body(cfg,messages),base_url=cfg.base_url,thinking=True)['messages']
                assert 'OTHER_RECIPIENT_PRIVATE_SENTINEL' not in str(projected)
                plans.append({'arm':arm,'stage':stage,'expected':{'code':state['code']},
                    'messages':deepcopy(messages),'wire_hash':digest(projected)})
    for arm in ('old','tail'):
        rows=[x for x in plans if x['arm']==arm]
        if grow:
            assert len({x['wire_hash'] for x in rows})==3
            assert [len(x['messages']) for x in rows]==[len(rows[0]['messages'])+2*n for n in range(3)]
        else:
            assert rows[0]['wire_hash']==rows[1]['wire_hash']
            assert rows[2]['wire_hash']==rows[3]['wire_hash']
            assert rows[1]['wire_hash']!=rows[2]['wire_hash']
    return {'nonce':nonce,'history_rows':history_rows,'plans':plans,
        'changed_prefix_bytes':{arm:common_prefix_bytes(*[x['messages'] for x in plans if x['arm']==arm and x['stage'] in (('round_1','round_2') if grow else ('identical_repeat','retrieval_changed'))]) for arm in ('old','tail')}}

def summarize(rows):
    result={}
    for arm in ('old','tail'):
        selected=[x for x in rows if x['arm']==arm]
        stages={x['stage']:{'behavior_pass':x.get('behavior_pass'),
            'input':x.get('usage',{}).get('input'),'cached':x.get('usage',{}).get('cached'),
            'cache_percent':(round(100*x['usage']['cached']/x['usage']['input'],2)
                if x.get('usage',{}).get('status')=='reported' and x['usage']['input'] else None),
            'elapsed_ms':x.get('elapsed_ms')} for x in selected}
        result[arm]=stages
    return result


def build_growth_suite(nonce):
    suites={name:build_plans(nonce+name,history_rows=size,grow=True)
            for name,size in (('short',4),('long',96))}
    plans=[]
    for name,suite in suites.items():
        for plan in suite['plans']:
            plans.append({**plan,'scenario':name,'stage':name+'_'+plan['stage']})
    return {'nonce':nonce,'plans':plans,'changed_prefix_bytes':{
        name:suite['changed_prefix_bytes'] for name,suite in suites.items()}}


def fresh_benchmark_transport(raw_factory):
    """Isolate local reasoning replay; upstream prefix caching is still measured.

    Benchmarks use scripted assistant history, not a live provider turn ledger.
    A shared adapter could enrich it with previous samples' reasoning, changing
    the locked wire input. Never clear or alter production replay state instead.
    The caller owns and must close the returned transport after this sample.
    """
    from telyincore.protocol import adapt
    raw=raw_factory()
    if hasattr(raw,'_chat_reasoning_replay'):
        raise ValueError('benchmark factory must return a fresh raw transport')
    return adapt(raw,kind_of=lambda:'chat',values_of=lambda:{'thinking':True})
