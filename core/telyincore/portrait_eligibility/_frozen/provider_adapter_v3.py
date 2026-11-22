"""Explicit v3 request gate; reuse unchanged final-wire/source/tool validation."""
import hashlib
from .contracts import Refused,Request,canonical
from .provider_adapter import wire_bytes as v2_wire_bytes
from .textspan_v3 import V2,V3

def wire_bytes(request,model,*,admitted_kinds=None):
    obj=request.read()
    if obj.get('protocol')!=V3:raise Refused('v3_wire_protocol_required')
    # Only the versioned in-memory request envelope changes; final Responses
    # encoding and all source/schema/span checks are the frozen implementation.
    obj['protocol']=V2;raw=canonical(obj)
    explicit_common=Request(request.stage,raw,hashlib.sha256(raw.encode('utf-8')).hexdigest())
    return v2_wire_bytes(explicit_common,model,admitted_kinds=admitted_kinds)
