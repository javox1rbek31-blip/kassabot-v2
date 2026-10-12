# -*- coding: utf-8 -*-
"""Kassa-bot v3 (python-telegram-bot 21.x). Tugmalar orqali boshqariladi, matnlar ruscha.
Guruh/kanalda bot HECH NARSA yozmaydi (faqat egasi/admin /top bersa reyting chiqaradi)."""
import os
import re
import asyncio
import logging
import datetime as dt

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup as K, ReactionTypeEmoji
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler, MessageHandler,
                          ChatMemberHandler, ContextTypes, filters)

import logic as L

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kassabot")

TOKEN = os.environ["BOT_TOKEN"]
OWNER = int(os.getenv("OWNER_ID", "8785896869"))
TOP_CMDS = {"/top": "td", "/topyest": "yd", "/topweek": "tw", "/toplast": "lw"}


def B(text, data):
    return InlineKeyboardButton(text, callback_data=data)


# ───────────────────────── yordamchilar ─────────────────────────
def owner_ids():
    return [OWNER] + [o for o in L.owners() if o != OWNER]


def is_owner(uid):
    """Ega = OWNER_ID akkaunti + egasi 'bu mening ikkinchi akkauntim' deb tasdiqlagan akkauntlar (@cashliy)."""
    return uid is not None and (uid == OWNER or uid in L.owners())


def is_staff(uid):
    return is_owner(uid) or (uid is not None and uid in L.admins())


def staff_ids():
    return owner_ids() + [a for a in L.admins() if a not in owner_ids()]


def vis_ws(uid, active_only=True):
    """Cashliy (egasining o'zi) faqat egaga ko'rinadi."""
    return [w for w in L.workers(active_only) if is_owner(uid) or not w["is_self"]]


def recips(wid):
    w = L.worker(wid) if wid else None
    return owner_ids() if (w and w["is_self"]) else staff_ids()


def menu_btn():
    return [B("🔙 Меню", "m:main")]


def period_rows(prefix):
    return [[B("Сегодня", f"{prefix}:td"), B("Вчера", f"{prefix}:yd")],
            [B("Эта неделя", f"{prefix}:tw"), B("Прошлая неделя", f"{prefix}:lw")],
            [B("Этот месяц", f"{prefix}:tm"), B("Прошлый месяц", f"{prefix}:lm")], menu_btn()]


def day_rows(prefix, n=6):
    t = L.today()
    ds = [t - dt.timedelta(days=i) for i in range(n)]
    return [[B(d.strftime("%d.%m"), f"{prefix}:{d.isoformat()}") for d in ds[i:i + 2]] for i in range(0, n, 2)]


async def send(bot, chat_id, text, kb=None):
    last = None
    parts = L.chunks(text)
    for i, part in enumerate(parts):
        try:
            last = await bot.send_message(chat_id, part, reply_markup=kb if i == len(parts) - 1 else None)
        except Exception as e:
            log.warning("send to %s failed: %s", chat_id, e)
    return last


async def send_many(app, ids, text, kb=None):
    out = []
    for sid in ids:
        m = await send(app.bot, sid, text, kb)
        if m:
            out.append(m)
    return out


async def edit(q, text, kb=None):
    try:
        await q.edit_message_text(text, reply_markup=kb)
    except Exception as e:
        if "not modified" not in str(e).lower():
            log.warning("edit failed: %s", e)


def main_kb(uid):
    pend = len(L.pending_reports()) + len(L.pending_adjs())
    rows = [[B("📊 Отчёты", "rs:menu"), B("🏆 Рейтинг", "rt:menu")],
            [B("📆 Отчёт за дату", "dt:all"), B("📄 PDF", "pdf:menu")],
            [B("👥 Обработчики", "w:list"), B("💲 Прайс", "pr:menu")],
            [B("💬 Чаты", "ch:menu"), B(f"❓ Вопросы ({pend})", "q:list")],
            [B("👑 Админы и доступ", "adm:menu")]]
    if is_owner(uid):
        rows.insert(2, [B("💰 Мой доход / Cashliy", "inc:menu")])
    return K(rows)


MAIN_TEXT = "Главное меню.\nОтчёт вручную: просто отправьте мне суммы сюда (например: Bayzed 1950 2600)."


# ───────────────────────── hisobotlar ─────────────────────────
def short_msg(wid, day_iso, for_uid):
    d = dt.date.fromisoformat(day_iso)
    res = L.calc(d, d, only=wid)
    if not res or not res[0]["n"]:
        return None
    text = L.short_report(res[0], L.label(d, d))
    tot, n = L.day_totals(day_iso, with_self=is_owner(for_uid))
    if tot and tot != res[0]["raw"]:
        text += f"\nВсего за день (все): {L.n3(tot)}"
    return text


async def finalize(app, rid):
    r = L.report(rid)
    if r is None or r["status"] == "done":
        return
    L.set_report_status(rid, "done")
    wid = r["worker_id"]
    w = L.worker(wid)
    for n in L.supersede(rid):
        am = ", ".join(map(str, n["amounts"]))
        if n["kind"] == "replace":
            await send_many(app, recips(wid), f"♻️ {w['name']}: прежний отчёт ({am}) заменён новым — дважды не считается.",
                            K([[B("↩️ Вернуть старый", f"rrs:{n['old']}")]]))
        else:
            await send_many(app, recips(wid), f"⚠️ {w['name']}: чек {am} уже был. Это повтор?",
                            K([[B("Да, убрать новый", f"rdup:{rid}"), B("Нет, оба верны", "okk")]]))
    for d in L.report_days(rid):
        for sid in recips(wid):
            text = short_msg(wid, d, sid)
            if text:
                await send(app.bot, sid, text, K([[B("💰 Зп", f"zp:{wid}:{d}")]]))


async def show_zp(bot, app, uid, wid, key):
    """Razbor + toza oylik. Cashliy — faqat ega."""
    w = L.worker(wid)
    if not w or (w["is_self"] and not is_owner(uid)):
        return
    s, e = L.period_range(key)
    res = L.calc(s, e, only=wid)
    if not res or not L.active(res[0]):
        await send(bot, uid, f"{w['name']}: за {L.label(s, e)} данных нет.", K([menu_btn()]))
        return
    lab = L.label(s, e)
    await send(bot, uid, L.explain(res[0], lab, owner=is_owner(uid)))
    text = L.zp_text(res[0], lab)
    kb = None
    if not w["is_self"] and [u for u in L.users_of_worker(wid) if not is_staff(u["tg_id"])]:
        app.bot_data.setdefault("prev", {})[(wid, key)] = text
        kb = K([[B(f"📤 Отправить: {w['name']}", f"snd:{wid}:{key}")]])
    await send(bot, uid, text, kb)


async def send_worker_short(bot, uid, key, wid):
    w = L.worker(wid)
    s, e = L.period_range(key)
    res = L.calc(s, e, only=wid)
    if not res or not res[0]["n"]:
        return False
    await send(bot, uid, L.short_report(res[0], L.label(s, e)), K([[B("💰 Зп", f"zp:{wid}:{key}")], menu_btn()]))
    return True


# ───────────────────────── tushunmagan joylarni so'rash ─────────────────────────
async def advance(app, rid):
    r = L.report(rid)
    if r is None:
        return
    need = L.report_needs(rid)
    if need is None:
        await finalize(app, rid)
        return
    kind, arg = need
    snippet = (r["raw"] or "")[:350]
    if kind == "worker":
        who = f"\nОтправитель: {r['sender_name']}" if r["sender_name"] else ""
        for sid in staff_ids():
            rows = [[B(w["name"], f"pw:{rid}:{w['id']}")] for w in vis_ws(sid)]
            rows.append([B("🗄 Из архива", f"pwa:{rid}"), B("🚫 Это не отчёт", f"pwi:{rid}")])
            await send(app.bot, sid, f"❓ Чей это отчёт?{who}\n\n{snippet}", K(rows))
    elif kind == "date":
        rows = day_rows(f"pd:{rid}", 4)
        rows.append([B("📅 Другая дата", f"pdo:{rid}"), B("🚫 Это не отчёт", f"pwi:{rid}")])
        name = L.worker(r["worker_id"])["name"]
        await send_many(app, recips(r["worker_id"]), f"❓ {name}: на какой день этот отчёт?\n\n{snippet}", K(rows))
    elif kind == "price":
        name = L.worker(r["worker_id"])["name"]
        msgs = await send_many(app, recips(r["worker_id"]),
                               f"❓ {name}: сколько $ стоит чек {arg:,}? В прайсе такой суммы нет.\n"
                               f"Ответьте на это сообщение числом (например 12). Я запомню цену.\n\n{snippet}")
        for m in msgs:
            app.bot_data.setdefault("pq", {})[(m.chat_id, m.message_id)] = (rid, arg)


KIND_RU = {"fine": "штраф", "bonus": "бонус", "adv": "аванс"}


async def ask_adj(app, aid):
    a = L.adj(aid)
    if a is None:
        return
    snippet = (a["raw"] or "")[:300]
    if a["worker_id"] is None:
        rows = [[B(w["name"], f"aw:{aid}:{w['id']}")] for w in L.workers(with_self=False)]
        rows.append([B("🚫 Игнорировать", f"ai:{aid}")])
        await send_many(app, staff_ids(), f"❓ Кому {KIND_RU.get(a['kind'], 'штраф/бонус/аванс')} {L.money(a['usd'])}$?\n\n{snippet}", K(rows))
    elif a["kind"] is None:
        rows = [[B("Штраф", f"ak:{aid}:fine"), B("Бонус", f"ak:{aid}:bonus"), B("Аванс", f"ak:{aid}:adv")],
                [B("🚫 Игнорировать", f"ai:{aid}")]]
        w = L.worker(a["worker_id"])["name"]
        await send_many(app, staff_ids(), f"❓ {w}: {L.money(a['usd'])}$ — штраф, бонус или аванс?\n\n{snippet}", K(rows))
    else:
        w = L.worker(a["worker_id"])["name"]
        await send_many(app, staff_ids(), f"❓ Правильно понял? {w}: {KIND_RU[a['kind']]} {L.money(a['usd'])}$\n\nСообщение: {snippet}",
                        K([[B("✅ Верно", f"ay:{aid}"), B("🚫 Нет", f"ai:{aid}")]]))


async def confirm_adjs(app, ids):
    lines, rows = [], []
    for aid in ids:
        a = L.adj(aid)
        w = L.worker(a["worker_id"])["name"]
        t = KIND_RU[a["kind"]].capitalize()
        lines.append(f"{w}: {t} {L.money(a['usd'])}$ ({L.dshort(a['day'])})")
        rows.append([B(f"↩️ Отменить: {w} {t.lower()} {L.money(a['usd'])}$", f"ac:{aid}")])
    rows.append(menu_btn())
    await send_many(app, staff_ids(), "✅ Записано:\n" + "\n".join(lines), K(rows))


async def handle_adjust(app, text, day, chat_id, msg_id):
    items, clean = L.parse_adjustments(text)
    if not items:
        await send_many(app, staff_ids(), f"❓ Штраф/бонус/аванс увидел, но сумму не понял:\n\n{text[:300]}")
        return
    done = []
    for it in items:
        ok = bool(it["worker_id"] and it["kind"] and clean)
        aid = L.add_adj(it["worker_id"], it["kind"], it["usd"], L.iso(day), text, chat_id, msg_id, 0 if ok else 1)
        if ok:
            done.append(aid)
        else:
            await ask_adj(app, aid)
    if done:
        await confirm_adjs(app, done)


# ───────────────────────── guruh / kanal ─────────────────────────
async def react(bot, chat_id, msg_id):
    try:
        await bot.set_message_reaction(chat_id=chat_id, message_id=msg_id, reaction=[ReactionTypeEmoji("👀")])
    except Exception as e:
        log.info("reaction failed: %s", e)


async def group_top(app, chat_id, text):
    cmd = text.split()[0].split("@")[0].lower()
    if cmd in TOP_CMDS:
        s, e = L.period_range(TOP_CMDS[cmd])
        await send(app.bot, chat_id, L.rating_text(L.calc(s, e), L.label(s, e)))


async def on_chat_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg, chat = update.effective_message, update.effective_chat
    if not msg or not chat:
        return
    text = (msg.text or msg.caption or "").strip()
    if not text:
        return
    app = ctx.application
    uid = msg.from_user.id if msg.from_user else None
    anon = uid is None or uid == L.ANON or msg.sender_chat is not None
    staff = anon or is_staff(uid)
    edited = bool(update.edited_message or update.edited_channel_post)

    row = L.chat(chat.id)
    if row is None:
        L.upsert_chat(chat.id, chat.title or str(chat.id))
        if staff:
            L.set_watch(chat.id, 1)
            row = L.chat(chat.id)
        else:
            await send(app.bot, OWNER, f"➕ Новый чат «{chat.title}». Читать там отчёты?",
                       K([[B("✅ Следить", f"cw:{chat.id}:1"), B("🚫 Нет", f"cw:{chat.id}:0")]]))
            return
    if not row["watch"]:
        return

    if text.startswith("/"):
        if staff and not edited:
            await group_top(app, chat.id, text)
        return

    day = L.post_day(msg.date)

    # shtraf / bonus / avans — faqat egasi/adminlar
    if staff and L.has_kind(text):
        if edited:
            L.drop_adj_msg(chat.id, msg.message_id)
        await handle_adjust(app, text, day, chat.id, msg.message_id)
        return

    # hisobot — faqat raqamlardan iborat xabar; boshqa hech narsa o'qilmaydi
    if not L.is_report_like(text):
        return
    wid = None
    if is_owner(uid):
        wid = L.self_worker()["id"]
        checks = L.report_checks(text, day, True)
    else:
        if uid and not anon:
            u = L.user(uid)
            if u and u["worker_id"]:
                wid = u["worker_id"]
                L.touch_user(uid, msg.from_user.full_name)
        checks = L.report_checks(text, day, False)
    if not checks:
        return
    await react(ctx.bot, chat.id, msg.message_id)
    sname = ""
    if msg.from_user:
        sname = msg.from_user.full_name + (f" @{msg.from_user.username}" if msg.from_user.username else "")
    old = L.report_amounts_by_msg(chat.id, msg.message_id) if edited else None
    rid = L.create_report(wid, chat.id, msg.message_id, uid, text, checks, sname)
    if old is not None:
        nm = L.worker(wid)["name"] if wid else (sname or "?")
        await send_many(app, recips(wid), f"✏️ {nm} изменил отчёт:\nбыло: {', '.join(map(str, old)) or '—'}\n"
                                          f"стало: {', '.join(str(a) for _, a in checks)}\nПересчитываю ⬇️")
    await advance(app, rid)


async def on_my_chat_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cm = update.my_chat_member
    chat = cm.chat
    if chat.type == "private":
        return
    st = cm.new_chat_member.status
    if st in ("member", "administrator"):
        L.upsert_chat(chat.id, chat.title or str(chat.id))
        if is_staff(cm.from_user.id if cm.from_user else None):
            L.set_watch(chat.id, 1)
            await send(ctx.bot, OWNER, f"✅ Слежу за отчётами в «{chat.title}».")
        elif not L.chat(chat.id)["watch"]:
            await send(ctx.bot, OWNER, f"➕ Бота добавили в «{chat.title}» (добавил не админ). Следить?",
                       K([[B("✅ Следить", f"cw:{chat.id}:1"), B("🚪 Выйти из чата", f"cl:{chat.id}")]]))
    elif st in ("left", "kicked"):
        L.upsert_chat(chat.id, chat.title or str(chat.id))
        L.set_watch(chat.id, 0)


# ───────────────────────── shaxsiy chat ─────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if update.effective_chat.type != "private":
        return
    ctx.user_data.pop("st", None)
    if is_staff(u.id):
        await update.message.reply_text(MAIN_TEXT, reply_markup=main_kb(u.id))
        return
    if L.user(u.id):
        return  # allaqachon qabul qilingan: jim
    # begona: unga hech narsa ko'rinmaydi, egaga/adminga so'rov ketadi
    ctx.application.bot_data.setdefault("names", {})[u.id] = u.full_name
    asked = ctx.application.bot_data.setdefault("asked", set())
    if u.id in asked:
        return
    asked.add(u.id)
    uname = f"@{u.username}" if u.username else "без username"
    for sid in staff_ids():
        rows = [[B(w["name"], f"ul:{u.id}:{w['id']}")] for w in vis_ws(sid) if not w["is_self"]]
        rows.append([B("➕ Новое имя", f"un:{u.id}")])
        if sid == OWNER:
            rows.append([B("👑 Это мой Cashliy-аккаунт (я)", f"uo:{u.id}")])
        rows.append([B("👑 Админ", f"ua:{u.id}"), B("🚫 Отказать", f"ux:{u.id}")])
        await send(ctx.bot, sid, f"🆕 Запрос доступа: {u.full_name} ({uname}, ID {u.id})\n"
                                 f"Привязать к обработчику (ему будет уходить только его зарплата, и только когда вы нажмёте «Отправить»):",
                   K(rows))


async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text(f"Ваш ID: {update.effective_user.id}")


# ───────────────────────── karta va ekranlar ─────────────────────────
def card(w):
    p = L.percent_on(w["id"], L.today())
    users = L.users_of_worker(w["id"])
    tg = "подключён" if users else "не подключён"
    adm = " · 👑 админ" if any(u["role"] == "admin" for u in users) else ""
    st = "активен" if w["active"] else "в архиве"
    return f"{'👑 ' if w['is_self'] else ''}{w['name']} ({st})\nПроцент: {L.money(p)}%\nTelegram: {tg}{adm}"


def card_kb(w):
    wid = w["id"]
    rows = [[B("📊 Сегодня", f"rep:td:{wid}"), B("📅 Вчера", f"rep:yd:{wid}")],
            [B("🗓 Эта неделя", f"rep:tw:{wid}"), B("🗓 Прошлая неделя", f"rep:lw:{wid}")],
            [B("📈 Этот месяц", f"rep:tm:{wid}"), B("📉 Прошлый месяц", f"rep:lm:{wid}")],
            [B("📆 За дату", f"dt:w:{wid}"), B("✏️ Исправить чеки", f"fx:{wid}")],
            [B("💵 Изменить %", f"wp:{wid}")]]
    if not w["is_self"]:
        rows += [[B("⚠️ Штраф", f"wf:{wid}"), B("🎁 Бонус", f"wb:{wid}"), B("➖ Бонус", f"wbm:{wid}")],
                 [B("💸 Выдать аванс", f"wa:{wid}"), B("↩️ Штрафы/бонусы", f"acl:{wid}")],
                 [B("🔀 Перенос зарплаты", f"tr1:{wid}"), B("🔁 Замена на день", f"sub:{wid}")],
                 [B("📤 Отправить работнику", f"pv:{wid}"), B("👑 Админ вкл/выкл", f"wad:{wid}")],
                 [B("✏️ Имя", f"wn:{wid}"), B("🔄 Заменить навсегда", f"wrep:{wid}")],
                 [B("🗑 Убрать" if w["active"] else "♻️ Вернуть", f"wrm:{wid}" if w["active"] else f"wre:{wid}")]]
    else:
        rows.append([B("✏️ Имя", f"wn:{wid}")])
    rows.append([B("🔙 Обработчики", "w:list"), B("🔙 Меню", "m:main")])
    return K(rows)


async def show_card(q, wid):
    w = L.worker(wid)
    await edit(q, card(w), card_kb(w))


async def show_acl(q, wid):
    rows = []
    for a in L.worker_adjs(wid):
        mark = "❌" if a["cancelled"] else "✅"
        rows.append([B(f"{mark} {KIND_RU[a['kind']]} {L.money(a['usd'])}$ · {L.dshort(a['day'])}", f"act:{a['id']}:{wid}")])
    for t in L.worker_transfers(wid):
        mark = "❌" if t["cancelled"] else "✅"
        a, b = L.worker(t["from_id"])["name"], L.worker(t["to_id"])["name"]
        rows.append([B(f"{mark} перенос {a}→{b} {L.money(t['pct'])}% · {L.dshort(t['day'])}", f"trt:{t['id']}:{wid}")])
    has = bool(rows)
    rows.append([B("🔙 Назад", f"w:{wid}")])
    await edit(q, "Нажмите, чтобы отменить / вернуть (✅ действует, ❌ отменён):" if has else "Штрафов, бонусов, авансов и переносов нет.", K(rows))


def price_title(r):
    rng = f"{r['lo']:,}" if r["lo"] == r["hi"] else f"{r['lo']:,}–{r['hi']:,}"
    return f"{rng} = {L.money(r['usd'])}$"


async def show_prices(q):
    rows = [[B(price_title(r), f"prc:{r['id']}")] for r in L.list_prices()]
    rows.append([B("➕ Добавить цену", "pra")])
    rows.append(menu_btn())
    await edit(q, "Прайс (нажмите на цену — изменить или удалить):", K(rows))


async def show_fx(q, wid, day):
    w = L.worker(wid)
    cs = L.day_checks(wid, day)
    rows, row = [], []
    for c in cs:
        row.append(B(("🚫 " if c["excl"] else "✅ ") + str(c["amount"]), f"fxc:{c['id']}:{wid}:{day}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows += [[B("➕ Чек", f"fxa:{wid}:{day}"), B("✏️ Сумма", f"fxe:{wid}:{day}")],
             [B("📊 Показать зп", f"fxr:{wid}:{day}"), B("🗑 Очистить день", f"fxz:{wid}:{day}")],
             [B("🔙 Карточка", f"w:{wid}")]]
    used = sum(1 for c in cs if not c["excl"])
    await edit(q, f"✏️ {w['name']} — {L.dshort(day)}\nУчтено: {used} из {len(cs)}\n"
                  f"Нажмите на чек: 🚫 не считать / ✅ считать.", K(rows))


async def show_admins(q):
    rows = []
    for u in L.all_users():
        wk = L.worker(u["worker_id"]) if u["worker_id"] else None
        base = (u["name"] or str(u["tg_id"])) + (f" [{wk['name']}]" if wk else "")
        rows.append([B(("👑 " if u["role"] in ("admin", "owner") else "👤 ") + base, f"adm:u:{u['tg_id']}")])
    rows.append([B("➕ Добавить админа по ID", "adm:new")])
    rows.append(menu_btn())
    await edit(q, "Админы и подключённые люди (нажмите на человека):\nАдмин управляет ботом полностью, "
                  "кроме Cashliy (это видите только вы).", K(rows))


def safe_name(s):
    return re.sub(r"[^\w.-]", "_", s)


async def send_pdf(bot, uid, key, wid):
    import pdfgen as P
    s, e = L.period_range(key)
    lab = L.label(s, e)
    try:
        if wid is None:
            res = L.calc(s, e)
            if not any(L.active(r) for r in res):
                await send(bot, uid, f"За {lab} данных нет.", K([menu_btn()]))
                return
            data = await asyncio.to_thread(P.all_pdf, res, lab, is_owner(uid))
            fname = f"obshiy_{s.isoformat()}_{e.isoformat()}.pdf"
        else:
            res = L.calc(s, e, only=wid)
            if not res or not L.active(res[0]):
                await send(bot, uid, f"За {lab} данных нет.", K([menu_btn()]))
                return
            data = await asyncio.to_thread(P.worker_pdf, res[0], lab)
            fname = f"{safe_name(res[0]['w']['name'])}_{s.isoformat()}_{e.isoformat()}.pdf"
        await bot.send_document(uid, document=data, filename=fname, caption=f"📄 {lab}")
    except Exception as ex:
        log.exception("pdf failed")
        await send(bot, uid, f"Не удалось создать PDF: {ex}")


# ───────────────────────── tugmalar ─────────────────────────
async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    d = q.data
    p = d.split(":")
    await q.answer()
    if not is_staff(uid):
        return  # begona/oddiy foydalanuvchi botda hech narsa qila olmaydi
    app = ctx.application

    if d == "m:main":
        ctx.user_data.pop("st", None)
        await edit(q, MAIN_TEXT, main_kb(uid))
    elif d == "okk":
        await edit(q, "Хорошо, оставил оба.")

    # ── hisobotlar
    elif d == "rs:menu":
        await edit(q, "За какой период отчёт?", K(period_rows("rs")))
    elif p[0] == "rs":
        rows = [[B("📋 Все вместе", f"rep:{p[1]}:all")]] + [[B(w["name"], f"rep:{p[1]}:{w['id']}")] for w in vis_ws(uid)]
        rows.append(menu_btn())
        await edit(q, "Чей отчёт?", K(rows))
    elif p[0] == "rep":
        s, e = L.period_range(p[1])
        lab = L.label(s, e)
        if p[2] == "all":
            res = L.calc(s, e)
            if not any(L.active(r) and (is_owner(uid) or not r["w"]["is_self"]) for r in res):
                await edit(q, f"За {lab} отчётов нет.", K([menu_btn()]))
                return
            await send(ctx.bot, uid, L.overall_text(res, lab, is_owner(uid)))
            if is_owner(uid):
                await send(ctx.bot, uid, L.income_text(res, lab), K([menu_btn()]))
        else:
            wid = int(p[2])
            if L.worker(wid)["is_self"] and not is_owner(uid):
                return
            if not await send_worker_short(ctx.bot, uid, p[1], wid):
                await edit(q, f"{L.worker(wid)['name']}: за {lab} отчётов нет.", K([menu_btn()]))
    elif p[0] == "zp":
        await show_zp(ctx.bot, app, uid, int(p[1]), p[2])
    elif d == "rt:menu":
        await edit(q, "Рейтинг за какой период?", K(period_rows("rat")))
    elif p[0] == "rat":
        s, e = L.period_range(p[1])
        res = L.calc(s, e)
        if not any(r["raw"] for r in res):
            await edit(q, f"За {L.label(s, e)} данных нет.", K([menu_btn()]))
        else:
            await send(ctx.bot, uid, L.rating_text(res, L.label(s, e)), K([menu_btn()]))
    elif d == "dt:all":
        ctx.user_data["st"] = ("datepick", None)
        await edit(q, "Напишите дату, например 05.10 или 05.10.2026:", K([menu_btn()]))
    elif p[0] == "dt" and p[1] == "w":
        ctx.user_data["st"] = ("datepick", int(p[2]))
        await edit(q, "Напишите дату, например 05.10:", K([menu_btn()]))

    # ── egasining daromadi (Cashliy) — faqat ega
    elif d == "inc:menu" and is_owner(uid):
        await edit(q, "Доход / Cashliy — за какой период?", K(period_rows("inc")))
    elif p[0] == "inc" and is_owner(uid):
        s, e = L.period_range(p[1])
        res = L.calc(s, e)
        lab = L.label(s, e)
        if not any(r["n"] for r in res):
            await edit(q, f"За {lab} данных нет.", K([menu_btn()]))
            return
        await send(ctx.bot, uid, L.income_text(res, lab))
        await send_worker_short(ctx.bot, uid, p[1], L.self_worker()["id"])

    # ── PDF
    elif d == "pdf:menu":
        await edit(q, "PDF за какой период?", K([[B("Эта неделя", "pdf:p:tw"), B("Прошлая неделя", "pdf:p:lw")],
                                                  [B("Этот месяц", "pdf:p:tm"), B("Прошлый месяц", "pdf:p:lm")], menu_btn()]))
    elif p[0] == "pdf" and p[1] == "p":
        rows = [[B("📚 Общий + каждый отдельно", f"pdf:e:{p[2]}")], [B("📦 Только общий", f"pdf:a:{p[2]}")]]
        rows += [[B(f"📄 {w['name']}", f"pdf:w:{p[2]}:{w['id']}")] for w in vis_ws(uid)]
        rows.append([B("🔙 Назад", "pdf:menu")])
        await edit(q, "Что отправить?", K(rows))
    elif p[0] == "pdf" and p[1] == "a":
        await send_pdf(ctx.bot, uid, p[2], None)
    elif p[0] == "pdf" and p[1] == "e":
        s, e = L.period_range(p[2])
        await send_pdf(ctx.bot, uid, p[2], None)
        for r in L.calc(s, e):
            if L.active(r) and (is_owner(uid) or not r["w"]["is_self"]):
                await send_pdf(ctx.bot, uid, p[2], r["w"]["id"])
    elif p[0] == "pdf" and p[1] == "w":
        await send_pdf(ctx.bot, uid, p[2], int(p[3]))

    # ── ishchiga yuborish (avval ko'rasiz, keyin bosasiz)
    elif p[0] == "pv":
        wid = p[1]
        await edit(q, "Какой период отправить обработчику?", K([[B("Сегодня", f"pvp:{wid}:td"), B("Вчера", f"pvp:{wid}:yd")],
                                                              [B("Эта неделя", f"pvp:{wid}:tw"), B("Прошлая неделя", f"pvp:{wid}:lw")],
                                                              [B("🔙 Назад", f"w:{wid}")]]))
    elif p[0] == "pvp":
        await show_zp(ctx.bot, app, uid, int(p[1]), p[2])
    elif p[0] == "snd":
        wid, key = int(p[1]), p[2]
        s, e = L.period_range(key)
        res = L.calc(s, e, only=wid)
        users = [u for u in L.users_of_worker(wid) if not is_staff(u["tg_id"])]
        if not res or not users:
            await edit(q, "Не отправлено: обработчик не подключён.")
            return
        text = L.zp_text(res[0], L.label(s, e))
        name = res[0]["w"]["name"]
        sent = app.bot_data.setdefault("sent", set())
        prev = app.bot_data.setdefault("prev", {}).get((wid, key))
        kb = K([[B(f"📤 Отправить: {name}", f"snd:{wid}:{key}")]])
        if (wid, key, text) in sent:
            await edit(q, f"✅ Уже отправлено {name}:\n\n{text}")
        elif prev != text:
            app.bot_data["prev"][(wid, key)] = text
            await edit(q, f"⚠️ Данные изменились. Теперь {name} получит:\n\n{text}", kb)
        else:
            for u in users:
                await send(ctx.bot, u["tg_id"], text)
            sent.add((wid, key, text))
            await edit(q, f"✅ Отправлено {name}:\n\n{text}")

    # ── obrabotchiklar
    elif d == "w:list":
        rows = [[B(f"{'👑 ' if w['is_self'] else ''}{w['name']} · {L.money(L.percent_on(w['id'], L.today()))}%", f"w:{w['id']}")]
                for w in vis_ws(uid)]
        rows.append([B("➕ Новый", "w:new"), B("🗄 Архив", "w:arch")])
        rows.append(menu_btn())
        await edit(q, "Обработчики:", K(rows))
    elif d == "w:arch":
        arch = [w for w in vis_ws(uid, False) if not w["active"]]
        rows = [[B(w["name"], f"w:{w['id']}")] for w in arch] + [[B("🔙 Обработчики", "w:list")]]
        await edit(q, "Архив:" if arch else "Архив пуст.", K(rows))
    elif d == "w:new":
        ctx.user_data["st"] = ("newname",)
        await edit(q, "Напишите имя нового обработчика:", K([menu_btn()]))
    elif p[0] == "w" and p[1].isdigit():
        if L.worker(int(p[1]))["is_self"] and not is_owner(uid):
            return
        await show_card(q, int(p[1]))
    elif p[0] == "wp":
        ctx.user_data["st"] = ("pct", int(p[1]))
        await edit(q, "Напишите новый процент числом (например 36):", K([menu_btn()]))
    elif p[0] == "pcd":
        wid, pct, when = int(p[1]), float(p[2]), p[3]
        t = L.today()
        frm = {"td": t, "tm": t + dt.timedelta(days=1), "mw": t - dt.timedelta(days=t.weekday())}.get(when, dt.date(2000, 1, 1))
        L.set_percent(wid, pct, L.iso(frm))
        await show_card(q, wid)
    elif p[0] in ("wf", "wb", "wbm", "wa"):
        wid = int(p[1])
        ctx.user_data["st"] = ({"wf": "fine", "wb": "bonus", "wbm": "bonusminus", "wa": "adv"}[p[0]], wid)
        what = {"wf": "штрафа", "wb": "бонуса", "wbm": "бонуса, который нужно вычесть", "wa": "аванса"}[p[0]]
        await edit(q, f"Напишите сумму {what} в $ (например 5):", K([menu_btn()]))
    elif p[0] == "acl":
        await show_acl(q, int(p[1]))
    elif p[0] == "act":
        L.adj_toggle(int(p[1]))
        await show_acl(q, int(p[2]))
    elif p[0] == "trt":
        L.transfer_toggle(int(p[1]))
        await show_acl(q, int(p[2]))
    elif p[0] == "ac":
        a = L.adj(int(p[1]))
        if a:
            L.adj_toggle(a["id"])
            c = L.adj(a["id"])["cancelled"]
            await edit(q, f"{'↩️ Отменено' if c else '✅ Восстановлено'}: {L.worker(a['worker_id'])['name']} {KIND_RU[a['kind']]} {L.money(a['usd'])}$",
                       K([[B("Вернуть" if c else "↩️ Отменить", f"ac:{a['id']}")], menu_btn()]))
    elif p[0] == "wn":
        ctx.user_data["st"] = ("rename", int(p[1]))
        await edit(q, "Напишите новое имя:", K([menu_btn()]))
    elif p[0] == "wrep":
        ctx.user_data["st"] = ("replace", int(p[1]))
        w = L.worker(int(p[1]))
        await edit(q, f"Кто будет работать вместо {w['name']} постоянно? Напишите имя. Процент перейдёт ему "
                      f"({L.money(L.percent_on(w['id'], L.today()))}%), его можно поменять. {w['name']} уйдёт в архив, история останется.",
                   K([menu_btn()]))
    elif p[0] == "wad":
        wid = int(p[1])
        us = L.users_of_worker(wid)
        if not us:
            await edit(q, "Сначала обработчик должен нажать /start у бота, а вы — подтвердить.", K([[B("🔙 Назад", f"w:{wid}")]]))
        else:
            for u in us:
                new = "worker" if u["role"] == "admin" else "admin"
                L.set_role(u["tg_id"], new)
                await send(ctx.bot, u["tg_id"], "👑 Вам выданы права админа. Нажмите /start" if new == "admin" else "Права админа сняты.")
            await show_card(q, wid)
    elif p[0] == "wrm":
        w = L.worker(int(p[1]))
        await edit(q, f"Убрать {w['name']} в архив? История сохранится.", K([[B("✅ Да", f"wrmy:{w['id']}"), B("Нет", f"w:{w['id']}")]]))
    elif p[0] == "wrmy":
        L.set_active(int(p[1]), 0)
        await edit(q, "Убран в архив.", K([[B("🔙 Обработчики", "w:list")]]))
    elif p[0] == "wre":
        L.set_active(int(p[1]), 1)
        await show_card(q, int(p[1]))

    # ── oylik o'tkazish
    elif p[0] == "tr1":
        rows = day_rows(f"tr2:{p[1]}", 6) + [[B("🔙 Назад", f"w:{p[1]}")]]
        await edit(q, f"🔀 Перенос зарплаты {L.worker(int(p[1]))['name']}. За какой день?", K(rows))
    elif p[0] == "tr2":
        wid, day = int(p[1]), p[2]
        if L.day_zp(wid, day) <= 0:
            await edit(q, f"У {L.worker(wid)['name']} нет зарплаты за {L.dshort(day)}.", K([[B("🔙 Назад", f"tr1:{wid}")]]))
        else:
            rows = [[B(w["name"], f"tr3:{wid}:{day}:{w['id']}")] for w in L.workers(with_self=False) if w["id"] != wid]
            rows.append([B("🔙 Назад", f"tr1:{wid}")])
            await edit(q, f"Зп за {L.dshort(day)}: {L.money(L.day_zp(wid, day))}$. Кому перенести?", K(rows))
    elif p[0] == "tr3":
        wid, day, to = int(p[1]), p[2], int(p[3])
        rows = [[B("100%", f"tr4:{wid}:{day}:{to}:100"), B("75%", f"tr4:{wid}:{day}:{to}:75")],
                [B("50%", f"tr4:{wid}:{day}:{to}:50"), B("25%", f"tr4:{wid}:{day}:{to}:25")],
                [B("✏️ Другой %", f"tro:{wid}:{day}:{to}")], [B("🔙 Назад", f"tr1:{wid}")]]
        await edit(q, f"Какую часть зп за {L.dshort(day)} перенести → {L.worker(to)['name']}?", K(rows))
    elif p[0] == "tro":
        ctx.user_data["st"] = ("trpct", int(p[1]), p[2], int(p[3]))
        await edit(q, "Напишите процент переноса числом (например 40):", K([menu_btn()]))
    elif p[0] == "tr4":
        wid, day, to, pct = int(p[1]), p[2], int(p[3]), float(p[4])
        tid = L.add_transfer(day, wid, to, pct)
        await edit(q, f"✅ Перенос: {L.worker(wid)['name']} → {L.worker(to)['name']} · {L.dshort(day)} · {L.money(pct)}% = "
                      f"{L.money(L.day_zp(wid, day) * pct / 100)}$", K([[B("↩️ Отменить перенос", f"trt:{tid}:{wid}")], menu_btn()]))

    # ── замена на день (o'rniga boshqa ishlagan)
    elif p[0] == "sub":
        await edit(q, f"🔁 Замена на день: за какой день отчёт {L.worker(int(p[1]))['name']} перенести другому?",
                   K(day_rows(f"sub2:{p[1]}", 6) + [[B("🔙 Назад", f"w:{p[1]}")]]))
    elif p[0] == "sub2":
        wid, day = int(p[1]), p[2]
        rows = [[B(w["name"], f"sub3:{wid}:{w['id']}:{day}")] for w in vis_ws(uid) if w["id"] != wid]
        rows.append([B("🔙 Назад", f"w:{wid}")])
        await edit(q, f"Кто работал вместо {L.worker(wid)['name']} {L.dshort(day)}?", K(rows))
    elif p[0] == "sub3":
        wid, w2, day = int(p[1]), int(p[2]), p[3]
        n = L.move_day(wid, w2, day)
        await edit(q, f"✅ Отчёт {L.worker(wid)['name']} за {L.dshort(day)} ({n} {L.chk_word(n)}) перенесён на {L.worker(w2)['name']}.",
                   K([[B(f"💰 Зп {L.worker(w2)['name']}", f"zp:{w2}:{day}")], [B("🔙 Карточка", f"w:{wid}")]]))

    # ── chek tuzatish
    elif p[0] == "fx":
        wid = int(p[1])
        days = L.worker_days(wid, 10)
        if not days:
            await edit(q, "Чеков пока нет.", K([[B("🔙 Карточка", f"w:{wid}")]]))
        else:
            rows = [[B(f"{L.dshort(r['rd'])} ({r['n']} {L.chk_word(r['n'])})", f"fxd:{wid}:{r['rd']}")] for r in days]
            rows.append([B("🔙 Карточка", f"w:{wid}")])
            await edit(q, "Выберите день:", K(rows))
    elif p[0] == "fxd":
        await show_fx(q, int(p[1]), p[2])
    elif p[0] == "fxc":
        L.toggle_check(int(p[1]))
        await show_fx(q, int(p[2]), p[3])
    elif p[0] == "fxr":
        await show_zp(ctx.bot, app, uid, int(p[1]), p[2])
    elif p[0] == "fxa":
        ctx.user_data["st"] = ("fxadd", int(p[1]), p[2])
        await edit(q, "Напишите сумму чека (например 1950):", K([menu_btn()]))
    elif p[0] == "fxe":
        ctx.user_data["st"] = ("fxedit", int(p[1]), p[2])
        await edit(q, "Напишите: старая новая (например 1950 2600) — заменю один чек.", K([menu_btn()]))
    elif p[0] == "fxz":
        await edit(q, f"Удалить ВСЕ чеки за {L.dshort(p[2])}?", K([[B("✅ Да, удалить", f"fxzy:{p[1]}:{p[2]}"), B("Нет", f"fxd:{p[1]}:{p[2]}")]]))
    elif p[0] == "fxzy":
        L.clear_day(int(p[1]), p[2])
        await edit(q, "🗑 День очищен.", K([[B("🔙 Карточка", f"w:{p[1]}")]]))
    elif p[0] == "rrs":  # eski hisobotni qaytarish
        L.set_report_status(int(p[1]), "done")
        await edit(q, "↩️ Старый отчёт возвращён (теперь считаются оба).")
    elif p[0] == "rdup":
        L.set_report_status(int(p[1]), "replaced")
        await edit(q, "✅ Повтор убран.")

    # ── narx-ro'yxati
    elif d == "pr:menu":
        await show_prices(q)
    elif p[0] == "prc":
        r = L.price_row(int(p[1]))
        if r:
            await edit(q, f"Цена: {price_title(r)}", K([[B("✏️ Изменить", f"pre:{r['id']}"), B("🗑 Удалить", f"prd:{r['id']}")],
                                                         [B("🔙 Прайс", "pr:menu")]]))
    elif p[0] == "pre":
        ctx.user_data["st"] = ("pricedit", int(p[1]))
        await edit(q, "Напишите новую цену: «сумма цена» (5200 20) или «от до цена» (5000 5400 20).", K([menu_btn()]))
    elif p[0] == "prd":
        r = L.price_row(int(p[1]))
        await edit(q, f"Удалить цену {price_title(r)}?", K([[B("✅ Да, удалить", f"prdy:{p[1]}"), B("Нет", f"prc:{p[1]}")]]))
    elif p[0] == "prdy":
        L.del_price(int(p[1]))
        await show_prices(q)
    elif d == "pra":
        ctx.user_data["st"] = ("priceadd",)
        await edit(q, "Напишите: «сумма цена» (5200 20) или «от до цена» (5000 5400 20).", K([menu_btn()]))

    # ── chatlar
    elif d == "ch:menu":
        rows = [[B(("✅ " if c["watch"] else "⏸ ") + (c["title"] or str(c["chat_id"])), f"cw:{c['chat_id']}:{0 if c['watch'] else 1}")]
                for c in L.all_chats()]
        rows.append(menu_btn())
        await edit(q, "Группы и каналы, где бот читает отчёты. Нажмите — включить/выключить.\n"
                      "Новый чат: просто добавьте бота в группу/канал — включится сам.", K(rows))
    elif p[0] == "cw":
        L.set_watch(int(p[1]), int(p[2]))
        await edit(q, "✅ Слежу за отчётами." if p[2] == "1" else "Хорошо, не слежу.", K([menu_btn()]))
    elif p[0] == "cl":
        try:
            await ctx.bot.leave_chat(int(p[1]))
        except Exception:
            pass
        L.set_watch(int(p[1]), 0)
        await edit(q, "🚪 Вышел из чата.")

    # ── savollar (birinchi javob beruvchi yutadi)
    elif d == "q:list":
        pr, pa = L.pending_reports(), L.pending_adjs()
        if not pr and not pa:
            await edit(q, "Открытых вопросов нет 😊", K([menu_btn()]))
        else:
            await edit(q, f"Повторяю {len(pr) + len(pa)} вопрос(а) ниже 👇", K([menu_btn()]))
            for rid in pr:
                await advance(app, rid)
            for aid in pa:
                await ask_adj(app, aid)
    elif p[0] == "pwa":
        arch = [w for w in vis_ws(uid, False) if not w["active"]]
        rows = [[B(w["name"], f"pw:{p[1]}:{w['id']}")] for w in arch] or [[B("(архив пуст)", "m:main")]]
        await edit(q, "Из архива:", K(rows))
    elif p[0] == "pw":
        rid, wid = int(p[1]), int(p[2])
        r = L.report(rid)
        if not r or r["worker_id"] is not None:
            await edit(q, "Уже решено.")
            return
        L.x("update reports set worker_id=? where id=?", (wid, rid))
        sid = r["sender_id"]
        if sid and sid != OWNER and not is_staff(sid) and not L.user(sid) and sid != L.ANON:
            L.upsert_user(sid, r["sender_name"] or "", "worker", wid)  # keyingi safar ID bo'yicha o'zi taniladi
        await edit(q, f"✅ Отчёт: {L.worker(wid)['name']}")
        await advance(app, rid)
    elif p[0] == "pd":
        rid = int(p[1])
        if not L.report(rid) or not L.q1("select 1 from checks where report_id=? and rd is null", (rid,)):
            await edit(q, "Уже решено.")
            return
        L.x("update checks set rd=? where report_id=? and rd is null", (p[2], rid))
        await edit(q, f"✅ Дата: {L.dshort(p[2])}")
        await advance(app, rid)
    elif p[0] == "pdo":
        ctx.user_data["st"] = ("pdate", int(p[1]))
        await edit(q, "Напишите дату, например 05.10 или 05.10.2026:")
    elif p[0] == "pwi":
        L.delete_report(int(p[1]))
        await edit(q, "🚫 Игнорирую это сообщение.")
    elif p[0] in ("aw", "ak", "ay"):
        a = L.adj(int(p[1]))
        if not a or not a["pending"]:
            await edit(q, "Уже решено.")
            return
        if p[0] == "aw":
            L.adj_resolve(a["id"], worker_id=int(p[2]))
        elif p[0] == "ak":
            L.adj_resolve(a["id"], kind=p[2])
        else:
            L.x("update adj set pending=0 where id=?", (a["id"],))
        a = L.adj(a["id"])
        await edit(q, "✅ Принято")
        if a["pending"]:
            await ask_adj(app, a["id"])
        else:
            await confirm_adjs(app, [a["id"]])
    elif p[0] == "ai":
        L.adj_delete(int(p[1]))
        await edit(q, "🚫 Игнорирую.")

    # ── yangi foydalanuvchi so'rovi / admin va dostup
    elif p[0] == "uo" and uid == OWNER:
        tid = int(p[1])
        name = app.bot_data.get("names", {}).get(tid, "")
        L.upsert_user(tid, name, "owner", L.self_worker()["id"])
        await edit(q, "✅ Это ваш второй аккаунт: видит всё, включая Cashliy.")
        await send(ctx.bot, tid, "✅ Принято. Нажмите /start")
    elif p[0] in ("ul", "ua", "ux", "un"):
        tid = int(p[1])
        if L.user(tid):
            await edit(q, "Уже обработано.")
            return
        name = app.bot_data.get("names", {}).get(tid, "")
        if p[0] == "ul":
            wid = int(p[2])
            L.upsert_user(tid, name, "worker", wid)
            await edit(q, f"✅ Привязан к: {L.worker(wid)['name']}\n(зарплату получит только когда вы нажмёте «Отправить»)")
            await send(ctx.bot, tid, "✅ Принято.")
        elif p[0] == "ua":
            L.upsert_user(tid, name, "admin", None)
            await edit(q, "✅ Назначен админом")
            await send(ctx.bot, tid, "✅ Принято. Нажмите /start")
        elif p[0] == "un":
            ctx.user_data["st"] = ("newlink", tid)
            await edit(q, "Напишите новое имя обработчика — привяжу к нему этого человека:")
        else:
            await edit(q, "🚫 Отказано")
    elif d == "adm:menu":
        await show_admins(q)
    elif d == "adm:new":
        ctx.user_data["st"] = ("addadmin",)
        await edit(q, "Напишите Telegram ID нового админа (число):", K([menu_btn()]))
    elif p[0] == "adm" and p[1] == "u":
        u = L.user(int(p[2]))
        if u:
            wk = L.worker(u["worker_id"])["name"] if u["worker_id"] else "—"
            rows = [[B("👑 Снять админа" if u["role"] == "admin" else "👑 Сделать админом", f"adm:t:{u['tg_id']}")],
                    [B("🔗 Привязать к обработчику", f"adm:l:{u['tg_id']}")],
                    [B("🚫 Удалить доступ", f"adm:d:{u['tg_id']}")], [B("🔙 Назад", "adm:menu")]]
            if uid == OWNER:
                rows.insert(2, [B("👑 Снять «это я»" if u["role"] == "owner" else "👑 Это мой Cashliy-аккаунт", f"adm:o:{u['tg_id']}")])
            await edit(q, f"{u['name'] or u['tg_id']} (ID {u['tg_id']})\nРоль: {'это вы (Cashliy)' if u['role'] == 'owner' else 'админ' if u['role'] == 'admin' else 'обычный'}\nОбработчик: {wk}", K(rows))
    elif p[0] == "adm" and p[1] == "o" and uid == OWNER:
        u = L.user(int(p[2]))
        if u:
            if u["role"] == "owner":
                L.set_role(u["tg_id"], "worker")
            else:
                L.upsert_user(u["tg_id"], u["name"], "owner", L.self_worker()["id"])
                await send(ctx.bot, u["tg_id"], "👑 Принято. Нажмите /start")
            await show_admins(q)
    elif p[0] == "adm" and p[1] == "t":
        u = L.user(int(p[2]))
        if u:
            new = "worker" if u["role"] == "admin" else "admin"
            L.set_role(u["tg_id"], new)
            await send(ctx.bot, u["tg_id"], "👑 Вам выданы права админа. Нажмите /start" if new == "admin" else "Права админа сняты.")
            await show_admins(q)
    elif p[0] == "adm" and p[1] == "l":
        rows = [[B(w["name"], f"adm:w:{p[2]}:{w['id']}")] for w in vis_ws(uid)] + [[B("🔙 Назад", f"adm:u:{p[2]}")]]
        await edit(q, "К какому обработчику привязать?", K(rows))
    elif p[0] == "adm" and p[1] == "w":
        L.set_user_worker(int(p[2]), int(p[3]))
        await show_admins(q)
    elif p[0] == "adm" and p[1] == "d":
        L.del_user(int(p[2]))
        await show_admins(q)


# ───────────────────────── matn kiritish ─────────────────────────
def to_num(t):
    try:
        return float(t.strip().replace(",", ".").replace("$", "").replace("%", ""))
    except ValueError:
        return None


def price_args(text):
    nums = [to_num(t) for t in text.split()]
    if None in nums or len(nums) not in (2, 3):
        return None
    return (int(nums[0]), int(nums[0]), nums[1]) if len(nums) == 2 else (int(nums[0]), int(nums[1]), nums[2])


async def manual_report(app, text, uid):
    """Egasi/admin botga o'zi hisobot tashlaydi (eski hisobotlar). Tushunmasa tugma bilan so'raydi."""
    checks = L.owner_checks(text, L.today())
    if not checks:
        return False
    wid = L.worker_by_name(text)
    if wid is None and is_owner(uid):
        wid = L.self_worker()["id"]
    rid = L.create_report(wid, 0, None, uid, text, checks, "вручную")
    await advance(app, rid)
    return True


async def on_private_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u, msg = update.effective_user, update.message
    if not is_staff(u.id):
        return
    text = (msg.text or "").strip()
    app = ctx.application

    # narx savoliga javob
    pq = app.bot_data.get("pq", {})
    key = None
    rc = getattr(msg, "reply_to_message", None)
    if rc and (msg.chat_id, rc.message_id) in pq:
        key = (msg.chat_id, rc.message_id)
    elif "st" not in ctx.user_data and to_num(text) is not None and len({v for v in pq.values()}) == 1:
        key = next((k for k in pq if k[0] == msg.chat_id), None)
    if key is not None:
        v = to_num(text)
        if v is None or v <= 0:
            await msg.reply_text("Нужно число, например 12. Ответьте ещё раз на тот же вопрос.")
            return
        rid, amount = pq[key]
        for k in [k for k, val in pq.items() if val == (rid, amount)]:
            pq.pop(k, None)
        L.add_price(amount, amount, v)
        await msg.reply_text(f"✅ Запомнил: {amount:,} = {L.money(v)}$")
        for r in L.pending_reports():
            need = L.report_needs(r)
            if need is None:
                await finalize(app, r)
            elif need[0] == "price" and not any(val == (r, need[1]) for val in pq.values()):
                await advance(app, r)
        return

    st = ctx.user_data.get("st")
    if not st:
        if L.has_kind(text) and not L.owner_checks(text, L.today()):
            await handle_adjust(app, text, L.today(), 0, None)
        elif not await manual_report(app, text, u.id):
            await msg.reply_text(MAIN_TEXT, reply_markup=main_kb(u.id))
        return
    act = st[0]

    async def done_card(wid):
        w = L.worker(wid)
        await msg.reply_text(card(w), reply_markup=card_kb(w))

    if act == "newname":
        if not text or L.name_taken(text):
            await msg.reply_text("Такое имя уже есть или пусто. Напишите другое:")
            return
        ctx.user_data["st"] = ("newpct", text)
        await msg.reply_text(f"Какой процент у {text}? Напишите число (например 30):")
    elif act == "newpct":
        v = to_num(text)
        if v is None or not 0 <= v <= 100:
            await msg.reply_text("Нужно число от 0 до 100:")
            return
        wid = L.add_worker(st[1], v)
        ctx.user_data.pop("st")
        await msg.reply_text(f"✅ Добавлен: {st[1]} · {L.money(v)}%")
        await done_card(wid)
    elif act == "newlink":
        if not text or L.name_taken(text):
            await msg.reply_text("Такое имя уже есть или пусто. Напишите другое:")
            return
        ctx.user_data["st"] = ("newlinkpct", st[1], text)
        await msg.reply_text(f"Какой процент у {text}? Напишите число (например 30):")
    elif act == "newlinkpct":
        v = to_num(text)
        if v is None or not 0 <= v <= 100:
            await msg.reply_text("Нужно число от 0 до 100:")
            return
        ctx.user_data.pop("st")
        wid = L.add_worker(st[2], v)
        L.upsert_user(st[1], app.bot_data.get("names", {}).get(st[1], ""), "worker", wid)
        await msg.reply_text(f"✅ Создан {st[2]} ({L.money(v)}%) и привязан к человеку.")
        await send(ctx.bot, st[1], "✅ Принято.")
    elif act == "pct":
        v = to_num(text)
        if v is None or not 0 <= v <= 100:
            await msg.reply_text("Нужно число от 0 до 100:")
            return
        ctx.user_data.pop("st")
        wid = st[1]
        await msg.reply_text(f"С какого дня новый процент {L.money(v)}%? (прошлые дни останутся по старому)",
                             reply_markup=K([[B("С сегодня", f"pcd:{wid}:{v}:td"), B("С завтра", f"pcd:{wid}:{v}:tm")],
                                             [B("С понедельника этой недели", f"pcd:{wid}:{v}:mw")],
                                             [B("За всё время", f"pcd:{wid}:{v}:all")]]))
    elif act in ("fine", "bonus", "bonusminus", "adv"):
        v = to_num(text)
        if v is None or v <= 0:
            await msg.reply_text("Нужно число больше нуля, например 5:")
            return
        ctx.user_data.pop("st")
        kind = {"fine": "fine", "bonus": "bonus", "bonusminus": "bonus", "adv": "adv"}[act]
        aid = L.add_adj(st[1], kind, -v if act == "bonusminus" else v, L.iso(L.today()), "кнопка")
        await confirm_adjs(app, [aid])
    elif act == "trpct":
        v = to_num(text)
        if v is None or not 0 < v <= 100:
            await msg.reply_text("Нужно число от 1 до 100:")
            return
        ctx.user_data.pop("st")
        _, wid, day, to = st
        tid = L.add_transfer(day, wid, to, v)
        await msg.reply_text(f"✅ Перенос: {L.worker(wid)['name']} → {L.worker(to)['name']} · {L.dshort(day)} · {L.money(v)}% = "
                             f"{L.money(L.day_zp(wid, day) * v / 100)}$",
                             reply_markup=K([[B("↩️ Отменить перенос", f"trt:{tid}:{wid}")], menu_btn()]))
    elif act == "fxadd":
        v = to_num(text)
        if v is None or v < 1:
            await msg.reply_text("Нужна сумма чека, например 1950:")
            return
        ctx.user_data.pop("st")
        L.add_check(st[1], st[2], int(v))
        await msg.reply_text(f"✅ Чек {int(v)} добавлен.", reply_markup=K([[B("✏️ К чекам", f"fxd:{st[1]}:{st[2]}")]]))
    elif act == "fxedit":
        nums = [to_num(t) for t in text.split()]
        if len(nums) != 2 or None in nums:
            await msg.reply_text("Формат: старая новая, например 1950 2600")
            return
        ctx.user_data.pop("st")
        ok = L.edit_check_amount(st[1], st[2], int(nums[0]), int(nums[1]))
        await msg.reply_text("✅ Заменил." if ok else "Такого чека за этот день нет.",
                             reply_markup=K([[B("✏️ К чекам", f"fxd:{st[1]}:{st[2]}")]]))
    elif act == "pdate":
        d0 = L.parse_date_text(text, L.today())
        if not d0:
            await msg.reply_text("Не понял дату. Пример: 05.10")
            return
        ctx.user_data.pop("st")
        L.x("update checks set rd=? where report_id=? and rd is null", (L.iso(d0), st[1]))
        await msg.reply_text(f"✅ Дата: {L.dshort(L.iso(d0))}")
        await advance(app, st[1])
    elif act == "datepick":
        d0 = L.parse_date_text(text, L.today())
        if not d0:
            await msg.reply_text("Не понял дату. Пример: 05.10")
            return
        ctx.user_data.pop("st")
        key = L.iso(d0)
        if st[1]:
            if not await send_worker_short(ctx.bot, u.id, key, st[1]):
                await msg.reply_text(f"За {L.dshort(key)} отчётов нет.", reply_markup=K([menu_btn()]))
        else:
            rows = [[B("📋 Все вместе", f"rep:{key}:all")]] + [[B(w["name"], f"rep:{key}:{w['id']}")] for w in vis_ws(u.id)]
            await msg.reply_text(f"Отчёт за {L.dshort(key)} — чей?", reply_markup=K(rows + [menu_btn()]))
    elif act == "rename":
        ctx.user_data.pop("st")
        try:
            L.rename_worker(st[1], text)
        except Exception:
            await msg.reply_text("Такое имя уже есть.")
            return
        await done_card(st[1])
    elif act == "replace":
        old = L.worker(st[1])
        if not text or L.name_taken(text):
            await msg.reply_text("Такое имя уже есть или пусто. Напишите другое:")
            return
        ctx.user_data.pop("st")
        pct = L.percent_on(old["id"], L.today())
        wid = L.add_worker(text, pct)
        L.set_active(old["id"], 0)
        await msg.reply_text(f"✅ {text} вместо {old['name']} · {L.money(pct)}% (старый в архиве)")
        await done_card(wid)
    elif act in ("priceadd", "pricedit"):
        args = price_args(text)
        if not args:
            await msg.reply_text("Формат: «сумма цена» (5200 20) или «от до цена» (5000 5400 20)")
            return
        ctx.user_data.pop("st")
        if act == "priceadd":
            L.add_price(*args)
        else:
            L.update_price(st[1], *args)
        await msg.reply_text("✅ Готово.", reply_markup=K([[B("💲 Прайс", "pr:menu")], menu_btn()]))
        for r in L.pending_reports():
            if L.report_needs(r) is None:
                await finalize(app, r)
    elif act == "addadmin":
        v = to_num(text)
        if v is None:
            await msg.reply_text("Нужен числовой ID:")
            return
        ctx.user_data.pop("st")
        tid = int(v)
        if not is_owner(tid):
            u0 = L.user(tid)
            L.upsert_user(tid, (u0["name"] if u0 else ""), "admin", (u0["worker_id"] if u0 else None))
            await send(ctx.bot, tid, "👑 Вам выданы права админа. Нажмите /start")
        await msg.reply_text("✅ Админ добавлен.", reply_markup=K([[B("👑 Админы", "adm:menu")], menu_btn()]))


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.exception("error: %s", ctx.error)
    try:
        await ctx.bot.send_message(OWNER, f"⚠️ Ошибка бота: {str(ctx.error)[:300]}")
    except Exception:
        pass


async def post_init(app: Application):
    for rid in L.pending_reports():
        await advance(app, rid)
    for aid in L.pending_adjs():
        await ask_adj(app, aid)


def main():
    L.db()
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_private_text))
    app.add_handler(MessageHandler((filters.ChatType.GROUPS | filters.ChatType.CHANNEL) & (filters.TEXT | filters.CAPTION), on_chat_text))
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
