# -*- coding: utf-8 -*-
"""
PDF Generator Module
"""
import io
from xml.sax.saxutils import escape
import logic as L

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

_ready = False

def _setup():
    global _ready
    if _ready: return
    try:
        pdfmetrics.registerFont(TTFont("DJ", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
        pdfmetrics.registerFont(TTFont("DJB", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
    except Exception:
        pass
    _ready = True

def _st():
    return {
        "title": ParagraphStyle("title", fontName="DJB", fontSize=18, leading=22, textColor=colors.HexColor("#111111")),
        "sub": ParagraphStyle("sub", fontName="DJ", fontSize=10, leading=14, textColor=colors.HexColor("#666666")),
        "sec": ParagraphStyle("sec", fontName="DJB", fontSize=12, leading=16, textColor=colors.HexColor("#111111")),
        "th": ParagraphStyle("th", fontName="DJB", fontSize=9, leading=12, textColor=colors.white, alignment=1),
        "td": ParagraphStyle("td", fontName="DJ", fontSize=9, leading=12, textColor=colors.HexColor("#222222")),
        "td_b": ParagraphStyle("td_b", fontName="DJB", fontSize=9, leading=12, textColor=colors.HexColor("#111111")),
        "td_r": ParagraphStyle("td_r", fontName="DJ", fontSize=9, leading=12, textColor=colors.HexColor("#222222"), alignment=2),
        "td_rb": ParagraphStyle("td_rb", fontName="DJB", fontSize=9, leading=12, textColor=colors.HexColor("#111111"), alignment=2),
    }

def _p(t, style):
    return Paragraph(escape(str(t)), style)

def worker_pdf(r, lab):
    _setup()
    st = _st()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    story = []

    story.append(_p(f"{r['w']['name']} — отчёт за неделю", st["title"]))
    story.append(_p(f"По дате Rd: {lab} · дата отчёта: {L.dshort(L.iso(L.today()))}.2026", st["sub"]))
    story.append(Spacer(1, 12))

    t1_data = [[_p("Дата Rd", st["th"]), _p("Деп=зп", st["th"]), _p("Кол-во", st["th"]), _p("Всего", st["th"])]]
    for d in r["days"]:
        items_str = ", ".join([f"{a} × {k} = {L.money(u*k)}$" if k > 1 else f"{a} = {L.money(u)}$" 
                               for (a, u), k in OrderedDict((x, d["items"].count(x)) for x in d["items"]).items()])
        t1_data.append([
            _p(L.dshort(d["day"]), st["td"]),
            _p(items_str, st["td"]),
            _p(len(d["items"]), st["td_r"]),
            _p(f"{L.money(d['usd'])}$", st["td_rb"])
        ])
    t1_data.append([
        _p("ВСЕГО", st["td_b"]),
        _p("", st["td"]),
        _p(r["n"], st["td_rb"]),
        _p(f"{L.money(r['usd'])}$", st["td_rb"])
    ])

    t1 = Table(t1_data, colWidths=[70, 310, 60, 80])
    t1.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1B365D")),
        ("GRID", (0,0), (-1,-1), 0.5, colors.HexColor("#E0E0E0")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0,1), (-1,-2), [colors.white, colors.HexColor("#F9FBFD")]),
        ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#EEF2F7")),
    ]))
    story.append(t1)
    story.append(Spacer(1, 16))

    story.append(_p("Расчёт зарплаты", st["sec"]))
    story.append(Spacer(1, 6))

    calc_data = [
        [_p("Касса", st["td"]), _p(f"{L.money(r['usd'])}$", st["td_rb"])],
        [_p(f"Зп: {L.money(r['usd'])}$ × {int(r['days'][0]['pct'] if r['days'] else 30)}%", st["td"]), _p(f"{L.money(r['zp'])}$", st["td_rb"])],
        [_p(f"Бонус: {L.money(r['bonus'])}$", st["td"]), _p(f"{L.money(r['bonus'])}$", st["td_rb"])],
        [_p(f"Штраф: {L.money(r['fine'])}$", st["td"]), _p(f"{L.money(r['fine'])}$", st["td_rb"])],
        [_p("Зп-Штраф", st["td_b"]), _p(f"{L.money(r['zp_minus_fine'])}$", st["td_rb"])],
        [_p("Итого", st["td_b"]), _p(f"{L.money(r['total'])}$", st["td_rb"])],
    ]
    t2 = Table(calc_data, colWidths=[380, 140])
    t2.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.5, colors.HexColor("#E0E0E0")),
        ("BACKGROUND", (0,-2), (-1,-1), colors.HexColor("#EEF2F7")),
    ]))
    story.append(t2)

    doc.build(story)
    return buf.getvalue()

def all_pdf(res, lab, owner=False):
    _setup()
    st = _st()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    story = []

    story.append(_p("Общий отчёт", st["title"]))
    story.append(_p(f"По дате Rd: {lab} · дата отчёта: {L.dshort(L.iso(L.today()))}.2026", st["sub"]))
    story.append(Spacer(1, 12))

    active_res = [r for r in res if r["n"] > 0 or r["fine"] > 0 or r["bonus"] > 0]
    names = [r["w"]["name"] for r in active_res]
    
    header = ["Дата Rd"] + names + ["Чеков", "Всего"]
    t1_data = [[_p(h, st["th"]) for h in header]]

    all_days = sorted(list(set(d["day"] for r in active_res for d in r["days"])))
    tot_checks = 0
    tot_usd = 0.0

    for d in all_days:
        row = [L.dshort(d)]
        day_checks = 0
        day_usd = 0.0
        for r in active_res:
            match = next((x for x in r["days"] if x["day"] == d), None)
            if match:
                row.append(f"{L.money(match['usd'])}$")
                day_checks += len(match["items"])
                day_usd += match["usd"]
            else:
                row.append("—")
        row.append(str(day_checks))
        row.append(f"{L.money(day_usd)}$")
        t1_data.append([_p(x, st["td_r"] if i > 0 else st["td"]) for i, x in enumerate(row)])
        tot_checks += day_checks
        tot_usd += day_usd

    tot_row = ["ВСЕГО"] + [f"{L.money(r['usd'])}$" for r in active_res] + [str(tot_checks), f"{L.money(tot_usd)}$"]
    t1_data.append([_p(x, st["td_rb"] if i > 0 else st["td_b"]) for i, x in enumerate(tot_row)])

    col_w = 520 / len(header)
    t1 = Table(t1_data, colWidths=[col_w]*len(header))
    t1.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1B365D")),
        ("GRID", (0,0), (-1,-1), 0.5, colors.HexColor("#E0E0E0")),
        ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#EEF2F7")),
    ]))
    story.append(t1)
    story.append(Spacer(1, 14))

    story.append(_p("Бонусы и штрафы", st["sec"]))
    story.append(Spacer(1, 4))
    t2_data = [[_p("Обработчик", st["th"]), _p("Бонус", st["th"]), _p("Штраф", st["th"])]]
    tot_b, tot_f = 0.0, 0.0
    for r in active_res:
        b_str = " + ".join([f"{L.money(b)}$" for b, _ in r["bonuses"]]) or "—"
        f_str = " + ".join([f"{L.money(f)}$" for f, _ in r["fines"]]) or "—"
        t2_data.append([_p(r["w"]["name"], st["td"]), _p(b_str, st["td_r"]), _p(f_str, st["td_r"])])
        tot_b += r["bonus"]
        tot_f += r["fine"]
    t2_data.append([_p("ВСЕГО", st["td_b"]), _p(f"{L.money(tot_b)}$", st["td_rb"]), _p(f"{L.money(tot_f)}$", st["td_rb"])])
    t2 = Table(t2_data, colWidths=[140, 190, 190])
    t2.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1B365D")),
        ("GRID", (0,0), (-1,-1), 0.5, colors.HexColor("#E0E0E0")),
        ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#EEF2F7")),
    ]))
    story.append(t2)
    story.append(Spacer(1, 14))

    story.append(_p("Выплаты: кому сколько", st["sec"]))
    story.append(Spacer(1, 4))
    t3_data = [[_p("Обработчик", st["th"]), _p("Зп", st["th"]), _p("Штраф", st["th"]), _p("Зп-Штраф", st["th"]), _p("Бонус", st["th"]), _p("Итого", st["th"])]]
    tot_zp, tot_tot = 0.0, 0.0
    for r in active_res:
        t3_data.append([
            _p(r["w"]["name"], st["td"]),
            _p(f"{L.money(r['zp'])}$", st["td_r"]),
            _p(f"{L.money(r['fine'])}$", st["td_r"]),
            _p(f"{L.money(r['zp_minus_fine'])}$", st["td_r"]),
            _p(f"{L.money(r['bonus'])}$", st["td_r"]),
            _p(f"{L.money(r['total'])}$", st["td_rb"]),
        ])
        tot_zp += r["zp"]
        tot_tot += r["total"]
    t3_data.append([
        _p("ВСЕГО", st["td_b"]), _p(f"{L.money(tot_zp)}$", st["td_rb"]),
        _p(f"{L.money(tot_f)}$", st["td_rb"]), _p(f"{L.money(tot_zp-tot_f)}$", st["td_rb"]),
        _p(f"{L.money(tot_b)}$", st["td_rb"]), _p(f"{L.money(tot_tot)}$", st["td_rb"])
    ])
    t3 = Table(t3_data, colWidths=[110, 80, 80, 85, 80, 85])
    t3.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1B365D")),
        ("GRID", (0,0), (-1,-1), 0.5, colors.HexColor("#E0E0E0")),
        ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#EEF2F7")),
    ]))
    story.append(t3)

    doc.build(story)
    return buf.getvalue()
