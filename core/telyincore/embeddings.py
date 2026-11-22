"""Local, offline dense retrieval. Vectors are disposable, content-addressed data.

Model installation belongs to scripts/setup_semantic.py; inference never downloads.
Missing dependencies leave lexical retrieval available and report degraded health.
"""

from __future__ import annotations

import pathlib
import os
import threading
from functools import wraps
from typing import Any, Sequence

#: 本机那一份（作者机器上已验 ）。 换模型就是改这一行 ＋ 重算向量 。
DEFAULT_MODEL = os.environ.get("CORE_EMBEDDING_MODEL", str(pathlib.Path(__file__).resolve().parents[1] / "models" / "bge-m3"))

#: 向量那一本（**派生件**：删了能重算 ）。
THE_JAR = "vectors.jsonl"

#: 一次算几条（CPU 上这个数够稳 ）。
BATCH = 16


class EmbeddingMissing(RuntimeError):
    """ 没装上 ／ 没配好 —— 调用方据此**照旧**走字面那一路（不是崩 ）。"""


_inference_lock = threading.RLock()

def _serialized(function):
    @wraps(function)
    def call(*args, **kwargs):
        with _inference_lock:
            return function(*args, **kwargs)
    return call


def availability(model_path=DEFAULT_MODEL):
    """Cheap presence check. Actual readiness is verified by inference at setup."""
    from importlib.util import find_spec
    if not (pathlib.Path(model_path) / "modules.json").is_file():
        return "本地语义模型未安装，请重新运行启动入口完成准备"
    if find_spec("sentence_transformers") is None or find_spec("torch") is None:
        return "语义模型依赖未安装，请重新运行启动入口完成准备"
    return ""


_model: Any = None
_model_at: str = ""
_model_content: str = ""

def _identity_root(model_path):
    return pathlib.Path(model_path)

def _content_identity(model_path):
    from .model_content_identity import identity
    return identity(_identity_root(model_path))

def _require_content(model_path, expected):
    if _content_identity(model_path) != expected:
        raise EmbeddingMissing('embedding model changed during operation; no cache commit')



def _load(model_path: str) -> Any:
    """ **懒加载**（第一次真用才吃内存 ）—— 且**离线**（不去网上拉 ）。"""
    global _model, _model_at, _model_content
    content = _content_identity(model_path)
    if _model is not None and _model_at == model_path and _model_content == content:
        return _model
    where = pathlib.Path(model_path)
    if not where.is_dir():
        raise EmbeddingMissing("那一份模型不在盘上：" + model_path)
    try:
        import os
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        from sentence_transformers import SentenceTransformer
    except ImportError as why: # 包没装 ⇒ 照实说（不假装 ）
        raise EmbeddingMissing("没装 sentence-transformers：" + str(why)) from why
    try:
        import torch
        torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
        loaded = SentenceTransformer(str(where), device="cpu")
        _require_content(model_path, content)
    except Exception as why: # noqa: BLE001 —— 加载失败也是"没配好"
        raise EmbeddingMissing("那一份模型加载不了：" + str(why)) from why
    _model, _model_at, _model_content = loaded, model_path, content
    return _model


@_serialized
def embed(texts: Sequence[str], *, model_path: str = DEFAULT_MODEL) -> list[list[float]]:
    """ 算向量（ 交回**归一化**过的那一串 —— 于是点积就是余弦 ）。

    ⚠️ **查询那一侧与存的那一侧用同一套**（bge-m3 **不需要**指令前缀 ——
       这一点它与 `bge-*-zh-v1.5` 不同，别把别处的写法搬过来 ）。
    """
    rows = [str(one) for one in texts]
    if not rows:
        return []
    expected = _content_identity(model_path)
    mine = _load(model_path)
    try:
        import numpy as np
        out = mine.encode(rows, normalize_embeddings=True, batch_size=BATCH,
                          show_progress_bar=False)
        _require_content(model_path, expected)
        return [one.tolist() for one in np.asarray(out)]
    except Exception as why: # noqa: BLE001 —— 算不动 ⇒ 也当"没配好"
        raise EmbeddingMissing("算不出向量：" + str(why)) from why


@_serialized
def a_road(texts: Sequence[str], query: str, *,
           model_path: str = DEFAULT_MODEL, at_most: int = 20,
           within: float = 0.9, jar: Any = None) -> list[str]:
    """ **语义那一路**（交给 `search.find_with(embedder=…)` 用）—— 交的是**名次**。

    ⚠️ **它只留"与最高分差得不远"的那些**（`within`，**相对**的 ）：
        2026-10-07 真跑量到：不这么收，它会把**底料全排一遍**
       （四份知识 ⇒ 四份全给 —— `/knowledge` 那里就是四份全出）。
       ⚠️ **绝对阈值不许用** ： 这个模型的绝对相似度整体偏高
       （无关的一对也能到 0.47 —— 那是我实测出来的）⇒ 只有**相对它自己最高的那一个**
       才是有意义的量 。
    ⚠️ `jar` 给了 ⇒ **先读向量本**（指纹一样就不重算）——
        2026-10-07 抓到：`keep` 那个向量本**一直没被用到** （每次都把底料重算一遍 ）。
    ⚠️ 它**不读钟、不写正本、不下"命中"的判断** （融不融、留几条，归 `search`）。
    """
    import numpy as np
    expected = _content_identity(model_path)
    rows = [str(one) for one in texts]
    if not rows or not str(query).strip():
        return []
    if jar is None:
        bags = embed([str(query)] + rows, model_path=model_path)
        head, rest = np.asarray(bags[0]), np.asarray(bags[1:])
    else:
        head = np.asarray(embed([str(query)], model_path=model_path)[0])
        same = the_jar(jar, model_path=model_path)
        need = [one for one in rows if the_key(one) not in same]
        if need:
            fresh = embed(need, model_path=model_path)
            put_the_jar(jar, {the_key(one): list(bag) for one, bag in zip(need, fresh)}, model_path=model_path, expected_content=expected)
            same = the_jar(jar, model_path=model_path)
        rest = np.asarray([same[the_key(one)] for one in rows])
    _require_content(model_path, expected)
    sims = rest @ head # 归一化过 ⇒ 点积就是余弦
    order = list(np.argsort(-sims))
    best = float(sims[order[0]]) if order else 0.0
    near = [i for i in order if best <= 0 or float(sims[i]) >= best * float(within)]
    return [str(i) for i in near[:at_most]]


def the_backlog(texts: Sequence[str], base: Any, *, model_path: str = DEFAULT_MODEL) -> int:
    """ **向量本还差几条**（2026-10-10）—— 给调用方一个"**要不要现在算**"的底 。

    ⚠️ **为什么非有它不可**（**本机真撞上过** ）：`a_road` 是**为"四份知识"那种小库
       写的** （ 它自己的注释逐字写着"四份知识 ⇒ 四份全给"）。 底料涨到 **1178 份**
       之后，一次页面搜索要**现算 1178 条向量**，而**整批算完才落盘**
       ⇒ 服务被拖在 **10 核 ／ 3 GB** 上跑了几分钟，而且**并发那几个请求各起一趟** 。
    ⚠️ **外面那两套都是"索引建在生产之外"**（Claude Code 干脆不建索引 ；
       Dust 的 `cat` 必须带 `limit` ）⇒ 这一条就是把"要不要现在算"**交回给调用方** 。
    ⚠️ **它不算向量、不读钟、不写盘** —— **"落后多少算太多"归调用方拍**
       （这一层只数数 ）。
    """
    same = the_jar(base, model_path=model_path)
    return sum(1 for one in texts if the_key(one) not in same)


def _jar_path(base: Any, model_path: str) -> pathlib.Path:
    # Unbound legacy jars remain untouched. They cannot prove the current model
    # content identity and need an explicit maintenance migration/coverage check.
    return pathlib.Path(base) / 'model-caches' / _content_identity(model_path) / THE_JAR


def the_jar(base: Any, *, model_path: str = DEFAULT_MODEL) -> dict[str, list[float]]:
    """ 读那个向量本（坏行跳过；没有 ⇒ 空）—— 键是**指纹** 。"""
    import json
    where = _jar_path(base, model_path)
    if not where.is_file():
        return {}
    out: dict[str, list[float]] = {}
    for one in where.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(one)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("fingerprint") and row.get("vector"):
            out[str(row["fingerprint"])] = [float(x) for x in row["vector"]]
    return out


def put_the_jar(base: Any, bags: dict[str, list[float]], *, model_path: str = DEFAULT_MODEL, expected_content: str | None = None) -> None:
    """ 往那个向量本里**追加**（不改前面的 ）。"""
    import json
    from .source_guard import lease
    # The lease protects a whole append against other processes and maintenance.
    with lease(pathlib.Path(base) / "vector-writer"):
        if expected_content is not None:
            _require_content(model_path, expected_content)
        where = _jar_path(base, model_path)
        where.parent.mkdir(parents=True, exist_ok=True)
        existing = the_jar(base, model_path=model_path)
        with where.open("a", encoding="utf-8", newline="\n") as handle:
            for stamp, bag in bags.items():
                if stamp not in existing:
                    handle.write(json.dumps({"fingerprint": stamp, "vector": list(bag)},
                                            ensure_ascii=False) + "\n")


def the_key(text: str) -> str:
    """ 那一条的指纹（ 增量：**一样就不重算** 用的是与 `search` 同一个式子 ）。"""
    from .search import a_fingerprint
    return a_fingerprint(text)


@_serialized
def keep(base: Any, pairs: Sequence[tuple[str, str]], *,
         model_path: str = DEFAULT_MODEL) -> dict[str, int]:
    """ **增量算向量**（ 只算没算过的 ／ 回来交"算了几个 ／ 跳过几个" ）。

    ⚠️ 向量存在 `THE_JAR` 里（一个 jsonl ⇒ **派生件** ）： 每行 `{key, fingerprint, vector}`。
    """
    import json
    expected = _content_identity(model_path)
    jar = _jar_path(base, model_path)
    jar.parent.mkdir(parents=True, exist_ok=True)
    had: dict[str, str] = {}
    if jar.is_file():
        for one in jar.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(one)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("key"):
                had[str(row["key"])] = str(row.get("fingerprint", ""))
    fresh: list[tuple[str, str]] = []
    for key, text in pairs:
        stamp = the_key(text)
        if had.get(key) == stamp:
            continue
        fresh.append((key, text))
    if fresh:
        bags = embed([text for _k, text in fresh], model_path=model_path)
        _require_content(model_path, expected)
        from .source_guard import lease
        with lease(pathlib.Path(base) / "vector-writer"), jar.open("a", encoding="utf-8", newline="\n") as handle:
            for (key, text), bag in zip(fresh, bags):
                handle.write(json.dumps({"key": key, "fingerprint": the_key(text),
                                         "vector": bag}, ensure_ascii=False) + "\n")
    return {"embedded": len(fresh), "untouched": len(pairs) - len(fresh)}
