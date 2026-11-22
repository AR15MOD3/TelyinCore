"""Compare the shared QQ plugin, voice runtime and active UI in two release ZIPs."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile


SHARED = {
    'launch.py', 'infinity_control.py', 'bot_local_capability.py', 'asr_install.py',
    'tts_install.py', 'voice_download.py', 'voice_setup.py', 'voice_runtime.py',
    'qq_voice_services.py', 'snowluma_recovery.py', 'qq_protocol_health.py',
    'qq_environment.py', 'service_layout.py', 'telyincore/model_proxy.py',
    'qq_voice_io.py', 'telyincore/voice_draft.py', 'telyincore/generate.py',
}
TREES = ('builtin/infinity/core-infinity-qq/', 'scripts/voice/')


def inventory(path, platform):
    with ZipFile(path) as archive:
        prefix = f'Telyincore_{platform}/core/'
        def read(name):
            return archive.read(prefix + name)
        names = SHARED | {name[len(prefix):] for name in archive.namelist()
                          if name.startswith(prefix) and name[len(prefix):].startswith(TREES)
                          and not name.endswith('/')}
        files = {name: hashlib.sha256(read(name)).hexdigest() for name in names}
        current = json.loads(read('dist/web/current.json'))
        generation = 'dist/web/generations/' + current['generation'] + '/'
        manifest = read(generation + 'manifest.json')
        if hashlib.sha256(manifest).hexdigest() != current['manifest_sha256']:
            raise ValueError('invalid web manifest')
        for name, record in json.loads(manifest)['files'].items():
            digest = hashlib.sha256(read(generation + name)).hexdigest()
            if digest != record['sha256']:
                raise ValueError(f'invalid active web asset: {name}')
            files['active-web/' + name] = digest
        if platform == 'win':
            entry = 'extensions/core-infinity-qq/index.py'
            if read(entry) != read('builtin/infinity/core-infinity-qq/index.py'):
                raise ValueError('Windows installed QQ entry differs from the builtin')
        return files


def verify(linux, windows):
    left = inventory(linux, 'linux')
    right = inventory(windows, 'win')
    differences = sorted(k for k in left.keys() | right.keys() if left.get(k) != right.get(k))
    if differences:
        raise ValueError('QQ release parity mismatch: ' + ', '.join(differences))
    return len(left)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('linux', type=Path)
    parser.add_argument('windows', type=Path)
    args = parser.parse_args()
    print(f'{verify(args.linux, args.windows)} shared QQ files and active web assets match.')
