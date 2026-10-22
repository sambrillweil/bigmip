"""Channel colors: parsing user input, reading colors stored in files, and building ImageJ LUTs."""
import re

import numpy as np

NAMED = {
    "red": (1, 0, 0), "green": (0, 1, 0), "blue": (0, 0, 1),
    "cyan": (0, 1, 1), "magenta": (1, 0, 1), "yellow": (1, 1, 0),
    "gray": (1, 1, 1), "grey": (1, 1, 1), "white": (1, 1, 1),
    "orange": (1, 0.5, 0),
}

# Used only when a file stores no usable color: typical color by excitation laser line (nm).
BY_WAVELENGTH = [(420, "blue"), (500, "green"), (580, "orange"), (620, "red"), (10_000, "magenta")]


def parse_color(text):
    """'red', 'magenta', '#ff8000' or 'ff8000' -> (r, g, b) floats in 0..1."""
    t = text.strip().lower()
    if t in NAMED:
        return tuple(float(v) for v in NAMED[t])
    h = t.lstrip("#")
    if re.fullmatch(r"[0-9a-f]{6}", h):
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    raise ValueError(f"unknown color '{text}' (use a name like {', '.join(NAMED)} or a hex code like #ff8000)")


def color_from_wavelength(text):
    """Guess a color from a channel name or wavelength string such as '488_GFP' or '561 nm'."""
    m = re.search(r"(\d{3})", text or "")
    if not m:
        return None
    nm = int(m.group(1))
    for limit, name in BY_WAVELENGTH:
        if nm < limit:
            return NAMED[name]
    return None


def color_from_ome(value, bgr=False):
    """OME-XML Channel Color is a signed 32-bit int. The OME spec says RGBA (0xRRGGBBAA). Andor Fusion / Bitplane
    writers store a Windows-style 0x00BBGGRR value instead; the two cannot be told apart from the number alone, so
    the caller says which layout to use (see TiffStack: decided from the writer named in the OME-XML)."""
    v = int(value) & 0xFFFFFFFF
    if bgr:
        r, g, b = v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF
    else:
        r, g, b = (v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF
    if r == g == b == 0:
        return None
    return (r / 255, g / 255, b / 255)


def parse_color_list(text, names):
    """'--colors' option: either a comma list in channel order ('blue,green,red,magenta') or assignments by channel
    number or name prefix ('1=blue,4=gray' or '405=blue,638=gray'). Returns {channel index: rgb}."""
    out = {}
    items = [s for s in text.split(",") if s.strip()]
    if all("=" not in s for s in items):
        for i, s in enumerate(items):
            out[i] = parse_color(s)
        return out
    for s in items:
        key, col = (p.strip() for p in s.split("=", 1))
        if key.isdigit() and 1 <= int(key) <= len(names):      # small number = channel number (1-based)
            hits = [int(key) - 1]
        else:                                                    # otherwise = start of the channel name, e.g. 405
            hits = [i for i, n in enumerate(names) if n.lower().startswith(key.lower())]
        if not hits:
            raise ValueError(f"--colors: no channel matches '{key}' (channels: {names})")
        for i in hits:
            out[i] = parse_color(col)
    return out


def imagej_lut(rgb):
    """(3, 256) uint8 LUT ramping from black to the given color, as ImageJ expects."""
    ramp = np.arange(256, dtype=np.float64)
    return np.stack([(ramp * c).round().astype(np.uint8) for c in rgb])
