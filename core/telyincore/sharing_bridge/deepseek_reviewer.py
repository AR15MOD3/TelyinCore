"""Host-only independent review adapter. Default-deny external data authorization."""
import json
from contextlib import contextmanager
from pathlib import Path
import subprocess
import threading
import time
import uuid


class ReviewUnavailable(RuntimeError):pass


def unique(pairs):
    out={}
    for k,v in pairs:
        if k in out:raise ValueError('duplicate_json_key')
        out[k]=v
    return out


class DeepSeekReviewer:
    def __init__(self, *, contracts, bridge, python, authorize=None, exchange=None, engine_root=None,
                 policy_root=None, bot=None):
        # Inject exact candidate module types; never create signing/model-facing APIs.
        self.contracts,self.bridge=contracts,bridge
        self.python=str(python)
        self.engine_root=Path(engine_root).resolve() if engine_root is not None else None
        self.policy_root=Path(policy_root).resolve() if policy_root is not None else None
        self.bot=bot
        self._session=None
        self.worker=Path(__file__).resolve().with_name('review_worker.py')
        self.authorize=authorize
        self._exchange_override=exchange
        self._lock=threading.Lock();self._closed=False
        self._active=False;self._uncertain=False
        self._owned=None
        self.requests=0;self.failures=0
        self.metadata=None

    @contextmanager
    def review_session(self):
        """One existing NativeSharing judgement, two phases, one model selection."""
        from .review_config import select
        if not self._lock.acquire(blocking=False):raise ReviewUnavailable('review_busy')
        try:
            if self._closed or self._uncertain or self._session is not None:
                raise ReviewUnavailable('review_closed_busy_or_unresolved')
            if self.engine_root is None or self.policy_root is None or not self.bot:
                raise ReviewUnavailable('review_host_context_required')
            selection=select(self.engine_root,self.policy_root,self.bot)
            self._session=(selection,threading.get_ident())
            self._active=True
        finally:self._lock.release()
        try:yield selection
        finally:
            with self._lock:
                self._session=None
                self._active=False

    def _exchange(self,payload):
        if self._exchange_override is not None:return self._exchange_override(payload)
        wire=json.dumps(payload,ensure_ascii=False).encode()
        if len(wire)>65536:raise ReviewUnavailable('review_input_too_large')
        self._uncertain=True  # fence partial/failed process creation before acquisition
        from native_python import python_command
        if self.engine_root is None:raise ReviewUnavailable('review_engine_binding_required')
        command,environment=python_command(['-I','-B',str(self.worker),str(self.engine_root)],python=self.python)
        process=subprocess.Popen(command,env=environment,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self._owned=process;self._uncertain=False
        primary=None
        try:
            try:out,err=process.communicate(wire,timeout=15)
            except subprocess.TimeoutExpired:
                # Only the exact child created above is terminated, never a named process.
                process.kill();process.communicate(timeout=5)
                raise ReviewUnavailable('review_process_timeout') from None
            if process.returncode!=0 or len(out)>16384 or err:
                raise ReviewUnavailable('review_process_failed_closed')
            result=json.loads(out,object_pairs_hook=unique)
            if type(result) is dict:
                diagnostics=result.get('diagnostics')
                if diagnostics is not None:
                    if (type(diagnostics) is not dict or set(diagnostics)!={'stderr_bytes','overflow','unexpected','http_requests_started','http_status_codes'}
                            or type(diagnostics['stderr_bytes']) is not int or diagnostics['stderr_bytes']<0
                            or type(diagnostics['overflow']) is not bool or type(diagnostics['unexpected']) is not bool
                            or type(diagnostics['http_requests_started']) is not int or not 0<=diagnostics['http_requests_started']<=4
                            or type(diagnostics['http_status_codes']) is not list or len(diagnostics['http_status_codes'])>4
                            or any(type(v) is not int or not 100<=v<=599 for v in diagnostics['http_status_codes'])):
                        raise ReviewUnavailable('invalid_worker_diagnostics')
                    self.metadata={'diagnostics':diagnostics}
            if type(result) is not dict or result.get('ok') is not True:
                code=result.get('error') if type(result) is dict else None
                allowed={'configured_key_missing','upstream_http_error','review_timeout',
                         'configured_profile_not_supported','unsafe_configured_endpoint',
                         'upstream_response_too_large','incomplete_or_invalid_response',
                         'unexpected_tool_or_message','invalid_response_content'}
                raise ReviewUnavailable(code if code in allowed else 'review_worker_failed_closed')
            if result.get('binding')!=payload['_review_context']['binding']:
                raise ReviewUnavailable('review_response_selection_changed')
            self.metadata={**(self.metadata or {}),**{k:result.get(k) for k in ('profile','model')}}
            return result['value']
        except BaseException as error:
            primary=error
            raise
        finally:
            failures=[]
            try:
                if process.poll() is None:
                    process.kill();process.wait(timeout=5)
            except BaseException as error:failures.append(error)
            for pipe in (process.stdin,process.stdout,process.stderr):
                if pipe is not None:
                    try:
                        pipe.close()
                        if pipe.closed is not True:raise RuntimeError('review_pipe_exit_unconfirmed')
                    except BaseException as error:failures.append(error)
            if failures:
                self._uncertain=True
                if primary is None:primary=ReviewUnavailable('review_resources_exit_unconfirmed')
                BaseException.__setattr__(primary,'_review_cleanup_errors',tuple(failures))
                BaseException.__setattr__(primary,'_review_owner',self)
                raise primary
            if not self._uncertain:self._owned=None

    def _ask(self,source,phase,payload):
        # Do not infer permission to send source text externally from a model label.
        try:authorized=self.authorize is not None and self.authorize(source,phase) is True
        except Exception:
            raise ReviewUnavailable('external_review_authorization_unavailable') from None
        if not authorized:
            raise ReviewUnavailable('external_review_data_not_authorized')
        if not self._lock.acquire(blocking=False):raise ReviewUnavailable('review_busy')
        try:
            if self._closed:raise ReviewUnavailable('review_closed')
            if self._uncertain:raise ReviewUnavailable('review_resource_unresolved')
            if self._session is None or self._session[1]!=threading.get_ident():
                raise ReviewUnavailable('review_session_required')
            from .review_config import verify,worker_context
            selection=self._session[0]
            verify(self.engine_root,self.policy_root,self.bot,selection)
            self._active=True;self.requests+=1
            request_id=uuid.uuid4().hex
            raw=self._exchange({'phase':phase,'request_id':request_id,**payload,
                                '_review_context':worker_context(selection)})
            verify(self.engine_root,self.policy_root,self.bot,selection)
            if self._closed or self.authorize(source,phase) is not True:
                raise ReviewUnavailable('review_source_or_session_revoked')
            if type(raw) is not dict or raw.get('request_id')!=request_id:
                raise ReviewUnavailable('review_response_binding_invalid')
            return raw
        except ReviewUnavailable:
            self.failures+=1;raise
        except Exception as error:
            self.failures+=1
            raise ReviewUnavailable('review_failed_closed') from error
        finally:
            self._active=self._session is not None;self._lock.release()

    def assess(self,event):
        raw=self._ask(event,'assess',{'source_text':event.text,'source_visibility':event.scope.split(':')[0]})
        if (set(raw)!={'request_id','classification'} or raw['classification'] not in
                ('ordinary','sensitive','confidential','credential','unknown')):
            raise ReviewUnavailable('invalid_source_assessment')
        # Model-proposed interest tags/recipient relevance are not host-verified facts.
        return self.bridge.SourceAssessment(raw['classification'])

    def review(self,request):
        raw=self._ask(request.evidence,'review',{'source_text':request.evidence.text,
            'source_visibility':request.evidence.visibility,'source_classification':request.evidence.classification,
            'proposed_text':request.final_text,'expression_mode':request.proposal.mode})
        if (set(raw)!={'request_id','approved','supported','sensitivity','identifiability','expectation_violation'}
                or type(raw['approved']) is not bool or type(raw['supported']) is not bool
                or not all(type(raw[k]) is int and 0<=raw[k]<=4 for k in
                           ('sensitivity','identifiability','expectation_violation'))):
            raise ReviewUnavailable('invalid_independent_review')
        exposure=self.contracts.Exposure(raw['sensitivity'],raw['identifiability'],raw['expectation_violation'])
        return self.bridge.ReviewDecision(request.binding,raw['approved'],raw['supported'],exposure,
                                          request.expires_ms)

    def close(self,timeout=None):
        if timeout is not None and (type(timeout) not in (int,float) or not 0<=timeout<=60):
            raise ValueError('finite_close_timeout_required')
        # Admission latch only; in-flight work remains owned until bounded worker exit.
        self._closed=True

    def shutdown_complete(self):
        return self._closed and not self._active and not self._uncertain and self._owned is None


def register_with_owner(owner, **kwargs):
    """Host-only: call before acquiring BridgeMount so reverse cleanup stops mount first."""
    holder={}
    def create():
        reviewer=DeepSeekReviewer(**kwargs)
        holder['value']=reviewer
        return reviewer
    return owner.controller('privacy_reviewer',create,current=lambda:holder.get('value'))
