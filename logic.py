# -*- coding: utf-8 -*-
import os
import re
import sqlite3
import datetime as dt
from collections import OrderedDict
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Tashkent"))
DB_PATH = os.getenv("DB_PATH", "kassa.db")


def today():
    return dt.datetime.now(TZ).date()


def iso(d):
    return d.isoformat()


def week_start(d=None):
    d = d or today()
    return d - dt.timedelta(days=d.weekday())


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
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
    usd REAL,
    skip INTEGER NOT NULL DEFAULT 0
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
CREATE INDEX IF NOT EXISTS idx_checks_report ON checks(report_id);
CREATE INDEX IF NOT EXISTS idx_checks_rd ON checks(rd);
CREATE INDEX IF NOT EXISTS idx_reports_worker ON reports(worker_id, status);
CREATE INDEX IF NOT EXISTS idx_adj_worker ON adj(worker_id, day);
"""

SEED_PRICES = [
    (400, 700, 4), (750, 1150, 5), (1400, 1400, 7), (1650, 2250, 7),
    (2300, 2800, 10), (3300, 3700, 13), (4000, 4000, 17), (6000, 6000, 25),
    (7000, 7000, 25), (9000, 12200, 100), (19200, 19200, 200), (49200, 49200, 500)
]

SEED_WORKERS = [
    ("Cashliy", 50, 1),
    ("Bayzed", 41, 0),
    ("Cred", 36, 0),
    ("Lazarus", 30, 0),
    ("Baxa", 30, 0)
]

_db = None


def _migrate(c):
    cols = [r["name"] for r in c.execute("PRAGMA table_info(checks)")]
    if "skip" not in cols:
        c.execute("ALTER TABLE checks ADD COLUMN skip INTEGER NOT NULL DEFAULT 0")
    # Bir martalik: eski (test) hisobotlarni tozalash va Cashliy = 50%
    if not c.execute("select 1 from meta where k='reset_v3'").fetchone():
        for t in ("checks", "reports", "adj", "advances"):
            c.execute(f"delete from {t}")
        s = c.execute("select id from workers where is_self=1").fetchone()
        if s:
            c.execute("delete from pct where worker_id=?", (s["id"],))
            c.execute("insert into pct values (?,?,?)", (s["id"], "2000-01-01", 50))
        c.execute("insert into meta values ('reset_v3','1')")


def db():
    global _db
    if _db is None:
        _db = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db.row_factory = sqlite3.Row
        try:
            _db.execute("PRAGMA journal_mode=WAL")
            _db.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass
        _db.executescript(SCHEMA)
        if not _db.execute("select 1 from workers").fetchone():
            for name, p, s in SEED_WORKERS:
                cur = _db.execute("insert into workers (name, is_self) values (?,?)", (name, s))
                _db.execute("insert into pct values (?,?,?)", (cur.lastrowid, "2000-01-01", p))
        if not _db.execute("select 1 from prices").fetchone():
            _db.executemany("insert into prices (lo, hi, usd) values (?,?,?)", SEED_PRICES)
        _migrate(_db)
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


# ---------- workers ----------

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


def set_percent(wid, percent):
    """Foiz shu haftaning dushanbasidan boshlab o'zgaradi."""
    mon = iso(week_start())
    x("delete from pct where worker_id=? and from_day>=?", (wid, mon))
    has = ql("select 1 from pct where worker_id=?", (wid,))
    x("insert into pct values (?,?,?)", (wid, mon if has else "2000-01-01", percent))


def set_active(wid, v):
    x("update workers set active=? where id=?", (v, wid))


def worker_by_name(text):
    names = sorted(workers(), key=lambda w: len(w["name"]), reverse=True)
    for w in names:
        if re.search(r"(?<!\w)" + re.escape(w["name"]) + r"(?!\w)", text, re.I):
            return w["id"]
    return None


# ---------- users ----------

def user(tg_id):
    return ql("select * from users where tg_id=?", (tg_id,))


def upsert_user(tg_id, name, role, worker_id):
    x("insert into users (tg_id, name, role, worker_id) values (?,?,?,?) "
      "on conflict(tg_id) do update set name=excluded.name, role=excluded.role, worker_id=excluded.worker_id",
      (tg_id, name, role, worker_id))


def admins():
    return [r["tg_id"] for r in q("select tg_id from users where role='admin'")]


def all_users():
    return q("select * from users order by role, tg_id")


def users_of_worker(wid):
    return q("select * from users where worker_id=?", (wid,))


def set_role(tg_id, role):
    x("update users set role=? where tg_id=?", (role, tg_id))


def remove_admin(tg_id):
    u = user(tg_id)
    if not u:
        return
    if u["worker_id"]:
        x("update users set role='worker' where tg_id=?", (tg_id,))
    else:
        x("delete from users where tg_id=?", (tg_id,))


# ---------- chats ----------

def chat(cid):
    return ql("select * from chats where chat_id=?", (cid,))


def upsert_chat(cid, title):
    x("insert into chats (chat_id, title) values (?,?) on conflict(chat_id) do update set title=excluded.title",
      (cid, title))


def set_watch(cid, v):
    x("update chats set watch=? where chat_id=?", (v, cid))


# ---------- prices ----------

def price_for(amount):
    r = ql("select usd from prices where lo<=? and hi>=? order by (hi-lo) asc limit 1", (amount, amount))
    return r["usd"] if r else None


def price_row(pid):
    return ql("select * from prices where id=?", (pid,))


def list_prices():
    return q("select * from prices order by lo")


def refresh_prices():
    for c in q("select id, amount from checks where usd is null"):
        p = price_for(c["amount"])
        if p is not None:
            x("update checks set usd=? where id=?", (p, c["id"]))


def reprice_week():
    """Narx o'zgarsa, shu haftaning dushanbasidan boshlab chek narxlari qayta hisoblanadi."""
    mon = iso(week_start())
    rows = [(price_for(c["amount"]), c["id"]) for c in q("select id, amount from checks where rd>=?", (mon,))]
    if rows:
        db().executemany("update checks set usd=? where id=?", rows)
        db().commit()


def add_price(lo, hi, usd):
    x("insert into prices (lo, hi, usd) values (?,?,?)", (min(lo, hi), max(lo, hi), usd))
    refresh_prices()
    reprice_week()


def update_price(pid, lo, hi, usd):
    x("update prices set lo=?, hi=?, usd=? where id=?", (min(lo, hi), max(lo, hi), usd, pid))
    refresh_prices()
    reprice_week()


def del_price(pid):
    x("delete from prices where id=?", (pid,))
    reprice_week()


# ---------- parsing ----------

RD_FIND = re.compile(r"(?<![A-Za-zА-Яа-яЁё])(?:rd|рд)\W{0,3}(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?", re.I)
DATE_RE = re.compile(r"(?<!\d)\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?(?!\d)")
DATE_G = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?!\d)")
TIME_RE = re.compile(r"\d{1,2}:\d{2}")
# 1950, 1,950, 1.950 (nuqta/vergul = minglik ajratgich). Bo'shliq ajratgich EMAS.
NUM_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[.,]\d{3})+|\d{3,6})(?!\d)")
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


def parse_dates(text, ref=None):
    out = []
    for m in DATE_G.finditer(text):
        d = mk_date(m.group(1), m.group(2), m.group(3), ref)
        if d:
            out.append(d)
    return out


def has_rd(text):
    return bool(RD_FIND.search(text))


def line_amounts(line):
    """Qatordagi chek summalari. Sana (08.10.), Rd sanasi va vaqt hisobga olinmaydi."""
    if TOTAL_RE.search(line):
        return []
    body = line.rsplit("=", 1)[0] if "=" in line else line
    body = RD_FIND.sub(" ", body)
    body = DATE_RE.sub(" ", body)
    body = TIME_RE.sub(" ", body)
    out = []
    for m in NUM_RE.finditer(body):
        v = int(re.sub(r"\D", "", m.group(1)))
        if 300 <= v <= 99999:
            out.append(v)
    return out


def parse_report(text, ref=None):
    """Hisobot kuni = 'Rd - dd.mm' sanasi.
    - chek qatorida Rd bo'lsa, shu qatorning Rd si;
    - chekdan KEYINGI alohida 'Rd - 11.10' qatori o'zidan oldingi cheklarga tegishli;
    - agar xabar 'Rd' bilan boshlansa (sarlavha), undan keyingi cheklar shu Rd ga tegishli;
    - Rd umuman bo'lmasa, xabar yuborilgan kun (ref)."""
    ref = ref or today()
    ents = [(find_rd(line, ref), line_amounts(line)) for line in text.splitlines()]

    mode, seen = "suffix", False
    for rd, amts in ents:
        if rd and not amts:
            mode = "suffix" if seen else "header"
            break
        if amts:
            seen = True

    slots, pending, cur = [], [], None
    for rd, amts in ents:
        if rd and amts:
            for a in amts:
                slots.append([rd, a])
            cur = rd
        elif rd:
            if mode == "suffix":
                for i in pending:
                    slots[i][0] = rd
                pending = []
            cur = rd
        elif amts:
            for a in amts:
                if mode == "header" and cur:
                    slots.append([cur, a])
                else:
                    slots.append([None, a])
                    pending.append(len(slots) - 1)
    fill = cur or ref
    return [(rd or fill, a) for rd, a in slots]


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
    tok = re.compile(
        rf"(?P<kind>{KIND_PAT})|(?P<name>(?<!\w)(?:{name_pat})(?!\w))|(?P<num>\d+(?:[.,]\d+)?)\s*\$?",
        re.I)
    t2 = DATE_RE.sub("", text)
    kind, wid = None, None
    items = []
    for m in tok.finditer(t2):
        if m.group("kind"):
            kind = kind_of(m.group("kind"))
        elif m.group("name"):
            wid = by_name.get(m.group("name").lower())
        elif m.group("num") and kind:
            items.append({"kind": kind, "worker_id": wid, "usd": float(m.group("num").replace(",", "."))})
    return items


# ---------- reports ----------

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
    if r is None:
        return None
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


def replace_old(rid):
    """Shu ishchining shu kunlar uchun eski hisobotlarini o'chiradi: yangisi eskisini almashtiradi."""
    r = report(rid)
    if r is None or r["worker_id"] is None:
        return []
    replaced = []
    for d in report_days(rid):
        olds = q("select distinct ch.report_id as rid from checks ch join reports ro on ro.id=ch.report_id "
                 "where ro.worker_id=? and ch.rd=? and ch.report_id<>?", (r["worker_id"], d, rid))
        for o in olds:
            x("delete from checks where report_id=? and rd=?", (o["rid"], d))
            if not ql("select 1 from checks where report_id=?", (o["rid"],)):
                x("delete from reports where id=?", (o["rid"],))
        if olds:
            replaced.append(d)
    return replaced


def worker_days(wid, limit=10):
    return q("select ch.rd as rd, count(*) as n from checks ch join reports ro on ro.id=ch.report_id "
             "where ro.worker_id=? and ch.rd is not null group by ch.rd order by ch.rd desc limit ?",
             (wid, limit))


def day_checks(wid, day):
    return q("select ch.id, ch.amount, ch.usd, ch.skip from checks ch join reports ro on ro.id=ch.report_id "
             "where ro.worker_id=? and ch.rd=? order by ch.id", (wid, day))


def toggle_check(cid):
    x("update checks set skip=1-skip where id=?", (cid,))


def move_day(from_wid, to_wid, day):
    """from_wid ning shu kundagi hisobotini to_wid ga o'tkazadi (from_wid o'sha kuni ishlamagan)."""
    rids = [r["rid"] for r in q(
        "select distinct ch.report_id as rid from checks ch join reports ro on ro.id=ch.report_id "
        "where ro.worker_id=? and ch.rd=?", (from_wid, day))]
    moved = 0
    for rid in rids:
        r = report(rid)
        cur = x("insert into reports (worker_id, chat_id, msg_id, sender_id, raw, status, created) "
                "values (?,?,?,?,?,?,?)",
                (to_wid, r["chat_id"], None, r["sender_id"], r["raw"], r["status"], dt.datetime.now(TZ).isoformat()))
        cur2 = x("update checks set report_id=? where report_id=? and rd=?", (cur.lastrowid, rid, day))
        moved += cur2.rowcount
        if not ql("select 1 from checks where report_id=?", (rid,)):
            x("delete from reports where id=?", (rid,))
    return moved


# ---------- adjustments / advances ----------

def add_adj(worker_id, kind, usd, day, raw, chat_id=None, msg_id=None, pending=0):
    return x("insert into adj (worker_id, kind, usd, day, raw, chat_id, msg_id, pending) values (?,?,?,?,?,?,?,?)",
             (worker_id, kind, usd, day, raw, chat_id, msg_id, pending)).lastrowid


def adj(aid):
    return ql("select * from adj where id=?", (aid,))


def adj_resolve(aid, worker_id=None, kind=None):
    a = adj(aid)
    wid = worker_id if worker_id is not None else a["worker_id"]
    kd = kind if kind is not None else a["kind"]
    pend = 0 if (wid is not None and kd is not None) else 1
    x("update adj set worker_id=?, kind=?, pending=? where id=?", (wid, kd, pend, aid))


def adj_toggle(aid):
    x("update adj set cancelled=1-cancelled where id=?", (aid,))


def adj_delete(aid):
    x("delete from adj where id=?", (aid,))


def drop_adj_msg(chat_id, msg_id):
    x("delete from adj where chat_id=? and msg_id=?", (chat_id, msg_id))


def pending_adjs():
    return [r["id"] for r in q("select id from adj where pending=1")]


def add_advance(worker_id, usd, day, note=""):
    return x("insert into advances (worker_id, usd, day, note) values (?,?,?,?)",
             (worker_id, usd, day, note)).lastrowid


def worker_advances(wid, start, end):
    s, e = iso(start), iso(end)
    r = q("select sum(usd) as total from advances where worker_id=? and day between ? and ?", (wid, s, e))[0]
    return r["total"] or 0.0


# ---------- periods & calc ----------

def period_range(key):
    t = today()
    if key == "td":
        return t, t
    if key == "yd":
        y = t - dt.timedelta(days=1)
        return y, y
    if key == "tw":
        return week_start(t), t
    if key == "lw":
        mon = week_start(t) - dt.timedelta(days=7)
        return mon, mon + dt.timedelta(days=6)
    if key == "tm":
        return t.replace(day=1), t
    if key == "lm":
        last = t.replace(day=1) - dt.timedelta(days=1)
        return last.replace(day=1), last
    return t, t


def calc(start, end, only=None):
    s, e = iso(start), iso(end)
    out = []
    for w in workers(active_only=False):
        if only and w["id"] != only:
            continue
        rows = q("select ch.rd, ch.amount, ch.usd from checks ch join reports ro on ro.id=ch.report_id "
                 "where ro.worker_id=? and ro.status='done' and ch.skip=0 and ch.rd between ? and ? "
                 "order by ch.rd, ch.id", (w["id"], s, e))
        adjs = q("select * from adj where worker_id=? and cancelled=0 and pending=0 and day between ? and ?",
                 (w["id"], s, e))
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
            "fine": fs, "bonus": bs, "adv": adv, "total": total
        })
    return out


def has_data(r):
    return r["n"] > 0 or bool(r["fines"]) or bool(r["bonuses"]) or r["adv"] > 0


# ---------- owner income ----------

def owner_income(res):
    """Ega daromadi:
    Umumiy kassa ($) = hamma (Cashliy ham) kassasi.
    Artur = umumiy / 2,  ega yarmi = umumiy / 2.
    Ega daromadi = umumiy / 2 - (Cashliy'dan boshqa hamma ishchilar zp yig'indisi).
    """
    rows = [r for r in res if r["n"] > 0]
    total = round(sum(r["usd"] for r in rows), 2)
    half = round(total / 2, 2)
    zps = round(sum(r["zp"] for r in rows if not r["w"]["is_self"]), 2)
    income = round(half - zps, 2)
    per = []
    for r in rows:
        if r["w"]["is_self"]:
            per.append({"name": r["w"]["name"], "usd": r["usd"], "zp": None,
                        "mine": round(r["usd"] / 2, 2), "self": True})
        else:
            per.append({"name": r["w"]["name"], "usd": r["usd"], "zp": r["zp"],
                        "mine": round(r["usd"] / 2 - r["zp"], 2), "self": False})
    return {"total": total, "half": half, "zps": zps, "income": income, "per": per}


def owner_lines(inc):
    out = [
        f"Общая касса: {money(inc['total'])}$",
        f"Артур (партнёр): {money(inc['total'])} ÷ 2 = {money(inc['half'])}$",
        f"Ваша половина: {money(inc['half'])}$",
        f"Зарплаты обработчиков: {money(inc['zps'])}$",
        f"Ваш доход: {money(inc['half'])} − {money(inc['zps'])} = {money(inc['income'])}$",
    ]
    if inc["per"]:
        out.append("")
        for p in inc["per"]:
            if p["self"]:
                out.append(f"{p['name']}: {money(p['usd'])}$ ÷ 2 = {money(p['mine'])}$")
            else:
                out.append(f"{p['name']}: {money(p['usd'])}$ ÷ 2 − зп {money(p['zp'])}$ = {money(p['mine'])}$")
    return out


# ---------- text ----------

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


def adj_lines(r):
    """Штраф / Бонус / Аванс / Итого: faqat 0 dan katta bo'lsa."""
    out = []
    if r["fines"]:
        out.append(f"Штраф: {adj_list(r['fines'])}")
    if r["bonuses"]:
        out.append(f"Бонус: {adj_list(r['bonuses'])}")
    if r["adv"] > 0:
        out.append(f"Аванс: {money(r['adv'])}$")
    if out:
        f = money(r["zp"])
        if r["fines"]:
            f += f"-{money(r['fine'])}"
        if r["bonuses"]:
            f += f"+{money(r['bonus'])}"
        if r["adv"] > 0:
            f += f"-{money(r['adv'])}"
        out.append(f"Итого: {f}={money(r['total'])}$")
    return out


def brief(r, updated=False):
    """Qisqa kunlik xabar."""
    d = r["days"][0]
    lines = []
    if updated:
        lines.append("♻️ Отчёт обновлён (старый заменён)")
    lines += [
        r["w"]["name"],
        f"Дата: {dshort(d['day'])}",
        f"Касса: {n3(d['raw'])}",
        f"Кол-во рд: {len(d['items'])}",
        f"Касса ($): {money(d['usd'])}$",
    ]
    return "\n".join(lines)


def worker_report(r, lab):
    """Ishchi o'zi ko'radigan hisobot (faqat o'zining zp'si)."""
    out = [f"{r['w']['name']} ({lab})"]
    for d in r["days"]:
        out.append(f"{dshort(d['day'])}: касса {n3(d['raw'])}, рд {len(d['items'])}")
    out.append(f"Зп: {money(r['zp'])}$")
    out += adj_lines(r)
    return "\n".join(out)


def explain(r, lab, owner=False):
    """Razbor: faqat admin va egaga."""
    out = [f"Разбор: {r['w']['name']} ({lab})"]
    if not r["days"]:
        out.append("Чеков нет.")
    for d in r["days"]:
        out.append("")
        out.append(f"📅 {dshort(d['day'])} — кол-во рд: {len(d['items'])}")
        cnt = OrderedDict()
        for a, u in d["items"]:
            cnt[(a, u)] = cnt.get((a, u), 0) + 1
        for (a, u), k in cnt.items():
            if u == 0:
                out.append(f"{a} = ❓ нет цены")
            elif k > 1:
                out.append(f"{a} = {money(u)}$ × {k} = {money(u * k)}$")
            else:
                out.append(f"{a} = {money(u)}$")
        out.append("Касса: " + " + ".join(str(a) for a, _ in d["items"]) + f" = {n3(d['raw'])}")
        out.append(f"По прайсу: {money(d['usd'])}$")
        out.append(f"Зп: {money(d['usd'])}$ × {money(d['pct'])}% = {money(d['usd'] * d['pct'] / 100)}$")
    out.append("")
    out.append(f"💰 Зп: {money(r['zp'])}$")
    extra = adj_lines(r)
    if extra:
        out += extra
    if owner and r["n"]:
        out.append("")
        if r["w"]["is_self"]:
            out.append(f"💼 Ваша доля: {money(r['usd'])}$ ÷ 2 = {money(r['usd'] / 2)}$")
        else:
            out.append(f"💼 Ваша доля: {money(r['usd'])}$ ÷ 2 − зп {money(r['zp'])}$ = "
                       f"{money(r['usd'] / 2 - r['zp'])}$")
    return "\n".join(out)


def overall_text(res, lab, owner=False):
    out = [f"📊 Общий отчёт ({lab})", ""]
    tn, traw, tusd = 0, 0, 0.0
    for r in res:
        n = r["n"]
        tn += n
        traw += r["raw"]
        tusd += r["usd"]
        out.append(f"{r['w']['name']}: {n} {chk_word(n)}, касса {n3(r['raw'])}, зп {money(r['total'])}$")
    out += ["", f"Всего чеков: {tn}", f"Касса: {n3(traw)}", f"Касса ($): {money(tusd)}$"]
    if owner:
        inc = owner_income(res)
        out += ["", "💼 Ваш доход (видно только вам):"] + owner_lines(inc)
    return "\n".join(out)


def rating_text(res, lab):
    rows = sorted([r for r in res if r["n"] > 0], key=lambda r: r["usd"], reverse=True)
    medal = ["🥇", "🥈", "🥉"]
    out = [f"🏆 Рейтинг кассы ({lab})", ""]
    for i, r in enumerate(rows):
        m = medal[i] if i < 3 else f"{i + 1}."
        out.append(f"{m} {r['w']['name']} — {n3(r['raw'])} ({r['n']} {chk_word(r['n'])})")
    return "\n".join(out)


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
