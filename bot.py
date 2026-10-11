# -*- coding: utf-8 -*-
import os
import logging
import datetime as dt
from telegram import Update, InlineKeyboardButton as B, InlineKeyboardMarkup as K
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler, MessageHandler,
                          ChatMemberHandler, ContextTypes, filters)
import logic as L

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kassabot")

TOKEN = os.environ["BOT_TOKEN"]
OWNER = int(os.environ["8785896869"])
REPORT_TIME = os.getenv("REPORT_TIME", "23:30")

PERIODS = [("Сегодня", "td"), ("Вчера", "yd"), ("Эта неделя", "tw"), ("Прошлая неделя", "lw")]

def is_staff(uid):
    return uid == OWNER or uid in L.admins()

def staff_ids():
    return [OWNER] + [a for a in L.admins() if a != OWNER]

def menu_btn():
    return [B("🔙 Меню", "m:main")]

def period_rows(prefix):
    return [
        [B(PERIODS[0][0], f"{prefix}:td"), B(PERIODS[1][0], f"{prefix}:yd")],
        [B(PERIODS[2][0], f"{prefix}:tw"), B(PERIODS[3][0], f"{prefix}:lw")],
        [B("📅 С даты по дату", f"{prefix}:custom")],
        [B("🔙 Меню", "m:main")]
    ]

async def send(bot, chat_id, text, kb=None):
    last = None
    for part in L.chunks(text):
        try:
            last = await bot.send_message(chat_id, part, reply_markup=kb)
        except Exception as e:
            log.warning("send to %s failed: %s", chat_id, e)
    return last

async def edit(q, text, kb=None):
    try:
        await q.edit_message_text(text, reply_markup=kb)
    except Exception as e:
        if "not modified" not in str(e).lower():
            log.warning("edit failed: %s", e)

def main_kb():
    return K([
        [B("📊 Отчёты", "rs:menu"), B("🏆 Рейтинг", "rt:menu")],
        [B("👥 Abrabotchiklar", "w:list"), B("💲 Прайс", "pr:menu")],
        [B("💬 Чаты", "ch:menu"), B(f"❓ Вопросы", "q:list")]
    ])

def worker_menu_kb():
    return K([
        [B("📥 Мой отчёт (Период)", "me:menu")],
        [B("💰 Мой баланс", "me:balance")]
    ])

async def send_overall(bot, chat_id, s, e, kb=None):
    res = L.calc(s, e)
    lab = L.label(s, e)
    await send(bot, chat_id, L.overall_text(res, lab))
    await send(bot, chat_id, L.rating_text(res, lab), kb)

async def finalize(app, rid):
    L.x("update reports set status='done' where id=?", (rid,))
    r = L.report(rid)
    wid = r["worker_id"]
    for d in L.report_days(rid):
        day = dt.date.fromisoformat(d)
        res = L.calc(day, day, only=wid)
        if not res:
            continue
        lab = L.label(day, day)
        for sid in staff_ids():
            await send(app.bot, sid, L.explain(res[0], lab))
            await send(app.bot, sid, L.clean_report(res[0], lab))

async def advance(app, rid):
    need = L.report_needs(rid)
    if need is None:
        await finalize(app, rid)
        return
    kind, arg = need
    r = L.report(rid)
    snippet = (r["raw"] or "")[:350]
    if kind == "worker":
        rows = [[B(w["name"], f"pw:{rid}:{w['id']}")] for w in L.workers()]
        rows.append([B("❌ Это не отчёт", f"pwi:{rid}")])
        await send(app.bot, OWNER, f"❓ Чей это отчёт?\n\n{snippet}", K(rows))
    elif kind == "date":
        t = L.today()
        ds = [t - dt.timedelta(days=i) for i in range(4)]
        rows = [[B(d.strftime("%d.%m"), f"pd:{rid}:{d.isoformat()}")] for d in ds]
        rows.append([B("❌ Это не отчёт", f"pwi:{rid}")])
        name = L.worker(r["worker_id"])["name"]
        await send(app.bot, OWNER, f"❓ {name}: на какой день этот отчёт?", K(rows))
    elif kind == "price":
        name = L.worker(r["worker_id"])["name"]
        await send(app.bot, OWNER, f"❓ {name}: сколько стоит чек {arg:,}? Ответьте числом.")
        app.bot_data.setdefault("pq", {})[r["msg_id"]] = (rid, arg)

async def ask_adj(app, aid):
    a = L.adj(aid)
    snippet = (a["raw"] or "")[:300]
    if a["worker_id"] is None:
        rows = [[B(w["name"], f"aw:{aid}:{w['id']}")] for w in L.workers()]
        rows.append([B("🚫 Игнорировать", f"ai:{aid}")])
        kind_name = "штраф" if a["kind"] == "fine" else "бонус"
        await send(app.bot, OWNER, f"❓ Кому {kind_name} {L.money(a['usd'])}$?\n\n{snippet}", K(rows))
    elif a["kind"] is None:
        rows = [[B("Штраф", f"ak:{aid}:fine"), B("Бонус", f"ak:{aid}:bonus")], [B("🚫 Игнорировать", f"ai:{aid}")]]
        w = L.worker(a["worker_id"])["name"]
        await send(app.bot, OWNER, f"❓ {w}: {L.money(a['usd'])}$ это штраф или бонус?\n\n{snippet}", K(rows))

async def confirm_adjs(app, ids):
    lines, rows = [], []
    for aid in ids:
        a = L.adj(aid)
        w = L.worker(a["worker_id"])["name"]
        t = "Штраф" if a["kind"] == "fine" else "Бонус"
        lines.append(f"{w}: {t} {L.money(a['usd'])}$ ({L.dshort(a['day'])})")
        rows.append([B(f"Отменить {w} {t.lower()}", f"ac:{aid}")])
    rows.append(menu_btn())
    for sid in staff_ids():
        await send(app.bot, sid, "✅ Записано:\n" + "\n".join(lines), K(rows))

async def on_chat_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return
    text = msg.text or msg.caption
    if not text:
        return
    row = L.chat(chat.id)
    if not row or not row["watch"]:
        return
    
    try:
        await msg.set_reaction(reaction="👀")
    except Exception:
        pass

    edited = bool(update.edited_message or update.edited_channel_post)
    uid = msg.from_user.id if msg.from_user else None
    staff = is_staff(uid)
    day = msg.date.astimezone(L.TZ).date()
    app = ctx.application

    if staff and L.has_kind(text):
        if edited:
            L.drop_adj_msg(chat.id, msg.message_id)
        items = L.parse_adjustments(text)
        if not items:
            return
        done = []
        for it in items:
            pending = 0 if (it["worker_id"] and it["kind"]) else 1
            aid = L.add_adj(it["worker_id"], it["kind"], it["usd"], L.iso(day), text, chat.id, msg.message_id, pending)
            if pending:
                await ask_adj(app, aid)
            else:
                done.append(aid)
        if done:
            await confirm_adjs(app, done)
        return

    checks = L.parse_report(text, day)
    if not checks:
        return
    wid = None
    if uid == OWNER:
        wid = L.self_worker()["id"]
    elif uid:
        u = L.user(uid)
        wid = u["worker_id"] if u else None
    if wid is None:
        wid = L.worker_by_name(text)
    if wid is None and not L.has_rd(text):
        return

    rid = L.create_report(wid, chat.id, msg.message_id, uid, text, checks)
    await advance(app, rid)

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
    if is_staff(u.id):
        await update.message.reply_text("Главное меню:", reply_markup=main_kb())
        return
    
    row = L.user(u.id)
    if row and row["worker_id"]:
        await update.message.reply_text("Ваша панель:", reply_markup=worker_menu_kb())
        return

    rows = [[B(w["name"], f"ul:{u.id}:{w['id']}")] for w in L.workers()]
    rows.append([B("👑 Назначить админом", f"ua:{u.id}"), B("❌ Отказать", f"ux:{u.id}")])
    await send(ctx.bot, OWNER, f"👤 Запрос доступа: {u.full_name} (@{u.username})", K(rows))
    await update.message.reply_text("Запрос отправлен.")

async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Ваш ID: {update.effective_user.id}")

def card(w):
    t = L.today()
    p = L.percent_on(w["id"], t)
    users = L.users_of_worker(w["id"])
    tg = "подключён" if users else "не подключён"
    status = "активен" if w["active"] else "в архиве"
    return f"{w['name']} ({status})\nПроцент: {L.money(p)}%\nTelegram: {tg}"

def card_kb(w):
    wid = w["id"]
    return K([
        [B("📊 Сегодня", f"rep:td:{wid}"), B("📅 Вчера", f"rep:yd:{wid}")],
        [B("📈 Этот месяц", f"rep:tw:{wid}"), B("📉 Прошлый", f"rep:lw:{wid}")],
        [B("💵 Изменить %", f"wp:{wid}"), B("⚠️ Штраф/Бонус", f"wf:{wid}")],
        [B("💸 Выдать аванс", f"adv:{wid}"), B("🔄 Замена", f"wrep:{wid}")],
        [B("🔙 Abrabotchiklar", "w:list")]
    ])

async def show_card(q, wid):
    w = L.worker(wid)
    await edit(q, card(w), card_kb(w))

async def show_prices(q):
    rows = []
    for r in L.list_prices():
        rng = f"{r['lo']:,}" if r["lo"] == r["hi"] else f"{r['lo']:,}-{r['hi']:,}"
        rows.append([B(f"{rng} = {L.money(r['usd'])}$", f"prd:{r['id']}")])
    rows.append([B("+ Добавить цену", "pra")])
    rows.append(menu_btn())
    await edit(q, "Прайс (нажмите для удаления):", K(rows))

async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    d = q.data
    p = d.split(":")
    
    await q.answer()
    
    if p[0] == "me":
        u = L.user(uid)
        if not u or not u["worker_id"]:
            await q.answer("У вас нет привязанного abrabotchika.", show_alert=True)
            return
        wid = u["worker_id"]
        if p[1] == "menu":
            await edit(q, "Выберите период для отчёта:", K([
                [B("Сегодня", "me:td"), B("Вчера", "me:yd")],
                [B("Эта неделя", "me:tw"), B("Прошлая неделя", "me:lw")],
                [B("🔙 Меню", "me:back")]
            ]))
        elif p[1] in ("td", "yd", "tw", "lw"):
            s, e = L.period_range(p[1])
            res = L.calc(s, e, only=wid)
            if res:
                await send(ctx.bot, uid, L.clean_report(res[0], L.label(s, e)))
        elif p[1] == "balance":
            t = L.today()
            res = L.calc(t, t, only=wid)
            if res:
                await send(ctx.bot, uid, f"Ваша текущая зп за сегодня: {L.money(res[0]['total'])}$")
        elif p[1] == "back":
            await edit(q, "Ваша панель:", worker_menu_kb())
        return

    if not is_staff(uid):
        return

    if d == "m:main":
        await edit(q, "Главное меню:", main_kb())
    elif d == "rs:menu":
        await edit(q, "За какой период общий отчёт?", K(period_rows("rs")))
    elif p[0] == "rs":
        s, e = L.period_range(p[1])
        await send_overall(ctx.bot, uid, s, e, K([menu_btn()]))
    elif d == "rt:menu":
        await edit(q, "Рейтинг за какой период?", K(period_rows("rat")))
    elif p[0] == "rat":
        s, e = L.period_range(p[1])
        await send(ctx.bot, uid, L.rating_text(L.calc(s, e), L.label(s, e)), K([menu_btn()]))
    elif d == "w:list":
        rows = [[B(f"{w['name']} ({L.money(L.percent_on(w['id'], L.today()))}%)", f"w:{w['id']}")] for w in L.workers()]
        rows.append([B("+ Новый abrabotchik", "w:new")])
        rows.append(menu_btn())
        await edit(q, "Abrabotchiklar:", K(rows))
    elif d == "w:new":
        ctx.user_data["st"] = ("newname",)
        await edit(q, "Напишите имя нового abrabotchika:", K([menu_btn()]))
    elif p[0] == "w" and p[1].isdigit():
        await show_card(q, int(p[1]))
    elif p[0] == "wp":
        ctx.user_data["st"] = ("pct", int(p[1]))
        await edit(q, "Напишите новый процент числом (например 35):", K([menu_btn()]))
    elif p[0] == "adv":
        ctx.user_data["st"] = ("adv", int(p[1]))
        await edit(q, "Напишите сумму аванса в $:", K([menu_btn()]))
    elif p[0] == "wf":
        ctx.user_data["st"] = ("fine", int(p[1]))
        await edit(q, "Напишите сумму штрафа в $:", K([menu_btn()]))
    elif p[0] == "rep":
        s, e = L.period_range(p[1])
        wid = int(p[2])
        res = L.calc(s, e, only=wid)
        if res:
            await send(ctx.bot, uid, L.explain(res[0], L.label(s, e)))
            await send(ctx.bot, uid, L.clean_report(res[0], L.label(s, e)))
    elif p[0] == "wrep":
        ctx.user_data["st"] = ("replace", int(p[1]))
        w = L.worker(int(p[1]))
        await edit(q, f"Кто заменяет {w['name']}? Напишите имя нового abrabotchika:", K([menu_btn()]))
    elif d == "pr:menu":
        await show_prices(q)
    elif p[0] == "prd":
        L.del_price(int(p[1]))
        await show_prices(q)
    elif d == "pra":
        ctx.user_data["st"] = ("priceadd",)
        await edit(q, "Напишите: «сумма цена» (например 5200 20) или «от до цена»", K([menu_btn()]))
    elif d == "ch:menu":
        rows = [[B(("✅ " if c["watch"] else "❌ ") + (c["title"] or str(c["chat_id"])), f"cw:{c['chat_id']}:{0 if c['watch'] else 1}")] for c in L.q("select * from chats")]
        rows.append(menu_btn())
        await edit(q, "Чаты и каналы (нажмите для включения/выключения):", K(rows))
    elif p[0] == "cw":
        L.set_watch(int(p[1]), int(p[2]))
        await edit(q, "Статус слежения обновлён.", K([menu_btn()]))
    elif d == "q:list":
        pr = L.pending_reports()
        if not pr:
            await edit(q, "Открытых вопросов нет 😊", K([menu_btn()]))
        else:
            for rid in pr:
                await advance(app, rid)
    elif p[0] == "pw":
        rid, wid = int(p[1]), int(p[2])
        L.x("update reports set worker_id=? where id=?", (wid, rid))
        await edit(q, f"✅ Abrabotchik назначен: {L.worker(wid)['name']}")
        await advance(app, rid)
    elif p[0] == "pd":
        rid = int(p[1])
        L.x("update checks set rd=? where report_id=? and rd is null", (p[2], rid))
        await edit(q, f"✅ Дата установлена: {L.dshort(p[2])}")
        await advance(app, rid)
    elif p[0] == "pwi":
        L.delete_report(int(p[1]))
        await edit(q, "❌ Отчёт проигнорирован.")
    elif p[0] == "ul":
        tid, wid = int(p[1]), int(p[2])
        L.upsert_user(tid, "", "worker", wid)
        await edit(q, f"✅ Пользователь подключен как {L.worker(wid)['name']}")
        await send(ctx.bot, tid, "Доступ открыт!", worker_menu_kb())
    elif p[0] == "ua":
        tid = int(p[1])
        L.upsert_user(tid, "", "admin", None)
        await edit(q, "✅ Назначен админом.")
        await send(ctx.bot, tid, "Вам выданы права админа.", main_kb())
    elif p[0] == "ux":
        await edit(q, "❌ Доступ отклонен.")

async def on_private_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    msg = update.message
    if not is_staff(u.id):
        return
    text = (msg.text or "").strip()
    app = ctx.application

    pq = app.bot_data.get("pq", {})
    if msg.reply_to_message and msg.reply_to_message.message_id in pq:
        key = msg.reply_to_message.message_id
    elif len(pq) == 1:
        key = next(iter(pq))
    else:
        key = None

    if key is not None:
        try:
            v = float(text.replace(",", "."))
        except ValueError:
            await msg.reply_text("Нужно число.")
            return
        rid, amount = pq.pop(key)
        L.add_price(amount, amount, v)
        await msg.reply_text(f"✅ Запомнил: {amount:,} = {L.money(v)}$")
        for r in L.pending_reports():
            await advance(app, r)
        return

    st = ctx.user_data.get("st")
    if not st:
        await msg.reply_text("Главное меню:", reply_markup=main_kb())
        return

    act = st[0]
    if act == "newname":
        if not text:
            await msg.reply_text("Имя не может быть пустым.")
            return
        ctx.user_data["st"] = ("newpct", text)
        await msg.reply_text(f"Какой процент у {text}? Напишите число (например 30):")
    elif act == "newpct":
        try:
            v = float(text)
        except ValueError:
            await msg.reply_text("Введите число от 0 до 100:")
            return
        wid = L.add_worker(st[1], v)
        ctx.user_data.pop("st")
        w = L.worker(wid)
        await msg.reply_text(f"✅ Добавлен abrabotchik: {st[1]} {L.money(v)}%", reply_markup=card_kb(w))
    elif act == "pct":
        try:
            v = float(text)
        except ValueError:
            await msg.reply_text("Введите число:")
            return
        ctx.user_data.pop("st")
        wid = st[1]
        L.set_percent(wid, v, L.iso(L.today()))
        w = L.worker(wid)
        await msg.reply_text(f"✅ Процент обновлен на {L.money(v)}%", reply_markup=card_kb(w))
    elif act == "adv":
        try:
            v = float(text)
        except ValueError:
            await msg.reply_text("Введите сумму числом:")
            return
        ctx.user_data.pop("st")
        wid = st[1]
        L.add_advance(wid, v, L.iso(L.today()), "Аванс")
        w = L.worker(wid)
        await msg.reply_text(f"✅ Выдан аванс: {L.money(v)}$", reply_markup=card_kb(w))
    elif act == "fine":
        try:
            v = float(text)
        except ValueError:
            await msg.reply_text("Введите число:")
            return
        ctx.user_data.pop("st")
        wid = st[1]
        aid = L.add_adj(wid, "fine", v, L.iso(L.today()), "вручную")
        await msg.reply_text(f"✅ Штраф добавлен: {L.money(v)}$")
    elif act == "replace":
        old = L.worker(st[1])
        ctx.user_data.pop("st")
        pct = L.percent_on(old["id"], L.today())
        wid = L.add_worker(text, pct)
        L.set_active(old["id"], 0)
        w = L.worker(wid)
        await msg.reply_text(f"✅ {text} заменяет {old['name']}", reply_markup=card_kb(w))
    elif act == "priceadd":
        nums = [float(t.replace(",", ".")) for t in text.split()]
        ctx.user_data.pop("st")
        if len(nums) == 2:
            L.add_price(int(nums[0]), int(nums[0]), nums[1])
        elif len(nums) == 3:
            L.add_price(int(nums[0]), int(nums[1]), nums[2])
        await msg.reply_text("✅ Добавлено в прайс.", reply_markup=main_kb())

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
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_private_text))
    app.add_handler(MessageHandler((filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP) & (filters.TEXT | filters.CAPTION) & ~filters.COMMAND, on_chat_text))
    
    hh, mm = (int(v) for v in REPORT_TIME.split(":"))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
