"""Restricted local-console canary, not a general sandbox or QQ logout proof."""
import asyncio
from functools import wraps
from time import monotonic
from .channel_admission import ChannelAdmission,decide_channel_batch
from .dispatch import DispatchResult,Receipt

class CanaryStopped(RuntimeError):
    def __init__(self, reason):
        self.reason = dict(reason)
        super().__init__('console canary stopped: ' + self.reason['kind'])


class ToolFreeConfig:
    tools=()
    retry_delays_ms=()
    def __init__(self,config):self._config=config
    def __getattr__(self,name):return getattr(self._config,name)

class ConsoleCanary:
    def __init__(self,stop,*,clock=monotonic):
        self.clock=clock;self.deadline=clock()+1800;self.requests=0;self._stop=stop;self.failed=False;self.first_failure=None
    def reject(self, kind, error=None, result=None):
        if self.first_failure is None:
            status = getattr(error if error is not None else result, 'status', None)
            self.first_failure = {'kind': kind,
                'exception_type': type(error).__name__ if error is not None else None,
                'status': status if status in ('completed', 'incomplete', 'failed') else 'unknown'}
        self.stop()
        return CanaryStopped(self.first_failure)

    def stop(self):
        self.failed=True
        self._stop()
    def allowed(self):
        if self.failed or self.clock()>=self.deadline or self.requests>=20:
            self.stop();return False
        return True
    def admit(self,host,scenes,batch):
        if (not self.allowed() or not batch or host is None or scenes is None
                or any(getattr(e,'origin',None)!='external'
                       or getattr(e,'source',None)!='console' for e in batch)):
            return ChannelAdmission(False,'provenance_unverified')
        return decide_channel_batch(host,scenes,batch)
    async def deny_tools(self,calls):
        self.stop()
        return DispatchResult(receipts=tuple(Receipt(call_id=c.call_id,name=c.name,
            text='宿主控制台灰度拒绝工具执行：未派发，没有新的工具执行结果。',stage='barrier')
            for c in calls),ended=True)
    def wrap_generate(self,generate):
        @wraps(generate)
        async def guarded(config,messages,transport,**kwargs):
            if not self.allowed():
                kind = 'prior_failure' if self.failed and self.first_failure else ('deadline' if self.clock()>=self.deadline else 'request_limit')
                raise self.reject(kind)
            self.requests+=1
            try:
                result=await asyncio.wait_for(generate(ToolFreeConfig(config),messages,
                                                       transport,**kwargs),90)
            except asyncio.CancelledError:
                self.reject('cancelled')
                raise
            except Exception as error:
                failure = self.reject('generate_exception', error=error)
                # Preserve the exact original exception and its public type.
                error.canary_terminal_reason = failure.reason
                raise
            except BaseException:
                self.stop();raise
            if (not str(getattr(result,'text','') or '').strip()
                    or getattr(result,'status','completed')!='completed'
                    or getattr(result,'calls',())):
                raise self.reject('result_rejected', result=result)
            if self.requests>=20:self.stop()
            return result
        return guarded
