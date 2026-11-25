"""Apply the bundled plugin version on Core startup, without changing Bot choices."""
from pathlib import Path
import shutil
import tempfile
from .plugin_updates import Version, read_json
from .plugin_versions import publish_version


def sync_bundled(core):
    from runtime_identity import safe_path
    core=Path(core).resolve();results=[]
    for manifest in (core/'builtin/infinity').glob('*/package.json'):
        stage=None
        try:
            manifest=safe_path(core,manifest);spec=read_json(manifest);name=spec['name']
            from .market import plugin_destination
            destination=plugin_destination(core/'extensions',name)
            if not destination.exists(): continue  # An uninstalled built-in stays uninstalled.
            current=read_json(destination/'package.json')
            if current.get('name')!=name: raise ValueError('已安装清单名称不匹配')
            if Version(spec['version'])<=Version(current['version']): continue
            stage=Path(tempfile.mkdtemp(prefix='.builtin-update-',dir=core/'extensions'))
            for source in manifest.parent.rglob('*'):
                source=safe_path(core,source)
                target=stage/source.relative_to(manifest.parent)
                if source.is_dir():target.mkdir(parents=True,exist_ok=True)
                elif source.is_file():target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
            from .plugins import incompatible_plugin
            reason=incompatible_plugin(spec)
            if reason: raise ValueError(reason)
            if not safe_path(core,stage/str(spec.get('main','index.py'))).is_file():
                raise ValueError('内置插件入口缺失')
            publish_version(core,name,stage,destination,current['version'],reason='core-update')
            results.append({'name':name,'ok':True,'version':spec['version']})
        except (OSError,ValueError,KeyError) as error:
            results.append({'name':manifest.parent.name,'ok':False,'message':str(error)})
        finally:
            if stage is not None and stage.exists():shutil.rmtree(stage)
    return results
