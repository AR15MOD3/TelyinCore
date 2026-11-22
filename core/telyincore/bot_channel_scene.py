"""Bot-owned channel scene selection, separate from host/plugin platform facts."""
from dataclasses import dataclass
import json,re
from pathlib import Path
from types import MappingProxyType

LIMIT=16*1024
HOST_ROUTES=frozenset({'console','internal','mixed','unknown'})
SOURCE=re.compile(r'[a-z][a-z0-9_.-]{0,63}\Z')

@dataclass(frozen=True)
class BotSceneSelection:
    status: str
    route: str
    text: str

def _read_scene(root, name):
    path=(root/name).resolve(strict=True)
    if not path.is_relative_to(root):raise ValueError('bot scene path escape')
    with path.open('rb') as stream:body=stream.read(LIMIT+1)
    if len(body)>LIMIT:raise ValueError('bot scene resource too large')
    decoded=body.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')
    text=re.sub(r'<!--.*?-->','',decoded,flags=re.S).strip()
    if not text:raise ValueError('bot scene resource empty')
    return text


class BotSceneMap:
    """Pin route ownership at startup and refresh the editable QQ scene on use."""
    def __init__(self,root):
        root=Path(root).resolve(strict=True)
        mapping=(root/'场景.json').resolve(strict=True)
        if not mapping.is_relative_to(root):raise ValueError('bot scene map path escape')
        with mapping.open('rb') as stream:raw=stream.read(LIMIT+1)
        if len(raw)>LIMIT:raise ValueError('bot scene map too large')
        data=json.loads(raw)
        if (not isinstance(data,dict) or type(data.get('version')) is not int
                or data.get('version')!=1 or not isinstance(data.get('routes'),dict)):
            raise ValueError('invalid bot scene map')
        routes=data['routes']
        if not HOST_ROUTES.issubset(routes) or len(routes)>128:
            raise ValueError('bot scene host routes required')
        staged={}
        self._editable_qq = None
        for source,route in routes.items():
            if not isinstance(source,str) or not SOURCE.fullmatch(source):
                raise ValueError('invalid bot scene source')
            if not isinstance(route,dict) or set(route)!={'provider','files'}:
                raise ValueError('invalid bot scene route')
            provider=route['provider'];files=route['files']
            if not isinstance(provider,str) or not provider:
                raise ValueError('invalid bot scene provider')
            if (source in HOST_ROUTES)!=(provider=='host'):
                raise ValueError('bot scene provider ownership mismatch')
            if (not isinstance(files,list) or not 1<=len(files)<=8
                    or not all(isinstance(f,str) for f in files)
                    or len(set(files))!=len(files)):
                raise ValueError('invalid bot scene resources')
            texts=[]
            for name in files:
                if (not isinstance(name,str) or not name or '\\' in name or ':' in name
                        or any(p in ('','.','..') for p in name.split('/'))
                        or not name.endswith('.md')):
                    raise ValueError('invalid bot scene resource path')
                texts.append(_read_scene(root, name))
            text='\n\n'.join(texts)
            if len(text.encode('utf-8'))>2*LIMIT:raise ValueError('bot scene combined text too large')
            staged[source]=(provider,text)
            if root.name == "环境" and source == "qq" and files == ["QQ.md"]:
                self._editable_qq = root
        self._routes=MappingProxyType(staged)

    def select(self,selection):
        status=selection.status;source=selection.source;provider=selection.provider
        if status not in {'builtin','plugin','mixed','unconfigured'}:
            raise ValueError('invalid platform context status')
        if provider=='host' and source in HOST_ROUTES and status in {'builtin','mixed'}:
            route=source
        elif (status=='plugin' and source not in HOST_ROUTES
                and source in self._routes and self._routes[source][0]==provider):
            route=source
        else:
            route='unknown'
        text = self._routes[route][1]
        if route == "qq" and self._editable_qq is not None:
            try:
                text = _read_scene(self._editable_qq, "QQ.md")
            except (OSError, ValueError):
                return BotSceneSelection("unmapped", "unknown", self._routes["unknown"][1])
        return BotSceneSelection('mapped' if route!='unknown' else 'unmapped',route,text)


def uses_bot_scene_map(root, *, force=False):
    """The versioned Bot map is also the durable mode marker across restarts."""
    path=Path(root)/'场景.json'
    return bool(force or path.exists() or path.is_symlink())


def scene_root_for(bot_root):
    from .prefix import uses_modern_layout

    bot = Path(bot_root)
    return bot / ("环境" if uses_modern_layout(bot / "persona") else "通道")
