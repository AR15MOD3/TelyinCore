"""Keeps strict admission closed until a local host explicitly arms a 3 minute window."""
from .shadow_owner import ShadowOwner
class ArmedShadowOwner(ShadowOwner):
 def __init__(self,kernel,*,admission_control,**kw):
  super().__init__(kernel,**kw);self.admission_control=admission_control;self.admission_state='disarmed';self.admission_broken=False
 def bind(self,mount):
  super().bind(mount)
  with self.lock:self.deadline=float('-inf');self.phase='disarmed';self._publish()
 def _refresh(self):
  with self.lock:
   if self.closed:return 'closed'
   if self.admission_broken:return 'invalid_control'
   try:state=self.admission_control.poll()
   except Exception:state='invalid_control';self.admission_broken=True
   changed=state!=self.admission_state;self.admission_state=state
   self.deadline=self.admission_control.deadline if state=='armed' else float('-inf')
   if changed:self.phase='ready_for_source' if state=='armed' else state;self._publish()
   return state
 def _on_idle(self):self._refresh()
 def ingress(self,event):
  if self._refresh()!='armed':return
  try:admitted=self.admission_control.admits(event)
  except Exception:return
  if admitted:super().ingress(event)
 def close(self):
  with self.lock:self.admission_state='closed'
  super().close()
 def summary(self):
  data=super().summary()
  if hasattr(self,'admission_control'):data['admission']=self.admission_control.summary(self.admission_state)
  return data
