"""Bounded ordinary-web launch flags, authenticated by the instance-only parent.

No root/Bot/port overrides, acceptance flags or host capabilities. Console input
is inherited separately from the authenticated private control pipe.
Trusted caller supplied options are not themselves host-coverage authority.
"""
def normalize_options(value):
    if value is None:return {}
    if type(value) is not dict or not set(value)<= {'counterpart','channel_context_v1','cache_prefix_diagnostics','retrieval_at_tail','start_paused','console_only_canary','console','runtime_evidence'}:
        raise ValueError('invalid ordinary web launcher options')
    out={}
    for name in ('counterpart','channel_context_v1','cache_prefix_diagnostics','retrieval_at_tail','start_paused','console_only_canary','console','runtime_evidence'):
        if name not in value:continue
        item=value[name]
        if name=='counterpart':
            if (type(item) is not str or len(item.encode('utf-8'))>512
                    or any(ord(c)<32 or ord(c)==127 for c in item)):
                raise ValueError('bounded non-control counterpart required')
            if item:out[name]=item
        else:
            if type(item) is not bool:raise ValueError('exact boolean launcher flag required')
            if item:out[name]=True
    if out.get('console_only_canary') and not out.get('start_paused'):
        raise ValueError('console-only canary requires start-paused')
    return out


def options_argv(value):
    options=normalize_options(value);argv=[]
    if 'counterpart' in options:argv.append('--counterpart='+options['counterpart'])
    if options.get('channel_context_v1'):argv.append('--channel-context-v1')
    if options.get('cache_prefix_diagnostics'):argv.append('--cache-prefix-diagnostics')
    if options.get('retrieval_at_tail'):argv.append('--experimental-retrieval-tail')
    if options.get('start_paused'):argv.append('--start-paused')
    if options.get('console_only_canary'):argv.append('--console-only-canary')
    if options.get('console'):argv.append('--console')
    if options.get('runtime_evidence'):argv.append('--runtime-evidence')
    return argv
