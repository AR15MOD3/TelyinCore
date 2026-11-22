"""Local voice setup probes. No QQ requests or Bot settings writes."""
import base64
import binascii
import io
import wave

ASR_DEFAULT_THREADS = 3
ASR_FILES = ('model.int8.onnx', 'tokens.txt', 'silero_vad.onnx')


def check_asr(model, audio):
    import numpy as np
    import sherpa_onnx
    from scipy.signal import resample_poly
    from math import gcd

    if not isinstance(audio, str) or not 0 < len(audio) <= 2_000_000:
        raise ValueError('请选择一段不超过 1.5 MB 的短 WAV 测试音频。')
    try:
        raw = base64.b64decode(audio, validate=True)
        with wave.open(io.BytesIO(raw), 'rb') as wav:
            rate, channels = wav.getframerate(), wav.getnchannels()
            if (wav.getsampwidth() != 2 or not 8000 <= rate <= 48000
                    or channels not in (1, 2) or not 0 < wav.getnframes() <= rate * 15):
                raise ValueError('测试音频需为 15 秒以内的 16 位 PCM WAV。')
            samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')
            samples = samples.reshape(-1, channels).astype(np.float32).mean(axis=1) / 32768
    except (binascii.Error, EOFError, wave.Error):
        raise ValueError('无法读取 WAV 测试音频。') from None
    if rate != 16000:
        common = gcd(rate, 16000)
        samples = resample_poly(samples, 16000 // common, rate // common).astype(np.float32)
    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(model / 'silero_vad.onnx')
    config.sample_rate = 16000
    vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=16)
    vad.accept_waveform(samples)
    vad.flush()
    if vad.empty():
        raise ValueError('测试音频未检测到人声，请换一段清晰讲话的 WAV。')
    recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(model / 'model.int8.onnx'), tokens=str(model / 'tokens.txt'),
        num_threads=ASR_DEFAULT_THREADS, provider='cpu', sample_rate=16000, language='auto', use_itn=True)
    stream = recognizer.create_stream()
    stream.accept_waveform(16000, samples)
    recognizer.decode_stream(stream)
    text = stream.result.text.strip()
    if not text:
        raise ValueError('模型已加载，但未识别到文字，请换一段测试音频。')
    return text
