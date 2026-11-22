"""New explicit shadow entry; default CHECK ONLY. No normal Core auto-enable."""
from pathlib import Path
import argparse,json,sys
from telyincore.sharing_delivery import entry as delivery_entry
from telyincore.sharing_delivery.configuration import load_mode
from .configuration import load_topics
from .owner import TopicOwner
from .tool_bridge import console_tool_boundary
def inspect(core=None):
 core=delivery_entry.core_root() if core is None else Path(core)
 result=delivery_entry.inspect(core);cfg,_=load_topics(core/'data/sharing-topics.json')
 return {**result,'topics_enabled':cfg['enabled'],'topics_mode':cfg['mode'],'natural_topic_hook_attached':False}
def run_shadow(core):
 cfg,_=load_topics(core/'data/sharing-topics.json');active_owner=[None]
 def factory(kernel,*,config_path,admission_control,**deps):
  delivery,_=load_mode(config_path)
  if delivery['enabled'] or set(deps)-{'on_status'}:raise ValueError('topic_entry_requires_disabled_proactive_dispatch')
  if deps.get('on_status') is not None and not callable(deps['on_status']):raise ValueError('host_status_callback_required')
  owner=TopicOwner(kernel,topics_config=core/'data/sharing-topics.json',admission_control=admission_control,delivery_enabled=False,on_status=deps.get('on_status'));active_owner[0]=owner;return owner
 old=delivery_entry.owner_from_config;delivery_entry.owner_from_config=factory
 try:
  if not cfg['enabled']:return delivery_entry.run(core,'shadow')
  with console_tool_boundary(lambda:active_owner[0]):return delivery_entry.run(core,'shadow')
 finally:
  if delivery_entry.owner_from_config is not factory:
   primary=sys.exception()
   if primary is not None:primary.add_note('topic_entry_factory_ownership_lost')
   else:raise RuntimeError('topic_entry_factory_ownership_lost')
  else:delivery_entry.owner_from_config=old
def main(argv=None):
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group();g.add_argument('--check',action='store_true');g.add_argument('--run-shadow',action='store_true');a=p.parse_args(argv);core=delivery_entry.core_root()
 if not a.run_shadow:print(json.dumps(inspect(core)));return 0
 return run_shadow(core)
if __name__=='__main__':raise SystemExit(main())
