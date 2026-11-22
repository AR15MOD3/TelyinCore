"""Bounded Windows CPython 3.11 launcher: OS job membership at creation.

Not a host inventory, sandbox, account proof or production coverage seal.
Only startup.py Web/console uses this backend. Unknown runtimes fail closed.
"""
from __future__ import annotations
import os
import sys
import subprocess
from startup_terminal import ChildJob, ChildJobFailure

PROFILE = 'windows10-x64-cpython311-job-list-v1'


class BirthPopen(subprocess.Popen):
    def __init__(self, args, *, job_name=None, **kw):
        import struct
        import sysconfig
        if (os.name != 'nt' or sys.implementation.name != 'cpython'
                or sys.version_info[:2] != (3, 11) or struct.calcsize('P') != 8
                or sysconfig.get_platform() != 'win-amd64'
                or sys.getwindowsversion().major < 10):
            raise RuntimeError('unsupported launcher birth profile')
        if (not isinstance(args, list) or not args or not all(type(x) is str and '\0' not in x for x in args)
                or not os.path.isabs(args[0]) or not os.path.isabs(kw.get('cwd', ''))
                or kw.get('shell', False) or kw.get('executable') is not None
                or kw.get('close_fds', True) is not True or kw.get('pass_fds')
                or kw.get('stdout') != subprocess.PIPE or kw.get('stderr') != subprocess.STDOUT
                or kw.get('stdin') not in (None, subprocess.PIPE)
                or kw.get('creationflags', 0) & ~ (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
                or type(kw.get('env')) is not dict
                or not all(type(k) is str and type(v) is str and k and '=' not in k and '\0' not in k+v for k,v in kw['env'].items())):
            raise ValueError('unsupported launcher birth arguments')
        if len({k.casefold() for k in kw['env']}) != len(kw['env']):
            raise ValueError('duplicate environment key')
        si = kw.get('startupinfo')
        if si is not None and (si.dwFlags or set(si.lpAttributeList or {}) - {'handle_list'}):
            raise ValueError('unsupported launcher startup attributes')
        self._birth_stdin_mode = 'inherited' if kw.get('stdin') is None else 'pipe'
        self._birth_command = tuple(args)
        from pathlib import Path
        self._birth_cwd = Path(kw['cwd']).resolve()
        self.birth_job = self._birth_job = (ChildJob(None) if job_name is None else ChildJob(None, name=job_name))
        self._birth_handle = None
        try:
            super().__init__(list(self._birth_command), **kw)
        except BaseException as error:
            # Caller owns this exact job/process even if creation or verification failed.
            if isinstance(error, ChildJobFailure):raise
            failure = ChildJobFailure(self.birth_job, 'birth creation')
            raise failure from error

    def _execute_child(self, args, executable, preexec_fn, close_fds, pass_fds,
                       cwd, env, startupinfo, creationflags, shell,
                       p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite,
                       unused_restore_signals, unused_gid, unused_gids, unused_uid,
                       unused_umask, unused_start_new_session, unused_process_group):
        # CPython owns binary pipes/wait/poll. Only CreateProcess is replaced;
        # no global monkeypatch and no broad inherit-handles fallback.
        import ctypes as c
        from ctypes import wintypes as w
        import _winapi
        class SI(c.Structure):
            _fields_ = [('cb',w.DWORD),('reserved',w.LPWSTR),('desktop',w.LPWSTR),('title',w.LPWSTR)] + [(n,w.DWORD) for n in ('x','y','xs','ys','xc','yc','fill','flags')] + [('show',w.WORD),('reserved_size',w.WORD),('reserved_ptr',c.c_void_p),('stdin',w.HANDLE),('stdout',w.HANDLE),('stderr',w.HANDLE)]
        class SIX(c.Structure):
            _fields_ = [('si',SI),('attributes',c.c_void_p)]
        class PI(c.Structure):
            _fields_ = [('process',w.HANDLE),('thread',w.HANDLE),('pid',w.DWORD),('tid',w.DWORD)]
        k = self.birth_job.kernel
        k.InitializeProcThreadAttributeList.argtypes = [c.c_void_p,w.DWORD,w.DWORD,c.POINTER(c.c_size_t)]
        k.InitializeProcThreadAttributeList.restype = w.BOOL
        k.UpdateProcThreadAttribute.argtypes = [c.c_void_p,w.DWORD,c.c_size_t,c.c_void_p,c.c_size_t,c.c_void_p,c.c_void_p]
        k.UpdateProcThreadAttribute.restype = w.BOOL
        k.DeleteProcThreadAttributeList.argtypes = [c.c_void_p]
        k.DeleteProcThreadAttributeList.restype = None
        k.CreateProcessW.argtypes = [w.LPCWSTR,w.LPWSTR,c.c_void_p,c.c_void_p,w.BOOL,w.DWORD,c.c_void_p,w.LPCWSTR,c.POINTER(SIX),c.POINTER(PI)]
        k.CreateProcessW.restype = w.BOOL
        k.ResumeThread.argtypes = [w.HANDLE]
        k.ResumeThread.restype = w.DWORD
        size = c.c_size_t()
        initialized = False
        try:
            handles = list((startupinfo.lpAttributeList or {}).get('handle_list', [])) if startupinfo else []
            if -1 in (p2cread,c2pwrite,errwrite):raise ValueError('explicit native standard handles required')
            handles += [int(p2cread),int(c2pwrite),int(errwrite)]
            handles = self._filter_handle_list(handles)
            if not handles:raise ValueError('empty native handle whitelist')
            self._birth_inherited = tuple(handles)
            self._birth_stdio = (int(p2cread),int(c2pwrite),int(errwrite))
            inherited = (w.HANDLE * len(handles))(*handles)
            jobs = (w.HANDLE * 1)(self.birth_job.handle)
            k.InitializeProcThreadAttributeList(None,2,0,c.byref(size))
            if not size.value:raise c.WinError(c.get_last_error())
            attributes = c.create_string_buffer(size.value)
            if not k.InitializeProcThreadAttributeList(attributes,2,0,c.byref(size)):raise c.WinError(c.get_last_error())
            initialized = True
            for key,value in ((0x20002,inherited),(0x2000D,jobs)):
                if not k.UpdateProcThreadAttribute(attributes,0,key,value,c.sizeof(value),None,None):raise c.WinError(c.get_last_error())
            info = SIX();info.si.cb=c.sizeof(info);info.si.flags=0x100
            info.si.stdin=int(p2cread);info.si.stdout=int(c2pwrite);info.si.stderr=int(errwrite)
            info.attributes=c.cast(attributes,c.c_void_p)
            command=c.create_unicode_buffer(subprocess.list2cmdline(args))
            environment=c.create_unicode_buffer('\0'.join(k+'='+v for k,v in sorted(env.items(),key=lambda x:x[0].upper()))+'\0\0')
            pi=PI()
            sys.audit('subprocess.Popen',args[0],command.value,str(cwd),env)
            # OS assigns the specified noninherited job as part of process creation.
            if not k.CreateProcessW(args[0],command,None,None,True,creationflags|0x80000|0x400|0x4,environment,str(cwd),c.byref(info),c.byref(pi)):
                raise c.WinError(c.get_last_error())
            # Retain both native outputs BEFORE any later fallible verification.
            self._handle=subprocess.Handle(pi.process);self.pid=pi.pid;self._child_created=True
            self._birth_handle=self._handle
            self.birth_job.thread_handle=pi.thread
            self.birth_job.process=self;self.birth_job._process_handle=self._handle
            self.birth_job.assigned=True
            self.birth_job.verify()
            if k.ResumeThread(self.birth_job.thread_handle) != 1:
                raise ChildJobFailure(self.birth_job, 'primary thread release',c.get_last_error())
            if not k.CloseHandle(self.birth_job.thread_handle):raise ChildJobFailure(self.birth_job,'primary thread close',c.get_last_error())
            self.birth_job.thread_handle=None
        finally:
            if initialized:k.DeleteProcThreadAttributeList(attributes)
            self._close_pipe_fds(p2cread,p2cwrite,c2pread,c2pwrite,errread,errwrite)
