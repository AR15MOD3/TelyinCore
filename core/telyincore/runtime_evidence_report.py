"""Read-only out-of-process observation and bounded local report commits.

No business replay, service control, external ACK or inferred resource retirement.
The observer has its own clock origin. Deadlines bound polling, not kernel I/O.
"""
import hashlib,json,os,time,uuid
from pathlib import Path
from .runtime_evidence import encode,verify

LIMIT=65536

def _read(path):
    if path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400:
        raise ValueError('linked report file')
    with path.open('rb') as stream:raw=stream.read(LIMIT+1)
    if len(raw)>LIMIT:raise ValueError('report capacity')
    return raw

def _publish(path,raw):
    if len(raw)>LIMIT:raise ValueError('report capacity')
    stage=path.with_name(path.name+'.'+uuid.uuid4().hex+'.pending')
    with stage.open('xb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    if _read(stage)!=raw:raise ValueError('report readback')
    os.link(stage,path)
    stage.unlink()
    if _read(path)!=raw:raise ValueError('published report readback')

def commit(destination,phase,report):
    if phase not in ('MAIN','LATE','PROCESS'):raise ValueError('phase')
    destination=Path(destination)
    raw=encode(report);digest=hashlib.sha256(raw).hexdigest()
    _publish(destination/(phase+'.json'),raw)
    _publish(destination/(phase+'.receipt.json'),encode({
        'schema':'TEV-LOCAL-REPORT-ACK/1','phase':phase,'sha256':digest,
        'scope':'LOCAL_FILE_FSYNC_READBACK_ONLY'}))
    return digest

def read_commit(destination,phase):
    if phase not in ('MAIN','LATE','PROCESS'):raise ValueError('phase')
    root=Path(destination)
    raw=_read(root/(phase+'.json'));receipt=json.loads(_read(root/(phase+'.receipt.json')))
    digest=hashlib.sha256(raw).hexdigest()
    if receipt!={'schema':'TEV-LOCAL-REPORT-ACK/1','phase':phase,'sha256':digest,
                 'scope':'LOCAL_FILE_FSYNC_READBACK_ONLY'}:raise ValueError('unconfirmed report commit')
    report=json.loads(raw)
    if encode(report)!=raw or report.get('schema')!='TEV-OBSERVATION/1' or report.get('phase')!=phase:
        raise ValueError('report contract')
    if report.get('business_success_proven') is not False or report.get('resource_retirement_proven') is not False:
        raise ValueError('unsupported authority')
    return report,digest

def snapshot(source,*,max_events=16384):
    try:
        result=verify(source,max_events=max_events)
        return {'integrity':'VERIFIED_AT_READ',**result}
    except (OSError,ValueError,TypeError,KeyError) as exc:
        # Concurrent tail, missing receipt and corrupt evidence never turn green.
        return {'integrity':'UNVERIFIED','status':'INCOMPLETE','reason_type':type(exc).__name__,
                'business_success_proven':False,'resource_retirement_proven':False}

def observe(source,destination,*,main_after=1.0,late_for=1.0,poll=.02,max_events=16384):
    for value in (main_after,late_for):
        if type(value) not in (int,float) or not 0<=value<=180:raise ValueError('observation budget')
    if type(poll) not in (int,float) or not .001<=poll<=1:raise ValueError('poll interval')
    if type(max_events) is not int or not 1<=max_events<=266000:raise ValueError('scan capacity')
    source=Path(source).absolute();destination=Path(destination).absolute()
    for root in (source,destination):
        for path in (root,*root.parents):
            if path.is_symlink() or (path.exists() and getattr(path.lstat(),'st_file_attributes',0)&0x400):
                raise ValueError('linked observation path')
    if destination==source or source in destination.parents:raise ValueError('report must be outside event directory')
    destination.mkdir(parents=True,exist_ok=False)
    origin=uuid.uuid4().hex;start=time.monotonic_ns();main_deadline=start+int(main_after*1e9)
    previous=None;epoch=None
    for phase,deadline in (('MAIN',main_deadline),('LATE',main_deadline+int(late_for*1e9))):
        samples=0;unverified=0
        while True:
            sample_start=time.monotonic_ns();view=snapshot(source,max_events=max_events)
            if view.get('epoch') is not None:
                if epoch is None:epoch=view['epoch']
                elif view['epoch']!=epoch:
                    view={'integrity':'UNVERIFIED','status':'INCOMPLETE','reason_type':'EpochChanged',
                          'business_success_proven':False,'resource_retirement_proven':False}
            sample_end=time.monotonic_ns();samples+=1
            if view['integrity']!='VERIFIED_AT_READ':unverified+=1
            if sample_end>=deadline:break
            time.sleep(min(poll,(deadline-sample_end)/1e9))
        report={'schema':'TEV-OBSERVATION/1','phase':phase,'clock_origin_id':origin,
                'source_path_sha256':hashlib.sha256(os.fsencode(os.path.normcase(str(source)))).hexdigest(),
                'start_ns':start,'deadline_ns':deadline,'sample_start_ns':sample_start,
                'coverage_end_ns':sample_end,'samples':samples,'unverified_samples':unverified,
                'previous_report_sha256':previous,'snapshot':view,
                'business_success_proven':False,'resource_retirement_proven':False}
        previous=commit(destination,phase,report)
    return read_commit(destination,'LATE')[0]

def recover(destination):
    """Read existing commits after restart. Never replay or modify either phase."""
    main,digest=read_commit(destination,'MAIN')
    try:late,late_digest=read_commit(destination,'LATE')
    except FileNotFoundError:
        return {'status':'MAIN_ONLY_INCOMPLETE','main':main,'late':None}
    if (late.get('previous_report_sha256')!=digest
            or late.get('clock_origin_id')!=main.get('clock_origin_id')
            or late.get('source_path_sha256')!=main.get('source_path_sha256')
            or late.get('coverage_end_ns',-1)<main.get('coverage_end_ns',0)):
        raise ValueError('late report binding')
    process=None
    process_path=Path(destination)/'PROCESS.json'
    if process_path.exists():
        try:process,_=read_commit(destination,'PROCESS')
        except FileNotFoundError:
            return {'status':'PROCESS_ACK_MISSING','main':main,'late':late,'process':None}
        if process.get('main_sha256')!=digest or process.get('late_sha256')!=late_digest:
            raise ValueError('process report binding')
    return {'status':'LOCAL_REPORTS_COMMITTED','main':main,'late':late,'process':process}

def retention_plan(bot_root,*,max_runs=32,max_events=16384):
    """Read-only inventory; no automatic rotation, deletion or ownership claim."""
    bot_root=Path(bot_root)
    if not bot_root.is_absolute() or not bot_root.is_dir():raise ValueError('explicit Bot root')
    if type(max_runs) is not int or not 1<=max_runs<=1024:raise ValueError('run capacity')
    parent=bot_root/'runtime-evidence'
    for path in (parent,*parent.parents):
        if path.is_symlink() or (path.exists() and getattr(path.lstat(),'st_file_attributes',0)&0x400):
            raise ValueError('linked evidence inventory')
    rows=[];locked=False
    if parent.exists():
        for entry in parent.iterdir():
            if entry.name=='.allocation.lock':locked=True;continue
            if len(rows)>=1024:raise ValueError('inventory capacity')
            if (len(entry.name)!=32 or any(c not in '0123456789abcdef' for c in entry.name)
                    or not entry.is_dir() or entry.is_symlink()
                    or getattr(entry.lstat(),'st_file_attributes',0)&0x400):
                rows.append({'name_sha256':hashlib.sha256(os.fsencode(entry.name)).hexdigest(),
                             'status':'FOREIGN_OR_LINKED','eligible_for_manual_archive_review':False})
                continue
            view=snapshot(entry,max_events=max_events)
            rows.append({'run':entry.name,'snapshot':view,
                'eligible_for_manual_archive_review':view.get('status')=='OBSERVATION_SEALED'
                    and view.get('integrity')=='VERIFIED_AT_READ'})
    return {'schema':'TEV-RETENTION-PLAN/1','runs':rows,'retained_count':len(rows),'max_runs':max_runs,
            'allocation_lock_present':locked,'capacity_exhausted':len(rows)>=max_runs,
            'automatic_actions':[],'business_success_proven':False,'resource_retirement_proven':False}

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('source');parser.add_argument('destination')
    parser.add_argument('--main-after',type=float,default=1.0)
    parser.add_argument('--late-for',type=float,default=1.0)
    args=parser.parse_args()
    observe(args.source,args.destination,main_after=args.main_after,late_for=args.late_for)
