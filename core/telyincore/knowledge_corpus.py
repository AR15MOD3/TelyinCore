"""One-query knowledge snapshots without a corpus-sized body/token container.

Originals are copied once, after the usual current source/grant checks. Only
query-term presence contributes to DF; complete bodies are decoded for shown
results. Public full-list APIs and semantic callback name lists stay unchanged.
"""
from collections import Counter
from contextlib import closing
import json
import math
from pathlib import Path
import re
import sqlite3
import tempfile
import sys

from .derived_cache import CacheDirectory
from . import original_spool


def _name(value):
    # POSIX filenames and valid JSON titles can contain lone surrogates.
    return json.dumps(value,ensure_ascii=True)


class KnowledgeCorpus:
    SMALL_BYTES = 512 * 1024
    SMALL_ENTRIES = 128

    def __init__(self):
        self.directory = self.db = self.originals = None
        self.count = 0
        self.df = Counter()
        self.units = set()
        self._small = {}
        self._small_bytes = 0

    def __enter__(self):return self

    def _disk_put(self,name,text,title,hits,ordinal,matched):
        offset = original_spool.write(self.originals,dict(text=text))
        self.db.execute('INSERT INTO docs VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET '
                        'offset=excluded.offset,hits=excluded.hits,title=excluded.title,matched=excluded.matched',
                        (_name(name),ordinal,offset,_name(list(hits)),_name(title) if title is not None else None,int(bool(matched))))

    def _spill(self):
        self.directory = CacheDirectory()
        self.db = sqlite3.connect(str(Path(self.directory.name)/'names.sqlite3'))
        self.db.execute('PRAGMA cache_size=-1024')
        self.db.execute('PRAGMA temp_store=FILE')
        self.db.execute('PRAGMA synchronous=OFF')
        self.db.execute('CREATE TABLE docs(name TEXT PRIMARY KEY,ordinal INTEGER UNIQUE,offset INTEGER,hits TEXT,title TEXT,matched INTEGER)')
        self.originals = tempfile.TemporaryFile()
        from .temp_volumes import observe
        observe(tempfile.gettempdir())
        for name,(text,title,hits,ordinal,size,matched) in self._small.items():
            self._disk_put(name,text,title,hits,ordinal,matched)
        self._small.clear()
        self._small_bytes = 0

    def __exit__(self,kind,error,traceback):
        primary = error
        for obj,method in [(self.originals,'close'),(self.db,'close'),(self.directory,'cleanup')]:
            close = getattr(obj,method,None)
            if close is not None:
                try:close()
                except BaseException as failure:
                    if primary is None:primary=failure
                    else:primary.add_note('knowledge snapshot cleanup failed: '+type(failure).__name__)
        self._small.clear()
        self._small_bytes = 0
        self.df.clear()
        self.units.clear()
        if error is None and primary is not None:raise primary

    def load(self,home,visibility,query):
        from . import memory, learned_knowledge
        from .lexical_statistics import CACHE
        from .memory_ranking import statistics
        self.units = set(memory.tokens_of(str(query)))
        query_text = str(query).casefold()
        # ASCII title topics contain only [a-z0-9]. Their previous negative
        # lookaround rule is exactly membership in these maximal query runs.
        # Build once instead of compiling a unique regex for every file title.
        ascii_topics = set(re.findall(r'[a-z0-9]+',query_text))
        def put(name,text,title=None):
            key = _name(name)
            if self.db is None:
                saved = self._small.get(name)
                old = (saved[3],saved[2]) if saved is not None else None
            else:
                found = self.db.execute('SELECT ordinal,hits FROM docs WHERE name=?',(key,)).fetchone()
                old = (found[0],json.loads(found[1])) if found is not None else None
            if old is None:
                ordinal = self.count
                self.count += 1
            else:
                ordinal = old[0]
                self.df.subtract(old[1])
            # Preserve adjacency across name/body exactly as the old tokenizer.
            if self.units:
                _,counts = CACHE.get(str(name).replace('/',' ')+'\n'+str(text),self.units,statistics)
                hits = set(counts)
            else:hits = set()
            self.df.update(hits)
            topics = re.findall(r'[^\W_]+',(title if title is not None else Path(name).stem).casefold())
            matched = False
            for topic in topics:
                if len(topic)<2:continue
                matched = topic in ascii_topics if topic.isascii() else topic in query_text
                if matched:break
            if self.db is None:
                size = 256+sum(sys.getsizeof(value) for value in (name,text,title,hits))
                size += sum(sys.getsizeof(term)+32 for term in hits)
                prior_size = self._small[name][4] if name in self._small else 0
                if self.count<=self.SMALL_ENTRIES and self._small_bytes-prior_size+size<=self.SMALL_BYTES:
                    self._small[name]=(text,title,hits,ordinal,size,bool(matched))
                    self._small_bytes += size-prior_size
                    return
                self._spill()
            self._disk_put(name,text,title,hits,ordinal,matched)
        with closing(memory._iter_knowledge(home,visibility=visibility)) as documents:
            for name,text in documents:put(name,text)
        with closing(learned_knowledge.visible(home,visibility)) as documents:
            for key,row in documents:put(key,learned_knowledge.render(row),row['title'])
        if self.db is not None:self.db.commit()

    def __len__(self):return self.count

    def __iter__(self):
        if self.db is None:
            yield from self._small
            return
        with closing(self.db.execute('SELECT name FROM docs ORDER BY ordinal')) as rows:
            for (name,) in rows:yield json.loads(name)

    def __getitem__(self,name):
        if self.db is None:return self._small[name][0]
        row = self.db.execute('SELECT offset FROM docs WHERE name=?',(_name(name),)).fetchone()
        if row is None:raise KeyError(name)
        return original_spool.read(self.originals,row[0])['text']

    def title(self,name):
        if self.db is None:return self._small[name][1]
        row = self.db.execute('SELECT title FROM docs WHERE name=?',(_name(name),)).fetchone()
        if row is None:raise KeyError(name)
        return json.loads(row[0]) if row[0] is not None else None

    @property
    def title_matches(self):
        # Compatibility accessor; the search path uses per-row flags instead.
        if self.db is None:return {name for name,row in self._small.items() if row[5]}
        with closing(self.db.execute('SELECT name FROM docs WHERE matched=1')) as rows:
            return {json.loads(row[0]) for row in rows}

    def _scored_rows(self):
        from .memory import ENOUGH_UNITS
        total = max(1,self.count)
        idf = {unit:math.log(1.0+total/max(1,self.df[unit])) for unit in self.units}
        whole = sum(idf.values()) or 1.0
        with closing(self._hits()) as rows:
            for name,row,matched in rows:
                hits = [unit for unit in self.units if unit in row]
                if self.units and (len(hits)>=min(ENOUGH_UNITS,len(self.units)) or matched):
                    yield sum(idf[unit] for unit in hits)/whole,name,bool(matched)

    def scores(self):
        # Existing complete-list API retained for callers that request it.
        result = sorted(self._scored_rows(),key=lambda row:(not row[2],-row[0],row[1]))
        return [(score,name) for score,name,_ in result]

    def ranked(self,limit,indexed=None):
        """Return exact match count and only the needed leading candidates.

        Large query snapshots also own the ranking table. Live permissions and
        source validation precede this operation; no final result is cached.
        The optional callback keeps duplicate RRF contributions and title-first
        order. A failed callback discards its partial ranking contribution.
        """
        from .search import rrf, THE_K
        if self.db is None:
            scored=self.scores();titles=self.title_matches
            road=[name for _,name in scored]
            eligible=set(road)-titles
            if eligible and indexed is not None:
                try:extra=[name for name in indexed() if name in eligible]
                except (OSError,ValueError,sqlite3.Error):extra=[]
                if extra:road=([name for name in road if name in titles]
                               +rrf([name for name in road if name in eligible],extra,at_most=len(eligible)))
            scores=dict((name,score) for score,name in scored)
            return len(road),[(scores[name],name) for name in road[:limit]]
        self.db.execute('DROP TABLE IF EXISTS ranking')
        self.db.execute('CREATE TABLE ranking(name TEXT PRIMARY KEY,sortkey BLOB,score REAL,matched INTEGER,fused REAL)')
        with closing(self._scored_rows()) as rows:
            self.db.executemany('INSERT INTO ranking VALUES(?,?,?,?,0)',
                ((_name(name),name.encode('utf-32be','surrogatepass'),score,int(matched)) for score,name,matched in rows))
        total=self.db.execute('SELECT COUNT(*) FROM ranking').fetchone()[0]
        order='matched DESC,score DESC,sortkey'
        eligible=self.db.execute('SELECT COUNT(*) FROM ranking WHERE matched=0').fetchone()[0]
        if eligible and indexed is not None:
            self.db.execute('SAVEPOINT reranking')
            self.db.execute('DROP TABLE IF EXISTS rank_extra')
            self.db.execute('CREATE TABLE rank_extra(ordinal INTEGER PRIMARY KEY,name TEXT,weight REAL)')
            found=False
            try:
                # Invoke once and insert accepted names in callback order. The
                # implicit ordinal advances only for an actual insertion, so
                # rejected names do not consume reciprocal-rank weight.
                self.db.executemany('INSERT INTO rank_extra(name) '
                    'SELECT name FROM ranking WHERE name=? AND matched=0',
                    ((_name(name),) for name in indexed()))
                found=self.db.execute('SELECT 1 FROM rank_extra LIMIT 1').fetchone() is not None
                if found:
                    # Keep literal-first addition and every duplicate callback
                    # contribution in its original order, including float ties.
                    with closing(self.db.execute('SELECT name FROM ranking WHERE matched=0 ORDER BY score DESC,sortkey')) as rows:
                        self.db.executemany('UPDATE ranking SET fused=? WHERE name=?',
                            ((1.0/(THE_K+rank+1),name) for rank,(name,) in enumerate(rows)))
                    with closing(self.db.execute('SELECT name,ordinal FROM rank_extra ORDER BY ordinal')) as rows:
                        self.db.executemany('UPDATE ranking SET fused=fused+? WHERE name=?',
                            ((1.0/(THE_K+ordinal),name) for name,ordinal in rows))
                    order='matched DESC,CASE WHEN matched=1 THEN score ELSE fused END DESC,sortkey'
            except (OSError,ValueError,sqlite3.Error):
                self.db.execute('ROLLBACK TO reranking');found=False
            finally:self.db.execute('RELEASE reranking')
        with closing(self.db.execute('SELECT score,name FROM ranking ORDER BY '+order+' LIMIT ?', (min(limit,total),))) as rows:
            return total,[(score,json.loads(name)) for score,name in rows]

    def _hits(self):
        if self.db is None:
            for name,entry in self._small.items():yield name,entry[2],entry[5]
            return
        with closing(self.db.execute('SELECT name,hits,matched FROM docs ORDER BY ordinal')) as rows:
            for name,raw,matched in rows:yield json.loads(name),set(json.loads(raw)),matched
