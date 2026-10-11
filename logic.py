# -*- coding: utf-8 -*-
import os
import re
import sqlite3
import datetime as dt
from collections import OrderedDict
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Tashkent"))
DB_PATH = os.getenv("DB_PATH", "kassa.db")
ANON = 1087968824

def today():
    return dt.datetime.now(TZ).date()

def iso(d):
    return d.isoformat()

SCHEMA = """
CREATE TABLE IF NOT EXISTS workers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    is_self INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS pct (
    worker_id INTEGER NOT NULL,
    from_day TEXT NOT NULL,
    percent REAL NOT NULL,
    PRIMARY KEY (worker_id, from_day)
);
CREATE TABLE IF NOT EXISTS users (
    tg_id INTEGER PRIMARY KEY,
    name TEXT,
    role TEXT NOT NULL DEFAULT 'worker',
    worker_id INTEGER
);
CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    title TEXT,
    watch INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lo INTEGER NOT NULL,
    hi INTEGER NOT NULL,
    usd REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id INTEGER,
    chat_id INTEGER,
    msg_id INTEGER,
    sender_id INTEGER,
    raw TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    rd TEXT,
    amount INTEGER NOT NULL,
    usd REAL
);
CREATE TABLE IF NOT EXISTS adj (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id INTEGER,
    kind TEXT,
    usd REAL NOT NULL,
    day TEXT NOT NULL,
    raw TEXT,
    chat_id INTEGER,
    msg_id INTEGER,
    cancelled INTEGER NOT NULL DEFAULT 0,
    pending INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS advances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id INTEGER NOT NULL,
    usd REAL NOT NULL,
    day TEXT NOT NULL,
    note TEXT
);
"""

SEED_PRICES = [
    (400, 700, 4), (750, 1150, 5), (1400, 1400, 7), (1650, 2250, 7),
    (2300, 2800, 10), (3300, 3700, 13), (4000, 4000, 17), (6000, 6000, 25),
    (7000, 7000, 25), (9000, 12200, 100), (19200, 19200, 200), (49200, 49200, 500)
]

SEED_WORKERS = [
    ("Cashliy", 1, 1),
    ("Bayzed", 41, 0),
    ("Cred", 36, 0),
    ("Lazarus", 30, 0),
    ("Baxa", 30, 0)
]

_db = None

def db():
    global _db
    if _db is None:
        _db = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.executescript(SCHEMA)
        if not _db.execute("select 1 from workers").fetchone():
            for name, p, s in SEED_WORKERS:
                cur = _db.execute("insert into workers (name, is_self) values (?,?)", (name, s))
                _db.execute("insert into pct values (?,?,?)", (cur.lastrowid, "2000-01-01", p))
        if not _db.execute("select 1 from prices").fetchone():
            _db.executemany("insert into prices (lo, hi, usd) values (?,?,?)", SEED_PRICES)
        _db.commit()
    return _db

def q(sql, args=()):
    return db().execute(sql, args).fetchall()

def ql(sql, args=()):
    return db().execute(sql, args).fetchone()

def x(sql, args=()):
    cur = db().execute(sql, args)
    db().commit()
    return cur

def workers(active_only=True):
    sql = "select * from workers" + (" where active=1" if active_only else "")
    return q(sql + " order by is_self desc, id")

def worker(wid):
    return ql("select * from workers where id=?", (wid,))

def self_worker():
    return ql("select * from workers where is_self=1")

def percent_on(wid, day):
    d = day if isinstance(day, str) else iso(day)
    r = ql("select percent from pct where worker_id=? and from_day<=? order by from_day desc limit 1", (wid, d))
    return r["percent"] if r else 0.0

def add_worker(name, percent, from_day="2000-01-01"):
    cur = x("insert into workers (name) values (?)", (name.strip(),))
    x("insert into pct values (?,?,?)", (cur.lastrowid, from_day, percent))
    return cur.lastrowid

def set_percent(wid, percent, from_day):
    x("insert or replace into pct values (?,?,?)", (wid, from_day, percent))

def rename_worker(wid, name):
    x("update workers set name=? where id=?", (name.strip(), wid))

def set_active(wid, v):
    x("update workers set active=? where id=?", (v, wid))

def worker_by_name(text):
    names = sorted(workers(), key=lambda w: len(w["name"]), reverse=True)
    for w in names:
        if re.search(r"(?<!\w)" + re.escape(w["name"]) + r"(?!\w)", text, re.I):
            return w["id"]
    return None

def user(tg_id):
    return ql("select * from users where tg_id=?", (tg_id,))

def upsert_user(tg_id, name, role, worker_id):
    x("insert into users (tg_id, name, role, worker_id) values (?,?,?,?) "
      "on conflict(tg_id) do update set name=excluded.name, role=excluded.role, worker_id=excluded.worker_id",
      (tg_id, name, role, worker_id))

def admins():
    return [r["tg_id"] for r in q("select tg_id from users where role='admin'")]

def users_of_worker(wid):
    return q("select * from users where worker_id=?", (wid,))

def set_role(tg_id, role):
    x("update users set role=? where tg_id=?", (role, tg_id))

def chat(cid):
    return ql("select * from chats where chat_id=?", (cid,))

def upsert_chat(cid, title):
    x("insert into chats (chat_id, title) values (?,?) on conflict(chat_id) do update set title=excluded.title", (cid, title))

def set_watch(cid, v):
    x("update chats set watch=? where chat_id=?", (v, cid))

def price_for(amount):
    r = ql("select usd from prices where lo<=? and hi>=? order by (hi-lo) asc limit 1", (amount, amount))
    return r["usd"] if r else None

def add_price(lo, hi, usd):
    x("insert into prices (lo, hi, usd) values (?,?,?)", (min(lo, hi), max(lo, hi), usd))
    refresh_prices()

def del_price(pid):
    x("delete from prices where id=?", (pid,))

def list_prices():
    return q("select * from prices order by lo")

def refresh_prices():
    for c in q("select id, amount from checks where usd is null"):
        p = price_for(c["amount"])
        if p is not None:
            x("update checks set usd=? where id=?", (p, c["id"]))

RD_FIND = re.compile(r"(?<![A-Za-zA-Яа-я])(?:rd|рд)\W{0,3}(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?", re.I)
DATE_RE = re.compile(r"(?<!\d)\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?(?!\d)")
TIME_RE = re.compile(r"\d{1,2}:\d{2}")
NUM_RE = re.compile(r"(?<!\d)(\d{1,2}(?:,\d{3})+|\d{3,6})(?!\d)")
TOTAL_RE = re.compile(r"итого|всего|jami|umumiy|total|касса|kassa|сумма|зп\b|зарплата", re.I)

def mk_date(d, m, y=None, ref=None):
    ref = ref or today()
    try:
        if y:
            y = int(y)
            y = 2000 + y if y < 100 else y
            return dt.date(y, int(m), int(d))
        r = dt.date(ref.year, int(m), int(d))
        if r > ref + dt.timedelta(days=45):
            r = dt.date(ref.year - 1, int(m), int(d))
        return r
    except ValueError:
        return None

def find_rd(line, ref=None):
    m = RD_FIND.search(line)
    return mk_date(m.group(1), m.group(2), m.group(3), ref) if m else None

def has_rd(text):
    return bool(RD_FIND.search(text))

def parse_report(text, ref=None):
    lines = text.splitlines()
    rds = [find_rd(l, ref) for l in lines]
    first = next((r for r in rds if r), None)
    cur, out = None, []
    for line, rd in zip(lines, rds):
        if rd:
            cur = rd
        used_rd = cur or first
        if TOTAL_RE.search(line):
            continue
        body = line.rsplit("=", 1)[0] if "=" in line else line
        body = RD_FIND.sub("", body)
        body = DATE_RE.sub("", body)
        body = TIME_RE.sub("", body)
        for m in NUM_RE.finditer(body):
            v = int(re.sub(r"\D", "", m.group(1)))
            if 300 <= v <= 99999:
                out.append((used_rd, v))
    return out

KIND_PAT = r"штраф\w*|shtraf\w*|jarima\w*|бонус\w*|bonus\w*|премия\w*|прем\w*"
KIND_RE = re.compile(KIND_PAT, re.I)

def has_kind(text):
    return bool(KIND_RE.search(text))

def kind_of(tok):
    return "fine" if re.match(r"штраф|shtraf|jarima", tok, re.I) else "bonus"

def parse_adjustments(text):
    ws = workers()
    names = sorted([w["name"] for w in ws], key=len, reverse=True)
    name_pat = "|".join(re.escape(n) for n in names) or "(?!x)x"
    by_name = {w["name"].lower(): w["id"] for w in ws}
    tok = re.compile(rf"(?P<kind>{KIND_PAT})|(?P<name>(?<!\w)(?:{name_pat})(?!\w))|(?P<num>\d+(?:[.,]\d+)?)\s*\$?", re.I)
    t2 = DATE_RE.sub("", text)
    kind, wid = None, None
    items, kinds, wids = [], set(), set()
    for m in tok.finditer(t2):
        if m.group("kind"):
            kind = kind_of(m.group("kind"))
            kinds.add(kind)
        elif m.group("name"):
            wid = by_name.get(m.group("name").lower())
            wids.add(wid)
        elif m.group("num") and kind:
            items.append({"kind": kind, "worker_id": wid, "usd": float(m.group("num").replace(",", "."))})
    for it in items:
        if it["kind"] is None and len(kinds) == 1:
            it["kind"] = next(iter(kinds))
        if it["worker_id"] is None and len(wids) == 1:
            it["worker_id"] = next(iter(wids))
    return items

def report(rid):
    return ql("select * from reports where id=?", (rid,))

def create_report(worker_id, chat_id, msg_id, sender_id, raw, checks):
    if msg_id is not None:
        for o in q("select id from reports where chat_id=? and msg_id=?", (chat_id, msg_id)):
            delete_report(o["id"])
    cur = x("insert into reports (worker_id, chat_id, msg_id, sender_id, raw, created) values (?,?,?,?,?,?)",
            (worker_id, chat_id, msg_id, sender_id, raw, dt.datetime.now(TZ).isoformat()))
    rid = cur.lastrowid
    for rd, amt in checks:
        x("insert into checks (report_id, rd, amount, usd) values (?,?,?,?)",
          (rid, iso(rd) if rd else None, amt, price_for(amt)))
    return rid

def delete_report(rid):
    x("delete from checks where report_id=?", (rid,))
    x("delete from reports where id=?", (rid,))

def report_needs(rid):
    r = report(rid)
    if r["worker_id"] is None:
        return ("worker", None)
    if ql("select 1 from checks where report_id=? and rd is null", (rid,)):
        return ("date", None)
    row = ql("select amount from checks where report_id=? and usd is null limit 1", (rid,))
    if row:
        return ("price", row["amount"])
    return None

def pending_reports():
    return [r["id"] for r in q("select id from reports where status='pending'")]

def report_days(rid):
    return [r["rd"] for r in q("select distinct rd from checks where report_id=?", (rid,)) if r["rd"]]

def add_adj(worker_id, kind, usd, day, raw, chat_id=None, msg_id=None, pending=0):
    return x("insert into adj (worker_id, kind, usd, day, raw, chat_id, msg_id, pending) values (?,?,?,?,?,?,?,?)",
             (worker_id, kind, usd, day, raw, chat_id, msg_id, pending)).lastrowid

def adj(aid):
    return ql("select * from adj where id=?", (aid,))

def adj_resolve(aid, worker_id=None, kind=None):
    a = adj(aid)
    wid = worker_id if worker_id is not None else a["worker_id"]
    kd = kind if kind is not None else a["kind"]
    x("update adj set worker_id=?, kind=?, pending=0 where id=?", (wid, kd, aid))

def adj_toggle(aid):
    x("update adj set cancelled=1-cancelled where id=?", (aid,))

def drop_adj_msg(chat_id, msg_id):
    x("delete from adj where chat_id=? and msg_id=?", (chat_id, msg_id))

def pending_adjs():
    return [r["id"] for r in q("select id from adj where pending=1")]

def worker_adjs(wid, limit=15):
    return q("select * from adj where worker_id=? and pending=0 order by id desc limit ?", (wid, limit))

def add_advance(worker_id, usd, day, note=""):
    return x("insert into advances (worker_id, usd, day, note) values (?,?,?,?)", (worker_id, usd, day, note)).lastrowid

def worker_advances(wid, start, end):
    s, e = iso(start), iso(end)
    return q("select sum(usd) as total from advances where worker_id=? and day between ? and ?", (wid, s, e))[0]["total"] or 0.0

def period_range(key):
    t = today()
    if key == "td":
        return t, t
    if key == "yd":
        y = t - dt.timedelta(days=1)
        return y, y
    if key == "tw":
        mon = t - dt.timedelta(days=t.weekday())
        return mon, t
    if key == "lw":
        mon = t - dt.timedelta(days=t.weekday() + 7)
        return mon, mon + dt.timedelta(days=6)
    return t, t

def calc(start, end, only=None):
    s, e = iso(start), iso(end)
    out = []
    for w in workers(active_only=False):
        if only and w["id"] != only:
            continue
        rows = q("select ch.rd, ch.amount, ch.usd from checks ch join reports ro on ro.id=ch.report_id "
                 "where ro.worker_id=? and ro.status='done' and ch.rd between ? and ?", (w["id"], s, e))
        adjs = q("select * from adj where worker_id=? and cancelled=0 and pending=0 and day between ? and ?", (w["id"], s, e))
        adv = worker_advances(w["id"], start, end)
        if not only and not w["active"] and not rows and not adjs:
            continue
        days = OrderedDict()
        for r in rows:
            days.setdefault(r["rd"], []).append((r["amount"], r["usd"] or 0.0))
        dinfo, zp = [], 0.0
        for d, items in days.items():
            raw_sum = sum(u for _, u in items)
            p = percent_on(w["id"], d)
            dinfo.append({"day": d, "items": items, "raw": sum(a for a, _ in items), "usd": raw_sum, "pct": p})
            zp += raw_sum * p / 100
        fines = [(a["usd"], a["day"]) for a in adjs if a["kind"] == "fine"]
        bonuses = [(a["usd"], a["day"]) for a in adjs if a["kind"] == "bonus"]
        usd_all = sum(d["usd"] for d in dinfo)
        fs = sum(f for f, _ in fines)
        bs = sum(b for b, _ in bonuses)
        total = round(zp - fs + bs - adv, 2)
        out.append({
            "w": w, "days": dinfo, "n": len(rows), "raw": sum(d["raw"] for d in dinfo),
            "usd": round(usd_all, 2), "zp": round(zp, 2), "fines": fines, "bonuses": bonuses,
            "fine": fs, "bonus": bs, "adv": adv, "total": total, "profit": usd_all / 2
        })
    return out

def money(v):
    v = round(v + 1e-9, 2)
    return str(int(v)) if abs(v - int(v)) < 1e-9 else f"{v:.2f}"

def n3(v):
    return f"{int(v):,}"

def dshort(d):
    return dt.date.fromisoformat(d).strftime("%d.%m")

def label(s, e):
    return dshort(iso(s)) if s == e else f"{dshort(iso(s))} - {dshort(iso(e))}"

def chk_word(n):
    if n % 10 == 1 and n % 100 != 11:
        return "чек"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "чека"
    return "чеков"

def adj_list(items):
    v = money(sum(a for a, _ in items))
    if len(items) > 1:
        return f"{v}$ (" + "+".join(money(a) for a, _ in items) + ")"
    return f"{v}$"

def salary_lines(r):
    lines = [
        f"Зп: {money(r['zp'])}$",
        f"Штраф: {adj_list(r['fines']) if r['fines'] else '0$'}",
        f"Бонус: {adj_list(r['bonuses']) if r['bonuses'] else '0$'}"
    ]
    if r['adv'] > 0:
        lines.append(f"Аванс: {money(r['adv'])}$")
    lines.append(f"Итого: {money(r['zp'])}-{money(r['fine'])}+{money(r['bonus'])}-{money(r['adv'])}={money(r['total'])}$")
    return lines

def worker_text(r, lab):
    return "\n".join([f"{r['w']['name']} ({lab})"] + salary_lines(r))

def clean_report(r, lab):
    lines = [f"{r['w']['name']} ({lab})"]
    if r["n"] == 0:
        lines.append("Чеков нет")
    for d in r["days"]:
        am = [a for a, _ in d["items"]]
        lines.append(f"Дата: {dshort(d['day'])} | Чеки: {', '.join(map(str, am))}")
        lines.append("Касса: " + " + ".join(map(str, am)) + f" = {n3(d['raw'])}")
        lines.append("")
    lines += salary_lines(r)
    return "\n".join(lines)

def explain(r, lab):
    L = [f"Разбор: {r['w']['name']} ({lab})", "Каждый чек переводится в $ по прайсу:"]
    if r["n"] == 0:
        L.append("Чеков за период нет.")
    for d in r["days"]:
        L.append(f"Дата {dshort(d['day'])}:")
        cnt = OrderedDict()
        for a, u in d["items"]:
            cnt[(a, u)] = cnt.get((a, u), 0) + 1
        for (a, u), k in cnt.items():
            L.append(f"  {a} x{k} = {money(u * k)}$")
        L.append(f"  Сумма кассы: {n3(d['raw'])} → по прайсу {money(d['usd'])}$")
        L.append(f"  Зп за день: {money(d['usd'])}$ * {money(d['pct'])}% = {money(d['usd'] * d['pct'] / 100)}$")
    if r["n"]:
        L += ["", f"Касса по прайсу: {money(d['usd'])}$", f"Зп всего: {money(r['zp'])}$"]
    if r["fines"]:
        L.append("Штраф: " + ", ".join(f"{money(a)}$ ({dshort(dd)})" for a, dd in r["fines"]))
    if r["bonuses"]:
        L.append("Бонус: " + ", ".join(f"{money(a)}$ ({dshort(dd)})" for a, dd in r["bonuses"]))
    if r["adv"] > 0:
        L.append(f"Аванс: {money(r['adv'])}$")
    L.append(f"Итого: {money(r['zp'])}-{money(r['fine'])}+{money(r['bonus'])}-{money(r['adv'])}={money(r['total'])}$")
    return "\n".join(L)

def overall_text(res, lab):
    L = [f"📊 Общий отчёт ({lab})", ""]
    tot_n, tot_usd, tot_raw = 0, 0.0, 0
    for r in res:
        n = r["n"]
        tot_n += n
        tot_usd += r["usd"]
        tot_raw += r["raw"]
        L.append(f"{r['w']['name']}: {n} {chk_word(n)}, касса {n3(r['raw'])}, зп {money(r['total'])}$")
    L += ["", f"Всего чеков: {tot_n}", f"Касса (сумма чеков): {n3(tot_raw)}", f"Касса ($): {money(tot_usd)}$", f"Чистая прибыль: {money(tot_usd / 2)}$"]
    return "\n".join(L)

def rating_text(res, lab):
    rows = sorted([r for r in res if r["w"]["active"] or r["n"]], key=lambda r: r["usd"], reverse=True)
    medal = ["🥇", "🥈", "🥉"]
    L = [f"🏆 Рейтинг кассы ({lab})", ""]
    for i, r in enumerate(rows):
        m = medal[i] if i < 3 else f"{i + 1}️⃣"
        L.append(f"{m} {r['w']['name']} — {n3(r['raw'])} ({r['n']} {chk_word(r['n'])})")
    return "\n".join(L)

def chunks(text, limit=3800):
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit and cur:
            out.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        out.append(cur.rstrip("\n"))
    return out or [""]
