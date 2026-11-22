"""Pinned, hash-verified CosyVoice3 weight download; never installs or executes runtime code."""
from __future__ import annotations

import shutil
from pathlib import Path
from voice_download import VoiceDownload

import httpx

NAME = "Fun-CosyVoice3-0.5B-2512"
BASE = "https://huggingface.co/"
SOURCES = {"mirror": "https://hf-mirror.com/", "official": BASE}
FILES = (('CosyVoice-BlankEN/config.json',
  'FunAudioLLM/Fun-CosyVoice3-0.5B-2512/resolve/29e01c4e8d000f4bcd70751be16fa94bf3d85a18/CosyVoice-BlankEN/config.json',
  659,
  '168aa1bd401abc3bc262ba15ba4e499627a8b4e006e9d050b47c22de20660185'),
 ('CosyVoice-BlankEN/generation_config.json',
  'FunAudioLLM/Fun-CosyVoice3-0.5B-2512/resolve/29e01c4e8d000f4bcd70751be16fa94bf3d85a18/CosyVoice-BlankEN/generation_config.json',
  242,
  'e558847a8b4402616f1273797b015104dc266fe4b520056fca88823ba8f8ebe6'),
 ('CosyVoice-BlankEN/merges.txt',
  'FunAudioLLM/Fun-CosyVoice3-0.5B-2512/resolve/29e01c4e8d000f4bcd70751be16fa94bf3d85a18/CosyVoice-BlankEN/merges.txt',
  1402109,
  'ac8ff86a72bee70828fbc1119bc4398c6f3a9a6e490d7b0dbe917be025478bd0'),
 ('CosyVoice-BlankEN/model.safetensors',
  'FunAudioLLM/Fun-CosyVoice3-0.5B-2512/resolve/29e01c4e8d000f4bcd70751be16fa94bf3d85a18/CosyVoice-BlankEN/model.safetensors',
  988097824,
  '130282af0dfa9fe5840737cc49a0d339d06075f83c5a315c3372c9a0740d0b96'),
 ('CosyVoice-BlankEN/tokenizer_config.json',
  'FunAudioLLM/Fun-CosyVoice3-0.5B-2512/resolve/29e01c4e8d000f4bcd70751be16fa94bf3d85a18/CosyVoice-BlankEN/tokenizer_config.json',
  1287,
  '482bd979881423375ca5414e4e0d94cd7c5349dbb17fffd46b4d36d71e62a1bc'),
 ('CosyVoice-BlankEN/vocab.json',
  'FunAudioLLM/Fun-CosyVoice3-0.5B-2512/resolve/29e01c4e8d000f4bcd70751be16fa94bf3d85a18/CosyVoice-BlankEN/vocab.json',
  2776833,
  'ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910'),
 ('README.md',
  'FunAudioLLM/Fun-CosyVoice3-0.5B-2512/resolve/29e01c4e8d000f4bcd70751be16fa94bf3d85a18/README.md',
  11982,
  'd4ec0fe1342b60a424ce1cd8971c670d26be38068a96148bcb6a1b8a334c3088'),
 ('campplus.onnx',
  'FunAudioLLM/Fun-CosyVoice3-0.5B-2512/resolve/29e01c4e8d000f4bcd70751be16fa94bf3d85a18/campplus.onnx',
  28303423,
  'a6ac6a63997761ae2997373e2ee1c47040854b4b759ea41ec48e4e42df0f4d73'),
 ('config.json',
  'FunAudioLLM/Fun-CosyVoice3-0.5B-2512/resolve/29e01c4e8d000f4bcd70751be16fa94bf3d85a18/config.json',
  2,
  '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a'),
 ('configuration.json',
  'FunAudioLLM/Fun-CosyVoice3-0.5B-2512/resolve/29e01c4e8d000f4bcd70751be16fa94bf3d85a18/configuration.json',
  47,
  'c502b6328c67638b401df8dd05de89e9e8d1cff9cd0ada10dfbdbe13556c20de'),
 ('cosyvoice3.yaml',
  'FunAudioLLM/Fun-CosyVoice3-0.5B-2512/resolve/29e01c4e8d000f4bcd70751be16fa94bf3d85a18/cosyvoice3.yaml',
  6934,
  'f5a6b2c6f05139d0f18861a1fe506f751e787026b77c05f7e8fef9f8a4405965'),
 ('flow.pt',
  'FunAudioLLM/Fun-CosyVoice3-0.5B-2512/resolve/29e01c4e8d000f4bcd70751be16fa94bf3d85a18/flow.pt',
  1329116148,
  'a6fab32a7825e5b0bc855ddd948f8db9370b0a786fbc249caa4595e95b608e4b'),
 ('hift.pt',
  'FunAudioLLM/Fun-CosyVoice3-0.5B-2512/resolve/29e01c4e8d000f4bcd70751be16fa94bf3d85a18/hift.pt',
  83202622,
  'b279d7641eb97ae55b3b540cfba4f953c26492a2df758328a89a4d007ab87a65'),
 ('llm.pt',
  'FunAudioLLM/Fun-CosyVoice3-0.5B-2512/resolve/29e01c4e8d000f4bcd70751be16fa94bf3d85a18/llm.pt',
  2024669519,
  '69f43bd545131c30e98947fb360ea8b4dc9916d8e83dded7757c7ea4f5a24970'),
 ('speech_tokenizer_v3.onnx',
  'FunAudioLLM/Fun-CosyVoice3-0.5B-2512/resolve/29e01c4e8d000f4bcd70751be16fa94bf3d85a18/speech_tokenizer_v3.onnx',
  969451503,
  '23236a74175dbdda47afc66dbadd5bcb41303c467a57c261cb8539ad9db9208d'))


class TTSInstaller(VoiceDownload):
    name = NAME
    sources = SOURCES
    files = property(lambda self: FILES)
    success = "推荐模型已安装；请选择 Fun-CosyVoice3-0.5B-2512 并保存。仍需单独配置 TTS 运行环境。"

    def finish(self, stage):
        (stage / "SOURCE.txt").write_text(
            "FunAudioLLM/Fun-CosyVoice3-0.5B-2512 @29e01c4e8d000f4bcd70751be16fa94bf3d85a18\n"
            "License: Apache-2.0; weights only. No runtime or private voices included.\n",
            encoding="utf-8")
