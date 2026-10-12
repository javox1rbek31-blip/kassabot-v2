# -*- coding: utf-8 -*-
"""PDF hisobotlar (faqat tugma bosilganda). reportlab + DejaVu shrifti (kirill uchun)."""
import io
import os
import datetime as dt
import urllib.request
from xml.sax.saxutils import escape

import logic as L

_HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.getenv("FONT_DIR", os.path.join(os.path.dirname(os.path.abspath(L.DB_PATH)), "fonts"))
FONT_URLS = [
    "https://raw.githubusercontent.com/matplotlib/matplotlib/main/lib/matplotlib/mpl-data/fonts/ttf/",
    "https://cdn.jsdelivr.net/gh/matplotlib/matplotlib@main/lib/matplotlib/mpl-data/fonts/ttf/",
]
_ready = False


def _find_font(name):
    paths = [
        os.path.join(_HERE, name),
        os.path.join(FONT_DIR, name),
        "/usr/share/fonts/truetype/dejavu/" + name,
        "/usr/share/fonts/dejavu/" + name,
        "/usr/share/fonts/TTF/" + name,
    ]
    try:
        import matplotlib
        paths.append(os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf", name))
    except Exception:
        pass
    for p in paths:
        if os.path.isfile(p):
            return p
    os.makedirs(FONT_DIR, exist_ok=True)
    dest = os.path.join(FONT_DIR, name)
    last = None
    for base in FONT_URLS:
        try:
            with urllib.request.urlopen(base + name, timeout=30) as resp:
                data = resp.read()
            if len(data) < 100000:
                raise ValueError("shrift fayli noto'g'ri")
            with open(dest, "wb") as f:
                f.write(data)
            return dest
        except Exception as e:
            last = e
    raise RuntimeError(f"Не удалось загрузить шрифт {name}: {last}. "
                       f"Добавьте строку matplotlib в requirements.txt")


def _setup():
    global _ready
    if _ready:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    pdfmetrics.registerFont(TTFont("DJ", _find_font("DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DJB", _find_font("DejaVuSans-Bold.ttf")))
    _ready = True


def _st():
    from reportlab.lib.styles import ParagraphStyle
    return {
        "h": ParagraphStyle("h", fontName="DJB", fontSize=15, leading=19, spaceAfter=4),
        "p": ParagraphStyle("p", fontName="DJ", fontSize=10, leading=14),
        "b": ParagraphStyle("b", fontName="DJB", fontSize=11, leading=15),
        "c": ParagraphStyle("c", fontName="DJ", fontSize=8, leading=10),
        "cb": ParagraphStyle("cb", fontName="DJB", fontSize=8, leading=10),
    }


def _p(t, s):
    from reportlab.platypus import Paragraph
    return Paragraph(escape(str(t)), s)


def _table(data, widths):
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib import colors
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF7")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def _build(story):
    from reportlab.platypus import SimpleDocTemplate
    from reportlab.lib.pagesizes import A4
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=30, rightMargin=30,
                      topMargin=30, bottomMargin=30).build(story)
    return buf.getvalue()


def _footer(st):
    return _p("Сформировано: " + dt.datetime.now(L.TZ).strftime("%d.%m.%Y %H:%M"), st["c"])


def _worker_block(story, r, st):
    from reportlab.platypus import Spacer
    story.append(_p(r["w"]["name"], st["b"]))
    story.append(Spacer(1, 4))
    head = ["Дата", "Рд", "Чеки", "Касса", "По прайсу $", "%", "Зп $"]
    rows = [[_p(h, st["cb"]) for h in head]]
    prices = {}
    for d in r["days"]:
        am = [a for a, _ in d["items"]]
        for a, u in d["items"]:
            prices.setdefault(a, u)
        rows.append([
            _p(L.dshort(d["day"]), st["c"]),
            _p(len(am), st["c"]),
            _p(", ".join(map(str, am)), st["c"]),
            _p(L.n3(d["raw"]), st["c"]),
            _p(L.money(d["usd"]), st["c"]),
            _p(L.money(d["pct"]), st["c"]),
            _p(L.money(d["usd"] * d["pct"] / 100), st["c"]),
        ])
    if not r["days"]:
        rows.append([_p("Чеков нет", st["c"])] + [_p("", st["c"])] * 6)
    story.append(_table(rows, [42, 28, 240, 52, 52, 30, 45]))
    if prices:
        story.append(Spacer(1, 4))
        story.append(_p("Прайс: " + ", ".join(f"{a} = {L.money(u)}$" for a, u in sorted(prices.items())), st["c"]))
    story.append(Spacer(1, 6))
    story.append(_p(f"Зп: {L.money(r['zp'])}$", st["p"]))
    for line in L.adj_lines(r):
        story.append(_p(line, st["p"]))
    story.append(Spacer(1, 12))


def worker_pdf(r, lab):
    """Bitta ishchi uchun PDF. Ega ulushi YO'Q (ishchiga yuborish mumkin)."""
    _setup()
    from reportlab.platypus import Spacer
    st = _st()
    story = [_p(f"Отчёт: {r['w']['name']}", st["h"]), _p(f"Период: {lab}", st["p"]), Spacer(1, 10)]
    _worker_block(story, r, st)
    story.append(_footer(st))
    return _build(story)


def all_pdf(res, lab, owner=False):
    """Umumiy PDF. owner=True bo'lsa Cashliy va 'Ваш доход' ham chiqadi."""
    _setup()
    from reportlab.platypus import Spacer, PageBreak
    st = _st()
    rows_res = [r for r in res if L.has_data(r) and (owner or not r["w"]["is_self"])]
    story = [_p("Общий отчёт", st["h"]), _p(f"Период: {lab}", st["p"]), Spacer(1, 8)]
    if not rows_res:
        story.append(_p("За этот период отчётов нет.", st["p"]))
        story.append(Spacer(1, 10))
        story.append(_footer(st))
        return _build(story)

    head = ["Обработчик", "Рд", "Касса", "Касса $", "Зп", "Штраф", "Бонус", "Аванс", "К выплате"]
    rows = [[_p(h, st["cb"]) for h in head]]
    tot = dict(n=0, raw=0, usd=0.0, zp=0.0, fine=0.0, bonus=0.0, adv=0.0, total=0.0)
    for r in rows_res:
        rows.append([
            _p(r["w"]["name"], st["c"]), _p(r["n"], st["c"]), _p(L.n3(r["raw"]), st["c"]),
            _p(L.money(r["usd"]), st["c"]), _p(L.money(r["zp"]), st["c"]),
            _p(L.money(r["fine"]) if r["fine"] else "", st["c"]),
            _p(L.money(r["bonus"]) if r["bonus"] else "", st["c"]),
            _p(L.money(r["adv"]) if r["adv"] else "", st["c"]),
            _p(L.money(r["total"]), st["c"]),
        ])
        for k in tot:
            tot[k] += r[k]
    rows.append([
        _p("ИТОГО", st["cb"]), _p(tot["n"], st["cb"]), _p(L.n3(tot["raw"]), st["cb"]),
        _p(L.money(tot["usd"]), st["cb"]), _p(L.money(tot["zp"]), st["cb"]),
        _p(L.money(tot["fine"]) if tot["fine"] else "", st["cb"]),
        _p(L.money(tot["bonus"]) if tot["bonus"] else "", st["cb"]),
        _p(L.money(tot["adv"]) if tot["adv"] else "", st["cb"]),
        _p(L.money(tot["total"]), st["cb"]),
    ])
    story.append(_table(rows, [86, 34, 62, 50, 48, 46, 42, 42, 60]))

    if owner:
        inc = L.owner_income(res)
        story += [Spacer(1, 14), _p("Ваш доход (только для владельца)", st["b"]), Spacer(1, 4)]
        for line in L.owner_lines(inc):
            story.append(_p(line, st["p"]) if line else Spacer(1, 4))
    story.append(PageBreak())

    for r in rows_res:
        _worker_block(story, r, st)
    story.append(_footer(st))
    return _build(story)
