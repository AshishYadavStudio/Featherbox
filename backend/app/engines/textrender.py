"""Caption rendering with proper shaping for every script (Devanagari, Latin, ...).

Pillow's basic text layout cannot join Hindi conjuncts and matras correctly, so text
is shaped with HarfBuzz and rasterised glyph-by-glyph with FreeType.
"""
from __future__ import annotations

import functools
import os
import re
from pathlib import Path

import freetype
import numpy as np
import uharfbuzz as hb
from PIL import Image, ImageChops, ImageFilter

_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")

# Linux: the usual packaged fonts (Ubuntu/Debian: fonts-noto-core; Fedora: google-noto-sans-* fonts)
_LINUX = [Path("/usr/share/fonts/truetype"), Path("/usr/share/fonts/opentype"), Path("/usr/share/fonts")]

# (path, face index) candidates, first existing wins
_LATIN_FONTS = [(_FONTS / "seguibl.ttf", 0), (_FONTS / "ariblk.ttf", 0), (_FONTS / "arialbd.ttf", 0),
                *[(d / f, 0) for d in _LINUX for f in ("noto/NotoSans-Black.ttf", "noto/NotoSans-Bold.ttf",
                                                       "google-noto/NotoSans-Bold.ttf", "dejavu/DejaVuSans-Bold.ttf",
                                                       "liberation/LiberationSans-Bold.ttf")]]
_INDIC_FONTS = [(_FONTS / "Nirmala.ttc", 1), (_FONTS / "Nirmala.ttf", 0), (_FONTS / "NirmalaB.ttf", 0),
                *[(d / f, 0) for d in _LINUX for f in ("noto/NotoSansDevanagari-Bold.ttf",
                                                       "google-noto/NotoSansDevanagari-Bold.ttf",
                                                       "lohit-devanagari/Lohit-Devanagari.ttf",
                                                       "fonts-lohit-deva/Lohit-Devanagari.ttf")]]


def _fc_match(indic: bool) -> tuple[str, int] | None:
    """Ask fontconfig (Linux) for any installed bold font that covers the script."""
    import shutil
    import subprocess
    if not shutil.which("fc-match"):
        return None
    pattern = "sans:bold:lang=hi" if indic else "sans:bold"
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}|%{index}|%{lang}", pattern], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    path, _, rest = out.partition("|")
    index, _, langs = rest.partition("|")
    if not path or not Path(path).exists() or (indic and "hi" not in langs.split("|")):
        return None
    return path, int(index or 0)


def _pick(cands: list[tuple[Path, int]]) -> tuple[str, int]:
    for p, i in cands:
        if p.exists():
            return str(p), i
    found = _fc_match(cands is _INDIC_FONTS)
    if found:
        return found
    hint = ("" if os.name == "nt" else
            " On Ubuntu/Debian install them with: sudo apt install fonts-noto-core")
    raise RuntimeError(("No Hindi (Devanagari) caption font found." if cands is _INDIC_FONTS
                        else "No caption font found.") + hint)


class ShapedFont:
    def __init__(self, path: str, index: int, size: int) -> None:
        self.size = size
        self.hb_font = hb.Font(hb.Face(hb.Blob.from_file_path(path), index))
        self.hb_font.scale = (size * 64, size * 64)
        self.ft = freetype.Face(path, index)
        self.ft.set_char_size(size * 64)
        self.ascender = self.ft.size.ascender / 64
        self.line_h = self.ft.size.height / 64

    def _shape(self, text: str):
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.hb_font, buf, {"kern": True, "liga": True})
        return buf.glyph_infos, buf.glyph_positions

    def width(self, text: str) -> float:
        return sum(p.x_advance for p in self._shape(text)[1]) / 64

    def draw(self, mask: Image.Image, text: str, x: float, baseline: float) -> None:
        infos, positions = self._shape(text)
        pen = x
        for info, pos in zip(infos, positions):
            self.ft.load_glyph(info.codepoint, freetype.FT_LOAD_RENDER)
            g = self.ft.glyph
            bmp = g.bitmap
            if bmp.width and bmp.rows:
                arr = np.array(bmp.buffer, dtype=np.uint8).reshape(bmp.rows, bmp.pitch)[:, : bmp.width]
                glyph = Image.fromarray(arr, "L")
                gx = int(round(pen + pos.x_offset / 64 + g.bitmap_left))
                gy = int(round(baseline - pos.y_offset / 64 - g.bitmap_top))
                box = (gx, gy, gx + bmp.width, gy + bmp.rows)
                mask.paste(ImageChops.lighter(mask.crop(box), glyph), box[:2])
            pen += pos.x_advance / 64


@functools.lru_cache(maxsize=8)
def _font(indic: bool, size: int) -> ShapedFont:
    path, idx = _pick(_INDIC_FONTS if indic else _LATIN_FONTS)
    return ShapedFont(path, idx, size)


def _wrap(font: ShapedFont, text: str, max_w: float) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and font.width(trial) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def render_caption(text: str, out: Path, canvas_w: int = 1080, size: int = 84,
                   fill=(255, 255, 255), highlight=(255, 214, 10), stroke: int = 7) -> tuple[int, int]:
    """Bold white caption with a black outline; the last line is highlighted yellow."""
    indic = bool(_DEVANAGARI.search(text))
    if not indic:
        text = text.upper()
    font = _font(indic, size)
    max_w = canvas_w * 0.86
    lines = _wrap(font, text, max_w)
    pad = stroke * 2 + 8
    line_h = font.line_h * (1.05 if not indic else 1.25)
    w, h = canvas_w, int(line_h * len(lines) + pad * 2)
    rgba = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for li, line in enumerate(lines):
        mask = Image.new("L", (w, h), 0)
        lw = font.width(line)
        font.draw(mask, line, (w - lw) / 2, pad + font.ascender + li * line_h)
        outline = mask.filter(ImageFilter.MaxFilter(stroke * 2 + 1))
        shadow = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        rgba = Image.composite(shadow, rgba, outline)
        color = highlight if (li == len(lines) - 1 and len(lines) > 1) else fill
        rgba = Image.composite(Image.new("RGBA", (w, h), color + (255,)), rgba, mask)
    out.parent.mkdir(parents=True, exist_ok=True)
    rgba.save(out)
    return w, h
