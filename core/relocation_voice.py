"""Revalidate installed speech models after a deployment move."""
import base64
import hashlib
import os
import subprocess
from pathlib import Path
from bot_guarded_store import GuardedStore
from runtime_identity import safe_path
from relocation_files import write_json


def repair_voice(core, old):
    root = core.parent
    runtime = root/'services/CosyVoice3'
    assets = core/'scripts/voice'
    count = 0
    for kind, name in (('asr','SenseVoiceSmall-onnx'),('tts','Fun-CosyVoice3-0.5B-2512')):
        model = core/'models/voice'/kind/name
        marker = safe_path(root,(model if kind=='asr' else runtime)/('.core-'+kind+'-ready.json'))
        if not marker.is_file():continue
        from voice_download import stage_lock
        from voice_runtime import RuntimeSetup, model_stamp, python_at
        with stage_lock(marker.parent):
            disk = GuardedStore(root,marker)
            row = disk.read()
            if Path(row.get('model','')) != old/model.relative_to(root):continue
            if row.get('files') != model_stamp(model):
                raise ValueError('移动后的语音模型文件与原登记不一致')
            setup = RuntimeSetup(kind,runtime,assets/'cosyvoice3_server.py')
            if kind=='asr':
                from voice_setup import check_asr
                check_asr(model,base64.b64encode((assets/'asr-self-check.wav').read_bytes()).decode())
            else:
                env = dict(os.environ,TELYIN_TTS_RUNTIME=str(runtime),TELYIN_TTS_MODEL_DIR=str(model),
                           HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
                with (runtime/'.relocation-voice-check.log').open('wb') as out:
                    subprocess.run([str(python_at(runtime)),str(setup.entry),'--check-voice','default'],
                                   cwd=runtime,env=env,stdout=out,stderr=subprocess.STDOUT,check=True,timeout=900)
            from voice_runtime import __file__ as installer
            write_json(root,marker,disk.raw,dict(row,model=str(model.resolve()),installer=hashlib.sha256(Path(installer).read_bytes()).hexdigest()))
            if not setup.ready(model):raise ValueError('语音自检登记未通过')
            count += 1
    return count
