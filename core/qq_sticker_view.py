"""Bounded visual observations of QQ originals. Times are milliseconds from frame zero."""
from io import BytesIO

from PIL import Image, ImageDraw

from telyincore.image_policy import normalize_image


def view(raw, mime, *, at_ms=None, region=None, detail_frames=None):
    if at_ms is not None and (type(at_ms) is not int or at_ms < 0):
        raise ValueError('时间点必须是非负整数毫秒')
    if region is not None:
        if (not isinstance(region, list) or len(region) != 4
                or any(type(v) is not int for v in region)
                or not (0 <= region[0] < region[2] <= 100 and 0 <= region[1] < region[3] <= 100)):
            raise ValueError('区域需要左、上、右、下四个百分数，范围 0 到 100')
    with Image.open(BytesIO(raw), formats=('GIF', 'WEBP', 'JPEG', 'PNG')) as im:
        w, h = im.size
        if max(w, h) > 4096 or w*h > 16_000_000:
            raise ValueError('图片尺寸超限')
        def snapshot():
            frame = im.convert('RGBA')
            if region is not None:
                l, t, r, b = region
                frame = frame.crop((w*l//100, h*t//100, (w*r+99)//100, (h*b+99)//100))
            return frame
        if not getattr(im, 'is_animated', False):
            if at_ms not in (0, None):
                raise ValueError('这是一张静态图片')
            if region is None:
                data, kind = normalize_image(raw, mime)
                return data, kind, '静态图'
            frame, note = snapshot(), '静态图局部'
        else:
            # Bound both traversal and the second pass that composites sampled frames.
            limit = min(512, max(1, 48_000_000 // (w*h)))
            starts, duration, truncated = [], 0, False
            for i in range(limit + 1):
                try:
                    im.seek(i)
                except EOFError:
                    break
                if i == limit:
                    truncated = True
                    break
                starts.append(duration)
                duration += max(1, int(im.info.get('duration', 100)))
                if at_ms is not None and duration > at_ms:
                    break
            if at_ms is not None:
                if at_ms >= duration:
                    raise ValueError(f'此时间点超出可查看范围（已读至 {duration} ms）')
                frame, note = snapshot(), f'时间 {starts[-1]} ms 的画面'
            else:
                from bisect import bisect_right
                targets = (0, duration//3, duration*2//3, duration-1)
                indices = sorted({bisect_right(starts, target)-1 for target in targets})
                tiles = []
                for i in indices:
                    im.seek(i)
                    tile = snapshot()
                    if detail_frames is not None and len(indices) > 1:
                        original = tile.copy()
                        original.thumbnail((1280, 1280))
                        encoded = BytesIO()
                        original.save(encoded, 'PNG')
                        pixels, kind = normalize_image(encoded.getvalue(), 'image/png')
                        detail_frames.append((starts[i], pixels, kind))
                    tile.thumbnail((640, 640))
                    tiles.append(tile)
                width = max(tile.width for tile in tiles)
                height = max(tile.height for tile in tiles)
                columns = min(len(tiles), 2)
                frame = Image.new('RGB', (width*columns, (height+20)*((len(tiles)+1)//2)), '#303030')
                draw = ImageDraw.Draw(frame)
                for pos, (i, tile) in enumerate(zip(indices, tiles)):
                    x, y = pos%columns*width, pos//columns*(height+20)
                    frame.paste(tile, (x, y), tile)
                    draw.text((x+2, y+height+2), f'{starts[i]} ms', fill='white')
                note = '按行从左到右，抽样时间 ' + ', '.join(str(starts[i]) for i in indices)
                note += f' ms；{"仅覆盖开头" if truncated else "动画时长"} {duration} ms'
        out = BytesIO()
        frame.thumbnail((2048, 2048))
        frame.save(out, 'PNG')
        data, kind = normalize_image(out.getvalue(), 'image/png')
        return data, kind, note
