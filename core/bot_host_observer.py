"""Read-only host observer adapter, no process enumeration, command lines or kill.

An authoritative HOST-owned complete roster callable is mandatory. Neither a PID
file nor caller JSON constitutes a complete roster. Construction requires an explicit
roster; local controller integration is not proof of global registry coverage.
"""
from __future__ import annotations

import os
import copy
from pathlib import Path


def probe_process(pid):
    """Windows: query ONLY this explicitly supplied PID, identity pinned to one handle."""
    if type(pid) is not int or not 0 < pid < 0xFFFFFFFF:
        return {'pid':pid,'state':'unknown','reason':'invalid_pid'}
    if os.name!='nt':return {'pid':pid,'state':'unknown','reason':'unsupported_platform'}
    import ctypes as C
    from ctypes import wintypes as W
    kernel=C.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];kernel.OpenProcess.restype=W.HANDLE
    kernel.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD];kernel.WaitForSingleObject.restype=W.DWORD
    kernel.GetProcessTimes.argtypes=[W.HANDLE,*([C.POINTER(W.FILETIME)]*4)];kernel.GetProcessTimes.restype=W.BOOL
    kernel.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,C.POINTER(W.DWORD)]
    kernel.QueryFullProcessImageNameW.restype=W.BOOL
    kernel.CloseHandle.argtypes=[W.HANDLE];kernel.CloseHandle.restype=W.BOOL
    handle=kernel.OpenProcess(0x00100000|0x1000,False,pid)  # SYNCHRONIZE + QUERY_LIMITED_INFORMATION
    if not handle:
        error=C.get_last_error()
        return {'pid':pid,'state':'absent' if error==87 else 'unknown','reason':'open_'+str(error)}
    try:
        waited=kernel.WaitForSingleObject(handle,0)
        if waited==0:return {'pid':pid,'state':'absent','reason':'terminated_handle'}
        if waited!=258:return {'pid':pid,'state':'unknown','reason':'wait_failed'}
        created,exited,kernel_time,user_time=(W.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle,C.byref(created),C.byref(exited),C.byref(kernel_time),C.byref(user_time)):
            return {'pid':pid,'state':'unknown','reason':'creation_time_unavailable'}
        size=W.DWORD(32768);image=C.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle,0,image,C.byref(size)):
            return {'pid':pid,'state':'unknown','reason':'image_unavailable'}
        waited=kernel.WaitForSingleObject(handle,0)
        if waited==0:return {'pid':pid,'state':'absent','reason':'terminated_during_query'}
        if waited!=258:return {'pid':pid,'state':'unknown','reason':'wait_failed'}
        return {'pid':pid,'state':'alive','created_filetime':(created.dwHighDateTime<<32)|created.dwLowDateTime,
                'image':image.value}
    finally:
        kernel.CloseHandle(handle)


def retained_process_exit(process, expected, original_handle):
    """Observe only the launcher's original native handle; never adopt/release a PID.

    PID reuse after original exit is allowed for this local observation. It does
    NOT retire a HOST row, clear custody or prove the replacement process absent.
    """
    if os.name!='nt' or original_handle is None:return False
    try:
        if process._handle is not original_handle or process.pid!=expected['pid'] or process.poll() is None:return False
        import ctypes as C
        from ctypes import wintypes as W
        k=C.WinDLL('kernel32',use_last_error=True)
        k.GetProcessId.argtypes=[W.HANDLE];k.GetProcessId.restype=W.DWORD
        k.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD];k.WaitForSingleObject.restype=W.DWORD
        k.GetProcessTimes.argtypes=[W.HANDLE,*([C.POINTER(W.FILETIME)]*4)];k.GetProcessTimes.restype=W.BOOL
        k.GetExitCodeProcess.argtypes=[W.HANDLE,C.POINTER(W.DWORD)];k.GetExitCodeProcess.restype=W.BOOL
        handle=W.HANDLE(int(original_handle))
        if k.GetProcessId(handle)!=expected['pid'] or k.WaitForSingleObject(handle,0)!=0:return False
        created,exited,kernel,user=(W.FILETIME() for _ in range(4));code=W.DWORD()
        if not k.GetProcessTimes(handle,C.byref(created),C.byref(exited),C.byref(kernel),C.byref(user)):return False
        birth=(created.dwHighDateTime<<32)|created.dwLowDateTime
        return (birth==expected['created_filetime'] and k.GetExitCodeProcess(handle,C.byref(code))
                and code.value!=259 and code.value==process.poll()
                and process._handle is original_handle)
    except (Exception,):return False


def retained_process_alive(process, expected, original_handle):
    """Verify a still-running OWN original handle without reopening its PID.

    A failed/ambiguous query stays unknown. This is local observation only,
    never host completeness, row retirement or permission to close a job.
    """
    if os.name!='nt' or original_handle is None:return False
    try:
        if (process._handle is not original_handle or process.pid!=expected['pid']
                or process.poll() is not None):return False
        import ctypes as C
        from ctypes import wintypes as W
        k=C.WinDLL('kernel32',use_last_error=True)
        k.GetProcessId.argtypes=[W.HANDLE];k.GetProcessId.restype=W.DWORD
        k.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD];k.WaitForSingleObject.restype=W.DWORD
        k.GetProcessTimes.argtypes=[W.HANDLE,*([C.POINTER(W.FILETIME)]*4)];k.GetProcessTimes.restype=W.BOOL
        k.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,C.POINTER(W.DWORD)]
        k.QueryFullProcessImageNameW.restype=W.BOOL
        h=W.HANDLE(int(original_handle))
        if k.GetProcessId(h)!=expected['pid'] or k.WaitForSingleObject(h,0)!=258:return False
        created,exited,kernel,user=(W.FILETIME() for _ in range(4))
        if not k.GetProcessTimes(h,C.byref(created),C.byref(exited),C.byref(kernel),C.byref(user)):return False
        if (created.dwHighDateTime<<32)|created.dwLowDateTime!=expected['created_filetime']:return False
        size=W.DWORD(32768);image=C.create_unicode_buffer(size.value)
        if not k.QueryFullProcessImageNameW(h,0,image,C.byref(size)):return False
        if str(Path(image.value).resolve())!=str(Path(expected['image']).resolve()):return False
        return (k.WaitForSingleObject(h,0)==258 and process.poll() is None
                and process._handle is original_handle and process.pid==expected['pid'])
    except Exception:return False


class HostObserver:
    """Callable for managed_offline_lease. Roster is a trusted host service boundary,
    not a callback built by wrapping JSON from a user/request/fixture receipt.
    """
    def __init__(self, *, roster, probe=probe_process):
        if not callable(roster) or not callable(probe):raise ValueError('host roster/probe must be trusted callables')
        self.roster,self.probe=roster,probe;self.generation=None

    def __call__(self,core,names,records):
        result={'complete':False,'core':str(core.resolve()),'unresolved':[],'processes':[]}
        issues=result['unresolved'];entries={}
        try:
            data=copy.deepcopy(self.roster(core))
            if (not isinstance(data,dict) or set(data)!={'complete','core','generation','processes'}
                    or data['complete'] is not True or data['core']!=str(core.resolve())
                    or not isinstance(data['generation'],str) or not 1<=len(data['generation'])<=64
                    or not isinstance(data['processes'],list) or len(data['processes'])>1024):
                raise ValueError('incomplete_roster')
            if self.generation is None:self.generation=data['generation']
            if self.generation!=data['generation']:raise ValueError('roster_generation_changed')
            for row in data['processes']:
                if (not isinstance(row,dict) or set(row)!={'pid','bot','created_filetime','image'}
                        or type(row['pid']) is not int or row['pid']<=0 or row['pid'] in entries
                        or row['bot'] not in names or type(row['created_filetime']) is not int
                        or row['created_filetime']<=0 or not isinstance(row['image'],str)
                        or not Path(row['image']).is_absolute()):raise ValueError('invalid_roster_entry')
                entries[row['pid']]=row
        except Exception:
            issues.append('host_roster_incomplete_invalid_or_changed');return result
        pids=set(entries)
        registered={}
        for name,row in records.items():
            if not isinstance(row,dict) or type(row.get('pid')) is not int or row['pid']<=0:
                issues.append('invalid_instance_record');continue
            pid=row['pid']
            if pid in registered:issues.append('duplicate_registered_pid')
            registered[pid]=name;pids.add(pid)
            if pid in entries and entries[pid]['bot']!=name:issues.append('roster_registration_conflict')
        for pid in sorted(pids):
            try:
                seen=self.probe(pid)
                if not isinstance(seen,dict) or seen.get('pid')!=pid:raise ValueError()
                state=seen.get('state')
                if state=='absent':continue  # does NOT delete the stale registration
                if state!='alive':raise ValueError()
                expected=entries.get(pid)
                matched=(expected is not None and type(seen.get('created_filetime')) is int
                         and seen['created_filetime']==expected['created_filetime']
                         and isinstance(seen.get('image'),str)
                         and os.path.normcase(os.path.normpath(seen['image']))==os.path.normcase(os.path.normpath(expected['image'])))
                result['processes'].append({'pid':pid,'state':'alive','identity':'matched' if matched else 'unverified_or_reused'})
                if not matched:issues.append('process_identity_unverified_or_reused')
            except Exception:issues.append('native_probe_unknown_or_denied')
        # A roster can change while native probes are running. Never publish a
        # complete result from a generation/list that is no longer authoritative.
        try:
            if self.roster(core) != data:
                issues.append('host_roster_changed_during_probe')
        except Exception:
            issues.append('host_roster_final_check_unavailable')
        result['complete']=not issues
        return result
