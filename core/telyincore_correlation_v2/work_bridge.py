"""Integrity/compatibility alias, never native ownership authority.
The colocated reference is only for isolated probes. In Core use its existing
exact pinned bot_tool_work module; never overwrite it or duplicate its classes.
"""
import importlib,hashlib
from pathlib import Path
_m=importlib.import_module('bot_tool_work')
try:
 _p=Path(_m.__file__).resolve()
 if _p.name!='bot_tool_work.py' or hashlib.sha256(_p.read_bytes()).hexdigest()!='ca3a3cd1cdf0677d0ce0745042d7a68da30259057c08cfa3e430fc4ed2732066':raise ValueError()
 if _m.ToolWork.__module__!='bot_tool_work' or _m.ToolWorkIncomplete.__module__!='bot_tool_work':raise ValueError()
 ToolWork=_m.ToolWork;ToolWorkIncomplete=_m.ToolWorkIncomplete
except Exception:raise ImportError('work_framework_pin_mismatch') from None
