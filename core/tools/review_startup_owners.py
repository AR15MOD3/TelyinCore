"""Read-only startup-owner review. No --apply, deletion, PID adoption or grant."""
from pathlib import Path
import argparse,json,hashlib,re,sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bot_owner_review import review_owner
from bot_host_observer import probe_process
from bot_stable_file import read_stable

def main(argv=None,*,probe=probe_process):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--core',required=True);p.add_argument('--evidence',action='append',default=[])
    p.add_argument('--output')
    args=p.parse_args(argv);core=Path(args.core).resolve()
    output=Path(args.output).resolve() if args.output else None
    if output is not None and (output.is_relative_to(core) or output.is_relative_to(core.parent/'bots')):
        raise ValueError('review output must be outside product and Bot trees')
    receipts=[]
    for name in args.evidence:
        path=Path(name).resolve();raw,_=read_stable(path.parent,path,65536)
        receipts.append({'sha256':hashlib.sha256(raw).hexdigest(),'value':json.loads(raw)})
        if len(receipts)>64:raise ValueError('review evidence capacity')
    folder=core/'data/host-start-owners';rows=[]
    if folder.exists():
        for path in sorted(folder.iterdir()):
            if len(rows)>=1024:raise ValueError('review owner capacity')
            if re.fullmatch(r'[0-9a-f]{32}\.json',path.name) is None:
                raise ValueError('unknown owner control filename; no action')
            raw,identity=read_stable(core,path,65536);value=json.loads(raw)
            row=review_owner(raw,current_probe=probe(value['owner']['pid']),receipts=receipts)
            if read_stable(core,path,65536)!=(raw,identity):raise ValueError('owner changed during review')
            row['file']=path.name;rows.append(row)
    result={'version':1,'read_only':True,'rows':rows,'release_grants':0,'failure_records_deleted':0,
        'warning':'operator evidence is not original native Job authority'}
    if output:
        with output.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    else:print(json.dumps(result,ensure_ascii=False))
    return 0
if __name__=='__main__':raise SystemExit(main())
