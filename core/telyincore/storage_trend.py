"""Bounded aggregate metadata trends; never store message text or delete sources."""
import json
import math
import os
from pathlib import Path
from .source_bytes import checked_open

CATEGORIES = ('original_text', 'vectors', 'derived_indexes', 'media',
              'engineering_archives', 'databases_unclassified', 'other')
# Changing classification rules makes prior deltas incomparable.
SCHEMA = 'storage-categories-v2'
LIMIT = 1024 * 1024


def category(path):
    parts = tuple(p.casefold() for p in Path(path).parts); name = parts[-1]
    if name == 'vectors.jsonl' or name.startswith('vectors.jsonl.'):
        return 'vectors'
    if ((len(parts)>1 and parts[-2]=='search' and name in ('search.sqlite3','search.sqlite3-wal','search.sqlite3-shm','search.sqlite3-journal'))
            or any(p in ('derived-indexes', 'search-index', 'persistent-indexes') for p in parts)):
        return 'derived_indexes'
    if name.endswith(('.zip', '.tgz', '.tar.gz', '.7z')):
        return 'engineering_archives'
    if name.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif', '.wav', '.mp3', '.ogg', '.mp4', '.flac')):
        return 'media'
    if name.endswith(('.sqlite', '.sqlite3', '.db', '-wal', '-shm', '-journal')):
        return 'databases_unclassified'
    if '.jsonl' in name or 'record-history' in parts or name.endswith(('.md', '.txt')) or name == 'synthetic-history.bin':
        return 'original_text'
    return 'other'


def valid_timestamp(value):
    # Compare the bound before converting a possibly enormous JSON integer.
    return type(value) in (int, float) and 0 <= value < 2**63 and math.isfinite(value)


def _valid(row):
    if not isinstance(row, dict) or set(row) != {'at','complete','history_bytes','categories','free_bytes'}:return False
    if not valid_timestamp(row['at']):return False
    if type(row['complete']) is not bool:return False
    if type(row['history_bytes']) is not int or row['history_bytes'] < 0:return False
    if row['free_bytes'] is not None and (type(row['free_bytes']) is not int or row['free_bytes'] < 0):return False
    cats=row['categories']
    return (isinstance(cats,dict) and set(cats)==set(CATEGORIES)
            and all(type(v) is int and 0<=v<2**63 for v in cats.values()))


def update(folder, report):
    from .storage_watch import atomic
    sample={k:report[k] for k in ('at','complete','history_bytes','free_bytes')}
    sample['categories']=report['category_bytes']
    if not _valid(sample):raise ValueError('invalid trend sample')
    previous={'schema':SCHEMA,'hours':[],'days':[]}; reset=False
    path=Path(folder)/'trend.json'
    try:
        with checked_open(path) as stream:
            size = os.fstat(stream.fileno()).st_size
            if size > LIMIT:
                raise ValueError('oversized trend')
            raw = stream.read(size + 1)
        if len(raw)>LIMIT:raise ValueError('oversized trend')
        previous=json.loads(raw)
        if not isinstance(previous,dict) or set(previous)!={'schema','hours','days'} or previous['schema']!=SCHEMA:raise ValueError('trend schema')
        for field,maximum in [('hours',720),('days',366)]:
            rows=previous[field]
            if not isinstance(rows,list) or len(rows)>maximum or not all(_valid(r) for r in rows):raise ValueError('trend rows')
            if any(a['at']>=b['at'] for a,b in zip(rows,rows[1:])):raise ValueError('trend order')
    except FileNotFoundError:pass
    except (ValueError,TypeError,KeyError,RecursionError):
        previous={'schema':SCHEMA,'hours':[],'days':[]};reset=True
    # A backward clock must not fabricate negative/huge growth or evict future samples.
    if any(r['at']>sample['at'] for r in previous['hours']+previous['days']):
        return {'recorded':False,'reason':'clock_regressed','delta_bytes':None}
    delta=None
    old=previous['hours'][-1] if previous['hours'] else None
    if old and old['complete'] and sample['complete'] and old['at']<sample['at']:
        delta={k:sample['categories'][k]-old['categories'][k] for k in CATEGORIES}
    for field,seconds,count in [('hours',3600,720),('days',86400,366)]:
        bucket=int(sample['at']//seconds)
        rows={int(r['at']//seconds):r for r in previous[field] if bucket-count < int(r['at']//seconds) <= bucket}
        rows[bucket]=sample
        previous[field]=[rows[k] for k in sorted(rows)][-count:]
    raw=json.dumps(previous,separators=(',',':')).encode()
    if len(raw)>LIMIT:raise ValueError('trend budget exceeded')
    atomic(folder,'trend.json',raw)
    return {'recorded':True,'reset_invalid_state':reset,'bytes':len(raw),'delta_bytes':delta,
            'delta_interval_seconds':sample['at']-old['at'] if delta is not None else None,
            'coverage':'last observation per UTC hour (30 days) and day (366 days); no interpolation'}
