"""Native Path order without an additional whole-tree list of Path objects.

The input walker retains its platform traversal semantics. Sorting buffers at
most SMALL paths in Python; larger inventories use a disposable SQLite index.
This bounds the sorter's memory, not the native walker's directory buffers.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
from .derived_cache import CacheDirectory

SMALL = 128


def _key(path):
    # Path compares native-case-normalized components, not the joined string.
    # NUL cannot occur in a filesystem name. UTF-32BE preserves Python's
    # codepoint ordering, including surrogate-escaped POSIX filenames.
    parts = os.path.normcase(str(path)).split(os.sep)
    if any('\0' in part for part in parts):raise ValueError('NUL in path inventory')
    return ('\0'.join(parts) + '\0').encode('utf-32be', 'surrogatepass')


@contextmanager
def ordered(paths):
    iterator = iter(paths)
    directory = db = cursor = None
    try:
        small = []
        for _ in range(SMALL + 1):
            try:small.append(next(iterator))
            except StopIteration:
                yield iter(sorted(small))
                return
        directory = CacheDirectory()
        db = sqlite3.connect(str(Path(directory.name)/'names.sqlite3'))
        db.execute('PRAGMA cache_size=-1024')
        db.execute('PRAGMA temp_store=FILE')
        db.execute('PRAGMA synchronous=OFF')
        db.execute('CREATE TABLE names(ordinal INTEGER PRIMARY KEY,sortkey BLOB,name TEXT)')
        from itertools import chain
        with db:
            for path in chain(small, iterator):
                db.execute('INSERT INTO names(sortkey,name) VALUES(?,?)',
                           (_key(path), json.dumps(str(path), ensure_ascii=True)))
        small.clear()
        cursor = db.execute('SELECT name FROM names ORDER BY sortkey,ordinal')
        yield (Path(json.loads(row[0])) for row in cursor)
    except sqlite3.Error as error:
        raise OSError('文件检索路径排序不可用，结果不完整') from error
    finally:
        primary = __import__('sys').exception()
        for obj, method in [(cursor,'close'),(db,'close'),(directory,'cleanup'),(iterator,'close')]:
            close = getattr(obj, method, None)
            if close is not None:
                try:close()
                except BaseException as error:
                    if primary is None:primary=error
                    else:primary.add_note('path inventory cleanup failed: '+type(error).__name__)
        if primary is not None and __import__('sys').exception() is None:raise primary
