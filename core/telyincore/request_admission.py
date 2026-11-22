"""Opt-in host-owned HTTP opening fence, not an authorization policy.





The host must bind a trusted per-task grant and synchronize every relevant


permission mutation with its context manager. No model/UI configuration may


supply this factory. Descriptors and their prompt digests must not be logged.


The lease ends after response headers, NOT after stream consumption. Admission


cannot recall requests already opened, or coordinate uncooperative processes.


"""


from contextlib import asynccontextmanager


from dataclasses import dataclass, field


from copy import deepcopy


import hashlib


import inspect


import json








class SendAdmissionDenied(RuntimeError):


    """Non-retryable host admission/configuration failure; no secret details."""








@dataclass(frozen=True)


class OpenRequest:


    url: str


    model: str


    body_sha256: str = field(repr=False)








def bind_request(url, body):


    try:


        frozen=deepcopy(dict(body))


        encoded=json.dumps(frozen,ensure_ascii=False,sort_keys=True,


                           separators=(',', ':'),allow_nan=False).encode('utf8')


        model=frozen.get('model')


        if not isinstance(model,str):raise ValueError()


    except Exception:


        raise SendAdmissionDenied('invalid_request_binding') from None


    return frozen,OpenRequest(url,model,hashlib.sha256(encoded).hexdigest())








@asynccontextmanager


async def admit(factory,binding):


    cm = None
    try:


        cm=factory(binding)


        if not callable(getattr(cm,'__aenter__',None)) or not callable(getattr(cm,'__aexit__',None)):


            if inspect.iscoroutine(cm):cm.close()


            raise TypeError()


        await cm.__aenter__()


    except BaseException as failure:


        if isinstance(failure, Exception):
            denial = SendAdmissionDenied('admission_unavailable')
            denial.admission_failure = failure
        else:
            denial = failure
        denial.admission_owner = cm
        if inspect.isawaitable(cm) and not inspect.iscoroutine(cm):
            # Invalid live return is not permission to cancel or discard it.
            # Keep both its identity and eventual error on the public refusal.
            def observed(work):
                try:
                    denial.admission_work_result = work.result()
                except BaseException as error:
                    denial.admission_work_error = error
            add = getattr(cm, 'add_done_callback', None)
            if callable(add):
                try: add(observed)
                except BaseException as error: denial.admission_observation_error = error
        raise denial from None


    try:


        yield


    except BaseException:


        import sys


        failure=sys.exc_info()


        try:await cm.__aexit__(*failure)


        except BaseException as cleanup:
            primary = failure[1]
            primary.admission_owner = cm
            primary.admission_cleanup_errors = (*getattr(primary, 'admission_cleanup_errors', ()), cleanup)


        raise  # A host context cannot suppress an HTTP or cancellation failure.


    else:


        try:await cm.__aexit__(None,None,None)


        except BaseException as cleanup:


            if isinstance(cleanup, Exception):
                denial = SendAdmissionDenied('admission_release_failed')
            else:
                denial = cleanup
            denial.admission_owner = cm
            denial.admission_cleanup_errors = (cleanup,)
            raise denial from None


