"""Round-4 recipes, part B (Python, `unittest`): image processing with and without Pillow."""
from .recipes import RC, UT

R4B = [
RC("py-image-stdlib-png-resize-hash-palette", "Pure-Python images: PNG read/write, resize (nearest/bilinear/box), crop, dHash duplicates, median-cut dominant colours, pixel diff", "python",
   "Do basic image work in a script or tiny tool where installing Pillow/numpy is not an option, or understand what those libraries do.",
   "A small Image class (row-major RGB tuples); a spec-complete PNG codec with CRC checks and all five scanline filters (None/Sub/Up/Average/Paeth) for 8-bit gray, RGB, RGBA and palette images; resampling that picks the right filter (box average for shrinking, bilinear for enlarging); aspect-preserving fit that never upscales; difference hash plus Hamming distance for near-duplicate detection; median-cut dominant colours with shares; and a thresholded pixel diff. Cross-checked against Pillow when it is installed.",
   {"imgkit.py": r'''"""Tiny raster toolkit with no dependencies. Pixels are (r, g, b) tuples; alpha is dropped on decode."""
import struct
import zlib

SIG = b"\x89PNG\r\n\x1a\n"


class Image:
    def __init__(self, w, h, px):
        if len(px) != w * h:
            raise ValueError("pixel count does not match size")
        self.w, self.h, self.px = w, h, list(px)

    def get(self, x, y):
        return self.px[y * self.w + x]

    def __eq__(self, o):
        return (self.w, self.h, self.px) == (o.w, o.h, o.px)


# ---- PNG -------------------------------------------------------------------------------------
def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else (b if pb <= pc else c)


def _apply(ft, row, prev, bpp, inverse):
    out = bytearray(len(row))
    src = out if inverse else row                       # predictors always use the RECONSTRUCTED left neighbour
    for i, x in enumerate(row):
        a = src[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        pred = 0 if ft == 0 else a if ft == 1 else b if ft == 2 else (a + b) // 2 if ft == 3 else _paeth(a, b, c)
        out[i] = (x + pred) & 255 if inverse else (x - pred) & 255
    return bytes(out)


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def encode_png(img, filter_type=0):
    if not 0 <= filter_type <= 4:
        raise ValueError("filter_type must be 0..4")
    rows, prev = [], bytes(img.w * 3)
    for y in range(img.h):
        raw = bytes(v for p in img.px[y * img.w:(y + 1) * img.w] for v in p)
        rows.append(bytes([filter_type]) + _apply(filter_type, raw, prev, 3, False))
        prev = raw
    return SIG + _chunk(b"IHDR", struct.pack(">IIBBBBB", img.w, img.h, 8, 2, 0, 0, 0)) + _chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + _chunk(b"IEND", b"")


def decode_png(data):
    if data[:8] != SIG:
        raise ValueError("not a PNG")
    pos, idat, plte, hdr = 8, [], None, None
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body, crc = data[pos + 8:pos + 8 + n], data[pos + 8 + n:pos + 12 + n]
        if len(body) != n or struct.unpack(">I", crc)[0] != zlib.crc32(kind + body) & 0xFFFFFFFF:
            raise ValueError("corrupt chunk %r" % kind)
        if kind == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            plte = [tuple(body[i:i + 3]) for i in range(0, n, 3)]
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + n
    if not hdr:
        raise ValueError("missing IHDR")
    w, h, depth, ctype, _, _, interlace = hdr
    if depth not in (1, 2, 4, 8) or interlace != 0 or ctype not in (0, 2, 3, 4, 6) or (depth < 8 and ctype not in (0, 3)):
        raise ValueError("unsupported PNG variant (need non-interlaced, 8-bit, or 1/2/4-bit gray/palette)")
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bits = depth * ch
    bpp, stride = max(1, bits // 8), (w * bits + 7) // 8             # filters work on bytes; sub-byte pixels share a byte
    raw, prev, px, off = zlib.decompress(b"".join(idat)), bytes(stride), [], 0
    for _ in range(h):
        ft, row = raw[off], raw[off + 1:off + 1 + stride]
        if ft > 4 or len(row) != stride:
            raise ValueError("corrupt scanline")
        cur = _apply(ft, row, prev, bpp, True)
        if depth < 8:
            mask = (1 << depth) - 1
            vals = [(cur[i * depth // 8] >> (8 - depth - (i * depth) % 8)) & mask for i in range(w)]
            px += [plte[v] if ctype == 3 else (v * 255 // mask,) * 3 for v in vals]
        else:
            for i in range(0, stride, ch):
                p = cur[i:i + ch]
                px.append((p[0],) * 3 if ctype in (0, 4) else plte[p[0]] if ctype == 3 else tuple(p[:3]))
        prev, off = cur, off + 1 + stride
    return Image(w, h, px)


# ---- geometry ----------------------------------------------------------------------------------
def crop(img, x, y, w, h):
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > img.w or y + h > img.h:
        raise ValueError("crop box outside image")
    return Image(w, h, [img.get(i, j) for j in range(y, y + h) for i in range(x, x + w)])


def resize(img, w, h, method=None):
    """method: nearest | bilinear | box. Default: box when shrinking (no aliasing), bilinear when enlarging."""
    if w < 1 or h < 1:
        raise ValueError("size must be positive")
    method = method or ("box" if w < img.w or h < img.h else "bilinear")
    out = []
    for y in range(h):
        for x in range(w):
            if method == "nearest":
                out.append(img.get(min(img.w - 1, int(x * img.w / w)), min(img.h - 1, int(y * img.h / h))))
            elif method == "box":
                x0, x1 = int(x * img.w / w), max(int(x * img.w / w) + 1, -(-((x + 1) * img.w) // w))
                y0, y1 = int(y * img.h / h), max(int(y * img.h / h) + 1, -(-((y + 1) * img.h) // h))
                cell = [img.get(i, j) for j in range(y0, min(y1, img.h)) for i in range(x0, min(x1, img.w))]
                out.append(tuple(round(sum(c[k] for c in cell) / len(cell)) for k in range(3)))
            elif method == "bilinear":
                fx = min(max((x + 0.5) * img.w / w - 0.5, 0), img.w - 1)
                fy = min(max((y + 0.5) * img.h / h - 0.5, 0), img.h - 1)
                x0, y0 = int(fx), int(fy)
                x1, y1 = min(x0 + 1, img.w - 1), min(y0 + 1, img.h - 1)
                tx, ty = fx - x0, fy - y0
                a, b, c, d = img.get(x0, y0), img.get(x1, y0), img.get(x0, y1), img.get(x1, y1)
                out.append(tuple(round((a[k] * (1 - tx) + b[k] * tx) * (1 - ty) + (c[k] * (1 - tx) + d[k] * tx) * ty) for k in range(3)))
            else:
                raise ValueError("unknown method %r" % method)
    return Image(w, h, out)


def fit(img, max_w, max_h):
    """Aspect-preserving thumbnail size; never upscales."""
    s = min(max_w / img.w, max_h / img.h, 1)
    return resize(img, max(1, round(img.w * s)), max(1, round(img.h * s)))


def cover(img, w, h):
    """Scale to fill w*h then centre-crop (what CSS object-fit: cover does)."""
    s = max(w / img.w, h / img.h)
    big = resize(img, max(w, round(img.w * s)), max(h, round(img.h * s)))
    return crop(big, (big.w - w) // 2, (big.h - h) // 2, w, h)


# ---- analysis -----------------------------------------------------------------------------------
def gray(img):
    return [(299 * r + 587 * g + 114 * b) // 1000 for r, g, b in img.px]


def dhash(img, size=8):
    """Difference hash: robust to rescaling and mild recompression. Compare with hamming(); <= ~10 of 64 bits means 'same picture'."""
    small = resize(img, size + 1, size, "box")
    g, bits = gray(small), 0
    for y in range(size):
        for x in range(size):
            bits = (bits << 1) | (g[y * (size + 1) + x] > g[y * (size + 1) + x + 1])
    return bits


def hamming(a, b):
    return bin(a ^ b).count("1")


def dominant_colors(img, k=3, max_samples=10000):
    """Median-cut style palette (boxes are split at the middle of their widest channel range). -> [((r, g, b), share)] largest first."""
    step = max(1, len(img.px) // max_samples)
    boxes = [img.px[::step]]
    while len(boxes) < k:
        splittable = [b for b in boxes if len(b) > 1 and any(max(p[c] for p in b) != min(p[c] for p in b) for c in range(3))]
        if not splittable:
            break
        box = max(splittable, key=lambda b: max(max(p[c] for p in b) - min(p[c] for p in b) for c in range(3)))
        ch = max(range(3), key=lambda c: max(p[c] for p in box) - min(p[c] for p in box))
        mid = (max(p[ch] for p in box) + min(p[ch] for p in box)) / 2      # cut at the middle of the RANGE: a count-median would split a 75/25 two-colour image in half
        boxes.remove(box)
        boxes += [[p for p in box if p[ch] <= mid], [p for p in box if p[ch] > mid]]
    total = sum(len(b) for b in boxes)
    res = [(tuple(round(sum(p[c] for p in b) / len(b)) for c in range(3)), len(b) / total) for b in boxes]
    return sorted(res, key=lambda t: -t[1])


def diff(a, b, threshold=0):
    """-> (fraction of pixels differing by more than threshold in any channel, largest channel delta)."""
    if (a.w, a.h) != (b.w, b.h):
        raise ValueError("images differ in size")
    deltas = [max(abs(p[c] - q[c]) for c in range(3)) for p, q in zip(a.px, b.px)]
    return sum(d > threshold for d in deltas) / len(deltas), max(deltas, default=0)
''', "test_imgkit.py": r'''import io, unittest
import imgkit as K


def lcg_image(w, h, seed=7):
    px, s = [], seed
    for _ in range(w * h):
        s = (s * 1103515245 + 12345) & 0x7FFFFFFF; px.append((s >> 16 & 255, s >> 8 & 255, s & 255))
    return K.Image(w, h, px)


def gradient(w, h, invert=False):
    px = [((x * 255 // (w - 1)),) * 3 for y in range(h) for x in range(w)]
    return K.Image(w, h, [(255 - r,) * 3 for r, _, _ in px] if invert else px)


class Png(unittest.TestCase):
    def test_round_trip_every_filter(self):
        img = lcg_image(7, 5)
        for ft in range(5): self.assertEqual(K.decode_png(K.encode_png(img, ft)), img, "filter %d" % ft)

    def test_signature_and_corruption(self):
        good = K.encode_png(lcg_image(4, 4))
        with self.assertRaises(ValueError): K.decode_png(b"GIF89a" + good[6:])
        bad = bytearray(good); bad[30] ^= 0xFF
        with self.assertRaisesRegex(ValueError, "corrupt"): K.decode_png(bytes(bad))
        with self.assertRaises(ValueError): K.encode_png(lcg_image(2, 2), 9)

    def test_pillow_reads_our_png_and_we_read_pillows(self):
        try: from PIL import Image
        except ImportError: self.skipTest("Pillow not installed")
        img = lcg_image(13, 9)
        theirs = Image.open(io.BytesIO(K.encode_png(img, 4))).convert("RGB"); self.assertEqual(list(theirs.getdata()), img.px)
        buf = io.BytesIO(); Image.new("RGB", (1, 1)).resize((1, 1)); src = Image.new("RGB", (40, 30)); src.putdata([(x * 6 % 256, y * 8 % 256, (x * y) % 256) for y in range(30) for x in range(40)])
        src.save(buf, "PNG"); self.assertEqual(K.decode_png(buf.getvalue()).px, list(src.getdata()))
        pal = Image.new("RGB", (8, 8), (10, 200, 30)).quantize(4); buf = io.BytesIO(); pal.save(buf, "PNG")
        self.assertEqual(K.decode_png(buf.getvalue()).px[0], (10, 200, 30))                      # palette image
        lowbit = Image.new("P", (5, 3)); lowbit.putpalette([0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 255]); lowbit.putdata([0, 1, 2, 3, 1, 3, 2, 1, 0, 0, 1, 1, 1, 2, 3]); buf = io.BytesIO(); lowbit.save(buf, "PNG", bits=2)
        self.assertEqual(K.decode_png(buf.getvalue()).px, list(lowbit.convert("RGB").getdata()))             # 2-bit palette, width not a multiple of 4
        rgba = Image.new("RGBA", (3, 3), (1, 2, 3, 128)); buf = io.BytesIO(); rgba.save(buf, "PNG"); self.assertEqual(K.decode_png(buf.getvalue()).px[0], (1, 2, 3))


class Geometry(unittest.TestCase):
    def test_sizes_and_solid_colour_preserved(self):
        solid = K.Image(6, 4, [(9, 99, 199)] * 24)
        for m in ("nearest", "bilinear", "box"):
            r = K.resize(solid, 3, 8, m); self.assertEqual((r.w, r.h), (3, 8)); self.assertEqual(set(r.px), {(9, 99, 199)}, m)

    def test_box_is_a_mean_and_bilinear_is_monotonic(self):
        self.assertEqual(K.resize(K.Image(2, 2, [(0, 0, 0), (100, 100, 100), (200, 200, 200), (100, 100, 100)]), 1, 1).px, [(100, 100, 100)])
        up = K.resize(gradient(2, 1), 8, 1, "bilinear"); vals = [p[0] for p in up.px]; self.assertEqual(vals, sorted(vals)); self.assertEqual((vals[0], vals[-1]), (0, 255))
        with self.assertRaises(ValueError): K.resize(solid := K.Image(1, 1, [(0, 0, 0)]), 0, 1)

    def test_fit_never_upscales_and_keeps_aspect(self):
        img = K.Image(400, 200, [(1, 2, 3)] * 80000); f = K.fit(img, 100, 100); self.assertEqual((f.w, f.h), (100, 50))
        small = K.Image(10, 10, [(1, 2, 3)] * 100); self.assertEqual((K.fit(small, 100, 100).w), 10)

    def test_cover_fills_exactly(self):
        c = K.cover(gradient(40, 20), 10, 10); self.assertEqual((c.w, c.h), (10, 10))

    def test_crop_and_bounds(self):
        img = lcg_image(5, 5); c = K.crop(img, 1, 2, 3, 2); self.assertEqual((c.w, c.h, c.get(0, 0)), (3, 2, img.get(1, 2)))
        for box in ((4, 4, 2, 2), (-1, 0, 2, 2), (0, 0, 0, 1)):
            with self.assertRaises(ValueError): K.crop(img, *box)


class Analysis(unittest.TestCase):
    def test_dhash_survives_rescale_not_inversion(self):
        g = gradient(64, 64); a, b = K.dhash(g), K.dhash(K.resize(g, 40, 30))
        self.assertLessEqual(K.hamming(a, b), 6); self.assertGreaterEqual(K.hamming(a, K.dhash(gradient(64, 64, invert=True))), 48)

    def test_dominant_colours_and_shares(self):
        img = K.Image(10, 10, [(250, 0, 0)] * 75 + [(0, 0, 250)] * 25); top = K.dominant_colors(img, 2)
        self.assertEqual(top[0], ((250, 0, 0), 0.75)); self.assertEqual(top[1], ((0, 0, 250), 0.25))
        self.assertEqual(len(K.dominant_colors(K.Image(2, 2, [(5, 5, 5)] * 4), 5)), 1)       # nothing left to split

    def test_diff(self):
        a = lcg_image(4, 4); b = K.Image(4, 4, list(a.px)); self.assertEqual(K.diff(a, b), (0.0, 0))
        b.px[3] = (a.px[3][0] ^ 255, a.px[3][1], a.px[3][2]); frac, mx = K.diff(a, b); self.assertEqual((frac, mx > 0), (1 / 16, True))
        self.assertEqual(K.diff(a, b, threshold=255)[0], 0.0)
        with self.assertRaises(ValueError): K.diff(a, lcg_image(3, 3))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Decodes non-interlaced 8-bit PNG plus 1/2/4-bit gray/palette (16-bit and Adam7 raise a clear error); alpha is discarded, not composited", "Pure Python is for small images and understanding: 12 MP photos want Pillow/numpy", "JPEG/WebP codecs are far too big to write by hand - use Pillow for those"], compat={"python": ">=3.9"},
   tags=["image", "png", "resize", "crop", "thumbnail", "perceptual-hash", "dhash", "duplicate-images", "dominant-color", "palette", "median-cut", "pixel-diff", "image-processing", "no-dependencies", "zlib"],
   avoid="Resizing a big image down with nearest-neighbour or bilinear only (aliasing); hashing images with md5 to find 'same' pictures; ignoring PNG scanline filters; upscaling thumbnails."),

RC("py-pillow-image-batch-exif-watermark", "Pillow toolkit: thumbnails and cover-crop, EXIF read + orientation fix, format conversion, watermark text, dHash duplicates, dominant colours", "python",
   "Process a folder of photos: shrink them for the web, fix sideways phone pictures, read camera/GPS metadata, convert to WebP/JPEG, stamp a watermark, find near-duplicates.",
   "The Pillow idioms that are easy to get wrong: ImageOps.exif_transpose BEFORE any resizing, thumbnail() (in place, keeps aspect, never upscales) on a copy, ImageOps.fit for cover crops, RGBA->RGB compositing before saving JPEG, stripping EXIF (and GPS) on output unless asked to keep it, an alpha-overlay watermark that works on any mode, EXIF GPS rationals to decimal degrees, a batch runner that reports unreadable files instead of dying, and dHash / quantize helpers.",
   {"photos.py": r'''"""Pillow helpers (pip install pillow). Everything takes/returns PIL.Image so steps compose."""
import os
from PIL import ExifTags, Image, ImageChops, ImageDraw, ImageFont, ImageOps

LANCZOS = Image.Resampling.LANCZOS
SAVE_ARGS = {".jpg": dict(format="JPEG", quality=85, optimize=True, progressive=True), ".jpeg": dict(format="JPEG", quality=85, optimize=True, progressive=True),
             ".webp": dict(format="WEBP", quality=80), ".png": dict(format="PNG", optimize=True)}
IMAGE_EXTS = tuple(SAVE_ARGS) + (".gif", ".bmp", ".tif", ".tiff")


def open_upright(path):
    """Open and apply the EXIF orientation. Phone photos are often stored sideways with a flag; resize/crop only AFTER this."""
    im = Image.open(path)
    im.load()
    return ImageOps.exif_transpose(im)


def thumbnail(im, box=(256, 256)):
    t = im.copy()
    t.thumbnail(box, LANCZOS)                          # in place on the copy; keeps aspect ratio; never enlarges
    return t


def cover(im, size):
    return ImageOps.fit(im, size, LANCZOS)             # scale to fill, then centre-crop


def to_rgb(im, background=(255, 255, 255)):
    """JPEG has no alpha: composite transparency onto a background instead of letting it turn black."""
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, background)
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return im.convert("RGB")


def save(im, path, quality=None, keep_exif=None):
    """Format from the extension. EXIF (incl. GPS location!) is dropped unless you pass keep_exif=<Exif>."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in SAVE_ARGS:
        raise ValueError("unsupported output type %r (use one of %s)" % (ext, ", ".join(SAVE_ARGS)))
    args = dict(SAVE_ARGS[ext])
    if quality and "quality" in args:
        args["quality"] = quality
    if keep_exif is not None and args["format"] in ("JPEG", "WEBP"):
        args["exif"] = keep_exif
    if args["format"] == "JPEG":
        im = to_rgb(im)
    tmp = path + ".tmp"
    im.save(tmp, **args)
    os.replace(tmp, path)                              # never leave a half-written file under the final name


def _deg(dms, ref):
    d = float(dms[0]) + float(dms[1]) / 60 + float(dms[2]) / 3600
    return -d if ref in ("S", "W") else d


def exif_summary(path):
    """Make/model/date/orientation and GPS as decimal degrees; {} if there is no EXIF."""
    with Image.open(path) as im:
        ex = im.getexif()
        base = {ExifTags.TAGS.get(k, k): v for k, v in ex.items()}
        out = {k: base[k] for k in ("Make", "Model", "DateTime", "Orientation", "Software") if k in base}
        gps = ex.get_ifd(ExifTags.IFD.GPSInfo)
        if gps.get(2) and gps.get(4):
            out["gps"] = (round(_deg(gps[2], gps.get(1)), 6), round(_deg(gps[4], gps.get(3)), 6))
        return out


def watermark(im, text, opacity=140, margin=12):
    base = im.convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    font = ImageFont.load_default(size=max(12, base.width // 25))
    d = ImageDraw.Draw(layer)
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    xy = (base.width - (r - l) - margin - l, base.height - (b - t) - margin - t)
    d.text((xy[0] + 1, xy[1] + 1), text, font=font, fill=(0, 0, 0, opacity))      # 1px shadow keeps it legible on light photos
    d.text(xy, text, font=font, fill=(255, 255, 255, opacity))
    return Image.alpha_composite(base, layer)


def batch_thumbnails(src, dst, box=(256, 256), ext=".webp"):
    """Thumbnail every image directly in src into dst. -> {'done': [...], 'skipped': [(name, reason)]}. One bad file never stops the run."""
    os.makedirs(dst, exist_ok=True)
    rep = {"done": [], "skipped": []}
    for name in sorted(os.listdir(src)):
        if not name.lower().endswith(IMAGE_EXTS):
            continue
        stem = os.path.splitext(name)[0]
        try:
            save(thumbnail(open_upright(os.path.join(src, name)), box), os.path.join(dst, stem + ext))
            rep["done"].append(name)
        except (OSError, ValueError, Image.DecompressionBombError) as e:
            rep["skipped"].append((name, str(e)))
    return rep


def dhash(im, size=8):
    g = im.convert("L").resize((size + 1, size), LANCZOS)
    px = list(g.getdata())
    bits = 0
    for y in range(size):
        for x in range(size):
            bits = (bits << 1) | (px[y * (size + 1) + x] > px[y * (size + 1) + x + 1])
    return bits


def hamming(a, b):
    return bin(a ^ b).count("1")


def dominant(im, k=5):
    """[(rgb, share)] via median-cut quantisation on a 100px copy."""
    q = im.convert("RGB").resize((100, 100), LANCZOS).quantize(colors=k, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()
    return [(tuple(pal[i * 3:i * 3 + 3]), n / 10000) for n, i in sorted(q.getcolors(), reverse=True)]


def changed_region(a, b):
    """Bounding box (l, t, r, b) of differing pixels, or None when identical. Sizes must match."""
    return ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox()
''', "test_photos.py": r'''import os, tempfile, unittest
from PIL import Image
import photos as P


def tmp(name=""):
    return os.path.join(tempfile.mkdtemp(), name)


def sideways_jpeg(path, orientation=6, size=(40, 20)):
    ex = Image.Exif(); ex[274] = orientation; ex[271] = "ACME"; ex[272] = "PhoneCam 3"
    Image.new("RGB", size, (200, 30, 30)).save(path, exif=ex)


class Orientation(unittest.TestCase):
    def test_exif_orientation_applied(self):
        p = tmp("a.jpg"); sideways_jpeg(p, 6); self.assertEqual(P.open_upright(p).size, (20, 40))
        q = tmp("b.jpg"); sideways_jpeg(q, 1); self.assertEqual(P.open_upright(q).size, (40, 20))

    def test_summary(self):
        p = tmp("a.jpg"); sideways_jpeg(p, 6); s = P.exif_summary(p); self.assertEqual((s["Make"], s["Model"], s["Orientation"]), ("ACME", "PhoneCam 3", 6))
        plain = tmp("c.png"); Image.new("RGB", (4, 4)).save(plain); self.assertEqual(P.exif_summary(plain), {})

    def test_gps_to_decimal(self):
        ex = Image.Exif(); ex[0x8825] = {1: "S", 2: (33.0, 51.0, 36.0), 3: "E", 4: (151.0, 12.0, 0.0)}
        p = tmp("g.jpg"); Image.new("RGB", (8, 8)).save(p, exif=ex)
        lat, lon = P.exif_summary(p)["gps"]; self.assertAlmostEqual(lat, -33.86, places=3); self.assertAlmostEqual(lon, 151.2, places=3)


class Resizing(unittest.TestCase):
    def test_thumbnail_keeps_aspect_never_enlarges_and_copies(self):
        big = Image.new("RGB", (400, 200)); t = P.thumbnail(big, (100, 100)); self.assertEqual((t.size, big.size), ((100, 50), (400, 200)))
        self.assertEqual(P.thumbnail(Image.new("RGB", (10, 10)), (100, 100)).size, (10, 10))

    def test_cover_is_exact(self):
        self.assertEqual(P.cover(Image.new("RGB", (400, 200)), (100, 100)).size, (100, 100))


class Saving(unittest.TestCase):
    def test_png_with_transparency_to_jpeg_is_white_not_black(self):
        im = Image.new("RGBA", (8, 8), (255, 0, 0, 0)); p = tmp("t.jpg"); P.save(im, p)
        px = Image.open(p).getpixel((4, 4)); self.assertTrue(all(c > 240 for c in px), px)

    def test_exif_stripped_by_default_kept_on_request(self):
        src = tmp("s.jpg"); sideways_jpeg(src, 1); ex = Image.open(src).getexif()
        out = tmp("o.jpg"); P.save(Image.open(src), out); self.assertEqual(P.exif_summary(out), {})
        keep = tmp("k.jpg"); P.save(Image.open(src), keep, keep_exif=ex); self.assertEqual(P.exif_summary(keep)["Make"], "ACME")

    def test_formats_and_unsupported(self):
        im = Image.new("RGB", (16, 16), (1, 2, 3))
        for ext, fmt in ((".webp", "WEBP"), (".png", "PNG"), (".jpeg", "JPEG")):
            p = tmp("x" + ext); P.save(im, p); self.assertEqual(Image.open(p).format, fmt)
        with self.assertRaisesRegex(ValueError, "unsupported"): P.save(im, tmp("x.xyz"))
        d = tempfile.mkdtemp(); P.save(im, d + "/y.png"); self.assertEqual(os.listdir(d), ["y.png"])           # no .tmp left behind


class Watermark(unittest.TestCase):
    def test_changes_pixels_only_near_the_corner(self):
        base = Image.new("RGB", (400, 300), (30, 30, 30)); w = P.watermark(base, "(c) me")
        box = P.changed_region(base, w); self.assertIsNotNone(box); self.assertGreater(box[0], 200); self.assertGreater(box[1], 200)

    def test_works_on_palette_and_gray(self):
        for mode in ("L", "P", "RGBA"): self.assertEqual(P.watermark(Image.new(mode, (200, 100)), "x").mode, "RGBA")


class Batch(unittest.TestCase):
    def test_bad_file_is_reported_and_others_finish(self):
        src, dst = tempfile.mkdtemp(), tempfile.mkdtemp()
        Image.new("RGB", (300, 200), (9, 9, 9)).save(src + "/ok.png"); sideways_jpeg(src + "/phone.jpg", 6, (300, 200))
        open(src + "/broken.jpg", "wb").write(b"not an image"); open(src + "/notes.txt", "w").write("skip me")
        rep = P.batch_thumbnails(src, dst, (100, 100))
        self.assertEqual(rep["done"], ["ok.png", "phone.jpg"]); self.assertEqual([n for n, _ in rep["skipped"]], ["broken.jpg"])
        self.assertEqual(sorted(os.listdir(dst)), ["ok.webp", "phone.webp"]); self.assertEqual(Image.open(dst + "/phone.webp").size, (67, 100))      # rotated upright first


class Analysis(unittest.TestCase):
    def grad(self, w, h, inv=False):
        im = Image.new("L", (w, h)); im.putdata([(255 - x * 255 // (w - 1)) if inv else x * 255 // (w - 1) for y in range(h) for x in range(w)]); return im

    def test_dhash_duplicates(self):
        g = self.grad(64, 64); self.assertLessEqual(P.hamming(P.dhash(g), P.dhash(g.resize((30, 30)))), 6); self.assertGreaterEqual(P.hamming(P.dhash(g), P.dhash(self.grad(64, 64, True))), 48)

    def test_dominant(self):
        im = Image.new("RGB", (100, 100), (250, 0, 0)); im.paste((0, 0, 250), (0, 0, 100, 25)); top = P.dominant(im, 2)
        self.assertEqual(top[0][0], (250, 0, 0)); self.assertAlmostEqual(top[0][1], 0.75, places=1)

    def test_changed_region(self):
        a = Image.new("RGB", (10, 10)); b = a.copy(); b.putpixel((3, 4), (255, 0, 0)); self.assertEqual(P.changed_region(a, b), (3, 4, 4, 5)); self.assertIsNone(P.changed_region(a, a))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Requires Pillow >= 10.1 (ImageFont.load_default(size=...)); verified in the sandbox on Pillow 13", "Default font is a bitmap-ish fallback: pass a real TTF via ImageFont.truetype for production watermarks", "HEIC/AVIF need extra plugins (pillow-heif); RAW files need rawpy", "Set Image.MAX_IMAGE_PIXELS deliberately when handling untrusted uploads (decompression bombs)"], compat={"python": ">=3.9"},
   tags=["image", "pillow", "pil", "resize", "crop", "thumbnail", "exif", "orientation", "gps", "metadata", "photo", "convert", "webp", "jpeg", "png", "watermark", "dominant-color", "perceptual-hash", "duplicate-images", "batch", "folder", "image-processing"],
   avoid="Resizing before exif_transpose (sideways thumbnails); saving RGBA as JPEG (OSError or black background); publishing photos with GPS EXIF intact; calling thumbnail() on the original you still need; one corrupt file aborting the whole batch."),
]
