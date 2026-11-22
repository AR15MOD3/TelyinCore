"""Private framed originals for exact ranking; only winning frames are decoded.

Large JSON records are compressed in chunks. Small records stay uncompressed.
The spool never rereads mutable source files after corpus statistics are known.
"""
import json
import struct
try:
    import zlib
except ImportError:
    zlib = None
_ZLIB_ERRORS = (zlib.error,) if zlib is not None else ()
from .memory_json import flat_bytes

_HEADER = struct.Struct('<BQQ')
_BLOCK = 65536
SMALL = 4096


def write(stream, item):
    start = stream.tell()
    compressed = zlib is not None and any((isinstance(value,str) and len(value)>SMALL)
                     or isinstance(value,(dict,list,tuple)) for value in item.values())
    if not compressed:
        for block in flat_bytes(item,errors='backslashreplace'):stream.write(block)
        stream.write(b'\n')
        return start
    encoder = zlib.compressobj(level=1)
    stream.write(b'\0'*_HEADER.size)
    raw_size = 0
    for block in flat_bytes(item,errors='backslashreplace'):
        raw_size += len(block)
        stream.write(encoder.compress(block) if encoder is not None else block)
    if encoder is not None:stream.write(encoder.flush())
    end = stream.tell()
    stream.seek(start)
    stream.write(_HEADER.pack(int(compressed),end-start-_HEADER.size,raw_size))
    stream.seek(end)
    return start


def read(stream, offset):
    stream.seek(offset)
    first=stream.read(1)
    if first==b'{':return json.loads(first+stream.readline())
    header=first+stream.read(_HEADER.size-1)
    if len(header)!=_HEADER.size:raise ValueError('原文临时快照头不完整')
    mode, remaining, expected = _HEADER.unpack(header)
    if mode not in (0,1):raise ValueError('原文临时快照格式无效')
    if not mode and remaining!=expected:raise ValueError('原文临时快照长度无效')
    if mode and zlib is None:raise ValueError('原文临时快照压缩支持不可用')
    decoder = zlib.decompressobj() if mode else None
    raw = bytearray()
    try:
        while remaining:
            block = stream.read(min(_BLOCK,remaining))
            if not block:raise ValueError('原文临时快照不完整')
            remaining -= len(block)
            if decoder is None:raw.extend(block)
            else:
                while block:
                    piece = decoder.decompress(block,_BLOCK)
                    raw.extend(piece)
                    tail = decoder.unconsumed_tail
                    if tail==block and not piece:raise ValueError('原文临时快照解码未前进')
                    block = tail
                    if decoder.unused_data:raise ValueError('原文临时快照包含多余数据')
                    if len(raw)>expected:raise ValueError('原文临时快照长度无效')
        if decoder is not None and not decoder.eof:raise ValueError('原文临时快照压缩数据不完整')
    except _ZLIB_ERRORS as error:
        raise ValueError('原文临时快照压缩数据损坏') from error
    if len(raw)!=expected:raise ValueError('原文临时快照长度无效')
    return json.loads(raw)
