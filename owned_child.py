"""Own a spawned process tree, including Windows venv interpreter launchers."""
import os
import signal
import subprocess


class OwnedChild:
    def __init__(self, args, *, popen=subprocess.Popen, **kwargs):
        self.job = None
        if os.name != 'nt':
            self.process = popen(args, start_new_session=True, **kwargs)
            return
        import ctypes as c
        from ctypes import wintypes as w
        self.k = c.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            'SetInformationJobObject': ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            'AssignProcessToJobObject': ([w.HANDLE, w.HANDLE], w.BOOL),
            'TerminateJobObject': ([w.HANDLE, w.UINT], w.BOOL),
            'CloseHandle': ([w.HANDLE], w.BOOL),
            'IsProcessInJob': ([w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)], w.BOOL),
            'OpenProcess': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            'CreateToolhelp32Snapshot': ([w.DWORD, w.DWORD], w.HANDLE),
            'OpenThread': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            'ResumeThread': ([w.HANDLE], w.DWORD),
        }
        for name, (argtypes, restype) in signatures.items():
            func = getattr(self.k, name); func.argtypes = argtypes; func.restype = restype
        class Basic(c.Structure):
            _fields_ = [('PerProcessUserTimeLimit', c.c_longlong), ('PerJobUserTimeLimit', c.c_longlong),
                        ('LimitFlags', w.DWORD), ('MinimumWorkingSetSize', c.c_size_t),
                        ('MaximumWorkingSetSize', c.c_size_t), ('ActiveProcessLimit', w.DWORD),
                        ('Affinity', c.c_size_t), ('PriorityClass', w.DWORD), ('SchedulingClass', w.DWORD)]
        class Io(c.Structure):
            _fields_ = [(name, c.c_ulonglong) for name in ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]
        class Extended(c.Structure):
            _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', Io), ('ProcessMemoryLimit', c.c_size_t),
                        ('JobMemoryLimit', c.c_size_t), ('PeakProcessMemoryUsed', c.c_size_t), ('PeakJobMemoryUsed', c.c_size_t)]
        self.job = self.k.CreateJobObjectW(None, None)
        if not self.job: raise c.WinError(c.get_last_error())
        self.process = None
        try:
            info = Extended(); info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            if not self.k.SetInformationJobObject(self.job, 9, c.byref(info), c.sizeof(info)):
                raise c.WinError(c.get_last_error())
            # Assign before the initial thread can create any descendants.
            kwargs['creationflags'] = kwargs.get('creationflags', 0) | 0x4  # CREATE_SUSPENDED
            self.process = popen(args, **kwargs)
            if not self.k.AssignProcessToJobObject(self.job, int(self.process._handle)):
                raise c.WinError(c.get_last_error())
            class ThreadEntry(c.Structure):
                _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ThreadID', w.DWORD),
                            ('th32OwnerProcessID', w.DWORD), ('tpBasePri', w.LONG), ('tpDeltaPri', w.LONG), ('dwFlags', w.DWORD)]
            for name in ('Thread32First', 'Thread32Next'):
                getattr(self.k, name).argtypes = [w.HANDLE, c.POINTER(ThreadEntry)]
                getattr(self.k, name).restype = w.BOOL
            snapshot = self.k.CreateToolhelp32Snapshot(4, 0)
            if snapshot == w.HANDLE(-1).value: raise c.WinError(c.get_last_error())
            resumed = 0
            try:
                entry = ThreadEntry(); entry.dwSize = c.sizeof(entry)
                found = self.k.Thread32First(snapshot, c.byref(entry))
                while found:
                    if entry.th32OwnerProcessID == self.process.pid:
                        thread = self.k.OpenThread(2, False, entry.th32ThreadID)
                        if not thread: raise c.WinError(c.get_last_error())
                        try:
                            if self.k.ResumeThread(thread) == 0xffffffff: raise c.WinError(c.get_last_error())
                            resumed += 1
                        finally: self.k.CloseHandle(thread)
                    found = self.k.Thread32Next(snapshot, c.byref(entry))
            finally: self.k.CloseHandle(snapshot)
            if not resumed: raise RuntimeError('owned child has no resumable initial thread')
        except BaseException:
            if self.process is not None:
                self.k.TerminateJobObject(self.job, 1)
                self.process.kill()
                self.process.wait(5)
            self.k.CloseHandle(self.job); self.job = None
            raise

    @property
    def pid(self): return self.process.pid

    @property
    def returncode(self): return self.process.returncode

    def poll(self): return self.process.poll()

    def owns_pid(self, pid):
        if type(pid) is not int or pid <= 0: return False
        if os.name != 'nt':
            try: return os.getpgid(pid) == self.process.pid
            except ProcessLookupError: return False
        import ctypes as c
        from ctypes import wintypes as w
        process = self.k.OpenProcess(0x1000, False, pid)
        if not process: return False
        try:
            found = w.BOOL()
            if not self.k.IsProcessInJob(process, self.job, c.byref(found)): return False
            return bool(found.value)
        finally: self.k.CloseHandle(process)

    def terminate(self):
        if os.name == 'nt':
            if self.job and not self.k.TerminateJobObject(self.job, 1):
                import ctypes
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            try: os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError: pass

    def kill(self):
        if os.name == 'nt': self.terminate()
        else:
            try: os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError: pass

    def wait(self, timeout=None):
        result = self.process.wait(timeout)
        # Even a clean launcher exit must not leave owned grandchildren alive.
        if os.name == 'nt' and self.job:
            self.k.CloseHandle(self.job); self.job = None
        return result
