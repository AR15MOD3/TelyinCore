"""Bounded source-bound list filters, never authorization or final pages."""
from collections import OrderedDict
from contextlib import contextmanager
import sys

_FIELDS = ('scope','role','kind','is_head','ts_ms','who')


class ListingMetadata:
    MAX_BYTES = 4 * 1024 * 1024
    MAX_ENTRIES = 4096

    def __init__(self):
        self.entries = OrderedDict()
        self.bytes = 0
        self._scan_depth = 0
        self._seen = None

    def get(self,key):
        entry = self.entries.pop(key,None)
        if entry is None:return None
        self.entries[key]=entry
        if self._seen is not None:self._seen.add(key)
        return dict(entry[0])

    def add(self,key,row):
        if self.MAX_BYTES<=0 or self.MAX_ENTRIES<=0:return None
        values={name:row[name] for name in _FIELDS if name in row}
        for value in values.values():
            if type(value) not in (str,int,float,bool,type(None)):return None
            if isinstance(value,str) and len(value)>512:return None
            if type(value) is int and value.bit_length()>128:return None
        size=sys.getsizeof(key)+sys.getsizeof(values)+128
        size+=sum(sys.getsizeof(k)+sys.getsizeof(v) for k,v in values.items())
        if size>self.MAX_BYTES:return None
        # A full sequential pass must not evict its existing working set for
        # one-time misses; otherwise every over-budget scan defeats the cache.
        if self._scan_depth and key not in self.entries and (
                self.bytes+size>self.MAX_BYTES or len(self.entries)>=self.MAX_ENTRIES):return None
        old=self.entries.pop(key,None)
        if old is not None:self.bytes-=old[1]
        while self.entries and (self.bytes+size>self.MAX_BYTES or len(self.entries)>=self.MAX_ENTRIES):
            _,old=self.entries.popitem(last=False);self.bytes-=old[1]
        self.entries[key]=(values,size);self.bytes+=size
        if self._seen is not None:self._seen.add(key)
        return dict(values)


    @contextmanager
    def scan(self):
        outer = self._scan_depth == 0
        if outer:
            self._seen = set()
            while self.entries and (self.bytes>self.MAX_BYTES or len(self.entries)>self.MAX_ENTRIES):
                _,entry=self.entries.popitem(last=False);self.bytes-=entry[1]
        self._scan_depth += 1
        complete = False
        try:
            yield
            complete = True
        finally:
            self._scan_depth -= 1
            if outer:
                # Reclaim removed source identities only after a complete pass.
                # An error/partial iteration must not pretend it saw all rows.
                if complete:
                    for key in list(self.entries):
                        if key not in self._seen:
                            _,size=self.entries.pop(key);self.bytes-=size
                self._seen = None
