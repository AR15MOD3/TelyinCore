""" **检索那一层的芯**（2026-10-07「一步到位」第一刀）。

⚠️ 它**只用标准库**（`sqlite3` 自带的 **FTS5** ＋ 纯算术 ）——
    于是"要不要加载模型"这件事在这一层**根本不存在** 。

⚠️ **它今天就把"几万条"那一级过了**： FTS5 是倒排索引（内含 BM25 ），
   4 份和 4 万份走的是**同一段代码** —— 不需要"以后想起来再换" 。

⚠️ **它只认名次，不算权重**： 因为**裸检索分数不可靠**
   （arXiv 2604.03676 那条：`raw retrieval scores are unreliable for downstream routing
   without additional calibration` ）⇒ 融合用 **RRF**（把两路的**名次**融起来
   —— k=60 是 Cormack 等 2009 那篇的常数 ）。

⚠️ **正本 vs 派生件**： **正本永远是那几份 md** ／ 这一本索引**随时可以整库删掉重算**
   —— 于是"换了嵌入模型"这件事不会毁掉任何东西 。

⚠️ 本模块**不读时钟**（"什么时候索引的"由调用方递 ）；**不写那几个正本** 。
"""

from __future__ import annotations

import pathlib
import sqlite3
from typing import Any, Iterable, Sequence

#: 那一本索引的名字（放在调用方递来的那个目录底下 —— 它是**派生件** ）。
THE_INDEX = "search.sqlite3"

#: RRF 那个常数（Cormack 等 2009 ）。
THE_K = 60

#: FTS5 那一张表（列名照 FTS5 的规矩 ）。
_TABLE = "the_lines"


class SemanticUnavailable(tuple):
    """Empty ranking with explicit unavailability; safe for sequence callers.

    Contains no backend error or corpus details. Consumers must not interpret
    this marker as a completed semantic search with zero matches.
    """
    def __new__(cls):
        return super().__new__(cls, ())


def _open(base: Any) -> sqlite3.Connection:
    """ 开那一本（没有就**当场造** —— 它是派生件，丢了照旧造 ）。"""
    path = pathlib.Path(base) / THE_INDEX
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        # ⚠️ 2026-10-07：**等不到就抛**（默认会**无限等**，而这一层会被用在
        #    「出请求那一刻」里 ⇒ 它一卡，整轮就卡、它一句都不回。真跑量到过。）
        conn.execute("PRAGMA busy_timeout = 2000")
        # ⚠️ **分词器用 `trigram`**（2026-10-07 量到）： FTS5 默认那个（`unicode61`）
        # 把一整串汉字当成**一个 token** ⇒ 查「水母头」命不中「雪白色的水母头…」
        # ⇒ `trigram` 按**三个字一组**建索引 ⇒ 中文命中 ／ **英文与数字也照样切**
        # （ 于是"库里有英文词汇能不能命中"这件事，字面那一路也一并解了 ）
        # ⚠️ 它要 SQLite ≥ 3.34（我们 3.45 ）； 而它**纯内置、零依赖**
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS " + _TABLE +
            " USING fts5(key, body, trigger_words, fingerprint, tokenize='trigram')")
    except BaseException:
        conn.close()  # Failed schema initialization must not retain the Windows file handle.
        raise
    return conn


def a_fingerprint(text: str) -> str:
    """ 那一份的"指纹"（内容哈希 ）—— 增量索引就靠它：**一样就不重算** 。"""
    import hashlib
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def the_trigger_words(*, key: str, body: str, extra: Sequence[str] = ()) -> str:
    """ **碰到这些词就该拉我进来**（ 2026-10-07：新项目从来没有这一列 ）。

    ⚠️ 来源**两处**（2026-10-07 收窄）： 文件名 ／ 调用方手写的（`extra` ）——
     **不含「头一行」** （理由见下）。
     而它是**给"自动拉进"用的**，不是给排序用的 —— 排序归 FTS5 ／ RRF 。
    """
    # ⚠️ 2026-10-07 收窄：**不再拿"头一行"当触发词** ——
    # 它是**正文的第一句**，不保证是"词"（ 真跑量到：它把一条 HTML 注释整条塞进去，
    # 于是一句话里任何两个字都可能命中 ⇒ **几乎每句话都触发** ⇒ 每轮都过那 2 GB 模型 ）。
    # ⇒ 只取两样： ① 文件名（去扩展名）② **人显式给的**（`extra`）。
    # 而"内容像不像"那一半本来就归字面／语义那两路 ⇒ 触发词只管**点名** 。
    bag: list[str] = [str(key).rsplit(".", 1)[0]]
    bag.extend(str(one) for one in extra)
    seen: list[str] = []
    for one in bag:
        said = " ".join(one.split())
        if said and said not in seen:
            seen.append(said)
    return " ".join(seen)


def put(base: Any, *, key: str, body: str, triggers: str = "") -> str:
    """ **记一份**进索引（ 指纹没变 ⇒ **一个字节都不重写** 回来交指纹 ）。"""
    stamp = a_fingerprint(body)
    words = triggers or the_trigger_words(key=key, body=body)
    conn = _open(base)
    try:
        row = conn.execute("SELECT fingerprint, trigger_words FROM " + _TABLE + " WHERE key = ?",
                           (key,)).fetchone()
        if row is not None and row[0] == stamp and row[1] == words:
            return stamp # 没改过 ⇒ 不动它
        conn.execute("DELETE FROM " + _TABLE + " WHERE key = ?", (key,))
        conn.execute("INSERT INTO " + _TABLE + " (key, body, trigger_words, fingerprint)"
                     " VALUES (?, ?, ?, ?)", (key, body, words, stamp))
        conn.commit()
    finally:
        conn.close()
    return stamp


def put_all(base: Any, pairs: Iterable[tuple[str, str]], *,
            triggers: dict[str, str] | None = None,
            authoritative: bool = False, validate_before_commit=None) -> dict[str, int]:
    """Publish one complete batch atomically within this SQLite partition.

    Materialize source input before writes. An optional source verifier runs
    immediately before commit and must return exact True. This detects observed
    drift; it does not lock independent filesystem writers across the commit.
    """
    if validate_before_commit is not None and not callable(validate_before_commit):
        raise TypeError("index verifier must be callable")
    rows = [(key, body) for key, body in pairs]
    prepared = []
    named = set()
    for key, body in rows:
        if not isinstance(key, str) or not isinstance(body, str) or key in named:
            raise ValueError("index keys and bodies must be unique string pairs")
        named.add(key)
        prepared.append((key, body, (triggers or {}).get(key, "")
                         or the_trigger_words(key=key, body=body), a_fingerprint(body)))
    fresh = waited = dropped = 0
    if not prepared and authoritative is not True and validate_before_commit is None:
        return {"indexed": 0, "untouched": 0, "dropped": 0}
    conn = _open(base)
    primary = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        for key, body, words, stamp in prepared:
            row = conn.execute("SELECT fingerprint, trigger_words FROM " + _TABLE + " WHERE key = ?", (key,)).fetchone()
            if row is not None and row[0] == stamp and row[1] == words:
                waited += 1
                continue
            conn.execute("DELETE FROM " + _TABLE + " WHERE key = ?", (key,))
            conn.execute("INSERT INTO " + _TABLE + " (key, body, trigger_words, fingerprint) VALUES (?, ?, ?, ?)",
                         (key, body, words, stamp))
            fresh += 1
        if named or authoritative is True:
            gone = [row[0] for row in conn.execute("SELECT key FROM " + _TABLE).fetchall()
                    if row[0] not in named]
            for key in gone:
                conn.execute("DELETE FROM " + _TABLE + " WHERE key = ?", (key,))
            dropped = len(gone)
        if validate_before_commit is not None and validate_before_commit() is not True:
            raise ValueError("index source snapshot changed before commit")
        conn.commit()
    except BaseException as error:
        primary = error
        try:
            conn.rollback()
        except BaseException as cleanup:
            error.add_note("Index rollback failed: " + type(cleanup).__name__)
        raise
    finally:
        try:
            conn.close()
        except BaseException as cleanup:
            if primary is None:
                raise
            primary.add_note("Index close failed: " + type(cleanup).__name__)
    return {"indexed": fresh, "untouched": waited, "dropped": dropped}


def the_keys(base: Any) -> list[str]:
    """ 索引里今天有哪几份（ 用来对账：正本删了它还在 ⇒ 那是要清掉的 ）。"""
    conn = _open(base)
    try:
        return sorted(one[0] for one in conn.execute(
            "SELECT key FROM " + _TABLE).fetchall())
    finally:
        conn.close()


def _one_road(base: Any, query: str, *, at_most: int, readonly: bool = False) -> list[str]:
    """ **字面那一路**（FTS5 ＋ BM25 ）—— 交的是**名次**（第 0 名 ／ 第 1 名 ⋯ ）。"""
    said = " ".join(str(query).split())
    if not said:
        return []
    if readonly:
        path = (pathlib.Path(base) / THE_INDEX).absolute()
        if not path.is_file():
            return []
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.05)
    else:
        conn = _open(base)
    try:
        # ⚠️ **短查询走兜底**（2026-10-07 量到）： `trigram` 要求查询**至少三个字**
        # ⇒ 两个字（「布丁」）MATCH **一条都命不中** —— 那就退回**包含匹配**
        # （ 两字查询在最坏时也就几百份，扫一遍即可 ）
        if len(said) < 3:
            # User text is a literal substring, never a LIKE pattern. Escape
            # the escape character first so percent/underscore stay literal.
            pattern = "%" + said.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            rows = conn.execute(
                "SELECT key FROM " + _TABLE + " WHERE body LIKE ? ESCAPE '\\' OR key LIKE ? ESCAPE '\\'"
                " ORDER BY key LIMIT ?",
                (pattern, pattern, int(at_most))).fetchall()
        else:
            bits = [one for one in said.replace('"', " ").split() if one]
            expr = " OR ".join('"' + one + '"' for one in bits) if bits else '"' + said + '"'
            rows = conn.execute(
                "SELECT key FROM " + _TABLE + " WHERE " + _TABLE + " MATCH ?"
                " ORDER BY rank LIMIT ?", (expr, int(at_most))).fetchall()
    # SQL/schema/locking errors are not evidence of zero matches. The query
    # expression above quotes user terms; propagate failures to the caller.
    finally:
        conn.close()
    return [one[0] for one in rows]


def the_watchers(base: Any, text: str) -> list[str]:
    """ **触发／自动拉进**（ 新项目从来没有这一手 —— 它治的正是
   "真正需要的时候反而答不上来" ）： 这一段话**碰到了谁的触发词** ⇒ 交谁 。

    ⚠️ 它是**确定性**的（同一个输入 ⇒ 同一个输出 不读钟、不算分 ）。
    """
    said = " ".join(str(text).split())
    if not said:
        return []
    conn = _open(base)
    try:
        rows = conn.execute("SELECT key, trigger_words FROM " + _TABLE).fetchall()
    finally:
        conn.close()
    hit: list[str] = []
    for key, words in rows:
        for one in str(words or "").split():
            if len(one) >= 2 and one in said:
                hit.append(key)
                break
    return sorted(hit)


def rrf(*roads: Sequence[str], at_most: int = 20) -> list[str]:
    """ **把几路召回按名次融起来**（RRF ）—— **只看名次，不看分数** 。

    ⚠️ 为什么不用分数：上面那篇量过"**裸检索分数不可靠**" —— 而 RRF 恰好绕开它 。
    ⚠️ 排序是**确定的**（分一样时按名字 ）—— 于是判据能红 。
    """
    score: dict[str, float] = {}
    for road in roads:
        for rank, key in enumerate(road):
            score[str(key)] = score.get(str(key), 0.0) + 1.0 / (THE_K + rank + 1)
    return [key for key, _s in sorted(score.items(), key=lambda two: (-two[1], two[0]))][:at_most]


def find(base: Any, query: str, *, extra_roads: Sequence[Sequence[str]] = (),
         at_most: int = 20, readonly: bool = False) -> list[str]:
    """ 一次查：**字面那一路** ＋ 调用方递来的**别的路**（ 例如语义那一层 ）⇒ RRF 融 。

    ⚠️ **语义与重排是"可插"的**（`extra_roads` ）： 今天没有它也能跑 ——
       而它一接上，**同一个入口**立刻就多一路 （ 不必改这一层 ）。
    """
    roads: list[Sequence[str]] = [_one_road(base, query, at_most=at_most * 2, readonly=readonly)]
    roads.extend(extra_roads)
    return rrf(*roads, at_most=at_most)



def the_pairs(base: Any) -> list[tuple[str, str]]:
    """ 索引里今天的**每一份**（名字 ＋ 正文 ）—— 给"语义那一路"当底料用 。"""
    conn = _open(base)
    try:
        rows = conn.execute("SELECT key, body FROM " + _TABLE + " ORDER BY key").fetchall()
    finally:
        conn.close()
    return [(str(one[0]), str(one[1])) for one in rows]


def find_with(base: Any, query: str, *, embedder: Any = None,
              at_most: int = 20) -> list[str]:
    """ **两路接起来**（2026-10-07 作者拍的）：**字面 ＋ 语义 ⇒ RRF** 。

    ⚠️ **语义那一路是"注入"进来的**（`embedder` ）—— 不是 import ：
       理由两条：① 缺模型 ／ 缺包时，`search` 这一层**照旧能 import、照旧能查**
       （ 那也是本仓那条老规矩：**由入口递** ）；② 于是"用哪个模型"这件事
       **不写在这一层里** —— 换模型不用动检索的代码 。
    ⚠️ `embedder` 的形状： `(每一份的文本表, 查询) -> 下标表`（看得懂"谁更像" ）。
    ⚠️ **它坏了不许带走这一条查**： 抛了 ／ 给怪东西 ⇒ **照旧只走字面那一路**
       （ 与"缺了也能跑"同一条 ）。
    """
    literal = _one_road(base, query, at_most=at_most * 2)
    roads: list[Sequence[str]] = [literal]
    if callable(embedder):
        try:
            rows = the_pairs(base)
            if rows:
                got = embedder([text for _key, text in rows], query)
                road = [str(rows[int(i)][0]) for i in got]
                if road:
                    roads.append(road)
        except Exception: # noqa: BLE001 —— 语义那一路坏了，照旧能查
            pass
    return rrf(*roads, at_most=at_most)



def merge_two(literal: Sequence[int], semantic: Sequence[int], *,
              at_most: int = 20) -> list[int]:
    """ **把"字面那一路"与"语义那一路"融起来**（ 两边交的都是**下标** ）⇒ 交下标 。

    ⚠️ 它**只认名次**（RRF ）： 理由上面量过 —— 语义那一层的**绝对相似度不可信**
       （bge-m3 实测：无关的一对也能到 0.47 ）。
    ⚠️ 两路都空 ⇒ 空 ；一路空 ⇒ 另一路照旧 （ 缺模型时就该是这个行为 ）。
    """
    roads = [[str(one) for one in literal], [str(one) for one in semantic]]
    fused = rrf(*[one for one in roads if one], at_most=at_most)
    return [int(one) for one in fused]
