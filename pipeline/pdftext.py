"""Plain text out of a CV or letter PDF, with no third-party reader.

The lint reads the files that are actually on disk, so it needs the text of a
finished PDF, not the strings that built it, and CI must be able to run it
without extra tools. This module carries its own reader for the one producer
the pipeline has, reportlab: content streams filtered with FlateDecode and
ASCII85Decode, single-byte TrueType subsets with a ToUnicode CMap, and the
built-in Type1 fonts (WinAnsi).

The built-in reader goes first because it returns the text in drawing order
(main column, then sidebar), which lint.py relies on; a layout-mode external
reader would interleave the two columns. pypdf and pdftotext are only a
fallback for PDFs from other producers.

    extract_text(path) -> str      # one line per text run
"""
import base64
import re
import shutil
import subprocess
import zlib
from pathlib import Path

_OBJ = re.compile(rb"(\d+)\s+0\s+obj\s*(.*?)\s*endobj", re.S)
# reportlab ends an ASCII85 stream with "~>endstream", no newline before the keyword
_STREAM = re.compile(rb"^(.*?)\bstream\r?\n(.*?)\r?\n?endstream\s*$", re.S)
_FILTERS = re.compile(rb"/Filter\s*(?:\[([^\]]*)\]|/(\w+))")
_FONT_RES = re.compile(rb"/Font\s*(?:<<(.*?)>>|(\d+)\s+0\s+R)", re.S)
_FONT_REF = re.compile(rb"/(F[\w+]+)\s+(\d+)\s+0\s+R")
_TOUNICODE = re.compile(rb"/ToUnicode\s+(\d+)\s+0\s+R")
_BFCHAR = re.compile(rb"beginbfchar(.*?)endbfchar", re.S)
_BFRANGE = re.compile(rb"beginbfrange(.*?)endbfrange", re.S)
_HEX = re.compile(rb"<([0-9A-Fa-f]+)>")
_KIDS = re.compile(rb"/Contents\s+(?:\[([^\]]*)\]|(\d+)\s+0\s+R)")
_REF = re.compile(rb"(\d+)\s+0\s+R")
# text-showing operators in a decoded content stream: (string) Tj, [(a) -20 (b)] TJ, /F1 9 Tf, T*, ', "
_TOKEN = re.compile(rb"\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]*>|/[^\s/\[\]()<>]+|\[|\]|[^\s()<>\[\]/]+", re.S)


def extract_text(path: str | Path) -> str:
    try:
        text = builtin_extract(path)
    except (OSError, ValueError, zlib.error):
        text = ""
    if text.strip():
        return text
    for mod in ("pypdf", "PyPDF2"):
        try:
            reader = __import__(mod).PdfReader(str(path))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            if text.strip():
                return text
        except Exception:  # optional readers: any failure falls through to the next one
            continue
    if shutil.which("pdftotext"):
        try:
            r = subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True, timeout=20)
            if r.returncode == 0:
                return r.stdout
        except (OSError, subprocess.SubprocessError):
            pass
    return ""


# ─── the built-in reader ─────────────────────────────────────────────────────

def _decode_stream(head: bytes, data: bytes) -> bytes:
    m = _FILTERS.search(head)
    names = re.findall(rb"/(\w+)", m.group(1)) if m and m.group(1) is not None else ([m.group(2)] if m else [])
    for name in names:
        if name == b"ASCII85Decode":
            body = data.strip()
            if body.startswith(b"<~"):
                body = body[2:]
            if body.endswith(b"~>"):
                body = body[:-2]
            data = base64.a85decode(re.sub(rb"\s", b"", body))
        elif name == b"FlateDecode":
            data = zlib.decompress(data)
        else:
            raise ValueError(f"unsupported PDF filter {name!r}")
    return data


def _objects(raw: bytes) -> dict[int, tuple[bytes, bytes | None]]:
    """object number → (dictionary bytes, decoded stream or None)."""
    out: dict[int, tuple[bytes, bytes | None]] = {}
    for num, body in _OBJ.findall(raw):
        m = _STREAM.match(body)
        if m:
            try:
                out[int(num)] = (m.group(1), _decode_stream(m.group(1), m.group(2)))
            except (ValueError, zlib.error):
                out[int(num)] = (m.group(1), None)
        else:
            out[int(num)] = (body, None)
    return out


def _cmap(stream: bytes) -> dict[int, str]:
    """ToUnicode CMap → code → text (bfchar and bfrange, single-byte codes)."""
    table: dict[int, str] = {}

    def uni(hexs: bytes) -> str:
        return bytes.fromhex(hexs.decode()).decode("utf-16-be", errors="replace")

    for block in _BFCHAR.findall(stream):
        items = _HEX.findall(block)
        for src, dst in zip(items[0::2], items[1::2], strict=False):
            table[int(src, 16)] = uni(dst)
    for block in _BFRANGE.findall(stream):
        for line in block.strip().splitlines():
            items = _HEX.findall(line)
            if len(items) < 3:
                continue
            lo, hi, dst = int(items[0], 16), int(items[1], 16), items[2]
            base = bytes.fromhex(dst.decode())
            for i, code in enumerate(range(lo, hi + 1)):
                last = (int.from_bytes(base[-2:], "big") + i).to_bytes(2, "big")
                table[code] = (base[:-2] + last).decode("utf-16-be", errors="replace")
    return table


def _fonts(objects: dict, page_head: bytes) -> dict[bytes, dict[int, str] | None]:
    """Resource name → CMap, or None for a WinAnsi Type1 font."""
    fonts: dict[bytes, dict[int, str] | None] = {}
    res = _FONT_RES.search(page_head)
    if not res:
        return fonts
    # the font dictionary is inline (<< /F1 2 0 R >>) or an indirect object (/Font 1 0 R)
    table = res.group(1) if res.group(1) is not None else objects.get(int(res.group(2)), (b"", None))[0]
    for name, ref in _FONT_REF.findall(table):
        head, _ = objects.get(int(ref), (b"", None))
        tu = _TOUNICODE.search(head)
        stream = objects.get(int(tu.group(1)), (b"", None))[1] if tu else None
        fonts[name] = _cmap(stream) if stream else None
    return fonts


def _unescape(literal: bytes) -> bytes:
    out, i = bytearray(), 0
    while i < len(literal):
        c = literal[i]
        if c == 0x5C and i + 1 < len(literal):  # backslash
            n = literal[i + 1]
            if n in b"nrtbf":
                out.append({ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}[n])
                i += 2
            elif 0x30 <= n <= 0x37:
                j = i + 1
                while j < len(literal) and j < i + 4 and 0x30 <= literal[j] <= 0x37:
                    j += 1
                out.append(int(literal[i + 1:j], 8) & 0xFF)
                i = j
            else:
                out.append(n)
                i += 2
        else:
            out.append(c)
            i += 1
    return bytes(out)


def _decode_text(raw: bytes, cmap: dict[int, str] | None) -> str:
    if cmap is None:
        return raw.decode("cp1252", errors="replace")
    return "".join(cmap.get(b, "") for b in raw)


def _show(token: bytes, cmap: dict[int, str] | None) -> str:
    if token.startswith(b"<"):
        return _decode_text(bytes.fromhex(re.sub(rb"\s", b"", token[1:-1]).decode()), cmap)
    return _decode_text(_unescape(token[1:-1]), cmap)


def _runs(content: bytes, fonts: dict) -> list[str]:
    """Text runs in stream order; a new run per Tj or TJ, joined later by line."""
    runs: list[str] = []
    stack: list[bytes] = []
    cmap: dict[int, str] | None = None
    in_array: list[bytes] | None = None
    for tok in _TOKEN.findall(content):
        if tok == b"[":
            in_array = []
        elif tok == b"]":
            pass
        elif tok.startswith(b"(") or tok.startswith(b"<"):
            (in_array if in_array is not None else stack).append(tok)
        elif tok.startswith(b"/"):
            stack.append(tok)
        elif tok == b"Tf":
            names = [s for s in stack if s.startswith(b"/")]
            cmap = fonts.get(names[-1][1:]) if names else None
            stack.clear()
        elif tok in (b"Tj", b"'", b'"'):
            runs.extend(_show(s, cmap) for s in stack if s.startswith(b"(") or s.startswith(b"<"))
            stack.clear()
        elif tok == b"TJ":
            runs.append("".join(_show(s, cmap) for s in (in_array or []) if s.startswith(b"(") or s.startswith(b"<")))
            in_array = None
            stack.clear()
        elif tok in (b"T*", b"Td", b"TD", b"Tm", b"ET"):
            runs.append("\n")
            stack.clear()
        else:
            stack.append(tok)
    return runs


def builtin_extract(path: str | Path) -> str:
    raw = Path(path).read_bytes()
    objects = _objects(raw)
    lines: list[str] = []
    for _num, (head, _) in sorted(objects.items()):
        if not re.search(rb"/Type\s*/Page\b(?!s)", head):
            continue
        fonts = _fonts(objects, head)
        m = _KIDS.search(head)
        if not m:
            continue
        refs = _REF.findall(m.group(1)) if m.group(1) is not None else [m.group(2)]
        for ref in refs:
            content = objects.get(int(ref), (b"", None))[1]
            if content is None:
                continue
            buf = ""
            for run in _runs(content, fonts):
                if run == "\n":
                    if buf.strip():
                        lines.append(buf.strip())
                    buf = ""
                else:
                    buf += run
            if buf.strip():
                lines.append(buf.strip())
    return "\n".join(lines) + ("\n" if lines else "")
