"""Fixed-source, hash-verified optional SenseVoice installation (no archives)."""
from __future__ import annotations

import shutil
from pathlib import Path
from voice_download import VoiceDownload

import httpx

NAME = "SenseVoiceSmall-onnx"
BASE = "https://huggingface.co/"
SOURCES = {"mirror": "https://hf-mirror.com/", "official": BASE}
FILES = (
    ("model.int8.onnx", "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/2365bae/model.int8.onnx", 239233841, "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51"),
    ("tokens.txt", "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/2365bae/tokens.txt", 315894, "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc"),
    ("silero_vad.onnx", "csukuangfj/vad/resolve/fba88cd2/silero_vad.onnx", 1807522, "a35ebf52fd3ce5f1469b2a36158dba761bc47b973ea3382b3186ca15b1f5af28"),
)


class ASRInstaller(VoiceDownload):
    name = NAME
    sources = SOURCES
    files = property(lambda self: FILES)
    success = "推荐模型已安装；请选择 SenseVoiceSmall-onnx 并保存语音设置。"

    def finish(self, stage):
        (stage / "SOURCE.txt").write_text(
            "SenseVoiceSmall ONNX INT8; source: huggingface.co/csukuangfj/"
            "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17 @2365bae\n"
            "Silero VAD: huggingface.co/csukuangfj/vad @fba88cd2\n",
            encoding="utf-8")
        # Notices are shipped with the application, not downloaded from arbitrary URLs.
        notices = Path(__file__).parent / "models" / "voice" / "notices"
        for notice in notices.glob("LICENSE-*.txt"):
            shutil.copy2(notice, stage / notice.name)
