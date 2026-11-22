"""Display-only self-reported names. Never bind accounts or grant permissions."""
import re
from pathlib import Path
from telyincore import memory

_CONSOLE = {'console:local-owner', 'private:console:local-owner'}
# Deliberately narrow: complete direct introductions, not mentions or quotations.
_INTRO = re.compile(r'^(?:你好[，,。！! ]*)?(?:我叫|我的名字是|可以叫我|请叫我|你可以叫我|我是)\s*[「“\"]?([^\s。，,；;！？!?：:\n「」“”\"]{1,24})[」”\"]?[。！! ]*$')
# Only recognize direct follow-up requests with unambiguous display intent.
_INTRO_REQUEST = re.compile(
    r'[，,。][ \t]*(?:以后(?:你)?都这么称呼我|请更新画像|请把这个称呼写进画像)[。！! ]*$'
)
_FACT = re.compile(r'^(?:自称名字是|自称姓名是|自称名为|自称|名字是|姓名是|希望被称呼为|希望被称为|希望称呼为)\s*[「“\"]?([^\s。，,；;！？!?：:\n「」“”\"]{1,24})[」”\"]?[。 ]*$')


def console_name(home: Path, records=()) -> str:
    # A trusted account binding has display precedence; no automatic linking.
    from telyincore.identity import IdentityBook, person_display_name
    owner = IdentityBook(home / '身份绑定.json').resolve('console:local-owner')
    if owner and (name := person_display_name(owner.label, owner.profile_keys)):
        return name
    # A name from another conversation or an assistant/tool is never considered.
    for row in reversed(tuple(records)):
        if (getattr(row, 'role', '') == 'user' and getattr(row, 'kind', '') == 'message'
                and getattr(row, 'scope', '') in _CONSOLE):
            text = str(row.text).strip()
            hit = _INTRO.fullmatch(text) or _INTRO.fullmatch(_INTRO_REQUEST.sub('', text))
            if hit:
                return hit[1]
    for row in reversed(memory.profile_of(home, 'Console-local-owner')):
        if row.source in _CONSOLE:
            hit = _FACT.fullmatch(row.text.strip())
            if hit:
                return hit[1]
    return ''


def experience_names(home: Path, store) -> dict[tuple[str, str], str]:
    """QQ aliases are joined to adapter-verified account+room, never nicknames."""
    from portrait_display import _observed_label
    from telyincore.identity import IdentityBook
    book = IdentityBook(home / '身份绑定.json')
    result = {}
    cached = {}
    latest = store.latest_cursor()
    for cursor in range(latest, max(0, latest - 2000), -1):
        event = store.get(cursor)
        observed = _observed_label(event)
        if observed is None:
            continue
        scope, account, platform_label = observed
        key = (scope, account)
        if key not in cached:
            person = book.resolve(account)
            profile = person.profile_key if person and person.profile_key else 'QQ-' + account[3:]
            source = scope + ('|sender=' + account if scope.startswith('group:') else '')
            name = ''
            for fact in reversed(memory.profile_of(home, profile)):
                if fact.source == source:
                    hit = _FACT.fullmatch(fact.text.strip())
                    if hit:
                        name = hit[1]
                        break
            cached[key] = name
        result.setdefault((scope, event.text), cached[key] or platform_label)
    return result
