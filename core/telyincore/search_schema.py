"""Optional FTS storage, retaining positions and the exact candidate semantics."""
import sqlite3

def options(version=None):
    # contentless-delete supports the existing DELETE/REPLACE operations.
    # It requires docsize metadata; columnsize=0 is incompatible.
    return ", content='', contentless_delete=1" if (version or sqlite3.sqlite_version_info)>=(3,43,0) else ''
