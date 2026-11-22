"""Bounded diagnostic history; contains verdicts and bounded, credential-redacted audit responses."""
import datetime
import json
import logging
import os
import tempfile
from pathlib import Path

FIELDS = ('status', 'stage', 'error_type', 'error_code', 'review_reason',
          'review_dimensions', 'review_diagnostics', 'items', 'candidate_assessment', 'candidate_review_items', 'write_grants', 'at', 'rate_limit', 'request_admission', 'protocol_retry', 'candidate_quote_projection', 'memory_kind', 'joint_source_anchor', 'joint_source_review')

def _load_history(path):
    # Older Windows builds used the process locale for this diagnostic file.
    raw = path.read_bytes()
    try:
        return json.loads(raw)
    except UnicodeDecodeError:
        return json.loads(raw.decode('gb18030'))

def read_reviews(home):
    try:
        data=_load_history(Path(home)/'.portrait-write-gate/reviews.json')
        return data[-64:] if isinstance(data,list) else []
    except (OSError,ValueError):
        return []

def read_review(home):
    rows=read_reviews(home)
    return rows[-1] if rows else {}

def persist_review(home, record):
    path=Path(home)/'.portrait-write-gate/reviews.json'
    temporary=None
    try:
        path.parent.mkdir(exist_ok=True)
        old=_load_history(path) if path.exists() else []
        if not isinstance(old,list):raise ValueError('invalid review history')
        record['at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        row={key:record[key] for key in FIELDS if key in record}
        fd,temporary=tempfile.mkstemp(dir=path.parent,prefix='.reviews-')
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump((old+[row])[-64:],stream,ensure_ascii=False)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    except (OSError,ValueError):
        # A diagnostic failure must not turn an already committed write into a retry.
        logging.getLogger('portrait').warning('画像复核诊断保存失败；写入结果以工具回执为准')
    finally:
        if temporary:
            try:Path(temporary).unlink(missing_ok=True)
            except OSError:pass
