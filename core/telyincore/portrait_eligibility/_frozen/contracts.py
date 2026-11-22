"""Finite UTF-8 contracts; requests and responses have different purposes."""
import hashlib,json
from dataclasses import dataclass
MAX_SOURCE_CHARS=8192
MAX_AUTHOR_CHARS=128
MAX_SOURCE_ENVELOPE_BYTES=32768
MAX_REQUEST_BYTES=98304
MAX_AUDIT_RESPONSE_BYTES=16384
MAX_PROPOSAL_ARGUMENT_BYTES=16384
MAX_SEGMENTS_PER_SOURCE=16
class Refused(ValueError):pass
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def byte_count(s):
    try:return len(s.encode('utf-8'))
    except (UnicodeError,AttributeError):raise Refused('utf8_string_required') from None
def bounded_json(raw,limit,domain):
    if type(raw) is not str or byte_count(raw)>limit:raise Refused(domain+'_byte_budget')
    def pairs(items):
        d={}
        for k,v in items:
            if k in d:raise Refused(domain+'_duplicate_key')
            d[k]=v
        return d
    def constant(v):raise Refused(domain+'_nonfinite')
    try:d=json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)
    except (ValueError,TypeError) as exc:
        if isinstance(exc,Refused):raise
        raise Refused(domain+'_json') from None
    if type(d) is not dict:raise Refused(domain+'_object')
    return d
@dataclass(frozen=True)
class EvidenceEnvelope:
    serialized:str
    sha256:str
    @classmethod
    def build(cls,sources):
        if type(sources) is not list or not 1<=len(sources)<=2:raise Refused('source_count')
        clean=[]
        for i,s in enumerate(sources):
            if type(s) is not dict or type(s.get('author')) is not str or not 1<=len(s['author'])<=MAX_AUTHOR_CHARS or type(s.get('text')) is not str or not 1<=len(s['text'])<=MAX_SOURCE_CHARS:raise Refused('source_shape')
            clean.append(dict(source_index=i,author=s['author'],text=s['text'],char_length=len(s['text'])))
        raw=canonical(dict(sources=clean))
        if byte_count(raw)>MAX_SOURCE_ENVELOPE_BYTES:raise Refused('source_envelope_byte_budget')
        return cls(raw,hashlib.sha256(raw.encode('utf-8')).hexdigest())
    def read(self):
        if hashlib.sha256(self.serialized.encode('utf-8')).hexdigest()!=self.sha256:raise Refused('envelope_digest')
        return bounded_json(self.serialized,MAX_SOURCE_ENVELOPE_BYTES,'source_envelope')['sources']
@dataclass(frozen=True)
class Request:
    stage:str
    serialized:str
    sha256:str
    def read(self):
        if hashlib.sha256(self.serialized.encode('utf-8')).hexdigest()!=self.sha256:raise Refused('request_digest')
        return bounded_json(self.serialized,MAX_REQUEST_BYTES,'request')
def make_request(stage,system,data,tools=()):
    if stage not in ('selector','proposal'):raise Refused('request_stage')
    payload=dict(protocol='portrait-evidence-audit-v2',stage=stage,max_output_tokens=2048,messages=[dict(role='system',content=system),dict(role='user',content=canonical(data))])
    if tools:payload['tools']=list(tools)
    raw=canonical(payload)
    if byte_count(raw)>MAX_REQUEST_BYTES:raise Refused('request_byte_budget')
    return Request(stage,raw,hashlib.sha256(raw.encode('utf-8')).hexdigest())
