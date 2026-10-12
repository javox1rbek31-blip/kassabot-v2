# -*- coding: utf-8 -*-
"""Kassa-bot v3 — baza, parsing, hisob-kitob, ruscha matnlar. Telegramga bog'liq emas."""
import os
import re
import sqlite3
import datetime as dt
from collections import OrderedDict
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Tashkent"))
DB_PATH = os.getenv("DB_PATH", "kassa.db")
# Yangi toza baza (eski hisoblar o'chadi, eski fayl tegilmaydi): kassa_v3.db
DB_FILE = ":memory:" if DB_PATH == ":memory:" else os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "kassa_v3.db")
ANON = 1087968824
DAY_CUTOFF_HOUR = int(os.getenv("DAY_CUTOFF_HOUR", "0"))


def today():
    return dt.datetime.now(TZ).date()


def iso(d):
    return d.isoformat()


def post_day(msg_dt):
    return (msg_dt.astimezone(TZ) - dt.timedelta(hours=DAY_CUTOFF_HOUR)).date()


# ───────────────────────── baza ─────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS workers(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL,
  is_self INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS pct(worker_id INTEGER NOT NULL, from_day TEXT NOT NULL, percent REAL NOT NULL,
  PRIMARY KEY(worker_id, from_day));
CREATE TABLE IF NOT EXISTS users(tg_id INTEGER PRIMARY KEY, name TEXT, role TEXT NOT NULL DEFAULT 'worker', worker_id INTEGER);
CREATE TABLE IF NOT EXISTS chats(chat_id INTEGER PRIMARY KEY, title TEXT, watch INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS prices(id INTEGER PRIMARY KEY AUTOINCREMENT, lo INTEGER NOT NULL, hi INTEGER NOT NULL, usd REAL NOT NULL);
CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY AUTOINCREMENT, worker_id INTEGER, chat_id INTEGER, msg_id INTEGER,
  sender_id INTEGER, sender_name TEXT, raw TEXT, status TEXT NOT NULL DEFAULT 'pending', created TEXT);
CREATE TABLE IF NOT EXISTS checks(id INTEGER PRIMARY KEY AUTOINCREMENT, report_id INTEGER NOT NULL, rd TEXT,
  amount INTEGER NOT NULL, usd REAL, excl INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS transfers(id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, from_id INTEGER NOT NULL,
  to_id INTEGER NOT NULL, pct REAL NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS adj(id INTEGER PRIMARY KEY AUTOINCREMENT, worker_id INTEGER, kind TEXT, usd REAL NOT NULL,
  day TEXT NOT NULL, raw TEXT, chat_id INTEGER, msg_id INTEGER, cancelled INTEGER NOT NULL DEFAULT 0, pending INTEGER NOT NULL DEFAULT 0);
"""

SEED_PRICES = [
    (400, 700, 4), (750, 1150, 5), (1400, 1400, 7), (1650, 2250, 7),
    (2300, 2800, 10), (3300, 3700, 13), (4000, 4000, 17), (6000, 6000, 25),
    (7000, 7000, 30), (9000, 12200, 100), (19200, 19200, 200), (49200, 49200, 500),
]
CHANGE_DAY = "2026-10-11"
SEED_WORKERS = [  # (ism, o'zi?, faolmi?, [(qaysi kundan, foiz)])
    ("Cashliy", 1, 1, [("2000-01-01", 50)]),
    ("Bayzed", 0, 1, [("2000-01-01", 36), (CHANGE_DAY, 41)]),
    ("Lazarus", 0, 0, [("2000-01-01", 30)]),
    ("Cred", 0, 1, [("2000-01-01", 30), (CHANGE_DAY, 36)]),
    ("Baxa", 0, 1, [("2000-01-01", 30)]),
]
_db = None


def db():
    global _db
    if _db is None:
        _db = sqlite3.connect(DB_FILE, check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.executescript(SCHEMA)
        if not _db.execute("select 1 from workers").fetchone():
            for name, selfw, act, pcts in SEED_WORKERS:
                cur = _db.execute("insert into workers(name,is_self,active) values(?,?,?)", (name, selfw, act))
                for fd, pc in pcts:
                    _db.execute("insert into pct values(?,?,?)", (cur.lastrowid, fd, pc))
        if not _db.execute("select 1 from prices").fetchone():
            _db.executemany("insert into prices(lo,hi,usd) values(?,?,?)", SEED_PRICES)
        _db.commit()
    return _db


def q(sql, args=()):
    return db().execute(sql, args).fetchall()


def q1(sql, args=()):
    return db().execute(sql, args).fetchone()


def x(sql, args=()):
    cur = db().execute(sql, args)
    db().commit()
    return cur


# ───────────────────────── ishchilar ─────────────────────────
def workers(active_only=True, with_self=True):
    sql = "select * from workers where 1=1"
    if active_only:
        sql += " and active=1"
    if not with_self:
        sql += " and is_self=0"
    return q(sql + " order by is_self desc, id")


def worker(wid):
    return q1("select * from workers where id=?", (wid,))


def self_worker():
    return q1("select * from workers where is_self=1")


def percent_on(wid, day):
    d = day if isinstance(day, str) else iso(day)
    r = q1("select percent from pct where worker_id=? and from_day<=? order by from_day desc limit 1", (wid, d))
    return r["percent"] if r else 0.0


def add_worker(name, percent, from_day="2000-01-01"):
    cur = x("insert into workers(name) values(?)", (name.strip(),))
    x("insert into pct values(?,?,?)", (cur.lastrowid, from_day, percent))
    return cur.lastrowid


def set_percent(wid, percent, from_day):
    x("insert or replace into pct values(?,?,?)", (wid, from_day, percent))


def rename_worker(wid, name):
    x("update workers set name=? where id=?", (name.strip(), wid))


def set_active(wid, v):
    x("update workers set active=? where id=?", (v, wid))


def name_taken(name):
    return q1("select 1 from workers where lower(name)=lower(?)", (name.strip(),)) is not None


def worker_by_name(text):
    for w in sorted(workers(with_self=False), key=lambda w: -len(w["name"])):
        if re.search(r"(?<!\w)" + re.escape(w["name"]) + r"(?!\w)", text, re.I):
            return w["id"]
    return None


# ───────────────────────── foydalanuvchilar / chatlar ─────────────────────────
def user(tg_id):
    return q1("select * from users where tg_id=?", (tg_id,))


def upsert_user(tg_id, name, role, worker_id):
    x("insert into users(tg_id,name,role,worker_id) values(?,?,?,?) "
      "on conflict(tg_id) do update set name=excluded.name, role=excluded.role, worker_id=excluded.worker_id",
      (tg_id, name, role, worker_id))


def touch_user(tg_id, name):
    """Ishchi akkaunt nomini o'zgartirsa ham ID bo'yicha taniymiz; ismni yangilab boramiz."""
    u = user(tg_id)
    if u and name and u["name"] != name:
        x("update users set name=? where tg_id=?", (name, tg_id))


def admins():
    return [r["tg_id"] for r in q("select tg_id from users where role='admin'")]


def owners():
    """Egasining ikkinchi akkaunti (masalan @cashliy) — egasi bilan bitta odam."""
    return [r["tg_id"] for r in q("select tg_id from users where role='owner'")]


def users_of_worker(wid):
    return q("select * from users where worker_id=?", (wid,))


def all_users():
    return q("select * from users order by role, name")


def del_user(tg_id):
    x("delete from users where tg_id=?", (tg_id,))


def set_user_worker(tg_id, wid):
    x("update users set worker_id=? where tg_id=?", (wid, tg_id))


def set_role(tg_id, role):
    x("update users set role=? where tg_id=?", (role, tg_id))


def chat(cid):
    return q1("select * from chats where chat_id=?", (cid,))


def all_chats():
    return q("select * from chats order by title")


def upsert_chat(cid, title):
    x("insert into chats(chat_id,title) values(?,?) on conflict(chat_id) do update set title=excluded.title", (cid, title))


def set_watch(cid, v):
    x("update chats set watch=? where chat_id=?", (v, cid))


# ───────────────────────── narx-ro'yxati ─────────────────────────
def price_for(amount):
    r = q1("select usd from prices where lo<=? and hi>=? order by (hi-lo) asc limit 1", (amount, amount))
    return r["usd"] if r else None


def price_row(pid):
    return q1("select * from prices where id=?", (pid,))


def add_price(lo, hi, usd):
    x("insert into prices(lo,hi,usd) values(?,?,?)", (min(lo, hi), max(lo, hi), usd))
    refresh_prices()


def update_price(pid, lo, hi, usd):
    x("update prices set lo=?, hi=?, usd=? where id=?", (min(lo, hi), max(lo, hi), usd, pid))
    for c in q("select id, amount from checks where amount between ? and ?", (min(lo, hi), max(lo, hi))):
        p = price_for(c["amount"])
        if p is not None:
            x("update checks set usd=? where id=?", (p, c["id"]))
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


# ───────────────────────── parsing ─────────────────────────
RD_FIND = re.compile(r"(?<![A-Za-zА-Яа-я])(?:rd|рд)\W{0,3}(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?", re.I)
RD_WORD = re.compile(r"(?<![A-Za-zА-Яа-я])(?:rd|рд)(?![A-Za-zА-Яа-я])", re.I)
DATE_RE = re.compile(r"(?<!\d)\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?(?!\d)")
TIME_RE = re.compile(r"\d{1,2}:\d{2}")
NUM_RE = re.compile(r"(?<!\d)(\d{1,2}(?:[ ,.]\d{3})+|\d{3,6})(?!\d)")
TOTAL_RE = re.compile(r"итого|всего|jami|umumiy|total|касса|kassa|сумма|зп\b|зарплат|oylik|бонус|штраф|аванс|bonus|shtraf|avans", re.I)


def mk_date(d, m, y=None, ref=None):
    ref = ref or today()
    try:
        if y:
            y = int(y)
            y += 2000 if y < 100 else 0
            return dt.date(y, int(m), int(d))
        r = dt.date(ref.year, int(m), int(d))
        if r > ref + dt.timedelta(days=45):
            r = dt.date(ref.year - 1, int(m), int(d))
        return r
    except ValueError:
        return None


def parse_date_text(text, ref=None):
    m = DATE_RE.search(text)
    if not m:
        return None
    p = re.split(r"[./]", m.group(0))
    return mk_date(p[0], p[1], p[2] if len(p) > 2 else None, ref)


def is_report_like(text):
    """Faqat raqam/sana/Rd/ajratgichdan iborat xabar hisobot. Boshqa har qanday gap o'qilmaydi."""
    t = RD_WORD.sub(" ", text.strip())
    return bool(t.strip()) and re.fullmatch(r"[\d\s,.;+=:/\-–]+", t) is not None


def scan_lines(text, ref=None):
    """-> [(amounts, dates:set, rd|None)] har qator uchun (jami/kassa qatorlari tashlanadi)."""
    out = []
    for line in text.splitlines():
        if TOTAL_RE.search(line):
            continue
        body = line.rsplit("=", 1)[0] if "=" in line else line
        m = RD_FIND.search(body)
        rd = mk_date(m.group(1), m.group(2), m.group(3), ref) if m else None
        dates = set()
        for dm in DATE_RE.finditer(body):
            p = re.split(r"[./]", dm.group(0))
            d = mk_date(p[0], p[1], p[2] if len(p) > 2 else None, ref)
            if d:
                dates.add(d)
        b = TIME_RE.sub(" ", DATE_RE.sub(" ", RD_FIND.sub(" ", body)))
        am = []
        for nm in NUM_RE.finditer(b):
            v = int(re.sub(r"\D", "", nm.group(1)))
            if 300 <= v <= 99999:
                am.append(v)
        out.append((am, dates, rd))
    return out


def parse_amounts(text):
    return [a for am, _, _ in scan_lines(text) for a in am]


def owner_checks(text, ref):
    """Hisobot kuni = 'Rd - dd.mm' sanasi (chek yonidagi sana e'tiborga olinmaydi). -> [(date|None, amount)]
    Rd chekdan keyingi qatorda ham, shu qatorda ham bo'lishi mumkin."""
    lines = scan_lines(text, ref)
    if not any(l[0] for l in lines):
        return []
    out, pending, last_rd = [], [], None
    for am, dates, rd in lines:
        if am and rd:
            out += [(rd, a) for a in am]
            last_rd = rd
        elif am:
            pending.append(am)
        elif rd:
            last_rd = rd
            for pa in pending:
                out += [(rd, a) for a in pa]
            pending = []
    if last_rd or any(l[2] for l in lines):
        for pa in pending:
            out += [(last_rd, a) for a in pa]
        return out
    # Rd yo'q: umumiy sana(lar)
    L1 = [l for l in lines if l[0]]
    flat = lambda d: [(d, a) for l in L1 for a in l[0]]
    all_dates = set().union(*[l[1] for l in lines])
    if not all_dates:
        return flat(ref)
    if all(l[1] for l in L1):
        common = set.intersection(*[l[1] for l in L1])
        if common:
            return flat(max(common))
    if len(all_dates) == 1:
        return flat(next(iter(all_dates)))
    return flat(None)


def report_checks(text, post, is_owner):
    """Ishchi: Rd bo'lsa o'sha kun, aks holda tashlangan kun (doim bugungi). Egasi: tushunmasa None (bot so'raydi)."""
    out = owner_checks(text, post)
    if not is_owner:
        out = [(d or post, a) for d, a in out]
    return out


KIND_PAT = (r"штраф\w*|shtraf\w*|jarima\w*|бонус\w*|bonus\w*|премия\w*|прем\w*|pryob\w*|priyob\w*|prem\w*"
            r"|аванс\w*|avans\w*|avanc\w*")
KIND_RE = re.compile(KIND_PAT, re.I)


def has_kind(text):
    return bool(KIND_RE.search(text))


def kind_of(tok):
    if re.match(r"штраф|shtraf|jarima", tok, re.I):
        return "fine"
    if re.match(r"аванс|avans|avanc", tok, re.I):
        return "adv"
    return "bonus"


def parse_adjustments(text):
    """-> ([{kind, worker_id|None, usd}], toza_mi)"""
    ws = workers(with_self=False)
    names = sorted([w["name"] for w in ws], key=len, reverse=True)
    name_pat = "|".join(re.escape(n) for n in names) or "(?!x)x"
    by_name = {w["name"].lower(): w["id"] for w in ws}
    tok = re.compile(rf"(?P<kind>{KIND_PAT})|(?P<name>(?<!\w)(?:{name_pat})(?!\w))|(?P<num>\d+(?:[.,]\d+)?)", re.I)
    t2 = DATE_RE.sub(" ", text)
    kind = wid = None
    items, kinds, wids = [], set(), set()
    for m in tok.finditer(t2):
        if m.group("kind"):
            kind = kind_of(m.group("kind"))
            kinds.add(kind)
        elif m.group("name"):
            wid = by_name.get(m.group("name").lower())
            wids.add(wid)
        else:
            items.append({"kind": kind, "worker_id": wid, "usd": float(m.group("num").replace(",", "."))})
    for it in items:
        if it["kind"] is None and len(kinds) == 1:
            it["kind"] = next(iter(kinds))
        if it["worker_id"] is None and len(wids) == 1:
            it["worker_id"] = next(iter(wids))
    leftover = re.sub(r"[\s,;+$:/.\-–—()!]+", "", tok.sub(" ", t2))
    return items, not leftover


# ───────────────────────── hisobotlar ─────────────────────────
def report(rid):
    return q1("select * from reports where id=?", (rid,))


def report_amounts_by_msg(chat_id, msg_id):
    r = q1("select id from reports where chat_id=? and msg_id=?", (chat_id, msg_id))
    return None if not r else [c["amount"] for c in q("select amount from checks where report_id=? order by id", (r["id"],))]


def amounts_of(rid):
    return [c["amount"] for c in q("select amount from checks where report_id=? order by id", (rid,))]


def create_report(worker_id, chat_id, msg_id, sender_id, raw, checks, sender_name=""):
    if msg_id is not None:
        for o in q("select id from reports where chat_id=? and msg_id=?", (chat_id, msg_id)):
            delete_report(o["id"])
    cur = x("insert into reports(worker_id,chat_id,msg_id,sender_id,sender_name,raw,created) values(?,?,?,?,?,?,?)",
            (worker_id, chat_id, msg_id, sender_id, sender_name, raw, dt.datetime.now(TZ).isoformat()))
    rid = cur.lastrowid
    for rd, amt in checks:
        x("insert into checks(report_id,rd,amount,usd) values(?,?,?,?)", (rid, iso(rd) if rd else None, amt, price_for(amt)))
    return rid


def delete_report(rid):
    x("delete from checks where report_id=?", (rid,))
    x("delete from reports where id=?", (rid,))


def set_report_status(rid, st):
    x("update reports set status=? where id=?", (st, rid))


def report_needs(rid):
    r = report(rid)
    if r["worker_id"] is None:
        return ("worker", None)
    if q1("select 1 from checks where report_id=? and rd is null", (rid,)):
        return ("date", None)
    row = q1("select amount from checks where report_id=? and usd is null limit 1", (rid,))
    if row:
        return ("price", row["amount"])
    return None


def pending_reports():
    return [r["id"] for r in q("select id from reports where status='pending'")]


def report_days(rid):
    return [r["rd"] for r in q("select distinct rd from checks where report_id=? order by rd", (rid,))]


def supersede(rid):
    """Yangi hisobot eskisini to'liq ichiga olsa (>=2 chek) — eskisi hisobdan chiqadi (qayta tashlangan). -> [{old, amounts, kind}]"""
    r = report(rid)
    new = amounts_of(rid)
    out = []
    for d in report_days(rid):
        for o in q("select distinct r.id from reports r join checks c on c.report_id=r.id "
                   "where r.worker_id=? and r.status='done' and r.id!=? and c.rd=?", (r["worker_id"], rid, d)):
            old = amounts_of(o["id"])
            pool = list(new)
            ok = len(old) >= 2
            for a in old:
                if a in pool:
                    pool.remove(a)
                else:
                    ok = False
                    break
            if ok:
                set_report_status(o["id"], "replaced")
                out.append({"old": o["id"], "amounts": old, "kind": "replace"})
            elif len(old) == 1 and old == new:
                out.append({"old": o["id"], "amounts": old, "kind": "dup"})
    return out


def day_checks(wid, day):
    return q("select ch.id, ch.amount, ch.excl, ch.report_id from checks ch join reports r on r.id=ch.report_id "
             "where r.worker_id=? and r.status='done' and ch.rd=? order by ch.id", (wid, day))


def toggle_check(cid):
    x("update checks set excl=1-excl where id=?", (cid,))


def add_check(wid, day, amount):
    cur = x("insert into reports(worker_id,chat_id,sender_name,raw,status,created) values(?,?,?,?,?,?)",
            (wid, 0, "вручную", str(amount), "done", dt.datetime.now(TZ).isoformat()))
    x("insert into checks(report_id,rd,amount,usd) values(?,?,?,?)", (cur.lastrowid, day, amount, price_for(amount)))
    return cur.lastrowid


def edit_check_amount(wid, day, old, new):
    c = q1("select ch.id from checks ch join reports r on r.id=ch.report_id where r.worker_id=? and r.status='done' "
           "and ch.rd=? and ch.amount=? order by ch.id limit 1", (wid, day, old))
    if not c:
        return False
    x("update checks set amount=?, usd=? where id=?", (new, price_for(new), c["id"]))
    return True


def clear_day(wid, day):
    ids = [r["id"] for r in q("select distinct r.id from reports r join checks c on c.report_id=r.id "
                              "where r.worker_id=? and c.rd=?", (wid, day))]
    for i in ids:
        x("delete from checks where report_id=? and rd=?", (i, day))
        if not q1("select 1 from checks where report_id=?", (i,)):
            x("delete from reports where id=?", (i,))
    return len(ids)


def worker_days(wid, limit=10):
    return q("select ch.rd, count(*) n from checks ch join reports r on r.id=ch.report_id "
             "where r.worker_id=? and r.status='done' group by ch.rd order by ch.rd desc limit ?", (wid, limit))


def move_day(from_id, to_id, day):
    """Bir kunlik achotni boshqa ishchiga o'tkazish (o'rniga kimdir ishlagan)."""
    n = 0
    for r in q("select distinct r.id from reports r join checks c on c.report_id=r.id "
               "where r.worker_id=? and r.status='done' and c.rd=?", (from_id, day)):
        tot = q1("select count(*) c from checks where report_id=?", (r["id"],))["c"]
        dy = q1("select count(*) c from checks where report_id=? and rd=?", (r["id"], day))["c"]
        n += dy
        if tot == dy:
            x("update reports set worker_id=? where id=?", (to_id, r["id"]))
        else:
            cur = x("insert into reports(worker_id,chat_id,sender_name,raw,status,created) values(?,?,?,?,?,?)",
                    (to_id, 0, "перенос", "", "done", dt.datetime.now(TZ).isoformat()))
            x("update checks set report_id=? where report_id=? and rd=?", (cur.lastrowid, r["id"], day))
    return n


# ───────────────────────── shtraf / bonus / avans ─────────────────────────
def add_adj(worker_id, kind, usd, day, raw, chat_id=None, msg_id=None, pending=0):
    return x("insert into adj(worker_id,kind,usd,day,raw,chat_id,msg_id,pending) values(?,?,?,?,?,?,?,?)",
             (worker_id, kind, usd, day, raw, chat_id, msg_id, pending)).lastrowid


def adj(aid):
    return q1("select * from adj where id=?", (aid,))


def adj_resolve(aid, worker_id=None, kind=None):
    a = adj(aid)
    wid = worker_id if worker_id is not None else a["worker_id"]
    kd = kind if kind is not None else a["kind"]
    x("update adj set worker_id=?, kind=?, pending=? where id=?", (wid, kd, 0 if (wid and kd) else 1, aid))


def adj_toggle(aid):
    x("update adj set cancelled=1-cancelled where id=?", (aid,))


def adj_delete(aid):
    x("delete from adj where id=?", (aid,))


def drop_adj_msg(chat_id, msg_id):
    x("delete from adj where chat_id=? and msg_id=?", (chat_id, msg_id))


def pending_adjs():
    return [r["id"] for r in q("select id from adj where pending=1")]


def worker_adjs(wid, limit=15):
    return q("select * from adj where worker_id=? and pending=0 order by id desc limit ?", (wid, limit))


# ───────────────────────── oylik o'tkazish ─────────────────────────
def day_zp(wid, day):
    rows = q("select ch.usd from checks ch join reports r on r.id=ch.report_id "
             "where r.worker_id=? and r.status='done' and ch.excl=0 and ch.rd=?", (wid, day))
    return sum((r["usd"] or 0) for r in rows) * percent_on(wid, day) / 100


def add_transfer(day, from_id, to_id, pct):
    return x("insert into transfers(day,from_id,to_id,pct) values(?,?,?,?)", (day, from_id, to_id, pct)).lastrowid


def transfer_toggle(tid):
    x("update transfers set cancelled=1-cancelled where id=?", (tid,))


def worker_transfers(wid, limit=10):
    return q("select * from transfers where from_id=? or to_id=? order by id desc limit ?", (wid, wid, limit))


# ───────────────────────── hisob-kitob ─────────────────────────
def month_range(t, back=0):
    first = t.replace(day=1)
    for _ in range(back):
        first = (first - dt.timedelta(days=1)).replace(day=1)
    nxt = (first + dt.timedelta(days=32)).replace(day=1)
    return first, nxt - dt.timedelta(days=1)


def period_range(key):
    t = today()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", key):
        d = dt.date.fromisoformat(key)
        return d, d
    if key == "td":
        return t, t
    if key == "yd":
        y = t - dt.timedelta(days=1)
        return y, y
    if key == "tm":
        return month_range(t, 0)
    if key == "lm":
        return month_range(t, 1)
    mon = t - dt.timedelta(days=t.weekday())
    if key == "lw":
        mon -= dt.timedelta(days=7)
    return mon, mon + dt.timedelta(days=6)


def calc(start, end, only=None):
    s, e = iso(start), iso(end)
    out = []
    for w in workers(active_only=False):
        if only and w["id"] != only:
            continue
        rows = q("select ch.rd, ch.amount, ch.usd from checks ch join reports r on r.id=ch.report_id "
                 "where r.worker_id=? and r.status='done' and ch.excl=0 and ch.rd between ? and ? order by ch.rd, ch.id",
                 (w["id"], s, e))
        adjs = q("select * from adj where worker_id=? and cancelled=0 and pending=0 and day between ? and ? order by id",
                 (w["id"], s, e))
        outs = [{"id": t["id"], "day": t["day"], "other": worker(t["to_id"])["name"],
                 "amount": round(day_zp(w["id"], t["day"]) * t["pct"] / 100, 2)}
                for t in q("select * from transfers where from_id=? and cancelled=0 and day between ? and ? order by day,id",
                           (w["id"], s, e))]
        ins = [{"id": t["id"], "day": t["day"], "other": worker(t["from_id"])["name"],
                "amount": round(day_zp(t["from_id"], t["day"]) * t["pct"] / 100, 2)}
               for t in q("select * from transfers where to_id=? and cancelled=0 and day between ? and ? order by day,id",
                          (w["id"], s, e))]
        if not only and not w["active"] and not rows and not adjs and not outs and not ins:
            continue
        days = OrderedDict()
        for r in rows:
            days.setdefault(r["rd"], []).append((r["amount"], r["usd"] or 0.0))
        dinfo, zp = [], 0.0
        for d, items in days.items():
            usd = sum(u for _, u in items)
            p = percent_on(w["id"], d)
            dinfo.append({"day": d, "items": items, "raw": sum(a for a, _ in items), "usd": usd, "pct": p, "zp": usd * p / 100})
            zp += usd * p / 100
        fines = [(a["usd"], a["day"]) for a in adjs if a["kind"] == "fine"]
        bonuses = [(a["usd"], a["day"]) for a in adjs if a["kind"] == "bonus"]
        advs = [(a["usd"], a["day"]) for a in adjs if a["kind"] == "adv"]
        usd_all = sum(d["usd"] for d in dinfo)
        fs, bs, avs = sum(f for f, _ in fines), sum(b for b, _ in bonuses), sum(a for a, _ in advs)
        to_s, ti_s = sum(o["amount"] for o in outs), sum(i["amount"] for i in ins)
        zp = round(zp, 2)
        out.append({"w": w, "days": dinfo, "n": len(rows), "raw": sum(d["raw"] for d in dinfo), "usd": usd_all,
                    "zp": zp, "fines": fines, "bonuses": bonuses, "advs": advs, "fine": fs, "bonus": bs, "adv": avs,
                    "outs": outs, "ins": ins, "tr_out": to_s, "tr_in": ti_s,
                    "total": round(zp - fs + bs - avs - to_s + ti_s, 2),
                    "mine": (usd_all / 2) if w["is_self"] else (usd_all / 2 - zp)})
    return out


def active(r):
    return bool(r["n"] or r["fines"] or r["bonuses"] or r["advs"] or r["outs"] or r["ins"])


def day_totals(day, with_self=True):
    d = day if isinstance(day, str) else iso(day)
    sql = ("select c.amount from checks c join reports r on r.id=c.report_id join workers w on w.id=r.worker_id "
           "where r.status='done' and c.excl=0 and c.rd=?" + ("" if with_self else " and w.is_self=0"))
    rows = q(sql, (d,))
    return sum(r["amount"] for r in rows), len(rows)


# ───────────────────────── matnlar (ruscha) ─────────────────────────
def money(v):
    v = round(v + 0.0, 2)
    return str(int(v)) if abs(v - int(v)) < 1e-9 else f"{v:.2f}"


def n3(v):
    return f"{int(v):,}"


def dshort(d):
    return dt.date.fromisoformat(d).strftime("%d.%m")


def label(s, e):
    return dshort(iso(s)) if s == e else f"{dshort(iso(s))}–{dshort(iso(e))}"


def chk_word(n):
    if n % 10 == 1 and n % 100 != 11:
        return "чек"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "чека"
    return "чеков"


def adj_list(items):
    v = money(sum(a for a, _ in items))
    return f"{v}$ (" + "+".join(money(a) for a, _ in items) + ")" if len(items) > 1 else f"{v}$"


def zp_lines(r):
    """Зп — faqat summa. Shtraf/Bonus/Avans/Perenos/Itogo — faqat 0 dan katta bo'lsa."""
    lines = [f"Зп: {money(r['zp'])}$"]
    expr, extra = money(r["zp"]), False
    if r["fine"] > 0:
        lines.append(f"Штраф: {adj_list(r['fines'])}")
        expr += f"-{money(r['fine'])}"
        extra = True
    if r["bonus"] > 0:
        lines.append(f"Бонус: {adj_list(r['bonuses'])}")
        expr += f"+{money(r['bonus'])}"
        extra = True
    if r["adv"] > 0:
        lines.append(f"Аванс: {adj_list(r['advs'])}")
        expr += f"-{money(r['adv'])}"
        extra = True
    for o in r["outs"]:
        lines.append(f"Перенос: −{money(o['amount'])}$ → {o['other']} ({dshort(o['day'])})")
    for i in r["ins"]:
        lines.append(f"Перенос: +{money(i['amount'])}$ от {i['other']} ({dshort(i['day'])})")
    if r["tr_out"] > 0:
        expr += f"-{money(r['tr_out'])}"
        extra = True
    if r["tr_in"] > 0:
        expr += f"+{money(r['tr_in'])}"
        extra = True
    if extra:
        lines.append(f"Итого: {expr}={money(r['total'])}$")
    return lines


def zp_text(r, lab):
    return "\n".join([f"{r['w']['name']} · {lab}"] + zp_lines(r))


def short_report(r, lab):
    """Qisqa: sana, kassa, kol-vo rd."""
    L = [f"{r['w']['name']} · {lab}"]
    if len(r["days"]) <= 1:
        L += [f"Касса: {n3(r['raw'])}", f"Кол-во рд: {r['n']}", f"Касса ($): {money(r['usd'])}$"]
    else:
        for d in r["days"]:
            L.append(f"{dshort(d['day'])} · касса {n3(d['raw'])} · рд {len(d['items'])}")
        L += ["", f"Всего: касса {n3(r['raw'])} · кол-во рд: {r['n']} · {money(r['usd'])}$"]
    return "\n".join(L)


def price_note(amount):
    r = q1("select lo,hi from prices where lo<=? and hi>=? order by (hi-lo) asc limit 1", (amount, amount))
    return f" (прайс {r['lo']}–{r['hi']})" if r and r["lo"] != r["hi"] and not (amount == r["lo"] or amount == r["hi"]) else ""


def explain(r, lab, owner=False):
    w = r["w"]
    L = [f"🔎 Разбор: {w['name']} · {lab}"]
    for d in r["days"]:
        L.append(f"\n{dshort(d['day'])}:")
        cnt = OrderedDict()
        for a, u in d["items"]:
            cnt[(a, u)] = cnt.get((a, u), 0) + 1
        for (a, u), k in cnt.items():
            L.append(f"  {a} = {money(u)}$" + (f" ×{k} = {money(u * k)}$" if k > 1 else "") + price_note(a))
        L.append(f"  Касса: {n3(d['raw'])} → по прайсу {money(d['usd'])}$")
        L.append(f"  Зп: {money(d['usd'])}$ × {money(d['pct'])}% = {money(d['zp'])}$")
    if len(r["days"]) > 1:
        L.append(f"\nВсего по прайсу: {money(r['usd'])}$ · Зп: {money(r['zp'])}$")
    L.append("")
    L += zp_lines(r)
    if owner and r["n"]:
        if w["is_self"]:
            L.append(f"\n👑 Cashliy: {money(r['usd'])}$ ÷ 2 = {money(r['usd'] / 2)}$ (Артур получает вторую половину)")
        else:
            L.append(f"\n👑 Артур (партнёр): {money(r['usd'])} ÷ 2 = {money(r['usd'] / 2)}$\n"
                     f"Ваша половина: {money(r['usd'] / 2)} − зп {money(r['zp'])} = {money(r['mine'])}$")
    return "\n".join(L)


def overall_text(res, lab, owner=False):
    rows = [r for r in res if active(r) and (owner or not r["w"]["is_self"])]
    L = [f"📊 Общий отчёт · {lab}", ""]
    tn = traw = tusd = 0
    for r in rows:
        tn, traw, tusd = tn + r["n"], traw + r["raw"], tusd + r["usd"]
        L.append(f"{r['w']['name']}: рд {r['n']} · касса {n3(r['raw'])} · {money(r['usd'])}$")
        last = zp_lines(r)[-1] if len(zp_lines(r)) > 1 else zp_lines(r)[0]
        L.append(f"   {last}")
    L += ["", f"Всего: кол-во рд {tn} · касса {n3(traw)} · {money(tusd)}$"]
    return "\n".join(L)


def income_text(res, lab):
    """Faqat egaga: umumiy summa, Artur (hamkor) ulushi va sizning ulushingiz."""
    rows = [r for r in res if r["n"]]
    usd = sum(r["usd"] for r in rows)
    zps = sum(r["zp"] for r in rows if not r["w"]["is_self"])
    mine = usd / 2 - zps
    L = [f"👑 Ваш доход · {lab}", "",
         f"Общая касса: {money(usd)}$",
         f"Артур (партнёр): {money(usd)} ÷ 2 = {money(usd / 2)}$",
         f"Ваша половина: {money(usd / 2)}$",
         f"Зарплаты обработчиков: {money(zps)}$",
         f"Ваш доход: {money(usd / 2)} − {money(zps)} = {money(mine)}$", "", "По каждому:"]
    for r in rows:
        if r["w"]["is_self"]:
            L.append(f"{r['w']['name']} (своя касса): {money(r['usd'])}$ ÷ 2 = {money(r['mine'])}$")
        else:
            L.append(f"{r['w']['name']}: {money(r['usd'])}$ ÷ 2 − зп {money(r['zp'])}$ = {money(r['mine'])}$")
    return "\n".join(L)


def rating_text(res, lab):
    rows = sorted([r for r in res if r["raw"] > 0], key=lambda r: -r["raw"])
    if not rows:
        return f"Рейтинг · {lab}: данных нет"
    medal = ["🥇", "🥈", "🥉"]
    L = [f"🏆 Рейтинг кассы · {lab}", ""]
    for i, r in enumerate(rows):
        m = medal[i] if i < 3 else (f"{i + 1}\ufe0f\u20e3" if i < 9 else f"{i + 1}.")
        L.append(f"{m} 🟢 {r['w']['name']} — {n3(r['raw'])}")
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
