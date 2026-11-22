"""Process-local request-prefix similarity, never provider cache usage or tokens.

Only keyed digests and numeric counters survive observe(). No prompt text, keys,
images or tool results are retained or persisted. Bounded work and retention.
"""
from collections import OrderedDict, deque
import hashlib
import hmac
import json
import secrets
import threading

BLOCK = 1024
MAX_BYTES = 2 * 1024 * 1024
PARTITIONS = 32
HISTORY = 4


class LocalPrefixUsage:
    def __init__(self, *, by_epoch=False):
        self.by_epoch = by_epoch
        self._key = secrets.token_bytes(32)
        self._buckets = OrderedDict()
        self._lock = threading.RLock()

    def observe(self, identity, body, path=''):
        # A local representation, not the provider's hidden tokenizer/template.
        # Keep declarations before conversation. Sampling/output options are not
        # input; retain routing/protocol/cache settings in the comparison key.
        content = {k: body[k] for k in ('instructions','system','tools','messages','input') if k in body}
        settings = {k:v for k,v in body.items() if k not in content and k not in (
            'stream','stream_options','temperature','top_p','max_tokens',
            'max_output_tokens','max_completion_tokens')}
        key = json.dumps([{k:v for k,v in identity.items() if self.by_epoch or k != 'epoch'},path,settings],ensure_ascii=False,sort_keys=True,separators=(',',':'))
        key = hmac.digest(self._key,key.encode(),'sha256')
        # iterencode bounds allocation even for large media payloads.
        chunks=[];size=0
        for part in json.JSONEncoder(ensure_ascii=False,separators=(',',':'),allow_nan=False).iterencode(content):
            value=part.encode();size+=len(value)
            if size>MAX_BYTES:break
            chunks.append(value)
        with self._lock:
            bucket=self._buckets.pop(key,None)
            if bucket is None:
                bucket={'identity':dict(identity),'history':deque(maxlen=HISTORY),
                        'attempts':0,'measured':0,'skipped':0,'bytes':0,'reused_bytes':0}
            self._buckets[key]=bucket
            if len(self._buckets)>PARTITIONS:self._buckets.popitem(last=False)
            bucket['attempts']+=1
            if size>MAX_BYTES or not content:
                bucket['skipped']+=1
                return
            raw=b''.join(chunks)
            running=hmac.new(self._key,digestmod=hashlib.sha256);blocks=[]
            for offset in range(0,len(raw)//BLOCK*BLOCK,BLOCK):
                running.update(raw[offset:offset+BLOCK]);blocks.append(running.copy().digest())
            full=hmac.digest(self._key,raw,'sha256')
            best=0
            for old_blocks,old_full,old_size in bucket['history']:
                if full==old_full and len(raw)==old_size:best=len(raw);break
                count=0
                for a,b in zip(blocks,old_blocks):
                    if a!=b:break
                    count+=1
                best=max(best,count*BLOCK)
            bucket['history'].append((blocks,full,len(raw)))
            bucket['measured']+=1;bucket['bytes']+=len(raw);bucket['reused_bytes']+=best

    def view(self, identity):
        with self._lock:
            selected=[b for b in self._buckets.values() if
                      b['identity']['provider']==identity['provider'] and
                      b['identity']['model']==identity['model'] and
                      b['identity'].get('bot') in (identity.get('bot'),'unknown') and
                      (not self.by_epoch or b['identity'].get('epoch')==identity.get('epoch'))]
            result={k:sum(b[k] for b in selected) for k in ('attempts','measured','skipped','bytes','reused_bytes')}
        return dict(version=1,status='ready' if result['measured'] else 'no_sample',
                    unit='serialized_utf8_bytes',scope='process_all_paths',persistent=False,
                    model=identity['model'],block_bytes=BLOCK,history_per_partition=HISTORY,
                    share=100*result['reused_bytes']/result['bytes'] if result['bytes'] else None,
                    **result)
