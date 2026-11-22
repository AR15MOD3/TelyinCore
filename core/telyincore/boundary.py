# telyincore/boundary.py
"""主体边界：核心全树扫描的**实现**。

`docs/design/01-主体边界.md` 的纪律 1 规定了「核心全树扫描」这条校验方式 ——
这个模块就是它。扫描器住在核心里，是因为它校验的正是这棵树。

⚠️ **这个文件自己也在被扫描之列** ⇒ 下面那六个禁词**一律写成 `"\\uXXXX"` 转义**：

· 写字面量（或用 `chr()` 拼码位）的话，它会**举报自己** ——
  而**被举报的是检查器本身**，那是最难解释的一种红（写这段时就踩过一次）；
· 转义是**纯 ASCII 字节**、运行时解出来与字面量**逐字相同**，
  而且**人读得懂** —— 注释里于是也能照常讨论这件事，不必只写码位。
"""
from __future__ import annotations

import pathlib
import re
from typing import Iterator

#: 六个禁词，顺序与 `01-主体边界.md` 那句一致。
#: ⚠️ 值是转义；运行时与字面量**逐字相同**（`tests/test_boundary.py` 拿字面量比过）。
FORBIDDEN_TOKENS_TEXT: tuple[str, ...] = (
    "\u0054\u0065\u006c\u0079\u0069\u006e",   # 那个人名的 ASCII 形（大写 T 开头）
    "\u7279\u7433",                           # 同一人名的中文形
    "\u0071\u0071",                           # 环境侧那个缩写的两字母，小写
    "\u0051\u0051",                           # 同上，大写
    "\u5979",                                 # 主体的人称代词
    "\u5e73\u53f0",                           # 「环境」的另一种说法（两个字）
)

#: 这些词的 **UTF-8** 形态（表本身对外就是这一组）。
FORBIDDEN_TOKENS: tuple[bytes, ...] = tuple(
    t.encode("utf-8") for t in FORBIDDEN_TOKENS_TEXT)

#: ⚠️ **还要查别的编码** —— 一个用别的编码写下的核心文件，按 UTF-8 搜是**全瞎**的。
#:
#: 实测（2026-09-26）：
#: · `"\u5e73\u53f0"` 的 **GBK** 字节是 `c6 bd cc a8`，而它**恰好也是合法 UTF-8**
#:   （解出来是 `"\u01bd\u0328"`）⇒ **"能不能按 UTF-8 解码"这一条单独不够**，
#:   而它正是回评 m7 变异用的那个词；
#: · `"\u7279\u7433"` / `"\u5979"` 的 GBK 形态**不是**合法 UTF-8 ⇒ 那几个由解码检查兜住。
#: `gbk` 是本机的 ANSI 代码页（实测 `chcp` = **936**）。
#:
#: ⚠️ **UTF-16 的两种字节序故意没有登记**（实测，2026-09-26）：
#: `"\u5979"` 的 utf-16-le 形态是 `79 59` ＝ **普通 ASCII 的 `yY`** ——
#: 而 `b"# applyYear"` 里就有它 ⇒ **一个干净的核心文件会变红**。
#: `"\u7279\u7433"` 的 utf-16 形态同样是纯 ASCII（`yr3t` / `ryt3`）。
#: ⇒ 那是最可能让将来的维护者**去放松这道守卫**的失效模式，所以不收它们。
#: ⚠️ **而登记 GBK 换来的是两件事，要分开说**（2026-09-27 逐形态量过）：
#:
#: **① 检测力上的收益只落在三个非 ASCII 词上。**
#: 那三个 ASCII 词（`54 65 6c 79 69 6e` / `71 71` / `51 51`）的 GBK 形态
#: **与它们的 UTF-8 形态逐字节相同** ⇒ 对它们**没有多出任何检测力**；
#: 真正多出来的是 `"\u7279\u7433"` / `"\u5979"` / `"\u5e73\u53f0"` 的
#: `cc d8 c1 d5` / `cb fd` / `c6 bd cc a8`。
#:
#: **② 而那三个 ASCII 词**正是**能被别的东西制造出来的那一类** ——
#: 一个 GBK 双字节字的**尾字节**可以落在 ASCII 区（`0x40..0x7E`），
#: 于是「[尾字节 `0x51` 的字] ＋ ASCII `Q`」的字节流里**含 `51 51`**
#: （实测：`"\u4e35"` ＋ `Q` ⇒ `81 51 51`；`"\u4e79"` ＋ `q` ⇒ `81 71 71`；
#: `"\u4e40"` ＋ `elyin` ⇒ `81 54 65 6c 79 69 6e`）——
#: **每个首字节各有 116 个汉字**满足它。
#: ⚠️ **但这一类落不到"干净文件被误报"上**：那些字节**不是合法 UTF-8**，
#: 而 `scan_core` 会**先**报「不是 UTF-8」并**跳过**字节搜
#: ⇒ 文件是红了，红的是「它不该不是 UTF-8」，**不是**"它写了那个词"。
#:
#: **⇒ 所以唯一**可达**的假阳性是 `"\u5e73\u53f0"` 的 GBK 形态**：
#: `c6 bd cc a8` **恰好也是合法 UTF-8**（解出来是 `\u01bd\u0328`，两个字符）
#: ⇒ 核心树里写下那两个字符，扫描就红，**而文件是干净的**。
#: 它由 `tests/test_boundary.py::test_the_reachable_false_positive_is_the_gbk_form_of_the_two_char_token`
#: 钉着，并登记在 `01-主体边界.md` 的「已知边界 ④」。
#:
#: ⚠️ **而全角形态造不出这个**（这一点值得记，因为它看着像最可能的一个）：
#: GBK 里**全角全在 A3 行**（字母 `A3 C1..DA`、数字 `A3 B0..B9`、标点 `A3 A1..FE`），
#: 那一行的**尾字节范围是 `A1..FE`** ⇒ 取不到 `0x51`/`0x71`
#: （实测：`"\u0051\u0051"` 的全角形 GBK 是 `a3 d1 a3 d1` —— **零命中**）。
#: `tests/test_boundary.py::test_fullwidth_letters_do_not_false_positive` 钉着它。
OTHER_ENCODINGS: tuple[str, ...] = ("gbk",)

#: 逐 (字节形态, 编码名) —— 命中时要能说出**它是哪一种编码形态**。
FORBIDDEN_FORMS: tuple[tuple[bytes, str], ...] = tuple(
    (t.encode(enc), enc)
    for t in FORBIDDEN_TOKENS_TEXT
    for enc in ("utf-8",) + OTHER_ENCODINGS
)

#: 编译好的模式。**测试从这里取**，不许在测试里另写一份 ——
#: 否则"扫的是什么"与"测的是什么"会分家，收窄测试里的那份就能在绿灯下放走违规。
FORBIDDEN = re.compile(
    b"|".join(re.escape(form) for form, _ in FORBIDDEN_FORMS))

#: 字节形态 → 编码名（同形只留第一个，即优先报 `utf-8`）。
_FORM_LABELS: dict[bytes, str] = {}
for _form, _enc in FORBIDDEN_FORMS:
    _FORM_LABELS.setdefault(_form, _enc)
del _form, _enc

#: 被校验的那棵树 —— **核心自己**。
CORE = pathlib.Path(__file__).resolve().parent


def is_source(path: pathlib.Path) -> bool:
    """这个文件该不该被扫 —— **排除规则只写这一处**（测试拿它对齐，别各写一份）。

    ⚠️ 只排除**字节码缓存**：`.pyc` 里存着源码的**绝对路径**（`co_filename`）⇒
    按字节搜命中的是**仓库所在的目录名**，与文件内容无关（实测：7 个 pyc 各命中一次）。
    它们不在 git 里（`.gitignore` 有 `__pycache__/` 与 `*.py[cod]`），也不是源码。
    """
    return not (path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts)


def source_files(root: pathlib.Path) -> Iterator[pathlib.Path]:
    """一棵树里的源码文件 —— **所有**文件（不只是 `.py`），除了字节码缓存。

    ⚠️ **不许把清单写死成"当时那几个模块"**，也**不许收窄成 `*.py`** ——
    那样新模块（或 `NOTES.md` 这种）里的违规根本不会被扫。
    这里一律**遍历**；测试另有一条钉住"扫到的 == 目录里该扫的全部"。
    """
    for path in sorted(root.rglob("*")):
        if path.is_file() and is_source(path):
            yield path


def scan_core(root: pathlib.Path) -> list[str]:
    """扫一棵树，返回**违规清单**（空列表 = 干净）。

    ⚠️ 三条都要守住：
    · **按原始字节搜** —— 内容不经过解码，坏字节藏不住；
    · **每一种登记过的编码形态都搜**（见 `OTHER_ENCODINGS`）—— 只搜 UTF-8 的话，
      一个用别的编码写下的核心文件是**全瞎**的；
    · **不能按 UTF-8 解码的文件本身就算违规** —— 不许静默跳过它。
      （⚠️ 这一条**单独不够**：见 `OTHER_ENCODINGS` 上面那段实测。）
    """
    offenders: list[str] = []
    for path in source_files(root):
        raw = path.read_bytes()
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            offenders.append(f"{_show(path, root)}: 不是 UTF-8（{exc}）—— 按字节搜会漏")
            continue
        for m in FORBIDDEN.finditer(raw):
            line_no = raw[:m.start()].count(b"\n") + 1
            label = _FORM_LABELS.get(m.group(), "?")
            offenders.append(
                f"{_show(path, root)}:{line_no}: {m.group()!r}（{label} 形态）")
    return offenders


def _show(path: pathlib.Path, root: pathlib.Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:               # pragma: no cover —— 只在跨盘等怪情况下走到
        return str(path)
