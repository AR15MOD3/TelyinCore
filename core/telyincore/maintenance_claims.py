"""Bounded publication checks for self-reported durable maintenance.

A persona, recollection, successful lookup or unrelated write is not a receipt
for reorganizing an index. This recognizer covers explicit Chinese completion
claims, not arbitrary semantics. It never asserts that an operation did not
happen, and never changes the underlying records or runs another model.

Ordinary note-taking success and attributed reports are outside this contract:
their individual tool contracts remain authoritative. In particular this check
does not promote any generic ``success`` result to a maintenance completion.
"""
import re

UNVERIFIED = '〔Core 证据校正〕这句整理或归档完成的自述未绑定可核对的操作回执，是否完成仍待核实。'
# Bound each clause before matching; no expression spans unbounded prose.
_TARGET = re.compile(r'索引|档案|记忆库|记忆文件|历史记录|历史文件|对话记录|记忆条目|笔记|资料库|知识库')
_ACTION = re.compile(r'收好|收拾|收拢|理顺|归整|归置|整合|编排|整理|归完档|归档|归类|整编|重建|更新|合并|迁移')
_DONE = re.compile(r'(?:收拾|收拢|理顺|归整|归置|整合|编排)(?:完|好|了)|收好|归完档|(?:整理|归档|归类|整编|重建|更新|合并|迁移)(?:完毕|完成|完|好了|好|了)|(?:已经|已|刚刚|刚才|刚)(?:都|全部|全|又|把|将|给|为|重新|完成|做完)?')
# Resultative predicates also cover unseen handling verbs (e.g. 折腾完),
# but a completed lookup or explanation is not a durable mutation.
_RESULT = re.compile(r'完毕|完成|完了|完(?!整|全|美|备|善)|好了|收好|弄好|搞定|妥当|妥了')
_READ_ONLY = re.compile(r'找到|定位|检索|打开|浏览|拿到|读|看|查|听|解释|讲解|讨论|分析|想到|理解')
# A recent disposal construction asserts handling the object regardless of
# which synonym the model chooses. Do not enumerate every possible verb.
_DISPOSAL = re.compile(r'(?:刚刚|刚才|刚|已经|已)[^，,；;。！？!?]{0,12}(?:把|将)')
_DIRECT_NOTE = re.compile(r'(?:记|写)(?:下|进|到)|(?:交|递|发)给|转发|展示|说明')
_SELF = re.compile(r'^(?:[\s*（(]*)(?:(?:而|但|不过|嗯|哦|啊|那|现在|这会儿)[，,\s]*)?(?:我(?:自己)?|刚刚|刚才|刚把|刚将|刚整理|刚归档|刚归类|刚重建|刚更新|刚收|刚理|刚整合|刚编排|刚归整|刚归置)')
_QUALIFIED = re.compile(r'不确定|不能|无法|未能|未必|不一定|没(?:有)?|尚未|并未|还未|不是|并非|不曾|没有证据|是否|有没有|可能|也许|大概|似乎|好像|如果|假如|即使|假设|准备|打算|计划|想要|想把|想将|想整理|想归档|想更新|想重建|想合并|将要|会先|会把|会将|需要|应该|希望|争取|等我|待我|别说|不要说|不能说|声称|谎称|误称|误说|写成|说成|例如|举例|原话|你说|用户说|回执|记录显示|日志显示|之后|以后|再说|的说法|开始|正在|已经在|尝试|试着|后再|就(?:告诉|回答|通知)|让(?:你|他|她|大家)|到一半|做了一半|进行中|还在|尚在|途中')
_QUOTED = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|‘[^’]*’|"[^"\n]*"')


def corrections(parts):
    """Replace only unsupported assertion clauses; preserve adjacent greetings.

    Quotes, code blocks, negation, questions, plans and reported speech are data
    or non-completion claims. Scope is local: a later hypothetical must not hide
    an earlier claim. A quoted noun (e.g. “索引”) is retained as an object, while
    a quoted proposition is masked, preserving offsets for exact splicing.
    """
    changes = {}
    fenced = False
    for i, part in enumerate(parts):
        if '```' in part or '~~~' in part:
            if (part.count('```') + part.count('~~~')) % 2: fenced = not fenced
            continue
        if fenced or part.lstrip().startswith('>'):
            continue
        def mask(match):
            value = match.group()[1:-1]
            if len(value) <= 12 and _TARGET.fullmatch(value):
                return ' ' + value + ' '
            return ' ' * len(match.group())
        plain = _QUOTED.sub(mask, part)
        # Clauses are independently bounded; an earlier quote or qualifier
        # cannot accidentally excuse a later first-person assertion.
        edits = []
        for match in re.finditer(r'[^，,；;\n。！？!?]+(?:[，,；;\n。！？!?]|$)', plain):
            clause = match.group()
            if len(clause) > 512 or clause.endswith(('?', '？')):
                continue
            if not (_SELF.search(clause) and _TARGET.search(clause)):
                continue
            recent_handling = (_DISPOSAL.search(clause) and not _READ_ONLY.search(clause)
                               and not _DIRECT_NOTE.search(clause))
            explicit_completion = ((_ACTION.search(clause)
                or (_RESULT.search(clause) and not _READ_ONLY.search(clause)))
                and (_DONE.search(clause) or _RESULT.search(clause)))
            if not (recent_handling or explicit_completion):
                continue
            if _QUALIFIED.search(clause):
                continue
            edits.append((match.start(), match.end()))
        if edits:
            chunks = []; cursor = 0
            for start, end in edits:
                chunks.extend((part[cursor:start], UNVERIFIED + '\n'))
                cursor = end
            chunks.append(part[cursor:])
            changes[i] = ''.join(chunks)
    return changes
