"""Owned HostAssembly-compatible factory. Default off, no tool-plane send endpoint."""
from .armed_owner import ArmedShadowOwner
from .one_shot import OneShotDelivery
from telyincore.sharing_bridge.contracts import parse_proposal
import json
from telyincore.sharing_close_budget import bounded_close
class FormalDeliveryOwner(ArmedShadowOwner):
 def __init__(self,kernel,*,admission_control,delivery_enabled=False,mode_authority=None,approval=None,budget=None,journal=None,exchange=None,**kw):
  if type(delivery_enabled) is not bool or delivery_enabled and (not callable(mode_authority) or not callable(exchange) or any(x is None for x in (approval,budget,journal))):raise ValueError('explicit_single_dispatch_dependencies_required')
  self.delivery_enabled=delivery_enabled;self.mode_authority=mode_authority;self.sender=None
  self.approval=approval;self.budget=budget;self.journal=journal;self.exchange=exchange
  super().__init__(kernel,admission_control=admission_control,**kw)
 def _mode_allowed(self):
  if self.closed or not self.delivery_enabled or self.mode_authority is None:return False
  try:return self.mode_authority() is True
  except Exception:return False
 def _after_preview(self,ref,proposal,result):
  if not self._mode_allowed():return {'delivery':{'status':'disabled','send_attempted':False}}
  if result.get('action')!='allow':return {'delivery':{'status':'not_eligible','send_attempted':False}}
  with self.lock:
   if self.closed:return {'delivery':{'status':'closed','send_attempted':False}}
   if self.sender is None:
    self.sender=OneShotDelivery(binding=self.binding,approval=self.approval,budget=self.budget,journal=self.journal,exchange=self.exchange,enabled=True)
    authorize=self.sender.broker.transport.authorize_live
    def checked(permit,text):return self._mode_allowed() and authorize(permit,text) is True and self._mode_allowed()
    self.sender.broker.transport.authorize_live=checked
   sender=self.sender
  # A prior preview allow is NOT a real permit: sender performs fresh assessment
  # and exact-output review, then one-shot approval, budget and route validation.
  delivered=sender.dispatch(ref,parse_proposal(json.dumps(proposal)))
  return {'delivery':delivered,'dry_run':not delivered['send_attempted'],'will_send':delivered['send_attempted']}
 @bounded_close
 def close(self):
  with self.lock:
   self.delivery_enabled=False
   if self.sender is not None:self.sender.close()
  super().close()
 def shutdown_complete(self):return super().shutdown_complete() and (self.sender is None or self.sender.shutdown_complete() is True)
 def summary(self):
  data=super().summary();data['real_sending_enabled']=self._mode_allowed();data['automatic_proactive_sharing_enabled']=False;data['delivery_mode']='single-approved' if self.delivery_enabled else 'off';return data
