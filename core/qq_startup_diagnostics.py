"""Read-only Windows QQ startup evidence, scoped to one named Bot."""
import hashlib
import json
from pathlib import Path
import re
import stat
from datetime import datetime, timezone
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from bot_parent_account_grant import ParentAccountGrant
from bot_registry import bot_id
from bot_stable_file import read_stable
from bot_startup_probe import _json
from runtime_identity import safe_path

REV = re.compile(r'revision-[0-9a-f]{32}\.(json|pending)\Z')
LOG = re.compile(r'launcher-exit-(\d{1,10})-[0-9a-f]{32}\.jsonl\Z')
STAGES = set("""launcher_enter launcher_cleanup_finished launcher_cleanup_failed controller_exited
    account_retirement_failed account_retirement_finished companion_cleanup_begin lifecycle_revoked
    qq_cleanup_failed qq_cleanup_finished snowluma_birth_verified snowluma_cleanup_failed
    snowluma_cooperative_drain_failed snowluma_custody_created snowluma_custody_retired
    snowluma_custody_retired_after_failure snowluma_drain_begin snowluma_drain_finished
    snowluma_interrupted_save_archived snowluma_late_stop_receipt_accepted
    snowluma_orphan_custody_archived snowluma_preflight_denied snowluma_preflight_retained
    snowluma_stop_receipt_verified snowluma_stop_request_written""".split())


def revisions(root, directory, binding):
    """Return matching before-images, including stable evidence for offline recovery."""
    rows = {}
    if not directory.exists():
        return rows
    safe_path(root, directory)
    for i, path in enumerate(directory.iterdir()):
        if i >= 4096:
            raise ValueError('准入目录超出核验范围')
        match = REV.fullmatch(path.name)
        if not match:
            continue
        raw, fid = read_stable(root, path, 1024)
        row = _json(raw)
        if (set(row) != {'version','bot','account','store','owner','revision'}
                or type(row['version']) is not int or row['version'] != 1
                or not all(isinstance(row[k],str) and re.fullmatch('[0-9a-f]{64}',row[k])
                           for k in ('bot','store','account','revision'))
                or not isinstance(row['owner'],str) or not re.fullmatch('[0-9a-f]{32}',row['owner'])):
            raise ValueError('准入修订记录无法识别')
        if all(row[k] == value for k,value in binding.items()):
            rows[path.name] = (hashlib.sha256(raw).hexdigest(), list(fid))
    return rows


def exists(root, path):
    safe_path(root, path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('登记文件类型异常')
    return True


def account_state(core, name):
    root, base = core.parent, core/'data'
    manifest = _json(read_stable(root, base/'bot-runtime-owner.json',4096)[0])
    grant = ParentAccountGrant(core, name, baseline=bot_id(manifest['baseline']) == name)
    digest, revision = grant._snapshot()
    directory = safe_path(root, base/'parent-account-admissions')
    primary = directory/(digest+'.json')
    binding = dict(account=digest, bot=hashlib.sha256(name.encode()).hexdigest(),
                   store=hashlib.sha256(str(grant.store).encode()).hexdigest())
    counts = {typ:int(exists(root, primary.with_suffix(suffix))) for typ,suffix in
              [('admission','.json'),('work','.work.json'),('local_retired','.local-retired.json')]}
    summary = {'status':'ok', 'counts':counts}
    if counts['admission']:
        row = _json(read_stable(root, primary,1024)[0])
        summary['scope_matches'] = all(row.get(k) == v for k,v in binding.items())
        summary['credentials_revision_matches'] = row.get('revision') == revision
    matches = revisions(root, directory, binding)
    counts['revision_before_image'] = sum(n.endswith('.json') for n in matches)
    counts['revision_pending'] = sum(n.endswith('.pending') for n in matches)
    if counts['admission'] and summary['scope_matches']:
        owner_files = revisions(root, directory, dict(binding,owner=row.get('owner')))
        counts['before_images_for_active_grant'] = sum(n.endswith('.json') for n in owner_files)
    return summary


def failure_counts(core, name):
    try:
        return account_state(core, name)['counts']
    except (OSError, ValueError, KeyError, TypeError):
        return None


def launcher_history(core, name):
    directory = safe_path(core, core/'logs/launcher')
    if not directory.exists():
        return {'status':'log_directory_absent','runs':[]}
    pool = []
    skipped = 0
    for path in directory.iterdir():
        if LOG.fullmatch(path.name):
            try:
                safe_path(core,path)
                pool.append((path.lstat().st_mtime_ns,path))
            except (OSError, ValueError):
                skipped += 1
    runs = []
    for _, path in sorted(pool, reverse=True)[:128]:
        try:
            raw, _ = read_stable(core,path,65536)
            rows = []
            for line in raw.splitlines():
                try:
                    rows.append(_json(line))
                except (ValueError, UnicodeError):
                    skipped += 1
                    break  # Keep the complete prefix if a crash tore the final record.
            if not any(row.get('bot') == name for row in rows):
                continue
            single = all('bot' not in row or row['bot'] == name for row in rows)
            pid = int(LOG.fullmatch(path.name)[1])
            events = []
            for row in rows:
                if row.get('bot') != name and ('bot' in row or not single):
                    continue
                if row.get('stage') not in STAGES or row.get('pid') != pid:
                    continue
                stamp = datetime.fromisoformat(row['at'])
                if stamp.tzinfo is None:
                    raise ValueError('启动日志时间缺少时区')
                event = {'at':stamp.isoformat(),'stage':row['stage']}
                if row.get('check') in ('account-registration','account-fence','runtime'):
                    event['check'] = row['check']
                if type(row.get('companion_pid')) is int:
                    event['companion_pid'] = row['companion_pid']
                if row['stage'] == 'snowluma_preflight_denied' and isinstance(row.get('admission_counts'),dict):
                    event['admission_counts'] = {key:value for key,value in row['admission_counts'].items()
                        if key in ('admission','work','local_retired','revision_before_image','revision_pending',
                                   'before_images_for_active_grant') and type(value) is int and 0 <= value <= 4096}
                events.append(event)
            runs.append({'pid':pid,'events':events})
        except (OSError, ValueError, KeyError, TypeError):
            skipped += 1
    # Include the latest denial even if later runs filled the history window.
    chosen = runs[:5]
    denial = next((r for r in runs if any(e['stage']=='snowluma_preflight_denied' for e in r['events'])),None)
    if denial and denial not in chosen:
        chosen.append(denial)
    status = 'partial' if skipped or len(pool)>128 else ('ok' if chosen else 'no_matching_run')
    return {'status':status,'runs':chosen,
            'files_skipped':skipped,'scan_limit':128,'older_files_omitted':max(0,len(pool)-128),
            'matching_runs_omitted':len(runs)-len(chosen)}


def collect(core, name):
    core, name = Path(core).resolve(strict=True), bot_id(name)
    output = {'schema':1,'bot':name,'observed_at':datetime.now(timezone.utc).isoformat()}
    try:
        folder = safe_path(core.parent, core.parent/'bots'/name/'infinity/snowluma')
        output['startup_records'] = {
            'instance_present':exists(core.parent, core/'data/instances'/(name+'.json')),
            'private_custody_present':exists(core.parent,folder/'companion-custody.json'),
            'archived_private_custody_count':sum(1 for p in folder.glob('companion-custody.retired-*.json')
                if re.fullmatch(r'companion-custody\.retired-[0-9a-f]{32}\.json',p.name) and exists(core.parent,p))}
    except (ValueError,OSError) as error:
        output['startup_records'] = {'status':'unavailable','reason':type(error).__name__}
    try:
        candidate = _json(read_stable(core.parent,core.parent/'candidate.json',4096)[0])
        value = candidate.get('version')
        output['version'] = value if isinstance(value,str) and re.fullmatch(r'beta\.\d+(?:\.\d+){2,3}',value) else None
    except (OSError, ValueError):
        output['version'] = None
    for key, fn in [('admissions',account_state),('launcher',launcher_history)]:
        try:
            output[key] = fn(core,name)
        except (OSError,ValueError,KeyError,TypeError) as error:
            output[key] = {'status':'unavailable','reason':type(error).__name__}
    return output


def export(core, name):
    report = collect(core,name)
    folder = safe_path(core.parent,core.parent/'诊断包')
    folder.mkdir(exist_ok=True)
    path = folder/('QQ启动诊断-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8]+'.zip')
    with ZipFile(path,'x',ZIP_DEFLATED) as package:
        package.writestr('qq-startup.json',json.dumps(report,ensure_ascii=False,indent=2))
    return path


if __name__ == '__main__':
    print(export(Path(__file__).resolve().parent, input('要诊断的 Bot 名称：').strip()))
