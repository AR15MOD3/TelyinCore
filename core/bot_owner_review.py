"""Read-only historical startup-owner accounting. NEVER a release grant.

Operator receipts may describe observed exits; they cannot reconstruct a lost
original Job/pipe/object, delete responsibility, adopt a PID or authorize starts.
"""
import hashlib,json,re
from pathlib import Path

def _json(raw):
    def unique(rows):
        result={}
        for key,value in rows:
            if key in result:raise ValueError('duplicate owner field')
            result[key]=value
        return result
    if type(raw) is not bytes or len(raw)>65536:raise ValueError('bounded owner bytes required')
    row=json.loads(raw,object_pairs_hook=unique)
    if (type(row) is not dict or set(row)!={'version','owner','entry_sources'}
            or type(row['version']) is not int or row['version']!=1):raise ValueError('owner schema')
    n=row['owner'];pins=row['entry_sources']
    if (type(n) is not dict or set(n)!={'pid','state','created_filetime','image'}
            or type(n['pid']) is not int or not 0<n['pid']<0xffffffff or n['state']!='alive'
            or type(n['created_filetime']) is not int or n['created_filetime']<=0
            or type(n['image']) is not str or not Path(n['image']).is_absolute()
            or type(pins) is not dict or not 1<=len(pins)<=64
            or any(type(k) is not str or type(v) is not str or re.fullmatch('[0-9a-f]{64}',v) is None for k,v in pins.items())):
        raise ValueError('owner fields')
    return row

def review_owner(raw,*,current_probe,receipts=()):
    row=_json(raw);n=row['owner'];matches=[]
    result={'version':1,'original_owner_sha256':hashlib.sha256(raw).hexdigest(),'owner':n,
        'status':'HOLD_NO_ORIGINAL_EXIT_EVIDENCE','release_grant':False,'failure_cleared':False,
        'original_owner_record_retained':True,'job_retirement_reconstructed':False,
        'receipt_matches':matches,'evidence_role':'operator observation, never native authority'}
    if current_probe==n:result['status']='HOLD_ORIGINAL_OWNER_STILL_ALIVE';return result
    if (type(current_probe) is not dict or type(current_probe.get('pid')) is not int or current_probe.get('pid')!=n['pid']
            or current_probe.get('state') not in ('absent','alive')
            or (current_probe.get('state')=='alive' and
                (type(current_probe.get('created_filetime')) is not int or current_probe['created_filetime']<=0
                 or current_probe['created_filetime']==n['created_filetime']
                 or type(current_probe.get('image')) is not str or not Path(current_probe['image']).is_absolute()))):
        result['status']='HOLD_CURRENT_IDENTITY_UNKNOWN';return result
    for evidence in receipts:
        if type(evidence) is not dict:continue
        receipt=evidence.get('value');digest=evidence.get('sha256')
        if (type(receipt) is not dict or type(digest) is not str or re.fullmatch('[0-9a-f]{64}',digest) is None):continue
        original=receipt.get('original_receipt')
        if type(original) is not dict or original.get('original_launcher')!=n:continue
        exits=receipt.get('exits')
        if type(exits) is not list or not exits:continue
        owner_exits=[x for x in exits if type(x) is dict and x.get('native')==n]
        if (len(owner_exits)!=1 or not all(type(x) is dict and x.get('terminated') is True
                and type(x.get('code')) is int for x in exits)
                or type(original.get('code')) is not int or original['code']!=owner_exits[0]['code']):continue
        matches.append({'evidence_sha256':digest,'original_exit_code':original['code'],
            'main_returned_after_original_finally':original.get('main_returned_after_original_finally') is True,
            'known_process_count':len(exits)})
    if matches:
        result['status']=('FAILED_KNOWN_CHAIN_EXIT_OBSERVED_JOB_UNPROVEN'
            if any(x['original_exit_code']!=0 or not x['main_returned_after_original_finally'] for x in matches)
            else 'HOLD_SUCCESS_RECEIPT_WITH_UNRETIRED_OWNER_CONTRADICTION')
    result['remaining']=['original native Job/output closeout unavailable','global exclusive scope not proven',
                         'preserve failure and original record; no automatic retirement']
    return result
