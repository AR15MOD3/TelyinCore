"""Compatibility shims for the retired fixed-panel launcher display.

The launcher now appends stages in normal terminal scrollback. These helpers
never redirect stdout, reserve screen regions, create threads or clear output.
"""
from contextlib import contextmanager

@contextmanager
def dashboard(frame, *, core=None):
    print(frame, flush=True)
    yield

@contextmanager
def selection_surface():
    yield

def section(title):
    return False
