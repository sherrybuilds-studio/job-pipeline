"""Render a cover letter as a one-page A4 PDF in the CV's design family.

Same serif and sans pairing and accent rule as the CV, in a single column:
name header with two contact lines, the date, the recipient, a bold subject
line, then the letter body as plain paragraphs. Real text layer, no tables,
no images, so an ATS reads it exactly as written.

    python -m pipeline.letter_pdf <letter.txt> <out.pdf>
"""
import re
import sys
from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

from pipeline import profile
from pipeline.cv_builder import ACCENT, DARK, GRAY, _esc, _register_fonts, page_count

_MIN_SCALE = 0.86


def date_line(on: date | None = None) -> str:
    """"Berlin, 28 September 2026": the search city and today's date."""
    on = on or date.today()
    return f"{profile.city()}, {on.day} {on.strftime('%B %Y')}"


def _styles(scale: float) -> dict:
    serif, serif_b, sans, sans_b = _register_fonts()

    def f(pt: float) -> float:
        return round(pt * scale, 2)

    return {
        "name": ParagraphStyle("LName", fontName=serif_b, fontSize=f(18), leading=f(21), textColor=DARK),
        "contact": ParagraphStyle("LContact", fontName=sans, fontSize=f(8.6), leading=f(11.5), textColor=GRAY),
        "date": ParagraphStyle("LDate", fontName=sans, fontSize=f(9.4), leading=f(13), textColor=GRAY,
                               spaceBefore=8, spaceAfter=f(14), alignment=2),
        "recipient": ParagraphStyle("LRecipient", fontName=sans, fontSize=f(10.2), leading=f(14.4),
                                    textColor=DARK, spaceAfter=f(16)),
        "subject": ParagraphStyle("LSubject", fontName=sans_b, fontSize=f(10.8), leading=f(14.6),
                                  textColor=DARK, spaceAfter=f(12)),
        "body": ParagraphStyle("LBody", fontName=sans, fontSize=f(10.4), leading=f(15.8), textColor=DARK,
                               spaceAfter=f(9)),
        "sign": ParagraphStyle("LSign", fontName=serif_b, fontSize=f(10.6), leading=f(14), textColor=DARK),
    }


def _story(letter: str, when: str, s: dict, recipient: str = "", subject: str = "") -> list:
    p = profile.get()
    first = " · ".join(_esc(x) for x in (p["location"], p["email"], p["phone"]) if x)
    second = " · ".join(_esc(x) for x in (p["website"], p["code_url"]) if x)
    out = [Paragraph(_esc(p["name"]), s["name"]),
           # two fixed lines: a single one would wrap in the middle of a URL
           Paragraph(first + (f"<br/>{second}" if second else ""), s["contact"]),
           HRFlowable(width="100%", thickness=1.2, color=ACCENT, spaceBefore=5, spaceAfter=2),
           Paragraph(_esc(when), s["date"])]
    if recipient:
        out.append(Paragraph("<br/>".join(_esc(line) for line in recipient.split("\n")), s["recipient"]))
    if subject:
        out.append(Paragraph(_esc(subject), s["subject"]))
    paras = [x.strip() for x in re.split(r"\n\s*\n", letter.strip()) if x.strip()]
    for i, para in enumerate(paras):
        if i == len(paras) - 1 and "\n" in para:  # "Kind regards,\nName": keep the line break
            a, b = para.split("\n", 1)
            out += [Spacer(1, 4), Paragraph(_esc(a), s["body"]), Paragraph(_esc(b.strip()), s["sign"])]
        else:
            out.append(Paragraph(_esc(para).replace("\n", " "), s["body"]))
    return out


def render(letter: str, output_path: str, date_line_text: str | None = None, recipient: str = "",
           subject: str = "") -> str:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    when = date_line_text or date_line()
    name = profile.get()["name"]
    scale = 1.0
    while True:
        doc = SimpleDocTemplate(output_path, pagesize=A4, topMargin=1.8 * cm, bottomMargin=1.8 * cm,
                                leftMargin=2.2 * cm, rightMargin=2.2 * cm, title=f"{name}, cover letter", author=name)
        doc.build(_story(letter, when, _styles(scale), recipient, subject))
        if page_count(output_path) == 1:
            return output_path
        scale = round(scale - 0.03, 2)
        if scale < _MIN_SCALE:
            raise RuntimeError(f"letter does not fit one page at {scale + 0.03:.2f} scale")


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    render(Path(src).read_text(encoding="utf-8"), dst)
    print(f"Letter written to {dst}")
