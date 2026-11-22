"""Local operator confirmation; deliberately not exposed as an agent tool.

Run without --apply to inspect the proposed transition. Unknown outcomes stay
pending until a trusted host adapter confirms a real result, or the operator
explicitly confirms an outcome/cancels. This command never replays the operation.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys
import time
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from telyincore.attention import AttentionStore, AttentionConflict
from telyincore.retrieval_scope import RetrievalScope, valid_scope
from telyincore.record_history import rows


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--activity-dir',required=True)
    p.add_argument('--scope',required=True)
    p.add_argument('--identifier',required=True)
    p.add_argument('--revision',type=int,required=True)
    p.add_argument('--outcome',choices=['succeeded','failed','cancelled'],required=True)
    p.add_argument('--reason',required=True)
    p.add_argument('--session-file')
    p.add_argument('--evidence-ref')
    p.add_argument('--apply',action='store_true')
    a=p.parse_args(argv)
    if not valid_scope(a.scope) or not a.reason.strip() or len(a.reason)>2000:
        raise ValueError('invalid scope or operator reason')
    scope=RetrievalScope(a.scope,frozenset([a.scope]))
    store=AttentionStore(a.activity_dir,clock=lambda:int(time.time()*1000))
    found=store.read(scope,identifier=a.identifier)['items']
    if len(found)!=1:raise PermissionError('item unavailable in selected scope')
    item=found[0]
    if item['revision']!=a.revision:raise AttentionConflict('revision changed; inspect again')
    if item.get('result_status')!='unverified':raise ValueError('no unresolved host result')
    def original_available():
        if a.outcome=='cancelled':return True
        if not a.session_file or not a.evidence_ref or not a.evidence_ref.startswith('record:'):
            raise ValueError('a definite result needs --session-file and --evidence-ref; a readable reference alone does not establish its meaning')
        return any(True for _ in rows(Path(a.session_file),reference=a.evidence_ref[7:],_window=(0,0),_visible=scope.permits))
    if not original_available():raise PermissionError('result evidence unavailable')
    proposal={'identifier':a.identifier,'expected_revision':a.revision,'scope':a.scope,'outcome':a.outcome,
        'reason':a.reason,'evidence_ref':a.evidence_ref,'confirmation_kind':'explicit_local_operator',
        'operation_replayed':False,'model_text_treated_as_confirmation':False}
    if not a.apply:
        print(json.dumps({'applied':False,'proposal':proposal},ensure_ascii=False));return 0
    # An immutable operator instruction is retained even if a later version
    # conflict prevents application; the revision table proves what committed.
    token=uuid.uuid4().hex;reference='operator:'+token
    receipt=store.path.parent/('operator-confirmation-'+token+'.json')
    store._paths_safe()
    raw=json.dumps(proposal,ensure_ascii=False,sort_keys=True).encode()
    fd=os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    def verify(current,ref,outcome):
        return (current==scope and ref==reference and outcome==a.outcome
            and receipt.is_file() and not receipt.is_symlink() and receipt.read_bytes()==raw
            and original_available())
    result=store.confirm_result(scope,identifier=a.identifier,expected_revision=a.revision,
        outcome=a.outcome,reference=reference,verify=verify)
    print(json.dumps({'applied':True,'result':result,'operator_receipt':str(receipt),
        'operator_receipt_sha256':hashlib.sha256(raw).hexdigest()},ensure_ascii=False));return 0

if __name__=='__main__':raise SystemExit(main())
