# -*- coding: utf-8 -*-
import os
import re
import asyncio
import logging
import datetime as dt
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup as K
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler, MessageHandler,
                          ChatMemberHandler, ContextTypes, filters)
import logic as L
import pdfgen as P

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kassabot")

TOKEN = os.environ["BOT_TOKEN"]
OWNER = int(os.getenv("OWNER_ID", "8785896869"))
ANON = 1087968824  # "anonymous admin" akkaunti


def B(text, data):
    return InlineKeyboardButton(text, callback_data=data)


def is_staff(uid):
    return uid is not None and (uid == OWNER or uid in L.admins())


def staff_ids():
    return [OWNER] + [a for a in L.admins() if a != OWNER]


def notify_ids(wid):
    """Cashliy hisoboti faqat egaga ko'rinadi."""
    w = L.worker(wid)
    if w and w["is_self"]:
        return [OWNER]
    return staff_ids()


def can_see(uid, wid):
    w = L.worker(wid)
    return bool(w) and (uid == OWNER or not w["is_self"])


def visible(res, owner):
    return [r for r in res if L.has_data(r) and (owner or not r["w"]["is_self"])]


def own_check(cid, wid):
    """Chek shu ishchiga tegishlimi."""
    return L.ql("select ch.id from checks ch join reports ro on ro.id=ch.report_id "
                "where ch.id=? and ro.worker_id=?", (cid, wid)) is not None


def delete_day(wid, day):
    """Ishchining shu kundagi hamma cheklarini o'chiradi."""
    rids = [r["rid"] for r in L.q(
        "select distinct ch.report_id as rid from checks ch join reports ro on ro.id=ch.report_id "
        "where ro.worker_id=? and ch.rd=?", (wid, day))]
    L.x("delete from checks where rd=? and report_id in (select id from reports where worker_id=?)", (day, wid))
    for rid in rids:
        if not L.ql("select 1 from checks where report_id=?", (rid,)):
            L.x("delete from reports where id=?", (rid,))
    return len(rids)


def menu_btn():
    return [B("🔙 Меню", "m:main")]


def period_rows(prefix):
    return [
        [B("Сегодня", f"{prefix}:td"), B("Вчера", f"{prefix}:yd")],
        [B("Эта неделя", f"{prefix}:tw"), B("Прошлая неделя", f"{prefix}:lw")],
        [B("Этот месяц", f"{prefix}:tm"), B("Прошлый месяц", f"{prefix}:lm")],
        [B("🔙 Меню", "m:main")]
    ]


async def send(bot, chat_id, text, kb=None):
    parts = L.chunks(text)
    last = None
    for i, part in enumerate(parts):
        try:
            last = await bot.send_message(chat_id, part, reply_markup=kb if i == len(parts) - 1 else None)
        except Exception as e:
            log.warning("send to %s failed: %s", chat_id, e)
    return last


async def edit(q, text, kb=None):
    try:
        await q.edit_message_text(text, reply_markup=kb)
    except Exception as e:
        if "not modified" not in str(e).lower():
            log.warning("edit failed: %s", e)


async def show(bot, q, text, kb=None):
    """Xabarni tahrirlaydi (yangi xabar yaratmaydi). Juda uzun bo'lsa - bo'laklab yuboradi."""
    parts = L.chunks(text)
    if len(parts) == 1:
        await edit(q, parts[0], kb)
    else:
        await edit(q, "⬇️ Отчёт ниже", None)
        await send(bot, q.message.chat_id, text, kb)


async def tg_name(bot, tid):
    try:
        c = await bot.get_chat(tid)
        return c.full_name or (f"@{c.username}" if c.username else str(tid))
    except Exception:
        return str(tid)


def main_kb(uid):
    rows = [
        [B("📊 Отчёты", "rs:menu"), B("🏆 Рейтинг", "rt:menu")],
        [B("📆 Отчёт за дату", "dt:all"), B("📄 PDF за неделю", "pdf:menu")],
        [B("👥 Обработчики", "w:list"), B("💲 Прайс", "pr:menu")],
        [B("💬 Чаты", "ch:menu"), B("❓ Вопросы", "q:list")],
    ]
    if uid == OWNER:
        rows.append([B("👑 Cashliy", "ow:cash"), B("🛡 Админы", "adm:menu")])
    return K(rows)


def worker_menu_kb():
    return K([
        [B("📥 Мой отчёт (период)", "me:menu")],
        [B("💰 Мой баланс за неделю", "me:balance")]
    ])


# ---------- PDF ----------

def safe_name(s):
    return re.sub(r"[^\w.-]", "_", s)


async def send_pdf(bot, uid, key, wid):
    owner = uid == OWNER
    s, e = L.period_range(key)
    lab = L.label(s, e)
    try:
        if wid is None:
            res = L.calc(s, e)
            data = await asyncio.to_thread(P.all_pdf, res, lab, owner)
            fname = f"obshiy_{s.isoformat()}_{e.isoformat()}.pdf"
        else:
            if not can_see(uid, wid):
                return
            res = L.calc(s, e, only=wid)
            if not res or not L.has_data(res[0]):
                await send(bot, uid, f"За {lab} отчётов нет.")
                return
            data = await asyncio.to_thread(P.worker_pdf, res[0], lab)
            fname = f"{safe_name(res[0]['w']['name'])}_{s.isoformat()}_{e.isoformat()}.pdf"
        await bot.send_document(uid, document=data, filename=fname, caption=f"📄 {lab}")
    except Exception as ex:
        log.exception("pdf failed")
        await send(bot, uid, f"Не удалось создать PDF: {ex}")


async def send_all_pdfs(bot, uid, key):
    owner = uid == OWNER
    s, e = L.period_range(key)
    lab = L.label(s, e)
    res = visible(L.calc(s, e), owner)
    if not res:
        await send(bot, uid, f"За {lab} отчётов нет.")
        return
    for r in res:
        try:
            data = await asyncio.to_thread(P.worker_pdf, r, lab)
            fname = f"{safe_name(r['w']['name'])}_{s.isoformat()}_{e.isoformat()}.pdf"
            await bot.send_document(uid, document=data, filename=fname, caption=f"📄 {r['w']['name']} ({lab})")
        except Exception as ex:
            log.exception("pdf failed")
            await send(bot, uid, f"PDF {r['w']['name']}: ошибка {ex}")
            return


# ---------- отчёты ----------

def brief_kb(wid, day):
    return K([[B("💰 Зп", f"zp:{wid}:{day}"), B("🗑 Удалить", f"dl:{wid}:{day}")]])


async def finalize(app, rid):
    r = L.report(rid)
    if r is None:
        return
    L.x("update reports set status='done' where id=?", (rid,))
    replaced = set(L.replace_old(rid))
    wid = r["worker_id"]
    ids = notify_ids(wid)
    for d in L.report_days(rid):
        day = dt.date.fromisoformat(d)
        res = L.calc(day, day, only=wid)
        if not res or not res[0]["days"]:
            continue
        text = L.brief(res[0], d in replaced)
        for sid in ids:
            await send(app.bot, sid, text, brief_kb(wid, d))


def asker(r):
    """Savolni kimga yuborish: shaxsiy chatda tashlagan admin o'ziga, qolgan hammasi egaga."""
    s = r["sender_id"]
    if s and r["chat_id"] == s and is_staff(s):
        return s
    return OWNER


async def advance(app, rid):
    need = L.report_needs(rid)
    r = L.report(rid)
    if r is None:
        return
    if need is None:
        await finalize(app, rid)
        return
    kind, arg = need
    snippet = (r["raw"] or "")[:350]
    to = asker(r)
    if kind == "worker":
        rows = [[B(w["name"], f"pw:{rid}:{w['id']}")] for w in L.workers()
                if to == OWNER or not w["is_self"]]
        rows.append([B("❌ Это не отчёт", f"pwi:{rid}")])
        await send(app.bot, to, f"❓ Кто это отправил?\n\n{snippet}", K(rows))
    elif kind == "date":
        t = L.today()
        ds = [t - dt.timedelta(days=i) for i in range(4)]
        rows = [[B(d.strftime("%d.%m"), f"pd:{rid}:{d.isoformat()}")] for d in ds]
        rows.append([B("❌ Это не отчёт", f"pwi:{rid}")])
        name = L.worker(r["worker_id"])["name"]
        await send(app.bot, to, f"❓ {name}: на какой день этот отчёт?", K(rows))
    elif kind == "price":
        name = L.worker(r["worker_id"])["name"]
        m = await send(app.bot, OWNER, f"❓ {name}: сколько стоит чек {arg:,}? Ответьте числом.")
        if m:
            app.bot_data.setdefault("pq", {})[m.message_id] = (rid, arg)


async def ask_adj(app, aid):
    a = L.adj(aid)
    if a is None:
        return
    snippet = (a["raw"] or "")[:300]
    if a["worker_id"] is None:
        rows = [[B(w["name"], f"aw:{aid}:{w['id']}")] for w in L.workers()]
        rows.append([B("🚫 Игнорировать", f"ai:{aid}")])
        kind_name = "штраф" if a["kind"] == "fine" else "бонус"
        await send(app.bot, OWNER, f"❓ Кому {kind_name} {L.money(a['usd'])}$?\n\n{snippet}", K(rows))
    elif a["kind"] is None:
        rows = [[B("Штраф", f"ak:{aid}:fine"), B("Бонус", f"ak:{aid}:bonus")],
                [B("🚫 Игнорировать", f"ai:{aid}")]]
        w = L.worker(a["worker_id"])["name"]
        await send(app.bot, OWNER, f"❓ {w}: {L.money(a['usd'])}$ это штраф или бонус?\n\n{snippet}", K(rows))


async def confirm_adjs(app, ids):
    for sid in staff_ids():
        lines, rows = [], []
        for aid in ids:
            a = L.adj(aid)
            wk = L.worker(a["worker_id"])
            if wk["is_self"] and sid != OWNER:
                continue
            t = "Штраф" if a["kind"] == "fine" else "Бонус"
            lines.append(f"{wk['name']}: {t} {L.money(a['usd'])}$ ({L.dshort(a['day'])})")
            rows.append([B(f"Отменить {wk['name']} {t.lower()}", f"ac:{aid}")])
        if not lines:
            continue
        rows.append(menu_btn())
        await send(app.bot, sid, "✅ Записано:\n" + "\n".join(lines), K(rows))


def detect_worker(uid, text):
    """Qaytaradi: (worker_id, ism_matndan_topildimi)"""
    named = L.worker_by_name(text)
    if uid == OWNER:
        if named:
            return named, False
        sw = L.self_worker()
        return (sw["id"] if sw else None), False
    if uid:
        u = L.user(uid)
        if u and u["worker_id"]:
            return u["worker_id"], False
    return named, bool(named)


async def ask_link(app, uid, name, wid):
    asked = app.bot_data.setdefault("asked", set())
    if uid in asked:
        return
    asked.add(uid)
    w = L.worker(wid)
    await send(app.bot, OWNER,
               f"🔗 Аккаунт {name} (id {uid}) ещё не привязан.\nЭто {w['name']}?",
               K([[B(f"✅ Да, это {w['name']}", f"lk:{uid}:{wid}")],
                  [B("Другой обработчик", f"lko:{uid}")],
                  [B("🚫 Не привязывать", "lkx")]]))


async def submit_report(app, u, chat_id, msg_id, text, day, strict):
    """strict=True (guruh/kanal): sanasiz xabar hisobot emas.
    strict=False (botning shaxsiy chati): ism yozilmagan bo'lsa, kimniki ekanini so'raydi."""
    checks = L.parse_report(text, day)
    if not checks:
        return False
    if strict and not L.parse_dates(text, day):
        return False
    uid = u.id if u else None
    if u:
        app.bot_data.setdefault("names", {})[uid] = u.full_name
    if strict:
        wid, by_name = detect_worker(uid, text)
    else:
        wid, by_name = L.worker_by_name(text), False
    rid = L.create_report(wid, chat_id, msg_id, uid, text, checks)
    if by_name and uid and uid != ANON and not is_staff(uid) and not L.user(uid):
        await ask_link(app, uid, u.full_name, wid)
    await advance(app, rid)
    return True


async def on_chat_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return
    text = msg.text or msg.caption
    if not text:
        return

    row = L.chat(chat.id)
    if row is None:
        L.upsert_chat(chat.id, chat.title or str(chat.id))
        L.set_watch(chat.id, 1)
    elif not row["watch"]:
        return

    edited = bool(update.edited_message or update.edited_channel_post)
    u = msg.from_user
    uid = u.id if u else None
    staff = is_staff(uid)
    day = msg.date.astimezone(L.TZ).date()
    app = ctx.application

    # Штраф / бонус: reaksiyasiz, jimgina yoziladi
    if staff and L.has_kind(text):
        items = L.parse_adjustments(text)
        if items:
            if edited:
                L.drop_adj_msg(chat.id, msg.message_id)
            done = []
            for it in items:
                pending = 0 if (it["worker_id"] and it["kind"]) else 1
                aid = L.add_adj(it["worker_id"], it["kind"], it["usd"], L.iso(day), text,
                                chat.id, msg.message_id, pending)
                if pending:
                    await ask_adj(app, aid)
                else:
                    done.append(aid)
            if done:
                await confirm_adjs(app, done)
            return

    # Hisobot: faqat shunga 👀
    if await submit_report(app, u, chat.id, msg.message_id, text, day, True):
        try:
            await msg.set_reaction(reaction="👀")
        except Exception:
            pass


async def on_my_chat_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cm = update.my_chat_member
    chat = cm.chat
    if chat.type == "private":
        return
    st = cm.new_chat_member.status
    if st in ("member", "administrator"):
        L.upsert_chat(chat.id, chat.title or str(chat.id))
        L.set_watch(chat.id, 1)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if update.effective_chat.type != "private":
        return
    ctx.user_data.pop("st", None)
    if is_staff(u.id):
        await update.message.reply_text("Главное меню:", reply_markup=main_kb(u.id))
        return

    row = L.user(u.id)
    if row and row["worker_id"]:
        await update.message.reply_text("Ваша панель:", reply_markup=worker_menu_kb())
        return

    ctx.application.bot_data.setdefault("names", {})[u.id] = u.full_name
    rows = [[B(w["name"], f"lk:{u.id}:{w['id']}")] for w in L.workers()]
    rows.append([B("➕ Новое имя", f"un:{u.id}")])
    rows.append([B("👑 Админ", f"ua:{u.id}"), B("❌ Отказать", f"ux:{u.id}")])
    uname = f"@{u.username}" if u.username else "без username"
    await send(ctx.bot, OWNER, f"👤 Запрос доступа: {u.full_name} ({uname}), id {u.id}\nКто это?", K(rows))
    await update.message.reply_text("Запрос отправлен. Ожидайте.")


async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Ваш ID: {update.effective_user.id}")


# ---------- карточка обработчика ----------

def card(w):
    t = L.today()
    p = L.percent_on(w["id"], t)
    users = L.users_of_worker(w["id"])
    tg = f"подключено аккаунтов: {len(users)}" if users else "не подключён"
    status = "активен" if w["active"] else "в архиве"
    return f"{w['name']} ({status})\nПроцент: {L.money(p)}%\nTelegram: {tg}"


def card_kb(w):
    wid = w["id"]
    rows = [
        [B("📊 Сегодня", f"rep:td:{wid}"), B("📅 Вчера", f"rep:yd:{wid}")],
        [B("🗓 Эта неделя", f"rep:tw:{wid}"), B("🗓 Прошлая неделя", f"rep:lw:{wid}")],
        [B("📈 Этот месяц", f"rep:tm:{wid}"), B("📉 Прошлый месяц", f"rep:lm:{wid}")],
        [B("📆 За дату", f"dt:w:{wid}"), B("✏️ Исправить чеки", f"fx:{wid}")],
        [B("📄 PDF эта неделя", f"pdf:w:tw:{wid}"), B("📄 PDF прошлая", f"pdf:w:lw:{wid}")],
    ]
    if not w["is_self"]:
        rows.append([B("🔁 Замена на день", f"sub:{wid}")])
    rows += [
        [B("💵 Изменить %", f"wp:{wid}"), B("💸 Выдать аванс", f"adv:{wid}")],
        [B("⚠️ Штраф", f"wf:{wid}"), B("🎁 Бонус", f"wb:{wid}")],
    ]
    if not w["is_self"]:
        rows.append([B("🔄 Заменить навсегда", f"wrep:{wid}")])
        rows.append([B("🔙 Обработчики", "w:list")])
    else:
        rows.append(menu_btn())
    return K(rows)


async def show_card(q, wid):
    w = L.worker(wid)
    await edit(q, card(w), card_kb(w))


def price_label(r):
    rng = f"{r['lo']:,}" if r["lo"] == r["hi"] else f"{r['lo']:,}-{r['hi']:,}"
    return f"{rng} = {L.money(r['usd'])}$"


async def show_prices(q):
    rows = [[B(price_label(r), f"prm:{r['id']}")] for r in L.list_prices()]
    rows.append([B("+ Добавить цену", "pra")])
    rows.append(menu_btn())
    await edit(q, "Прайс (нажмите на цену, чтобы изменить или удалить):", K(rows))


async def show_fx(q, wid, day):
    w = L.worker(wid)
    cs = L.day_checks(wid, day)
    if not cs:
        await edit(q, "На этот день чеков нет.", K([[B("🔙 Карточка", f"w:{wid}")]]))
        return
    used = sum(1 for c in cs if not c["skip"])
    kb, row = [], []
    for c in cs:
        row.append(B(("🚫 " if c["skip"] else "✅ ") + str(c["amount"]), f"fxc:{c['id']}:{wid}:{day}"))
        if len(row) == 3:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([B("📅 Изменить дату чека", f"fxm:{wid}:{day}")])
    kb.append([B("📊 Показать разбор", f"fxr:{wid}:{day}")])
    kb.append([B("🗑 Удалить весь день", f"dl:{wid}:{day}")])
    kb.append([B("🔙 Карточка", f"w:{wid}")])
    await edit(q, f"✏️ {w['name']} — {L.dshort(day)}\nЗасчитано: {used} из {len(cs)}\n"
                  f"Нажмите на чек, чтобы исключить 🚫 или вернуть ✅:", K(kb))


async def show_admins(q):
    rows = []
    for u in L.all_users():
        base = u["name"] or str(u["tg_id"])
        if u["worker_id"]:
            wk = L.worker(u["worker_id"])
            if wk and not wk["is_self"]:
                base += f" [{wk['name']}]"
        if u["role"] == "admin":
            rows.append([B(f"👑 {base} — снять админа", f"adm:rm:{u['tg_id']}")])
        else:
            rows.append([B(f"➕ {base} — сделать админом", f"adm:add:{u['tg_id']}")])
    rows.append([B("+ Добавить по ID", "adm:new")])
    rows.append(menu_btn())
    await edit(q, "Админы и пользователи:", K(rows))


def link_account(app, tid, wid):
    names = app.bot_data.setdefault("names", {})
    if L.user(tid):
        L.x("update users set worker_id=? where tg_id=?", (wid, tid))
    else:
        L.upsert_user(tid, names.get(tid, ""), "worker", wid)


# ---------- кнопки ----------

async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    d = q.data
    p = d.split(":")
    app = ctx.application
    bot = ctx.bot

    if p[0] == "me":
        u = L.user(uid)
        if not u or not u["worker_id"]:
            await q.answer("У вас нет привязанного обработчика.", show_alert=True)
            return
        await q.answer()
        wid = u["worker_id"]
        back = K([[B("🔙 Назад", "me:menu")]])
        if p[1] == "menu":
            await edit(q, "Выберите период для отчёта:", K([
                [B("Сегодня", "me:td"), B("Вчера", "me:yd")],
                [B("Эта неделя", "me:tw"), B("Прошлая неделя", "me:lw")],
                [B("🔙 Назад", "me:back")]
            ]))
        elif p[1] in ("td", "yd", "tw", "lw"):
            s, e = L.period_range(p[1])
            res = L.calc(s, e, only=wid)
            if res and L.has_data(res[0]):
                await show(bot, q, L.worker_report(res[0], L.label(s, e)), back)
            else:
                await edit(q, f"За этот период ({L.label(s, e)}) отчётов нет.", back)
        elif p[1] == "balance":
            s, e = L.week_start(), L.today()
            res = L.calc(s, e, only=wid)
            if res and L.has_data(res[0]):
                await edit(q, f"Ваша зп за неделю ({L.label(s, e)}): {L.money(res[0]['total'])}$",
                           K([[B("🔙 Назад", "me:back")]]))
            else:
                await edit(q, "За эту неделю отчётов пока нет.", K([[B("🔙 Назад", "me:back")]]))
        elif p[1] == "back":
            await edit(q, "Ваша панель:", worker_menu_kb())
        return

    await q.answer()
    if not is_staff(uid):
        return
    owner = uid == OWNER
    if p[0] in ("adm", "un", "ua", "ux", "lk", "lko", "lkx", "ow") and not owner:
        return

    # --- меню ---
    if d == "m:main":
        ctx.user_data.pop("st", None)
        await edit(q, "Главное меню:", main_kb(uid))
    elif d == "ow:cash":
        sw = L.self_worker()
        if sw:
            await show_card(q, sw["id"])

    # --- общие отчёты ---
    elif d == "rs:menu":
        await edit(q, "За какой период общий отчёт?", K(period_rows("rs")))
    elif p[0] == "rs":
        s, e = L.period_range(p[1])
        lab = L.label(s, e)
        res = visible(L.calc(s, e), owner)
        kb = K([[B("🔙 Назад", "rs:menu")], menu_btn()])
        if not res:
            await edit(q, f"За этот период ({lab}) отчётов нет.", kb)
        else:
            await show(bot, q, L.overall_text(res, lab, owner), kb)
    elif d == "rt:menu":
        await edit(q, "Рейтинг за какой период?", K(period_rows("rat")))
    elif p[0] == "rat":
        s, e = L.period_range(p[1])
        lab = L.label(s, e)
        res = [r for r in visible(L.calc(s, e), owner) if r["n"] > 0]
        kb = K([[B("🔙 Назад", "rt:menu")], menu_btn()])
        if not res:
            await edit(q, f"За этот период ({lab}) отчётов нет.", kb)
        else:
            await edit(q, L.rating_text(res, lab), kb)

    # --- PDF ---
    elif d == "pdf:menu":
        await edit(q, "PDF за какую неделю?", K([
            [B("Эта неделя", "pdf:p:tw"), B("Прошлая неделя", "pdf:p:lw")],
            menu_btn()
        ]))
    elif p[0] == "pdf" and p[1] == "p":
        key = p[2]
        rows = [[B("📦 Общий (все вместе)", f"pdf:a:{key}")],
                [B("📚 Все по отдельности", f"pdf:e:{key}")]]
        for w in L.workers():
            if can_see(uid, w["id"]):
                rows.append([B(f"📄 {w['name']}", f"pdf:w:{key}:{w['id']}")])
        rows.append([B("🔙 Назад", "pdf:menu")])
        await edit(q, f"PDF ({L.label(*L.period_range(key))}):", K(rows))
    elif p[0] == "pdf" and p[1] == "a":
        await send_pdf(bot, uid, p[2], None)
    elif p[0] == "pdf" and p[1] == "e":
        await send_all_pdfs(bot, uid, p[2])
    elif p[0] == "pdf" and p[1] == "w":
        await send_pdf(bot, uid, p[2], int(p[3]))

    # --- отчёт за дату ---
    elif d == "dt:all":
        ctx.user_data["st"] = ("dateall",)
        await edit(q, "Напишите дату (например 11.10) или период (10.10-12.10):", K([menu_btn()]))
    elif p[0] == "dt" and p[1] == "w":
        if not can_see(uid, int(p[2])):
            return
        ctx.user_data["st"] = ("datew", int(p[2]))
        await edit(q, "Напишите дату (например 11.10) или период (10.10-12.10):", K([menu_btn()]))

    # --- кнопки под коротким отчётом ---
    elif p[0] == "zp":
        wid, day = int(p[1]), p[2]
        if not can_see(uid, wid):
            return
        dd = dt.date.fromisoformat(day)
        res = L.calc(dd, dd, only=wid)
        if res and res[0]["days"]:
            await edit(q, L.explain(res[0], L.label(dd, dd), owner),
                       K([[B("⬅️ Кратко", f"zb:{wid}:{day}"), B("🗑 Удалить", f"dl:{wid}:{day}")]]))
        else:
            await edit(q, "За этот день отчёта нет.")
    elif p[0] == "zb":
        wid, day = int(p[1]), p[2]
        if not can_see(uid, wid):
            return
        dd = dt.date.fromisoformat(day)
        res = L.calc(dd, dd, only=wid)
        if res and res[0]["days"]:
            await edit(q, L.brief(res[0]), brief_kb(wid, day))
        else:
            await edit(q, "За этот день отчёта нет.")

    # --- удаление отчёта за день ---
    elif p[0] == "dl":
        wid, day = int(p[1]), p[2]
        if not can_see(uid, wid):
            return
        w = L.worker(wid)
        await edit(q, f"🗑 Удалить отчёт {w['name']} за {L.dshort(day)}?\nВсе чеки этого дня будут удалены.",
                   K([[B("✅ Да, удалить", f"dy:{wid}:{day}")],
                      [B("↩️ Отмена", f"zb:{wid}:{day}")]]))
    elif p[0] == "dy":
        wid, day = int(p[1]), p[2]
        if not can_see(uid, wid):
            return
        w = L.worker(wid)
        delete_day(wid, day)
        await edit(q, f"🗑 Отчёт {w['name']} за {L.dshort(day)} удалён.", K([menu_btn()]))

    # --- обработчики ---
    elif d == "w:list":
        rows = [[B(f"{w['name']} ({L.money(L.percent_on(w['id'], L.today()))}%)", f"w:{w['id']}")]
                for w in L.workers() if not w["is_self"]]
        rows.append([B("+ Новый обработчик", "w:new")])
        rows.append(menu_btn())
        await edit(q, "Обработчики:", K(rows))
    elif d == "w:new":
        ctx.user_data["st"] = ("newname",)
        await edit(q, "Напишите имя нового обработчика:", K([menu_btn()]))
    elif p[0] == "w" and p[1].isdigit():
        if can_see(uid, int(p[1])):
            await show_card(q, int(p[1]))
    elif p[0] == "wp":
        ctx.user_data["st"] = ("pct", int(p[1]))
        await edit(q, "Напишите новый процент числом (например 35).\n"
                      "Он применится с понедельника этой недели:", K([menu_btn()]))
    elif p[0] == "adv":
        ctx.user_data["st"] = ("adv", int(p[1]))
        await edit(q, "Напишите сумму аванса в $:", K([menu_btn()]))
    elif p[0] == "wf":
        ctx.user_data["st"] = ("fine", int(p[1]))
        await edit(q, "Напишите сумму штрафа в $:", K([menu_btn()]))
    elif p[0] == "wb":
        ctx.user_data["st"] = ("bonus", int(p[1]))
        await edit(q, "Напишите сумму бонуса в $:", K([menu_btn()]))
    elif p[0] == "rep":
        wid = int(p[2])
        if not can_see(uid, wid):
            return
        s, e = L.period_range(p[1])
        lab = L.label(s, e)
        res = L.calc(s, e, only=wid)
        back = K([[B("🔙 Карточка", f"w:{wid}")]])
        if res and L.has_data(res[0]):
            await show(bot, q, L.explain(res[0], lab, owner), back)
        else:
            await edit(q, f"За этот период ({lab}) отчётов нет.", back)
    elif p[0] == "wrep":
        ctx.user_data["st"] = ("replace", int(p[1]))
        w = L.worker(int(p[1]))
        await edit(q, f"Кто заменяет {w['name']} навсегда? Напишите имя нового обработчика:", K([menu_btn()]))

    # --- замена на день ---
    elif p[0] == "sub":
        wid = int(p[1])
        w = L.worker(wid)
        rows = [[B(o["name"], f"sub2:{wid}:{o['id']}")] for o in L.workers()
                if o["id"] != wid and not o["is_self"]]
        rows.append([B("🔙 Назад", f"w:{wid}")])
        await edit(q, f"Кто работал вместо {w['name']}?", K(rows))
    elif p[0] == "sub2":
        wid, w2 = int(p[1]), int(p[2])
        days = L.worker_days(wid, 10)
        a, b = L.worker(wid)["name"], L.worker(w2)["name"]
        if not days:
            await edit(q, f"У {a} нет отчётов.", K([[B("🔙 Назад", f"w:{wid}")]]))
        else:
            rows = [[B(f"{L.dshort(r['rd'])} ({r['n']} {L.chk_word(r['n'])})", f"sub3:{wid}:{w2}:{r['rd']}")]
                    for r in days]
            rows.append([B("🔙 Назад", f"w:{wid}")])
            await edit(q, f"Отчёт {a} за какой день перенести на {b}?", K(rows))
    elif p[0] == "sub3":
        wid, w2, day = int(p[1]), int(p[2]), p[3]
        a, b = L.worker(wid)["name"], L.worker(w2)["name"]
        n = L.move_day(wid, w2, day)
        await edit(q, f"✅ Отчёт {a} за {L.dshort(day)} ({n} {L.chk_word(n)}) перенесён на {b}.\n"
                      f"{a} в этот день не работал.",
                   K([[B("🔙 Карточка", f"w:{wid}")]]))

    # --- исправление чеков ---
    elif p[0] == "fx":
        wid = int(p[1])
        if not can_see(uid, wid):
            return
        days = L.worker_days(wid, 10)
        if not days:
            await edit(q, "Отчётов пока нет.", K([[B("🔙 Карточка", f"w:{wid}")]]))
        else:
            rows = [[B(f"{L.dshort(r['rd'])} ({r['n']} {L.chk_word(r['n'])})", f"fxd:{wid}:{r['rd']}")]
                    for r in days]
            rows.append([B("🔙 Карточка", f"w:{wid}")])
            await edit(q, "Выберите день:", K(rows))
    elif p[0] == "fxd":
        if can_see(uid, int(p[1])):
            await show_fx(q, int(p[1]), p[2])
    elif p[0] == "fxc":
        cid, wid = int(p[1]), int(p[2])
        if can_see(uid, wid) and own_check(cid, wid):
            L.toggle_check(cid)
            await show_fx(q, wid, p[3])
    elif p[0] == "fxr":
        wid, day = int(p[1]), p[2]
        if not can_see(uid, wid):
            return
        dd = dt.date.fromisoformat(day)
        res = L.calc(dd, dd, only=wid)
        back = K([[B("🔙 К чекам", f"fxd:{wid}:{day}")]])
        if res and res[0]["days"]:
            await show(bot, q, L.explain(res[0], L.label(dd, dd), owner), back)
        else:
            await edit(q, "За этот день отчёта нет.", back)

    # --- изменить дату чека ---
    elif p[0] == "fxm":
        wid, day = int(p[1]), p[2]
        if not can_see(uid, wid):
            return
        cs = L.day_checks(wid, day)
        kb, row = [], []
        for c in cs:
            row.append(B(str(c["amount"]), f"fxm2:{c['id']}:{wid}:{day}"))
            if len(row) == 3:
                kb.append(row)
                row = []
        if row:
            kb.append(row)
        kb.append([B("🔙 Назад", f"fxd:{wid}:{day}")])
        await edit(q, f"📅 {L.dshort(day)}: у какого чека изменить дату?", K(kb))
    elif p[0] == "fxm2":
        cid, wid, day = int(p[1]), int(p[2]), p[3]
        if not can_see(uid, wid) or not own_check(cid, wid):
            return
        c = L.ql("select amount from checks where id=?", (cid,))
        t = L.today()
        rows, row = [], []
        for i in range(6):
            dy = (t - dt.timedelta(days=i)).isoformat()
            if dy == day:
                continue
            row.append(B(L.dshort(dy), f"fxm3:{cid}:{dy}:{wid}:{day}"))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([B("✍️ Другая дата", f"fxmt:{cid}:{wid}:{day}")])
        rows.append([B("🔙 Назад", f"fxm:{wid}:{day}")])
        await edit(q, f"Чек {c['amount']} сейчас на {L.dshort(day)}.\nНа какую дату перенести?", K(rows))
    elif p[0] == "fxm3":
        cid, newday, wid, day = int(p[1]), p[2], int(p[3]), p[4]
        if not can_see(uid, wid) or not own_check(cid, wid):
            return
        c = L.ql("select amount from checks where id=?", (cid,))
        L.x("update checks set rd=? where id=?", (newday, cid))
        await edit(q, f"✅ Чек {c['amount']} перенесён: {L.dshort(day)} → {L.dshort(newday)}",
                   K([[B(f"📋 Чеки {L.dshort(newday)}", f"fxd:{wid}:{newday}")],
                      [B(f"📋 Чеки {L.dshort(day)}", f"fxd:{wid}:{day}")],
                      [B("🔙 Карточка", f"w:{wid}")]]))
    elif p[0] == "fxmt":
        cid, wid, day = int(p[1]), int(p[2]), p[3]
        if not can_see(uid, wid) or not own_check(cid, wid):
            return
        ctx.user_data["st"] = ("chkdate", cid, wid, day)
        await edit(q, "Напишите правильную дату (например 10.10):", K([menu_btn()]))

    # --- прайс ---
    elif d == "pr:menu":
        await show_prices(q)
    elif p[0] == "prm":
        r = L.price_row(int(p[1]))
        if not r:
            await show_prices(q)
        else:
            await edit(q, f"Цена: {price_label(r)}", K([
                [B("✏️ Изменить", f"pre:{r['id']}"), B("🗑 Удалить", f"prd:{r['id']}")],
                [B("🔙 Прайс", "pr:menu")]
            ]))
    elif p[0] == "pre":
        ctx.user_data["st"] = ("priceedit", int(p[1]))
        await edit(q, "Напишите новую цену в $ (одно число).\n"
                      "Или «от до цена», например: 1900 2000 8", K([menu_btn()]))
    elif p[0] == "prd":
        L.del_price(int(p[1]))
        await show_prices(q)
    elif d == "pra":
        ctx.user_data["st"] = ("priceadd",)
        await edit(q, "Напишите: «сумма цена» (например 5200 20) или «от до цена»", K([menu_btn()]))

    # --- чаты ---
    elif d == "ch:menu" or p[0] == "cw":
        if p[0] == "cw":
            L.set_watch(int(p[1]), int(p[2]))
        rows = [[B(("✅ " if c["watch"] else "❌ ") + (c["title"] or str(c["chat_id"])),
                   f"cw:{c['chat_id']}:{0 if c['watch'] else 1}")] for c in L.q("select * from chats")]
        rows.append(menu_btn())
        await edit(q, "Чаты, куда добавлен бот.\n✅ — бот читает отчёты из этого чата\n❌ — игнорирует его\n"
                      "Нажмите на чат, чтобы включить/выключить:", K(rows))

    # --- вопросы ---
    elif d == "q:list":
        pr = L.pending_reports()
        pa = L.pending_adjs()
        if not pr and not pa:
            await edit(q, "Открытых вопросов нет 😊", K([menu_btn()]))
        else:
            await edit(q, "Открытые вопросы отправлены ниже 👇", K([menu_btn()]))
            for rid in pr:
                await advance(app, rid)
            for aid in pa:
                await ask_adj(app, aid)
    elif p[0] == "pw":
        rid, wid = int(p[1]), int(p[2])
        r = L.report(rid)
        if r is None:
            await edit(q, "Отчёт уже не найден.")
            return
        L.x("update reports set worker_id=? where id=?", (wid, rid))
        sender = r["sender_id"]
        linked = ""
        if (sender and sender not in (OWNER, ANON) and not is_staff(sender)
                and L.user(sender) is None and r["chat_id"] != sender):
            link_account(app, sender, wid)
            linked = "\n🔗 Аккаунт отправителя привязан."
        await edit(q, f"✅ Назначен: {L.worker(wid)['name']}{linked}")
        await advance(app, rid)
    elif p[0] == "pd":
        rid = int(p[1])
        L.x("update checks set rd=? where report_id=? and rd is null", (p[2], rid))
        await edit(q, f"✅ Дата установлена: {L.dshort(p[2])}")
        await advance(app, rid)
    elif p[0] == "pwi":
        L.delete_report(int(p[1]))
        await edit(q, "❌ Отчёт проигнорирован.")
    elif p[0] in ("aw", "ak"):
        aid = int(p[1])
        a = L.adj(aid)
        if a is None:
            return
        if p[0] == "aw":
            L.adj_resolve(aid, worker_id=int(p[2]))
        else:
            L.adj_resolve(aid, kind=p[2])
        a = L.adj(aid)
        if a["worker_id"] is not None and a["kind"] is not None:
            await edit(q, "✅ Принято.")
            await confirm_adjs(app, [aid])
        else:
            await edit(q, "Принято, нужно уточнить ещё.")
            await ask_adj(app, aid)
    elif p[0] == "ai":
        L.adj_delete(int(p[1]))
        await edit(q, "🚫 Проигнорировано.")
    elif p[0] == "ac":
        aid = int(p[1])
        L.adj_toggle(aid)
        a = L.adj(aid)
        if a:
            await edit(q, "↩️ Отменено." if a["cancelled"] else "✅ Восстановлено.", K([menu_btn()]))

    # --- доступ, привязка аккаунтов, админы (только владелец) ---
    elif p[0] == "lk":
        tid, wid = int(p[1]), int(p[2])
        link_account(app, tid, wid)
        await edit(q, f"✅ Аккаунт привязан к: {L.worker(wid)['name']}")
        await send(bot, tid, "Доступ открыт! Нажмите /start", worker_menu_kb())
    elif p[0] == "lko":
        tid = int(p[1])
        rows = [[B(w["name"], f"lk:{tid}:{w['id']}")] for w in L.workers()]
        rows.append([B("🚫 Не привязывать", "lkx")])
        await edit(q, "Выберите, кто это:", K(rows))
    elif d == "lkx":
        await edit(q, "Не привязано.")
    elif p[0] == "un":
        ctx.user_data["st"] = ("linknew", int(p[1]))
        await edit(q, "Напишите новое имя для этого человека:", K([menu_btn()]))
    elif p[0] == "ua":
        tid = int(p[1])
        L.upsert_user(tid, app.bot_data.get("names", {}).get(tid) or await tg_name(bot, tid), "admin", None)
        await edit(q, "✅ Назначен админом.")
        await send(bot, tid, "Вам выданы права админа. Нажмите /start")
    elif p[0] == "ux":
        await edit(q, "❌ Доступ отклонен.")
    elif d == "adm:menu":
        await show_admins(q)
    elif d == "adm:new":
        ctx.user_data["st"] = ("addadmin",)
        await edit(q, "Напишите Telegram ID нового админа (число):", K([menu_btn()]))
    elif p[0] == "adm" and p[1] == "add":
        tid = int(p[2])
        L.set_role(tid, "admin")
        await send(bot, tid, "Вам выданы права админа. Нажмите /start")
        await show_admins(q)
    elif p[0] == "adm" and p[1] == "rm":
        tid = int(p[2])
        L.remove_admin(tid)
        await send(bot, tid, "Права админа сняты.")
        await show_admins(q)


# ---------- личные сообщения ----------

def parse_range(text):
    ds = L.parse_dates(text, L.today())
    if not ds:
        return None
    return min(ds), max(ds)


async def on_private_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    msg = update.effective_message
    if not msg or not u or not is_staff(u.id):
        return
    text = (msg.text or "").strip()
    app = ctx.application
    st = ctx.user_data.get("st")
    owner = u.id == OWNER

    pq = app.bot_data.setdefault("pq", {})
    if pq and not st:
        key = None
        if msg.reply_to_message and msg.reply_to_message.message_id in pq:
            key = msg.reply_to_message.message_id
        elif len(pq) == 1:
            key = next(iter(pq))
        if key is not None:
            try:
                v = float(text.replace(",", "."))
            except ValueError:
                v = None
            if v is not None:
                rid, amount = pq[key]
                pq.clear()
                L.add_price(amount, amount, v)
                await msg.reply_text(f"✅ Запомнил: {amount:,} = {L.money(v)}$")
                for r in L.pending_reports():
                    need = L.report_needs(r)
                    if need is None or need[0] == "price":
                        await advance(app, r)
                return

    if not st:
        day = msg.date.astimezone(L.TZ).date()
        if L.has_kind(text):
            items = L.parse_adjustments(text)
            if items:
                done = []
                for it in items:
                    pending = 0 if (it["worker_id"] and it["kind"]) else 1
                    aid = L.add_adj(it["worker_id"], it["kind"], it["usd"], L.iso(day), text,
                                    u.id, msg.message_id, pending)
                    if pending:
                        await ask_adj(app, aid)
                    else:
                        done.append(aid)
                if done:
                    await confirm_adjs(app, done)
                return
        if L.parse_report(text, day):
            await submit_report(app, u, u.id, msg.message_id, text, day, False)
            return
        await msg.reply_text("Главное меню:", reply_markup=main_kb(u.id))
        return

    act = st[0]
    if act == "chkdate":
        cid, wid, oldday = st[1], st[2], st[3]
        ds = L.parse_dates(text, L.today())
        if not ds:
            await msg.reply_text("Не понял дату. Напишите, например: 10.10")
            return
        ctx.user_data.pop("st", None)
        if not can_see(u.id, wid) or not own_check(cid, wid):
            await msg.reply_text("Чек не найден.", reply_markup=main_kb(u.id))
            return
        newday = L.iso(ds[0])
        c = L.ql("select amount from checks where id=?", (cid,))
        L.x("update checks set rd=? where id=?", (newday, cid))
        await msg.reply_text(
            f"✅ Чек {c['amount']} перенесён: {L.dshort(oldday)} → {L.dshort(newday)}",
            reply_markup=K([[B(f"📋 Чеки {L.dshort(newday)}", f"fxd:{wid}:{newday}")],
                            [B("🔙 Карточка", f"w:{wid}")]]))
    elif act in ("dateall", "datew"):
        rng = parse_range(text)
        if not rng:
            await msg.reply_text("Не понял дату. Напишите, например: 11.10 или 10.10-12.10")
            return
        s, e = rng
        ctx.user_data.pop("st", None)
        lab = L.label(s, e)
        kb = K([menu_btn()])
        if act == "dateall":
            res = visible(L.calc(s, e), owner)
            if not res:
                await msg.reply_text(f"За {lab} отчётов нет.", reply_markup=kb)
            else:
                await send(ctx.bot, u.id, L.overall_text(res, lab, owner), kb)
        else:
            res = L.calc(s, e, only=st[1])
            if res and L.has_data(res[0]):
                await send(ctx.bot, u.id, L.explain(res[0], lab, owner), kb)
            else:
                await msg.reply_text(f"За {lab} отчётов нет.", reply_markup=kb)
    elif act == "newname":
        if not text:
            await msg.reply_text("Имя не может быть пустым.")
            return
        ctx.user_data["st"] = ("newpct", text)
        await msg.reply_text(f"Какой процент у {text}? Напишите число (например 30):")
    elif act == "newpct":
        try:
            v = float(text.replace(",", "."))
        except ValueError:
            await msg.reply_text("Введите число от 0 до 100:")
            return
        try:
            wid = L.add_worker(st[1], v)
        except Exception:
            ctx.user_data.pop("st", None)
            await msg.reply_text("Такое имя уже есть.", reply_markup=main_kb(u.id))
            return
        ctx.user_data.pop("st", None)
        w = L.worker(wid)
        await msg.reply_text(f"✅ Добавлен обработчик: {st[1]} {L.money(v)}%", reply_markup=card_kb(w))
    elif act == "linknew":
        if not text:
            await msg.reply_text("Имя не может быть пустым.")
            return
        ctx.user_data["st"] = ("linknewpct", st[1], text)
        await msg.reply_text(f"Какой процент у {text}? Напишите число (например 30):")
    elif act == "linknewpct":
        try:
            v = float(text.replace(",", "."))
        except ValueError:
            await msg.reply_text("Введите число от 0 до 100:")
            return
        tid, name = st[1], st[2]
        try:
            wid = L.add_worker(name, v)
        except Exception:
            ctx.user_data.pop("st", None)
            await msg.reply_text("Такое имя уже есть.", reply_markup=main_kb(u.id))
            return
        ctx.user_data.pop("st", None)
        link_account(app, tid, wid)
        await send(ctx.bot, tid, "Доступ открыт! Нажмите /start", worker_menu_kb())
        await msg.reply_text(f"✅ {name} ({L.money(v)}%) добавлен и подключён.", reply_markup=main_kb(u.id))
    elif act == "pct":
        try:
            v = float(text.replace(",", "."))
        except ValueError:
            await msg.reply_text("Введите число:")
            return
        ctx.user_data.pop("st", None)
        wid = st[1]
        L.set_percent(wid, v)
        w = L.worker(wid)
        mon = L.week_start().strftime("%d.%m")
        await msg.reply_text(f"✅ Процент {L.money(v)}% действует с понедельника ({mon}). "
                             f"Отчёты этой недели пересчитаны.", reply_markup=card_kb(w))
    elif act == "adv":
        try:
            v = float(text.replace(",", "."))
        except ValueError:
            await msg.reply_text("Введите сумму числом:")
            return
        ctx.user_data.pop("st", None)
        wid = st[1]
        L.add_advance(wid, v, L.iso(L.today()), "Аванс")
        w = L.worker(wid)
        await msg.reply_text(f"✅ Выдан аванс: {L.money(v)}$", reply_markup=card_kb(w))
    elif act in ("fine", "bonus"):
        try:
            v = float(text.replace(",", "."))
        except ValueError:
            await msg.reply_text("Введите число:")
            return
        ctx.user_data.pop("st", None)
        wid = st[1]
        L.add_adj(wid, act, v, L.iso(L.today()), "вручную")
        w = L.worker(wid)
        name = "Штраф" if act == "fine" else "Бонус"
        await msg.reply_text(f"✅ {name} добавлен: {L.money(v)}$", reply_markup=card_kb(w))
    elif act == "replace":
        if not text:
            await msg.reply_text("Имя не может быть пустым.")
            return
        old = L.worker(st[1])
        pct = L.percent_on(old["id"], L.today())
        try:
            wid = L.add_worker(text, pct)
        except Exception:
            ctx.user_data.pop("st", None)
            await msg.reply_text("Такое имя уже есть.", reply_markup=main_kb(u.id))
            return
        ctx.user_data.pop("st", None)
        L.set_active(old["id"], 0)
        w = L.worker(wid)
        await msg.reply_text(f"✅ {text} заменяет {old['name']}", reply_markup=card_kb(w))
    elif act == "priceadd":
        try:
            nums = [float(t.replace(",", ".")) for t in text.split()]
        except ValueError:
            await msg.reply_text("Нужны только числа. Пример: 5200 20")
            return
        if len(nums) == 2:
            L.add_price(int(nums[0]), int(nums[0]), nums[1])
        elif len(nums) == 3:
            L.add_price(int(nums[0]), int(nums[1]), nums[2])
        else:
            await msg.reply_text("Нужно 2 или 3 числа. Пример: 5200 20")
            return
        ctx.user_data.pop("st", None)
        await msg.reply_text("✅ Добавлено в прайс.", reply_markup=main_kb(u.id))
    elif act == "priceedit":
        try:
            nums = [float(t.replace(",", ".")) for t in text.split()]
        except ValueError:
            await msg.reply_text("Нужны только числа. Пример: 8")
            return
        row = L.price_row(st[1])
        if not row:
            ctx.user_data.pop("st", None)
            await msg.reply_text("Эта цена уже удалена.", reply_markup=main_kb(u.id))
            return
        if len(nums) == 1:
            L.update_price(row["id"], row["lo"], row["hi"], nums[0])
        elif len(nums) == 2:
            L.update_price(row["id"], int(nums[0]), int(nums[0]), nums[1])
        elif len(nums) == 3:
            L.update_price(row["id"], int(nums[0]), int(nums[1]), nums[2])
        else:
            await msg.reply_text("Нужно 1, 2 или 3 числа. Пример: 8")
            return
        ctx.user_data.pop("st", None)
        await msg.reply_text("✅ Цена изменена. Чеки этой недели пересчитаны.", reply_markup=main_kb(u.id))
    elif act == "addadmin":
        if not text.isdigit():
            await msg.reply_text("Нужно число (Telegram ID).")
            return
        tid = int(text)
        ctx.user_data.pop("st", None)
        if L.user(tid):
            L.set_role(tid, "admin")
        else:
            L.upsert_user(tid, "", "admin", None)
        await msg.reply_text("✅ Добавлен админ. Пусть нажмёт /start у бота.", reply_markup=main_kb(u.id))


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.error("Ошибка: %s", ctx.error, exc_info=ctx.error)


async def post_init(app: Application):
    for rid in L.pending_reports():
        await advance(app, rid)
    for aid in L.pending_adjs():
        await ask_adj(app, aid)


def main():
    L.db()
    app = (Application.builder()
           .token(TOKEN)
           .post_init(post_init)
           .concurrent_updates(True)
           .build())
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_private_text))
    app.add_handler(MessageHandler(
        (filters.ChatType.GROUPS | filters.ChatType.CHANNEL) & (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
        on_chat_text))
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
