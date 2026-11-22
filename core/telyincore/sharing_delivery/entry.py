"""Explicit controlled entry. Default is check-only. Never edits config/stops services.
--run-shadow needs preview opt-in; --run-one-shot additionally needs a new exact
operator approval and existing budget/journal in Core's owned delivery directory.
"""
from pathlib import Path
from contextlib import closing
import argparse,hashlib,json,sys,types,uuid,time,os,socket
from .configuration import load_mode,owner_from_config
from .arming import ArmGate
from .one_shot import OneShotApproval
from .live_broker import Budget,Policy
from .qq_transport import DispatchJournal,ConfiguredOneBot
from .shadow_owner import mounted_shadow
from telyincore.sharing_bridge.core_binding import CoreTestFactory
ANCHOR='    p_root = core_root\n'
INSERT='    plugin_host["sharing_shadow_console"] = lambda: attached_console[0]\n    plugin_host["sharing_shadow_quiet_at"] = lambda ms: _it_is_the_quiet_hours(datetime.datetime.fromtimestamp(ms / 1000))\n'
def core_root():return Path(__file__).resolve().parents[2]
def snapshot_match(core):
 manifest=json.loads(Path(__file__).with_name('core-snapshot.json').read_text(encoding='utf-8'))
 expected={'launch.py','telyincore/console.py','telyincore/loop.py','telyincore/sharing_bridge/core_binding.py','telyincore/sharing_bridge/mount.py'}
 if type(manifest) is not dict or set(manifest)!=expected:return False
 return all(hashlib.sha256((core/key).read_bytes()).hexdigest()==value for key,value in manifest.items())
def inspect(core=None):
 core=core_root() if core is None else Path(core)
 cfg,_=load_mode(core/'data/sharing-delivery.json');preview=json.loads((core/'data/sharing-preview-test.json').read_bytes())
 return {'installed_core_matches':snapshot_match(core),'mode':cfg['mode'],'delivery_config_enabled':cfg['enabled'],'preview_test_config_enabled':preview.get('enabled') is True,'core_started':False,'network_called':False,'automatic_sharing_enabled':False}
def checked_approval_paths(core,approval,budget,journal):
 if any(p is None for p in (approval,budget,journal)):raise ValueError('new_approval_budget_journal_required')
 base=(core/'data/sharing-delivery').resolve();paths=[Path(p).resolve() for p in (approval,budget,journal)]
 if len(set(paths))!=3 or any(not p.is_relative_to(base) or not p.is_file() for p in paths):raise ValueError('owned_distinct_existing_ledgers_required')
 now=int(time.time()*1000)
 with closing(OneShotApproval.connect(paths[0])) as db:
  row=OneShotApproval.row(db)
  if row[6]!='free' or not row[3]<=now<row[4]:raise ValueError('fresh_unconsumed_operator_approval_required')
 with closing(Budget.connect(paths[1])) as db:
  _,paused,last,policy=Budget.control(db)
  if paused or now<last or policy!=Policy().digest():raise ValueError('budget_not_ready')
 with closing(DispatchJournal.connect(paths[2])):pass
 return paths

def preflight(core,mode,approval=None,budget=None,journal=None):
 status=inspect(core)
 if not status['installed_core_matches']:raise ValueError('core_drift_requires_revalidation')
 if not status['preview_test_config_enabled']:raise ValueError('explicit_preview_test_opt_in_required')
 if mode=='shadow':
  if status['delivery_config_enabled'] or any(x is not None for x in (approval,budget,journal)):raise ValueError('shadow_requires_disabled_delivery_and_no_ledgers')
  return {}
 if mode!='single-approved' or status['mode']!='single-approved' or not status['delivery_config_enabled']:raise ValueError('explicit_single_approved_mode_required')
 a,b,j=checked_approval_paths(core,approval,budget,journal);return {'approval':a,'budget':b,'journal':j,'exchange':ConfiguredOneBot(core)}
def require_free_port():
 with socket.socket() as s:
  s.settimeout(1)
  if s.connect_ex(('127.0.0.1',7799))==0:raise ValueError('normal_core_must_exit_first')
def load_core(core):
 if not snapshot_match(core):raise ValueError('core_drift_requires_revalidation')
 path=core/'launch.py';raw=path.read_bytes();expected=json.loads(Path(__file__).with_name('core-snapshot.json').read_text())['launch.py']
 if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('core_drift_requires_revalidation')
 source=raw.decode('utf-8-sig').replace(chr(13)+chr(10),chr(10))
 if source.count(ANCHOR)!=1:raise ValueError('runtime_callback_seam_changed')
 name='_sharing_delivery_explicit_launch'
 if name in sys.modules:raise ValueError('entry_module_already_owned')
 module=types.ModuleType(name);module.__file__=str(path);sys.modules[name]=module
 try:exec(compile(source.replace(ANCHOR,INSERT+ANCHOR),str(path)+' [controlled sharing entry]','exec'),module.__dict__)
 except BaseException:sys.modules.pop(name,None);raise
 return module

def writer(path):
 def save(data):
  tmp=path.with_name('.entry-'+uuid.uuid4().hex+'.tmp')
  try:
   with tmp.open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
   os.replace(tmp,path)
  finally:
   if tmp.exists():tmp.unlink()
 return save

def run_owned(core,module,kernel,folder,dependencies):
 gate=ArmGate(folder/'arm-control.json',clock=kernel.clock,monotonic=time.monotonic)
 owner=owner_from_config(kernel,config_path=core/'data/sharing-delivery.json',admission_control=gate,on_status=writer(folder/'status.json'),**dependencies)
 try:
  with mounted_shadow(owner):
   return module.main(['--root',str(core/'data'),'--bot','Telyin','--port','7799','--sharing-preview-test'],sharing_bridge_factory=owner)
 finally:
  primary=sys.exception();confirmed=False;cleanup_error=None
  try:
   owner.close();confirmed=owner.shutdown_complete() is True
   if not confirmed:raise RuntimeError('sharing_delivery_resources_unconfirmed')
  except BaseException as error:
   if primary is None:cleanup_error=error
   else:primary.add_note('sharing_delivery_resources_unconfirmed')
  finally:
   try:writer(folder/'entry-exit.json')({'sharing_owned_resources_confirmed':confirmed,'ordinary_core_resources':'not_asserted','automatic_sharing_enabled':False})
   except BaseException:
    if primary is not None:primary.add_note('sharing_delivery_exit_audit_unconfirmed')
    elif cleanup_error is not None:cleanup_error.add_note('sharing_delivery_exit_audit_unconfirmed')
    else:raise
   if cleanup_error is not None:raise cleanup_error

def run(core,mode,approval=None,budget=None,journal=None):
 dependencies=preflight(core,mode,approval,budget,journal)
 require_free_port()
 kernel=CoreTestFactory(core,bot='Telyin') # Exact scope/config validation, before any reviewer starts.
 module=load_core(core)
 folder=core/'data/sharing-delivery-runs'/uuid.uuid4().hex;folder.mkdir(parents=True)
 return run_owned(core,module,kernel,folder,dependencies)
def main(argv=None):
 parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group();group.add_argument('--check',action='store_true');group.add_argument('--run-shadow',action='store_true');group.add_argument('--run-one-shot',action='store_true');parser.add_argument('--approval');parser.add_argument('--budget');parser.add_argument('--journal');args=parser.parse_args(argv);core=core_root()
 if not args.run_shadow and not args.run_one_shot:
  if any(x is not None for x in (args.approval,args.budget,args.journal)):parser.error('ledger paths require an explicit run mode')
  print(json.dumps(inspect(core),ensure_ascii=False));return 0
 return run(core,'shadow' if args.run_shadow else 'single-approved',args.approval,args.budget,args.journal)
if __name__=='__main__':raise SystemExit(main())
