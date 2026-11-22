"""Bound extra main-database pages allocated by optional search operations.

This is deliberately not a quota on source history, mandatory snapshots,
SQLite journals or temporary sorting files. Existing free pages can be reused.
Callers hold their connection lock and finish rollback before leaving limit().
"""
from contextlib import contextmanager


class SearchGrowth:
    def __init__(self):
        self.used_bytes = 0

    @contextmanager
    def limit(self, db, budget):
        size = db.execute('PRAGMA page_size').fetchone()[0]
        before = db.execute('PRAGMA page_count').fetchone()[0]
        previous = db.execute('PRAGMA max_page_count').fetchone()[0]
        allowance = max(0, budget - self.used_bytes) // size
        ceiling = min(previous, before + allowance)
        installed = db.execute(f'PRAGMA max_page_count={ceiling}').fetchone()[0]
        try:
            yield
        finally:
            # Transaction exit/rollback belongs inside this context. Charge
            # only surviving growth, conservatively including canonical rows
            # written by an update that also maintained the optional index.
            after = db.execute('PRAGMA page_count').fetchone()[0]
            self.used_bytes += max(0, after - before) * size
            current = db.execute('PRAGMA max_page_count').fetchone()[0]
            # Do not relax another tighter limit installed by the caller.
            restore = previous if current == installed else min(previous, current)
            db.execute(f'PRAGMA max_page_count={restore}').fetchone()
