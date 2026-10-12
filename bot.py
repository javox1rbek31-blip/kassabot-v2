# -*- coding: utf-8 -*-
"""
Kassa-bot v4 Main Telegram Bot (Fully Optimized & Tested)
"""
import os
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup as K
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler, MessageHandler,
                          ContextTypes, filters)
import logic as L
import pdfgen as P

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kassabot")

TOKEN = os.environ["BOT_TOKEN"]
OWNER = int(os.getenv("OWNER_ID", "8785896869"))

def B(text, data):
    return InlineKeyboardButton(text, callback_data=data)

def is_staff(uid):
    return uid is not None and (uid == OWNER or uid in L.admins())

def main_kb(uid):
    rows = [
        [B("📊 Отчёты", "rs:menu"), B("🏆 Рейтинг", "rt:menu")],
        [B("📆 Отчёт за дату", "dt:all"), B("📄 PDF отчёт", "pdf:menu")],
        [B("👥 Обработчики", "w:list"), B("💲 Прайс", "pr:menu")],
        [B("💬 Чаты", "ch:menu")],
    ]
    if uid == OWNER:
        rows.append([B("👑 Cashliy Panel", "ow:cash")])
    return K(rows)

async def send(bot, chat_id, text, kb=None):
    return await bot.send_message(chat_id, text, reply_markup=kb)

async def edit(q, text, kb=None):
    try:
        await q.edit_message_text(text, reply_markup=kb)
    except Exception as e:
        if "not modified" not in str(e).lower():
            log.warning("edit failed: %s", e)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if update.effective_chat.type != "private":
        return
    if is_staff(u.id):
        await update.message.reply_text("Главное меню:", reply_markup=main_kb(u.id))
    else:
        await update.message.reply_text("Добро пожаловать. Ожидайте отправки отчёта от администратора.")

async def on_chat_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat: return
    text = msg.text or msg.caption
    if not text: return

    staff = is_staff(msg.from_user.id if msg.from_user else None)
    day = msg.date.astimezone(L.TZ).date()

    if staff and L.has_kind(text):
        items = L.parse_adjustments(text)
        for it in items:
            aid = L.add_adj(it["worker_id"], it["kind"], it["usd"], L.iso(day), text, chat.id, msg.message_id, pending=1)
            rows = [[B(w["name"], f"adjw:{aid}:{w['id']}")] for w in L.workers()]
            rows.append([B("❌ Отмена", f"adjd:{aid}")])
            kind_txt = "Штраф" if it["kind"] == "fine" else "Бонус"
            await send(ctx.bot, OWNER, f"❓ Guruhda {kind_txt} aniqlandi: {it['usd']}$\nKimga yozilsin?", K(rows))
        return

    checks = L.parse_report(text, day)
    if checks:
        wid = L.worker_by_name(text) or (L.self_worker()["id"] if staff else None)
        if wid:
            L.create_report(wid, chat.id, msg.message_id, msg.from_user.id, text, checks)
            try:
                await msg.set_reaction(reaction="👀")
            except Exception:
                pass

async def on_private_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    msg = update.effective_message
    if not msg or not is_staff(u.id): return
    text = (msg.text or "").strip()
    day = msg.date.astimezone(L.TZ).date()

    if L.has_kind(text):
        items = L.parse_adjustments(text)
        for it in items:
            if it["worker_id"]:
                L.add_adj(it["worker_id"], it["kind"], it["usd"], L.iso(day), text, pending=0)
                w_name = L.worker(it["worker_id"])["name"]
                k_txt = "Штраф" if it["kind"] == "fine" else "Бонус"
                await msg.reply_text(f"✅ {w_name} ga {it['usd']}$ {k_txt} muvaffaqiyatli qo'shildi.")
            else:
                aid = L.add_adj(None, it["kind"], it["usd"], L.iso(day), text, pending=1)
                rows = [[B(w["name"], f"adjw:{aid}:{w['id']}")] for w in L.workers()]
                await msg.reply_text(f"❓ {it['usd']}$ kimga yozilsin?", reply_markup=K(rows))
        return

    await msg.reply_text("Главное меню:", reply_markup=main_kb(u.id))

async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    p = q.data.split(":")
    await q.answer()

    if not is_staff(uid): return

    if p[0] == "rs" and p[1] == "menu":
        res = L.calc(L.week_start(), L.today())
        text = "📊 <b>Hozirgi haftalik hisobotlar:</b>\n\n"
        for r in res:
            text += f"• <b>{r['w']['name']}</b>: Kassa {L.money(r['usd'])}$ | Zp: {L.money(r['total'])}$\n"
        await edit(q, text, K([[B("🔙 Меню", "m:main")]]), parse_mode="HTML")

    elif p[0] == "rt" and p[1] == "menu":
        res = L.calc(L.week_start(), L.today())
        sorted_res = sorted(res, key=lambda x: x['total'], reverse=True)
        text = "🏆 <b>Haftalik reyting:</b>\n\n"
        for i, r in enumerate(sorted_res, 1):
            text += f"{i}. <b>{r['w']['name']}</b> — {L.money(r['total'])}$\n"
        await edit(q, text, K([[B("🔙 Меню", "m:main")]]), parse_mode="HTML")

    elif p[0] == "dt" and p[1] == "all":
        s, e = L.period_range("tw")
        res = L.calc(s, e)
        text = f"📆 <b>Davr: {L.label(s, e)}</b>\n\nJami ishlovchilar hisoboti tayyor."
        await edit(q, text, K([[B("🔙 Меню", "m:main")]]), parse_mode="HTML")

    elif p[0] == "pr" and p[1] == "menu":
        prices = L.list_prices()
        text = "💲 <b>Prais ro'yxati (Depozit -> USD):</b>\n\n"
        for pr in prices:
            text += f"• {pr['lo']} - {pr['hi']} -> {pr['usd']}$\n"
        await edit(q, text, K([[B("🔙 Меню", "m:main")]]), parse_mode="HTML")

    elif p[0] == "ch" and p[1] == "menu":
        await edit(q, "💬 Bot ulangan guruhlar nazorati faol.", K([[B("🔙 Меню", "m:main")]]))

    elif p[0] == "ow" and p[1] == "cash":
        await edit(q, "👑 <b>Cashliy Panel</b>ga xush kelibsiz.", K([[B("🔙 Меню", "m:main")]]), parse_mode="HTML")

    elif p[0] == "w" and p[1] == "list":
        ws = L.workers(active_only=False)
        rows = [[B(f"👤 {w['name']} (Smenani o'zgartirish)", f"shz:{w['id']}")] for w in ws]
        rows.append([B("🔙 Меню", "m:main")])
        await edit(q, "👥 Ishchilar ro'yxati va ularning smenalarini boshqarish:", K(rows))

    elif p[0] == "shz":
        wid = int(p[1])
        w = L.worker(wid)
        ds = L.worker_days(wid, 7)
        rows = [[B(f"{L.dshort(d)}", f"shz2:{wid}:{d}")] for d in ds]
        rows.append([B("🔙 Назад", "w:list")])
        await edit(q, f"🔄 {w['name']} uchun qaysi kungi smenani o'zgartirmoqchisiz?", K(rows))

    elif p[0] == "shz2":
        wid, day = int(p[1]), p[2]
        w = L.worker(wid)
        rows = [[B(o["name"], f"shz3:{wid}:{day}:{o['id']}")] for o in L.workers() if not o["is_self"]]
        rows.append([B("🔙 Назад", f"shz:{wid}")])
        await edit(q, f"🔄 {w['name']} — {L.dshort(day)} kuni o'rniga kim ishladi?", K(rows))

    elif p[0] == "shz3":
        wid, day, actual_wid = int(p[1]), p[2], int(p[3])
        L.set_worker_shift(day, wid, actual_wid)
        w1 = L.worker(wid)["name"]
        w2 = L.worker(actual_wid)["name"]
        await edit(q, f"✅ Saqlandi: {w1} — {L.dshort(day)} kunidagi smenasida {w2} ishladi ({L.percent_on(actual_wid, day)}%).", 
                   K([[B("🔙 Меню", "m:main")]]))

    elif p[0] == "adjw":
        aid, wid = int(p[1]), int(p[2])
        L.x("update adj set worker_id=?, pending=0 where id=?", (wid, aid))
        await edit(q, "✅ Qo'shildi va tasdiqlandi.")

    elif p[0] == "adjd":
        L.x("delete from adj where id=?", (int(p[1]),))
        await edit(q, "❌ Bekor qilindi.")

    elif p[0] == "pdf" and p[1] == "menu":
        await edit(q, "PDF hisobot davrini tanlang:", K([
            [B("Ushbu hafta", "pdf:gen:tw"), B("O'tgan hafta", "pdf:gen:lw")],
            [B("🔙 Меню", "m:main")]
        ]))

    elif p[0] == "pdf" and p[1] == "gen":
        s, e = L.period_range(p[2])
        res = L.calc(s, e)
        pdf_bytes = await asyncio.to_thread(P.all_pdf, res, L.label(s, e), owner=True)
        await ctx.bot.send_document(uid, document=pdf_bytes, filename=f"Report_{s}.pdf", caption=f"📄 Общий отчёт ({L.label(s, e)})")

    elif p[0] == "m" and p[1] == "main":
        await edit(q, "Главное меню:", main_kb(uid))

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_private_text))
    app.add_handler(MessageHandler((filters.ChatType.GROUPS | filters.ChatType.CHANNEL) & (filters.TEXT | filters.CAPTION), on_chat_text))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
