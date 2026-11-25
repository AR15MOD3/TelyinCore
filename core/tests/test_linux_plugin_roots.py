import json

from telyincore import plugin_paths, plugins
from telyincore.plugin_updates import PluginUpdates
from telyincore.plugin_versions import bundled


def test_linux_profile_uses_distribution_for_builtin_and_profile_for_install(tmp_path, monkeypatch):
    distribution = tmp_path / 'core'
    profile = distribution / '.linux-acceptance/core'
    monkeypatch.setattr(plugin_paths, '__file__', str(distribution / 'telyincore/plugin_paths.py'))
    monkeypatch.setattr(plugin_paths.sys, 'platform', 'linux')

    def manifest(folder, name):
        path = folder / name / 'package.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'name': name, 'version': '1.0.0',
                                    'repository': 'https://github.com/example/' + name}))
        return path.parent

    qq = manifest(distribution / 'builtin/infinity', 'core-infinity-qq')
    manifest(profile / 'extensions', 'core-infinity-qq')
    manifest(profile / 'extensions', 'community-demo')
    assert plugins.the_builtin(profile) == distribution / 'builtin'
    assert bundled(profile, 'core-infinity-qq') == qq
    rows = {r['name']: r['status'] for r in PluginUpdates(profile).inventory()}
    assert rows == {'core-infinity-qq': 'managed', 'community-demo': 'pending'}
    assert bundled(tmp_path / 'another-deployment', 'core-infinity-qq') is None
