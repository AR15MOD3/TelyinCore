"""Explicit native QQ voice worker; remote cancellation is not a drain receipt."""
import base64
import re
import time

import httpx


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{32}', value) is not None


def _one(response):
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise ValueError('invalid TTS receipt')
    return value


def render_voice(snapshot, text, cancelled, check, log, draft=None):
    """Called only inside the instance OwnedBlockingIO slot, never on the loop.

    If remote work cannot be proven drained, the worker deliberately remains
    owned. Socket timeouts and repeated observer cancellation cannot release it.
    Drain polling is read-only; generate/send are each attempted at most once.
    """
    def note(message):
        try:
            log(message)
        except BaseException:
            pass  # Optional diagnostics cannot release a live remote lease.

    client = snapshot.tts_client
    if client is None:
        raise RuntimeError('未配置本机 TTS 服务 ttsUrl')
    if not isinstance(text, str) or not text.strip() or len(text) > 1000:
        raise ValueError('语音文字必须是 1 到 1000 字')
    if not _identifier(snapshot.voice_batch) or not _identifier(snapshot.generation):
        raise RuntimeError('voice requires frozen producer owner/batch')
    check()
    capability = _one(client.get('api/queue/status', timeout=5))
    if (type(capability.get('protocol')) is not int or capability['protocol'] != 1
            or type(capability.get('drain_protocol')) is not int
            or capability['drain_protocol'] != 1
            or not _identifier(capability.get('instance_id'))):
        raise RuntimeError('TTS 服务不支持可证明排空；未开始合成，需先独立验收对应服务候选')
    if draft is not None and capability.get("phrase_protocol") != 1:
        raise ValueError("TTS service uses completed-text input")
    instance = capability['instance_id']
    check()
    binding = {'instance_id': instance, 'queue_owner': snapshot.generation,
               'queue_batch': snapshot.voice_batch}
    lease = _one(client.post('api/queue/lease', data=binding, timeout=5))
    if (type(lease.get('drain_protocol')) is not int or lease['drain_protocol'] != 1
            or lease.get('instance_id') != instance or not _identifier(lease.get('lease'))):
        # No generate has been attempted. Unknown unused registrations consume
        # bounded server capacity, but cannot enter inference by themselves.
        raise RuntimeError('TTS 未确认当前服务代的生产者租约；未开始合成')
    binding = {**binding, 'lease': lease['lease']}
    complete, result, wave = False, None, None
    try:
        check()
        data = {'text': text, 'queue_owner': snapshot.generation,
                'queue_batch': snapshot.voice_batch, 'queue_lease': lease['lease'],
                'queue_wait_seconds': '15', 'voice': snapshot.voice,
                'instruction': '用自然、温柔的语气说。' if snapshot.voice == 'telyin'
                               else '用自然、平静的语气说。',
                'seed': '42', 'model_stream': 'true',
                'split_mode': 'sentence' if snapshot.voice == 'telyin' else 'paragraph',
                'pause_ms': '360' if snapshot.voice == 'telyin' else '180'}
        if (type(capability.get('retention_protocol')) is int
                and capability['retention_protocol'] == 1):
            data['result_kind'] = 'qq_temporary'
        if draft is not None:
            with client.stream("POST", "api/phrase", params={**binding, "voice": snapshot.voice},
                               content=draft.upload()) as audio:
                audio.raise_for_status()
                if (audio.headers.get("X-Phrase-Protocol") != "1"
                        or audio.headers.get("X-Voice") != snapshot.voice):
                    raise ValueError("invalid phrase response")
                wave = _read_wave(audio, check)
            job = snapshot.voice_batch
            data["split_mode"] = "phrase_stream"
            data["pause_ms"] = "0"
        else:
            result = _one(client.post('api/generate', data=data))
            if (type(result.get('queue_protocol')) is not int or result['queue_protocol'] != 1
                    or result.get('voice') != snapshot.voice
                    or not isinstance(result.get('audio_url'), str)
                    or not re.fullmatch(r'/audio/[0-9a-f]{32}', result['audio_url'])):
                raise RuntimeError('TTS 未返回当前音色的有效完整回执，未发送语音')
            job = result["audio_url"].rsplit("/", 1)[-1]
        check()
        complete = True
    except httpx.TimeoutException:
        raise RuntimeError('TTS 生成超时，结果未确认；不会自动重试') from None
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f'TTS HTTP 返回 {exc.response.status_code}，不会自动重试') from None
    except httpx.RequestError:
        raise RuntimeError('TTS 连接失败，结果未确认；不会自动重试') from None
    except ValueError:
        raise RuntimeError('TTS 返回无效回执，结果未确认；不会自动重试') from None
    finally:
        if not complete or cancelled.is_set():
            try:
                # Exactly one cancellation attempt, never a generation retry.
                client.post('api/queue/drain-cancel', data=binding, timeout=1).raise_for_status()
            except BaseException:
                note('QQ TTS 取消未确认；仍保留远端生产者责任')
        warned = False
        while True:
            try:
                receipt = _one(client.post('api/queue/drain-status', data=binding, timeout=1))
                if (type(receipt.get('drain_protocol')) is int
                        and receipt['drain_protocol'] == 1
                        and receipt.get('instance_id') == instance
                        and receipt.get('lease') == lease['lease']
                        and receipt.get('admission_closed') is True
                        and receipt.get('drained') is True):
                    break
            except BaseException:
                pass
            if not warned:
                note('QQ TTS 远端尚未证明排空；保留线程、实例及渠道归属，不重发命令')
                warned = True
            time.sleep(.1)
        try:
            client.post('api/queue/drain-ack', data=binding, timeout=1).raise_for_status()
        except BaseException:
            # A lost capacity acknowledgement cannot revive a one-shot producer.
            note('QQ TTS 已排空；租约容量回收未确认，未自动重试')
    check()
    if wave is None:
        with client.stream('GET', result['audio_url']) as audio:
            audio.raise_for_status()
            wave = _read_wave(audio, check)
    return wave, job, data


def _read_wave(audio, check):
    buf = bytearray()
    for part in audio.iter_bytes():
        check()
        if len(buf) + len(part) > 8 * 1024 * 1024:
            raise RuntimeError("TTS 音频超过 8 MB")
        buf.extend(part)
    if len(buf) < 44 or buf[:4] != b'RIFF' or buf[8:12] != b'WAVE':
        raise RuntimeError("TTS 返回的不是有效 WAV 音频")
    return bytes(buf)


def owned_voice(snapshot, text, cancelled, check, send, log):
    from telyincore.voice_draft import take_audio
    check()
    rendered = take_audio(snapshot, text, cancelled, check)
    if rendered is None:
        rendered = render_voice(snapshot, text, cancelled, check, log)
    wave, job, data = rendered
    check()
    receipt = send([{'type': 'record', 'data': {
        'file': 'base64://' + base64.b64encode(wave).decode('ascii')}}])
    # Only the frozen, opt-in corpus receives acknowledged synthetic output.
    # Collection failure cannot turn a sent message into a retryable error.
    if snapshot.corpus is not None:
        try:
            snapshot.corpus.capture(text, snapshot.voice, wave, {
                'model': 'unreported_by_service',
                'job': job,
                'instruction': data['instruction'], 'seed': 42, 'model_stream': True,
                'split_mode': data['split_mode'], 'pause_ms': int(data['pause_ms']),
                'plugin_sha256': snapshot.corpus_hash})
        except Exception:
            try:
                log('QQ TTS 语料收集失败，消息已发送；不会重试发送')
            except Exception:
                pass
    return receipt
