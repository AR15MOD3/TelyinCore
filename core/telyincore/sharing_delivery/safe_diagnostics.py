"""Constant-only diagnostics. Never serialize exception messages, paths or tracebacks."""
STAGES=frozenset({'readiness_wait','shadow_preview','audit_publish','worker'})
CODES=frozenset({'shadow_not_enabled','host_clock_rollback','mount_retired_or_rebuilt','host_gate_unknown','source_authority_lost','console_readiness_unknown','quiet_state_unknown','host_state_rollback','source_reference_not_owned','console_not_assembled','source_or_readiness_changed','source_authority_required','exact_host_dependencies_required','host_owner_mismatch'})
KINDS={ValueError:'ValueError',TypeError:'TypeError',RuntimeError:'RuntimeError',OSError:'OSError',TimeoutError:'TimeoutError',PermissionError:'PermissionError',AttributeError:'AttributeError',NameError:'NameError'}
def diagnose(exc,stage):
 kind=KINDS.get(type(exc),'OtherError')
 code=exc.args[0] if type(exc) is ValueError and len(exc.args)==1 and type(exc.args[0]) is str and exc.args[0] in CODES else 'opaque_exception'
 return {'stage':stage if stage in STAGES else 'worker','kind':kind,'code':code}
