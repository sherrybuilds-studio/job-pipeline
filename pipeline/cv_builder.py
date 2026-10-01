"""One-page CV from the profile: a master copy and a per-job tailored copy.

Layout: A4 with a slim left sidebar (contact, availability, languages, skill
chips) and a main column (headline, projects, experience, education). It stays
readable for an ATS: two platypus frames and no tables, the main column first
in the content stream so a parser reads the headline and projects before the
sidebar, embedded TrueType text, and standard section headings. The sidebar
tint is a canvas rectangle behind the frame; no text lives in it.

Tailoring is honest and mechanical: the project that best matches the
posting's keywords moves first and matching skill chips are highlighted.
Nothing is added. If the page overflows, type shrinks in 3% steps down to a
floor; below it the build fails instead of shipping an unreadable page.

    python -m pipeline cv [output.pdf]      # master CV, default output/<Name>_CV.pdf
"""
import os
import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate, Flowable, Frame, FrameBreak, HRFlowable, PageTemplate, Paragraph, Spacer

from pipeline import config, cv_copy, profile

ACCENT = colors.HexColor("#2d3a8c")
DARK = colors.HexColor("#1a1a2e")
GRAY = colors.HexColor("#555555")
SIDEBAR_BG = colors.HexColor("#eef0f7")
ACCENT_SOFT = colors.HexColor("#c9cee9")   # hairlines, card border, empty language dots
CHIP_BG = colors.HexColor("#e0e4f3")       # skill chips; keyword-matched chips go solid ACCENT

SIDEBAR_W = 5.6 * cm
MARGIN = 1.15 * cm
GUTTER = 0.6 * cm
# DejaVu has the diamond bullet and the contact icons; set CV_FONT_DIR if your
# system keeps it elsewhere. Without it the build falls back to Times and Helvetica.
_FONT_DIR = Path(os.getenv("CV_FONT_DIR") or "/usr/share/fonts/truetype/dejavu")
_MIN_SCALE = 0.86            # refuse to shrink body type below about 8 pt
_SIDEBAR_MIN_SCALE = 0.95    # the sidebar has spare height and never shrinks past 95%
_CONTACT_ICONS = ("⚑", "✉", "☎", "⌂", "⎇")   # location, e-mail, phone, website, code

# Section markers; lint.py splits the extracted text on the sidebar ones.
CONTACT_HEADING = "CONTACT"
SKILLS_HEADING = "TECHNICAL SKILLS"


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _register_fonts() -> tuple[str, str, str, str]:
    """(serif, serif bold, sans, sans bold): DejaVu when present, built-ins otherwise."""
    try:
        for name, file in (("DejaVuSerif", "DejaVuSerif.ttf"), ("DejaVuSerif-Bold", "DejaVuSerif-Bold.ttf"),
                           ("DejaVuSans", "DejaVuSans.ttf"), ("DejaVuSans-Bold", "DejaVuSans-Bold.ttf")):
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(_FONT_DIR / file)))
        # italic is optional: its absence must not knock the family back to the built-ins
        italic = _FONT_DIR / "DejaVuSerif-Italic.ttf"
        if italic.exists() and "DejaVuSerif-Italic" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("DejaVuSerif-Italic", str(italic)))
        return "DejaVuSerif", "DejaVuSerif-Bold", "DejaVuSans", "DejaVuSans-Bold"
    except Exception:  # missing font files: reportlab raises its own error types
        return "Times-Roman", "Times-Bold", "Helvetica", "Helvetica-Bold"


def _styles(scale: float = 1.0) -> dict:
    serif, serif_b, sans, sans_b = _register_fonts()
    base = getSampleStyleSheet()

    def f(pt: float) -> float:
        return round(pt * scale, 2)

    italic = "DejaVuSerif-Italic" if "DejaVuSerif-Italic" in pdfmetrics.getRegisteredFontNames() else serif
    return {
        "name": ParagraphStyle("Name", parent=base["Normal"], fontName=serif_b, fontSize=f(20),
                               leading=f(23), textColor=DARK, spaceAfter=1),
        "tagline": ParagraphStyle("Tagline", parent=base["Normal"], fontName=italic,
                                  fontSize=f(10.4), leading=f(13.8), textColor=GRAY, spaceAfter=2),
        "section": ParagraphStyle("Section", parent=base["Normal"], fontName=serif_b, fontSize=f(12),
                                  leading=f(14.5), textColor=DARK, spaceBefore=8, spaceAfter=0),
        "sbsection": ParagraphStyle("SbSection", parent=base["Normal"], fontName=sans_b, fontSize=f(7.8),
                                    leading=f(10), textColor=ACCENT, spaceBefore=10, spaceAfter=0),
        "sblabel": ParagraphStyle("SbLabel", parent=base["Normal"], fontName=sans_b, fontSize=f(8.1),
                                  leading=f(10.6), textColor=DARK, spaceBefore=4, spaceAfter=2.5),
        # availability sits in a white card: the facts a recruiter scans for first
        "sbcard": ParagraphStyle("SbCard", parent=base["Normal"], fontName=sans, fontSize=f(8.4),
                                 leading=f(11.8), textColor=DARK, backColor=colors.white,
                                 borderColor=ACCENT_SOFT, borderWidth=0.7, borderRadius=3,
                                 borderPadding=(5, 6, 5, 6), leftIndent=6, rightIndent=6,
                                 spaceBefore=7, spaceAfter=7),
        "body": ParagraphStyle("Body", parent=base["Normal"], fontName=sans, fontSize=f(8.9),
                               leading=f(12), textColor=DARK, spaceAfter=1.5),
        "meta": ParagraphStyle("Meta", parent=base["Normal"], fontName=sans, fontSize=f(8.2),
                               leading=f(10.8), textColor=GRAY, spaceAfter=3),
        "proj": ParagraphStyle("Proj", parent=base["Normal"], fontName=sans_b, fontSize=f(9.6),
                               leading=f(12.5), textColor=DARK, spaceBefore=3.5, spaceAfter=0.5,
                               leftIndent=10, firstLineIndent=-10),
        "sb": ParagraphStyle("Sb", parent=base["Normal"], fontName=sans, fontSize=f(8.4),
                             leading=f(11.4), textColor=DARK, spaceAfter=1.2),
        # one notch smaller so the e-mail never wraps mid-token (a split address
        # is unreadable to a parser); the icon is the bullet, in a fixed column
        "sbcontact": ParagraphStyle("SbContact", parent=base["Normal"], fontName=sans, fontSize=f(7.9),
                                    leading=f(11.2), textColor=DARK, spaceAfter=1.4, leftIndent=13,
                                    bulletIndent=0, bulletFontName=sans, bulletFontSize=f(8.4),
                                    bulletColor=ACCENT),
        "_sans": sans, "_sans_b": sans_b, "_serif": serif,
    }


def _project_rank(keywords: list[str]) -> list[dict]:
    """Projects ordered by keyword overlap (description and stack), stable otherwise."""
    kws = [k.lower() for k in keywords or []]
    projects = list(profile.get()["projects"])

    def hits(proj: dict) -> int:
        hay = (proj["description"] + " " + " ".join(proj["stack"])).lower()
        return sum(1 for k in kws if k in hay)

    return sorted(projects, key=hits, reverse=True) if kws else projects


def _skill_hit(name: str, kws: list[str]) -> bool:
    """A job keyword matches a skill by its name or its profile description, on
    whole-word tokens, so "sql" lights SQL and never a skill that merely
    contains the letters."""
    hay = f"{name} {profile.get()['core_skills'].get(name, '')}".lower()
    for k in kws:
        for tok in re.split(r"[\s/,()+]+", k.lower()):
            if (len(tok) >= 3 or tok == "ai") and re.search(rf"\b{re.escape(tok)}\b", hay):
                return True
    return False


def skill_groups(keywords: list[str]) -> list[tuple[str, list[str]]]:
    """Within each group, matched skills first. Nothing added, nothing removed."""
    kws = [k.lower() for k in keywords or []]
    out = []
    for label, names in cv_copy.skill_groups():
        if kws:
            names = sorted(names, key=lambda n: not _skill_hit(n, kws))
        out.append((label, names))
    return out


def _diamond(s: dict) -> str:
    return f"<font name='{s['_sans']}' color='{ACCENT.hexval()}'>◆</font>&nbsp;"


def _section(title: str, s: dict) -> list:
    """Serif heading with a short accent rule beneath it."""
    return [Paragraph(_esc(title), s["section"]),
            HRFlowable(width=1.5 * cm, thickness=1.6, color=ACCENT, hAlign="LEFT", spaceBefore=1.5, spaceAfter=4)]


def _badge(text: str, s: dict) -> str:
    """The project's one number as a small accent-coloured badge."""
    return (f"&nbsp;&nbsp;<font name='{s['_sans']}' size='{s['proj'].fontSize * 0.82:.1f}' "
            f"color='{ACCENT.hexval()}'>{_esc(text)}</font>")


def _nb(text: str) -> str:
    """Non-breaking spaces, so a date range never splits across two lines."""
    return text.replace(" ", " ")


def _repo_link(proj: dict, lead: str = " · code: ") -> str:
    """The repository as a link: the full URL lives in the href, not in the line."""
    code_url = profile.get()["code_url"]
    if not proj["repo"] and not proj["repo_url"]:
        return ""
    url = proj["repo_url"] or (f"https://{code_url}/{proj['repo']}" if code_url else "")
    if not url:
        return ""
    if proj["repo_lead"]:  # a public write-up rather than code
        lead = lead.replace("code", proj["repo_lead"]).replace("Code", proj["repo_lead"].capitalize())
    label = proj["repo_label"] or proj["repo"] or url
    return f"{lead}<a href='{url}' color='{ACCENT.hexval()}'>{_esc(label)}</a>"


def _main_column(keywords: list[str], s: dict) -> list:
    p = profile.get()
    out = [Paragraph(_esc(p["name"]), s["name"]), Paragraph(_esc(cv_copy.headline()), s["tagline"])]
    ranked = _project_rank(keywords)
    featured = cv_copy.featured()
    out += _section(cv_copy.projects_heading(), s)
    for proj in ranked[:featured]:
        copy = cv_copy.project_copy(proj)
        title = _diamond(s) + f"<b>{_esc(copy['name'])}</b>" + (_badge(copy["badge"], s) if copy["badge"] else "")
        out.append(Paragraph(title, s["proj"]))
        out.append(Paragraph(_esc(copy["text"]), s["body"]))
        out.append(Paragraph(_esc(", ".join(proj["stack"])) + _repo_link(proj), s["meta"]))
    if ranked[featured:]:
        out += _section(cv_copy.more_heading(), s)
    for proj in ranked[featured:]:
        copy = cv_copy.project_copy(proj)
        title = _diamond(s) + f"<b>{_esc(copy['name'])}</b>" + (_badge(copy["badge"], s) if copy["badge"] else "")
        out.append(Paragraph(title, s["proj"]))
        out.append(Paragraph(_esc(copy["short"]) + _repo_link(proj, " Code: "), s["body"]))

    if p["experience"]:
        out += _section("Experience", s)
    for e in p["experience"]:
        period, detail = cv_copy.experience_copy(e)
        out.append(Paragraph(
            _diamond(s) + f"<b>{_esc(e['role'])}</b>, {_esc(e['company'])} "
            f"<font name='{s['_sans']}' color='{GRAY.hexval()}'>  {_esc(_nb(period))}</font>", s["proj"]))
        if detail:
            out.append(Paragraph(_esc(detail), s["body"]))

    out += _section("Education", s)
    edu = p["education"]
    out.append(Paragraph(_diamond(s) + f"<b>{_esc(edu['degree'])}</b>, {_esc(edu['school'])}", s["proj"]))
    if cv_copy.education_line():
        out.append(Paragraph(_esc(cv_copy.education_line()), s["body"]))
    return out


def _sb_head(title: str, s: dict) -> list:
    """Small accent label with a hairline beneath: the sidebar's section marker."""
    return [Paragraph(title, s["sbsection"]),
            HRFlowable(width="100%", thickness=0.6, color=ACCENT_SOFT, hAlign="LEFT", spaceBefore=1.5, spaceAfter=4.5)]


def _lang_dots(level: str) -> int:
    """CEFR-style level text → filled dots out of five."""
    low = level.lower()
    if any(t in low for t in ("native", "c2")):
        return 5
    for tag, n in (("c1", 4), ("b2", 4), ("b1", 3), ("a2", 2), ("a1", 1)):
        if tag in low:
            return n
    return 5 if "fluent" in low else 3


class _LangRow(Flowable):
    """Language name, five proficiency dots right-aligned, the level beneath.
    Drawn as real text, so a parser still reads "English C1, fluent"."""

    def __init__(self, name: str, level: str, s: dict):
        super().__init__()
        self.name, self.level, self.s = name, level, s
        self.size = s["sb"].fontSize

    def wrap(self, aw, ah):
        self.width, self.height = aw, self.size * 2.5 + 2
        return aw, self.height

    def draw(self):
        c, s, sz = self.canv, self.s, self.size
        top = self.height - sz
        c.setFillColor(DARK)
        c.setFont(s["_sans_b"], sz)
        c.drawString(0, top, self.name + " ")
        step = sz * 0.98
        c.setFont(s["_sans"], sz * 0.92)
        x0 = self.width - 5 * step
        filled = _lang_dots(self.level)
        for i in range(5):
            c.setFillColor(ACCENT if i < filled else ACCENT_SOFT)
            c.drawString(x0 + i * step, top + sz * 0.04, "●")
        c.setFillColor(GRAY)
        c.setFont(s["_sans"], sz * 0.93)
        c.drawString(0, top - sz * 1.3, self.level)


class _Chips(Flowable):
    """Skill names as rounded chips that wrap inside the sidebar. Each label is
    drawn as text with a trailing space, so extraction reads the plain list.
    Chips that match the job's keywords are solid accent, the rest a soft tint."""

    PAD_X, GAP, ROW_GAP = 4.2, 3.0, 3.2

    def __init__(self, names: list[str], matched: set[str], s: dict):
        super().__init__()
        self.names, self.matched, self.s = names, matched, s
        self.size = s["sb"].fontSize * 0.92
        self.ch = self.size + 5.4
        self.rows: list[list[tuple[str, float, float]]] = []

    def wrap(self, aw, ah):
        rows, row, x = [], [], 0.0
        for n in self.names:
            w = pdfmetrics.stringWidth(n, self.s["_sans"], self.size) + 2 * self.PAD_X
            if row and x + w > aw:
                rows.append(row)
                row, x = [], 0.0
            row.append((n, x, w))
            x += w + self.GAP
        if row:
            rows.append(row)
        self.rows, self.width = rows, aw
        self.height = len(rows) * (self.ch + self.ROW_GAP) - self.ROW_GAP
        return aw, self.height

    def draw(self):
        c = self.canv
        c.setFont(self.s["_sans"], self.size)
        y = self.height - self.ch
        for row in self.rows:
            for name, x, w in row:
                hot = name in self.matched
                c.setFillColor(ACCENT if hot else CHIP_BG)
                c.roundRect(x, y, w, self.ch, 2.6, stroke=0, fill=1)
                c.setFillColor(colors.white if hot else DARK)
                c.drawString(x + self.PAD_X, y + (self.ch - self.size) / 2 + self.size * 0.16, name + " ")
            y -= self.ch + self.ROW_GAP


def _sidebar_column(keywords: list[str], s: dict) -> list:
    p = profile.get()
    kws = [k.lower() for k in keywords or []]
    out = [Spacer(1, 4)] + _sb_head(CONTACT_HEADING, s)
    phone_href = "tel:" + re.sub(r"[^\d+]", "", p["phone"]) if p["phone"] else None
    contact = ((p["location"], None), (p["email"], f"mailto:{p['email']}"), (p["phone"], phone_href),
               (p["website"], f"https://{p['website']}" if p["website"] else None),
               (p["code_url"], f"https://{p['code_url']}" if p["code_url"] else None))
    icons = _CONTACT_ICONS if s["_sans"] == "DejaVuSans" else ("",) * len(_CONTACT_ICONS)
    for (line, href), icon in zip(contact, icons, strict=True):
        if not line:
            continue
        text = _esc(line)
        if href:
            text = f"<a href='{href}' color='{DARK.hexval()}'>{text}</a>"
        out.append(Paragraph(text, s["sbcontact"], bulletText=icon))

    lines = cv_copy.availability_lines()
    if lines:
        out += _sb_head("AVAILABILITY", s)
        card = [f"<b>{_esc(lines[0])}</b>"]
        card += [f"<font color='{GRAY.hexval()}'>{_esc(line)}</font>" for line in lines[1:-1]]
        if len(lines) > 1:
            card.append(re.sub(r"up to (.+)$", r"up to <b>\1</b>", _esc(lines[-1])))
        out.append(Paragraph("<br/>".join(card), s["sbcard"]))

    out += _sb_head("LANGUAGES", s)
    for lang, level in p["languages"].items():
        out.append(_LangRow(lang, cv_copy.language_level(level), s))

    out += _sb_head(SKILLS_HEADING, s)
    for label, names in skill_groups(keywords):
        out.append(Paragraph(_esc(label), s["sblabel"]))
        out.append(_Chips([cv_copy.skill_label(n) for n in names],
                          {cv_copy.skill_label(n) for n in names if kws and _skill_hit(n, kws)}, s))
    return out


def _doc(output_path: str) -> BaseDocTemplate:
    width, height = A4
    main_x = MARGIN + SIDEBAR_W + GUTTER
    main = Frame(main_x, MARGIN, width - main_x - MARGIN, height - 2 * MARGIN, id="main",
                 leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    side = Frame(MARGIN, MARGIN, SIDEBAR_W, height - 2 * MARGIN, id="sidebar",
                 leftPadding=8, rightPadding=8, topPadding=6, bottomPadding=6)

    def paint(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(SIDEBAR_BG)
        canvas.rect(MARGIN, MARGIN, SIDEBAR_W, height - 2 * MARGIN, stroke=0, fill=1)
        canvas.setFillColor(ACCENT)
        canvas.rect(MARGIN, MARGIN, 3, height - 2 * MARGIN, stroke=0, fill=1)  # accent stripe
        canvas.setStrokeColor(ACCENT)
        canvas.setLineWidth(1.4)
        canvas.line(main_x, height - MARGIN, width - MARGIN, height - MARGIN)
        canvas.restoreState()

    name = profile.get()["name"]
    doc = BaseDocTemplate(output_path, pagesize=A4, title=f"{name}, CV", author=name,
                          leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN)
    doc.addPageTemplates([PageTemplate(id="cv", frames=[main, side], onPage=paint)])
    return doc


_PAGE_OBJ = re.compile(rb"/Type\s*/Page\b(?!s)")


def page_count(path: str) -> int:
    """Pages in a reportlab PDF without a third-party reader: reportlab writes one
    uncompressed "/Type /Page" object per page. Relying on an optional PDF
    library here would silently disable the one-page guard wherever it is missing."""
    return len(_PAGE_OBJ.findall(Path(path).read_bytes())) or 1


def build(output_path: str, keywords: list[str]) -> str:
    """Render one page, shrinking type in 3% steps down to the floor."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    scale = 1.0
    while True:
        s = _styles(scale)
        side = s if scale >= _SIDEBAR_MIN_SCALE else _styles(_SIDEBAR_MIN_SCALE)
        story = _main_column(keywords, s) + [FrameBreak()] + _sidebar_column(keywords, side)
        _doc(output_path).build(story)
        pages = page_count(output_path)
        if pages == 1:
            return output_path
        scale = round(scale - 0.03, 2)
        if scale < _MIN_SCALE:
            raise RuntimeError(f"CV does not fit one page even at {scale + 0.03:.2f} scale ({pages} pages): "
                               "shorten the profile's CV copy")


def build_cv(output_path: str | None = None) -> str:
    output_path = output_path or str(config.OUTPUT_DIR / profile.cv_filename())
    build(output_path, [])
    return output_path


def build_tailored_cv(job: dict, keywords: list[str], output_path: str | None = None) -> str:
    """Per-job CV: matching project and skills first. Same facts, same page."""
    if output_path is None:
        slug = re.sub(r"[^a-z0-9]+", "-", f"{job.get('company', 'unknown')}-{job.get('title', 'role')}".lower())
        output_path = str(config.APPS_DIR / slug.strip("-")[:60] / profile.cv_filename())
    return build(output_path, keywords)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = build_cv(argv[0] if argv else None)
    print(f"CV written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
