"""Pinned host config. Disabling or editing the file revokes wire admission."""
from pathlib import Path
import json,hashlib
from .formal_owner import FormalDeliveryOwner

def load_mode(path):
 path=Path(path);raw=path.read_bytes();cfg=json.loads(raw)
 if type(cfg) is not dict or set(cfg)!={'enabled','mode','bot_id','recipient'} or type(cfg['enabled']) is not bool or cfg['bot_id']!='Telyin' or cfg['recipient']!='1228600196' or cfg['mode'] not in ('disabled','single-approved') or (cfg['enabled']!=(cfg['mode']=='single-approved')):raise ValueError('explicit_delivery_mode_required')
 digest=hashlib.sha256(raw).digest()
 def authority():
  try:return cfg['enabled'] is True and hashlib.sha256(path.read_bytes()).digest()==digest
  except OSError:return False
 return cfg,authority

def owner_from_config(kernel,*,config_path,admission_control,**dependencies):
 cfg,authority=load_mode(config_path)
 return FormalDeliveryOwner(kernel,admission_control=admission_control,delivery_enabled=cfg['enabled'],mode_authority=authority,**dependencies)
