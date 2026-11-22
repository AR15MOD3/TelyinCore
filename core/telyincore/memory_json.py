"""Stdlib-compatible JSON bytes with bounded string buffers at every depth.

Builtin containers are traversed without a whole encoded subtree. Existing
scalar/custom-subclass semantics and separators remain those of json.dumps.
The input objects, nesting frames, large scalar numbers and requested complete
output still have their own size; this is not a total process memory bound.
"""
import json


def flat_bytes(value, *, errors='strict'):
    if type(value) is dict and all(isinstance(k,str) for k in value):
        members=value.values();budget=sum(len(k) for k in value)
    elif type(value) in (list,tuple):
        members=value;budget=0
    else:
        yield from _walk(value,errors)
        return
    # Preserve the native fast path for bounded flat everyday records.
    for item in members:
        budget+=len(item) if isinstance(item,str) else 40
        if budget>4096 or isinstance(item,(dict,list,tuple)):break
    else:
        yield json.dumps(value,ensure_ascii=False).encode('utf-8',errors)
        return
    yield from _walk(value,errors)


def _string(value, errors):
    if type(value) is not str:
        yield json.dumps(value,ensure_ascii=False).encode('utf-8',errors)
        return
    yield b'"'
    for offset in range(0,len(value),4096):
        yield json.dumps(value[offset:offset+4096],ensure_ascii=False)[1:-1].encode('utf-8',errors)
    yield b'"'


def _key(value):
    if isinstance(value,str):return value
    if value is None or isinstance(value,(int,float)):
        return json.dumps(value,ensure_ascii=False)
    # Delegate the invalid-key error wording/type to the compatibility encoder.
    json.dumps({value:None},ensure_ascii=False)
    raise TypeError('unsupported JSON key')


def _walk(value, errors):
    active=set();stack=[('value',value)]
    while stack:
        frame=stack.pop();kind=frame[0]
        if kind=='value':
            item=frame[1]
            if type(item) in (dict,list,tuple):
                identity=id(item)
                if identity in active:raise ValueError('Circular reference detected')
                active.add(identity)
                mapping=type(item) is dict
                yield b'{' if mapping else b'['
                stack.append(('object' if mapping else 'array',iter(item.items() if mapping else item),identity,True))
            elif type(item) is str:yield from _string(item,errors)
            else:yield json.dumps(item,ensure_ascii=False).encode('utf-8',errors)
            continue
        iterator,identity,first=frame[1:]
        try:item=next(iterator)
        except StopIteration:
            active.remove(identity);yield b'}' if kind=='object' else b']';continue
        if not first:yield b', '
        stack.append((kind,iterator,identity,False))
        if kind=='object':
            key,item=item
            yield from _string(_key(key),errors)
            yield b': '
        stack.append(('value',item))
