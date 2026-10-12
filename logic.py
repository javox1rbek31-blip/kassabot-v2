# -*- coding: utf-8 -*-
"""
Kassa-bot v4 Logic Module (Ultimate Optimized & Error-Free)
"""
import os
import re
import sqlite3
import datetime as dt
from collections import OrderedDict
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Tashkent"))
DB_PATH = os.getenv("DB_PATH", "kassa_v4.db")

def today():
    return dt.datetime.now(TZ).date()

def iso(d):
    return d.isoformat()

def week_start(d=None):
    d = d or today()
    return d - dt.timedelta(days=d.weekday())

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS workers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, is_self INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS pct (worker_id INTEGER NOT NULL, from_day TEXT NOT NULL, percent REAL NOT NULL, PRIMARY KEY (worker_id, from_day));
CREATE TABLE IF NOT EXISTS users (tg_id INTEGER PRIMARY KEY, name TEXT, role TEXT NOT NULL DEFAULT 'worker', worker_id INTEGER);
CREATE TABLE IF NOT EXISTS chats (chat_id INTEGER PRIMARY KEY, title TEXT, watch INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS prices (id INTEGER PRIMARY KEY AUTOINCREMENT, lo INTEGER NOT NULL, hi INTEGER NOT NULL, usd REAL NOT NULL);
CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY AUTOINCREMENT, worker_id INTEGER, chat_id INTEGER, msg_id INTEGER, sender_id INTEGER, raw TEXT, status TEXT NOT NULL DEFAULT 'pending', created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS checks (id INTEGER PRIMARY KEY AUTOINCREMENT, report_id INTEGER NOT NULL, rd TEXT, amount INTEGER NOT NULL, usd REAL, skip INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS adj (id INTEGER PRIMARY KEY AUTOINCREMENT, worker_id INTEGER, kind TEXT, usd REAL NOT NULL, day TEXT NOT NULL, raw TEXT, chat_id INTEGER, msg_id INTEGER, cancelled INTEGER NOT NULL DEFAULT 0, pending INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS worker_shifts (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, worker_id INTEGER NOT NULL, actual_worker_id INTEGER NOT NULL, UNIQUE(day, worker_id));
"""

SEED_PRICES = [
    (400, 700, 4), (750, 1150, 5), (1400, 1400, 7), (1650, 2250, 7),
    (2300, 2800, 10), (3300, 3700, 13), (4000, 4000, 17), (6000, 6000, 25),
    (7000, 7000, 25), (9000, 12200, 100), (19200, 19200, 200), (49200, 49200, 500)
]

SEED_WORKERS = [
    ("Cashliy", 50, 1),
    ("Bayzed", 41, 0),
    ("Baxa", 30, 0),
    ("Cred", 36, 0),
    ("Lazarus", 30, 0)
]

_db = None

def db():
    global _db
    if _db is None:
        _db = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.execute("PRAGMA journal_mode=WAL;")
        _db.execute("PRAGMA synchronous=NORMAL;")
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

def set_percent(wid, percent):
    mon = iso(week_start())
    x("delete from pct where worker_id=? and from_day>=?", (wid, mon))
    x("insert into pct values (?,?,?)", (wid, mon, percent))

def add_worker(name, percent):
    cur = x("insert into workers (name) values (?)", (name.strip(),))
    x("insert into pct values (?,?,?)", (cur.lastrowid, "2000-01-01", percent))
    return cur.lastrowid

def worker_by_name(text):
    for w in sorted(workers(), key=lambda x: len(x["name"]), reverse=True):
        if re.search(r"(?<!\w)" + re.escape(w["name"]) + r"(?!\w)", text, re.I):
            return w["id"]
    return None

def set_worker_shift(day, worker_id, actual_worker_id):
    d = day if isinstance(day, str) else iso(day)
    x("insert into worker_shifts (day, worker_id, actual_worker_id) values (?,?,?) "
      "on conflict(day, worker_id) do update set actual_worker_id=excluded.actual_worker_id", 
      (d, worker_id, actual_worker_id))

def get_worker_shift(day, worker_id):
    d = day if isinstance(day, str) else iso(day)
    r = ql("select actual_worker_id from worker_shifts where day=? and worker_id=?", (d, worker_id))
    return r["actual_worker_id"] if r else None

def worker_days(wid, limit=7):
    t = today()
    return [(t - dt.timedelta(days=i)).isoformat() for i in range(limit)]

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

def chat(cid):
    return ql("select * from chats where chat_id=?", (cid,))

def upsert_chat(cid, title):
    x("insert into chats (chat_id, title) values (?,?) on conflict(chat_id) do update set title=excluded.title", (cid, title))

def set_watch(cid, v):
    x("update chats set watch=? where chat_id=?", (v, cid))

def price_for(amount):
    r = ql("select usd from prices where lo<=? and hi>=? order by (hi-lo) asc limit 1", (amount, amount))
    return r["usd"] if r else None

def list_prices():
    return q("select * from prices order by lo")

RD_FIND = re.compile(r"(?<![A-Za-zА-Яа-яЁё])(?:rd|рд)\W{0,3}(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?", re.I)
DATE_RE = re.compile(r"(?<!\d)\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?(?!\d)")
TIME_RE = re.compile(r"\d{1,2}:\d{2}")
NUM_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[.,]\d{3})+|\d{3,6})(?!\d)")
TOTAL_RE = re.compile(r"итого|всего|jami|umumiy|total|касса|kassa|сумма|зп\b", re.I)

def mk_date(d, m, y=None, ref=None):
    ref = ref or today()
    try:
        y = int(y) + (2000 if int(y) < 100 else 0) if y else ref.year
        return dt.date(y, int(m), int(d))
    except ValueError:
        return None

def parse_report(text, ref=None):
    ref = ref or today()
    lines = text.splitlines()
    out = []
    cur_rd = ref
    for line in lines:
        if TOTAL_RE.search(line):
            continue
        m = RD_FIND.search(line)
        if m:
            cur_rd = mk_date(m.group(1), m.group(2), m.group(3), ref) or cur_rd
        body = RD_FIND.sub(" ", line)
        body = DATE_RE.sub(" ", body)
        body = TIME_RE.sub(" ", body)
        for nm in NUM_RE.finditer(body):
            v = int(re.sub(r"\D", "", nm.group(1)))
            if 300 <= v <= 99999:
                out.append((cur_rd, v))
    return out

KIND_PAT = r"штраф\w*|shtraf\w*|jarima\w*|бонус\w*|bonus\w*|премия\w*"
KIND_RE = re.compile(KIND_PAT, re.I)

def has_kind(text):
    return bool(KIND_RE.search(text))

def parse_adjustments(text):
    ws = workers()
    by_name = {w["name"].lower(): w["id"] for w in ws}
    items = []
    for line in text.splitlines():
        if not KIND_RE.search(line):
            continue
        kind = "fine" if re.search(r"штраф|shtraf|jarima", line, re.I) else "bonus"
        wid = None
        for name, w_id in by_name.items():
            if re.search(r"\b" + re.escape(name) + r"\b", line, re.I):
                wid = w_id
                break
        nums = [float(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", line)]
        if nums:
            items.append({"kind": kind, "worker_id": wid, "usd": nums[0]})
    return items

def create_report(worker_id, chat_id, msg_id, sender_id, raw, checks):
    cur = x("insert into reports (worker_id, chat_id, msg_id, sender_id, raw, created) values (?,?,?,?,?,?)",
            (worker_id, chat_id, msg_id, sender_id, raw, dt.datetime.now(TZ).isoformat()))
    rid = cur.lastrowid
    for rd, amt in checks:
        x("insert into checks (report_id, rd, amount, usd) values (?,?,?,?)", (rid, iso(rd), amt, price_for(amt)))
    return rid

def add_adj(worker_id, kind, usd, day, raw, chat_id=None, msg_id=None, pending=0):
    return x("insert into adj (worker_id, kind, usd, day, raw, chat_id, msg_id, pending) values (?,?,?,?,?,?,?,?)",
             (worker_id, kind, usd, day, raw, chat_id, msg_id, pending)).lastrowid

def period_range(key):
    t = today()
    if key == "td": return t, t
    if key == "yd": return t - dt.timedelta(days=1), t - dt.timedelta(days=1)
    if key == "tw": return week_start(t), t
    if key == "lw": return week_start(t) - dt.timedelta(days=7), week_start(t) - dt.timedelta(days=1)
    if key == "tm": return t.replace(day=1), t
    if key == "lm": return (t.replace(day=1) - dt.timedelta(days=1)).replace(day=1), t.replace(day=1) - dt.timedelta(days=1)
    return t, t

def calc(start, end, only=None):
    s, e = iso(start), iso(end)
    out = []
    baxa_total_usd = 0.0
    
    ws = workers(active_only=False)
    
    baxa_id = next((w["id"] for w in ws if w["name"] == "Baxa"), None)
    if baxa_id:
        baxa_rows = q("select ch.usd from checks ch join reports ro on ro.id=ch.report_id "
                      "where ro.worker_id=? and ch.skip=0 and ch.rd between ? and ?", (baxa_id, s, e))
        baxa_total_usd = sum((r["usd"] or 0.0) for r in baxa_rows)

    baxa_5pct = round(baxa_total_usd * 0.05, 2)

    for w in ws:
        if only and w["id"] != only:
            continue
        rows = q("select ch.rd, ch.amount, ch.usd from checks ch join reports ro on ro.id=ch.report_id "
                 "where ro.worker_id=? and ch.skip=0 and ch.rd between ? and ? order by ch.rd, ch.id", (w["id"], s, e))
        adjs = q("select * from adj where worker_id=? and cancelled=0 and pending=0 and day between ? and ?", (w["id"], s, e))
        
        if not only and not rows and not adjs:
            continue
            
        days = OrderedDict()
        for r in rows:
            days.setdefault(r["rd"], []).append((r["amount"], r["usd"] or 0.0))
            
        dinfo, zp = [], 0.0
        for d, items in days.items():
            raw_sum = sum(u for _, u in items)
            shift_wid = get_worker_shift(d, w["id"])
            p = percent_on(shift_wid, d) if shift_wid else percent_on(w["id"], d)
            
            dinfo.append({"day": d, "items": items, "raw": sum(a for a, _ in items), "usd": raw_sum, "pct": p})
            zp += raw_sum * p / 100
            
        fines = [(a["usd"], a["day"]) for a in adjs if a["kind"] == "fine"]
        bonuses = [(a["usd"], a["day"]) for a in adjs if a["kind"] == "bonus"]
        
        if w["name"] == "Bayzed" and baxa_5pct > 0:
            bonuses.append((baxa_5pct, "Baxa 5%"))
            
        usd_all = sum(d["usd"] for d in dinfo)
        fs = sum(f for f, _ in fines)
        bs = sum(b for b, _ in bonuses)
        
        zp_minus_fine = round(zp - fs, 2)
        total = round(zp_minus_fine + bs, 2)
        
        out.append({
            "w": w, "days": dinfo, "n": len(rows), "raw": sum(d["raw"] for d in dinfo),
            "usd": round(usd_all, 2), "zp": round(zp, 2), "fines": fines, "bonuses": bonuses,
            "fine": fs, "bonus": bs, "zp_minus_fine": zp_minus_fine, "total": total
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
