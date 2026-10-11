# -*- coding: utf-8 -*-
import os
import logging
import datetime as dt
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup as K
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler, MessageHandler,
                          ChatMemberHandler, ContextTypes, filters)
import logic as L

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kassabot")

TOKEN = os.environ["BOT_TOKEN"]
OWNER = int(os.getenv("OWNER_ID", "8785896869"))


def B(text, data):
    return InlineKeyboardButton(text, callback_data=data)


def is_staff(uid):
    return uid is not None and (uid == OWNER or uid in L.admins())


def staff_ids():
    return [OWNER] + [a for a in L.admins() if a != OWNER]


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


def main_kb():
    return K([
        [B("📊 Отчёты", "rs:menu"), B("🏆 Рейтинг", "rt:menu")],
        [B("👥 Обработчики", "w:list"), B("💲 Прайс", "pr:menu")],
        [B("💬 Чаты", "ch:menu"), B("❓ Вопросы", "q:list")]
    ])


def worker_menu_kb():
    return K([
        [B("📥 Мой отчёт (период)", "me:menu")],
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
    if r is None:
        return
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
    r = L.report(rid)
    if r is None:
        return
    if need is None:
        await finalize(app, rid)
        return
    kind, arg = need
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
    if row is None:
        L.upsert_chat(chat.id, chat.title or str(chat.id))
        L.set_watch(chat.id, 1)
    elif not row["watch"]:
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
            aid = L.add_adj(it["worker_id"], it["kind"], it["usd"], L.iso(day), text,
                            chat.id, msg.message_id, pending)
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
        sw = L.self_worker()
        wid = sw["id"] if sw else None
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
    ctx.user_data.pop("st", None)
    if is_staff(u.id):
        await update.message.reply_text("Главное меню:", reply_markup=main_kb())
        return

    row = L.user(u.id)
    if row and row["worker_id"]:
        await update.message.reply_text("Ваша панель:", reply_markup=worker_menu_kb())
        return

    rows = [[B(w["name"], f"ul:{u.id}:{w['id']}")] for w in L.workers()]
    rows.append([B("👑 Назначить админом", f"ua:{u.id}"), B("❌ Отказать", f"ux:{u.id}")])
    uname = f"@{u.username}" if u.username else "без username"
    await send(ctx.bot, OWNER, f"👤 Запрос доступа: {u.full_name} ({uname})", K(rows))
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
        [B("📈 Этот месяц", f"rep:tm:{wid}"), B("📉 Прошлый месяц", f"rep:lm:{wid}")],
        [B("🗓 Эта неделя", f"rep:tw:{wid}"), B("🗓 Прошлая неделя", f"rep:lw:{wid}")],
        [B("💵 Изменить %", f"wp:{wid}"), B("⚠️ Штраф", f"wf:{wid}")],
        [B("💸 Выдать аванс", f"adv:{wid}"), B("🔄 Замена", f"wrep:{wid}")],
        [B("🔙 Обработчики", "w:list")]
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
    app = ctx.application

    if p[0] == "me":
        u = L.user(uid)
        if not u or not u["worker_id"]:
            await q.answer("У вас нет привязанного обработчика.", show_alert=True)
            return
        await q.answer()
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

    await q.answer()
    if not is_staff(uid):
        return

    if d == "m:main":
        ctx.user_data.pop("st", None)
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
        rows = [[B(f"{w['name']} ({L.money(L.percent_on(w['id'], L.today()))}%)", f"w:{w['id']}")]
                for w in L.workers()]
        rows.append([B("+ Новый обработчик", "w:new")])
        rows.append(menu_btn())
        await edit(q, "Обработчики:", K(rows))
    elif d == "w:new":
        ctx.user_data["st"] = ("newname",)
        await edit(q, "Напишите имя нового обработчика:", K([menu_btn()]))
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
        await edit(q, f"Кто заменяет {w['name']}? Напишите имя нового обработчика:", K([menu_btn()]))
    elif d == "pr:menu":
        await show_prices(q)
    elif p[0] == "prd":
        L.del_price(int(p[1]))
        await show_prices(q)
    elif d == "pra":
        ctx.user_data["st"] = ("priceadd",)
        await edit(q, "Напишите: «сумма цена» (например 5200 20) или «от до цена»", K([menu_btn()]))
    elif d == "ch:menu":
        rows = [[B(("✅ " if c["watch"] else "❌ ") + (c["title"] or str(c["chat_id"])),
                   f"cw:{c['chat_id']}:{0 if c['watch'] else 1}")] for c in L.q("select * from chats")]
        rows.append(menu_btn())
        await edit(q, "Чаты и каналы (нажмите для включения/выключения):", K(rows))
    elif p[0] == "cw":
        L.set_watch(int(p[1]), int(p[2]))
        await edit(q, "Статус слежения обновлён.", K([menu_btn()]))
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
        L.x("update reports set worker_id=? where id=?", (wid, rid))
        await edit(q, f"✅ Обработчик назначен: {L.worker(wid)['name']}")
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
    elif p[0] == "ul":
        tid, wid = int(p[1]), int(p[2])
        L.upsert_user(tid, "", "worker", wid)
        await edit(q, f"✅ Пользователь подключен как {L.worker(wid)['name']}")
        await send(ctx.bot, tid, "Доступ открыт! Нажмите /start", worker_menu_kb())
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
    if not msg or not is_staff(u.id):
        return
    text = (msg.text or "").strip()
    app = ctx.application
    st = ctx.user_data.get("st")

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
        # Отчёт, отправленный боту в личку
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
        checks = L.parse_report(text, day)
        if checks:
            wid = None
            if u.id == OWNER:
                sw = L.self_worker()
                wid = sw["id"] if sw else None
            if wid is None:
                wid = L.worker_by_name(text)
            await msg.reply_text("✅ Отчёт принят, считаю...")
            rid = L.create_report(wid, u.id, msg.message_id, u.id, text, checks)
            await advance(app, rid)
            return
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
            v = float(text.replace(",", "."))
        except ValueError:
            await msg.reply_text("Введите число от 0 до 100:")
            return
        try:
            wid = L.add_worker(st[1], v)
        except Exception:
            ctx.user_data.pop("st", None)
            await msg.reply_text("Такое имя уже есть.", reply_markup=main_kb())
            return
        ctx.user_data.pop("st", None)
        w = L.worker(wid)
        await msg.reply_text(f"✅ Добавлен обработчик: {st[1]} {L.money(v)}%", reply_markup=card_kb(w))
    elif act == "pct":
        try:
            v = float(text.replace(",", "."))
        except ValueError:
            await msg.reply_text("Введите число:")
            return
        ctx.user_data.pop("st", None)
        wid = st[1]
        L.set_percent(wid, v, L.iso(L.today()))
        w = L.worker(wid)
        await msg.reply_text(f"✅ Процент обновлен на {L.money(v)}%", reply_markup=card_kb(w))
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
    elif act == "fine":
        try:
            v = float(text.replace(",", "."))
        except ValueError:
            await msg.reply_text("Введите число:")
            return
        ctx.user_data.pop("st", None)
        wid = st[1]
        L.add_adj(wid, "fine", v, L.iso(L.today()), "вручную")
        w = L.worker(wid)
        await msg.reply_text(f"✅ Штраф добавлен: {L.money(v)}$", reply_markup=card_kb(w))
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
            await msg.reply_text("Такое имя уже есть.", reply_markup=main_kb())
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
        await msg.reply_text("✅ Добавлено в прайс.", reply_markup=main_kb())


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
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
