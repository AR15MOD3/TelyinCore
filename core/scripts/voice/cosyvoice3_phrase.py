"""One bounded text upload, one native CosyVoice utterance, one complete WAV."""
import asyncio
import io
import json
import queue
import threading

import numpy as np
import soundfile as sf
from fastapi import HTTPException, Request, Response
from starlette.requests import ClientDisconnect


def install(app, host):
    @app.post("/api/phrase")
    async def phrase(request: Request, instance_id: str, queue_owner: str,
                     queue_batch: str, lease: str, voice: str):
        scheduler = host.scheduler
        binding = (instance_id, queue_owner, queue_batch, lease)
        _, path, instruction, preset = host.prepare("。", None, "", "", voice)
        try:
            scheduler.producer("claim", *binding)
        except host.QueueError as exc:
            raise host._queue_error(exc) from None
        parts = queue.Queue()
        abandoned = threading.Event()

        def phrases():
            while not abandoned.is_set():
                piece = parts.get()
                if piece is None:
                    break
                spoken = piece.replace("特琳", "[t][è][l][ín]") if preset == "telyin" else piece
                normalized = host.engine.frontend.text_normalize(spoken, split=False, text_frontend=True)
                if normalized:
                    if piece[:1].isspace() and normalized[:1].isascii():
                        normalized = " " + normalized.lstrip()
                    yield normalized

        def render():
            ticket = None
            try:
                ticket = host._admit(queue_owner, queue_batch, 15, lease)
                with host.lock:
                    scheduler.check(ticket)
                    frames, count = [], 0
                    # Use the upstream text generator for one continuous utterance.
                    source = phrases()
                    first = next(source, None)
                    if first is None or abandoned.is_set():
                        raise ValueError("no accepted phrase")

                    def spoken_input():
                        # Never enter upstream with an empty generator if the caller
                        # disconnects between worker admission and the first LM step.
                        yield first
                        yield from source

                    model = host.engine.model
                    original = model.llm_job
                    workers, errors = [], []

                    def run_llm(*args, **kwargs):
                        workers.append(threading.current_thread())
                        try:
                            return original(*args, **kwargs)
                        except Exception as error:
                            errors.append(error)
                            model.llm_end_dict[args[-1]] = True

                    model.llm_job = run_llm
                    try:
                        for wave in host.chunks(spoken_input(), path, "", instruction, 42):
                            if abandoned.is_set() or ticket.cancelled:
                                frames.clear()
                                continue
                            count += len(wave)
                            if count * 2 + 44 > 8 * 1024 * 1024:
                                abandoned.set()
                                parts.put(None)
                                frames.clear()
                                continue
                            frames.append(wave)
                    finally:
                        # Retain the scheduler slot until the upstream inference thread exits.
                        if not ended or errors:
                            abandoned.set()
                        parts.put(None)
                        for thread in workers:
                            thread.join()
                        model.llm_job = original
                    if errors:
                        raise RuntimeError("short phrase inference failed") from errors[0]
                    scheduler.check(ticket)
                    if abandoned.is_set() or not frames:
                        raise ValueError("incomplete phrase audio")
                    output = io.BytesIO()
                    sf.write(output, np.concatenate(frames), host.engine.sample_rate,
                             format="WAV", subtype="PCM_16")
                    return output.getvalue()
            finally:
                if ticket:
                    host._finish(ticket, path)
                scheduler.producer("finish", *binding)

        work = None
        try:
            pending = b""
            size = chars = 0
            ended = False
            async with asyncio.timeout(60):
                async for packet in request.stream():
                    size += len(packet)
                    if size > 16384:
                        raise ValueError("phrase upload too large")
                    pending += packet
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        piece = json.loads(line)
                        if ended:
                            raise ValueError("data after end")
                        if piece is None:
                            ended = True
                            parts.put(None)
                            continue
                        if not isinstance(piece, str) or not piece or chars + len(piece) > 1000:
                            raise ValueError("invalid phrase")
                        chars += len(piece)
                        parts.put(piece)
                        if work is None:
                            work = asyncio.create_task(asyncio.to_thread(render))
            if not ended or pending or not work:
                raise ValueError("incomplete phrase upload")
            wave = await asyncio.shield(work)
            return Response(wave, media_type="audio/wav",
                            headers={"X-Voice": voice, "X-Phrase-Protocol": "1"})
        except ClientDisconnect as exc:
            raise HTTPException(400, "短语输入已断开") from exc
        except (ValueError, TimeoutError) as exc:
            raise HTTPException(422, "短语输入未完整结束") from exc
        finally:
            abandoned.set()
            parts.put(None)
            if work is None:
                scheduler.producer("finish", *binding)
            else:
                await asyncio.gather(asyncio.shield(work), return_exceptions=True)
