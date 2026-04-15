import os
import re
import json
import asyncio
import threading
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
KITCHEN_CHAT_ID = int(os.environ["KITCHEN_CHAT_ID"])
ADMIN_ID = int(os.environ["ADMIN_ID"])
DIRECTION_CHAT_ID = int(os.environ["DIRECTION_CHAT_ID"])
STATS_FILE = "stats.json"
REMINDER_MINUTES = 4
PORT = int(os.environ.get("PORT", 10000))

# ─── HELPER ASYNC POUR HTTP SERVER ──────────
async def send_to_direction(text):
    from telegram import Bot
    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(chat_id=DIRECTION_CHAT_ID, text=text, parse_mode="Markdown")

# ─── MINI HTTP SERVER ────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
    def do_GET(self):
        if self.path == "/daily":
            day_key = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            text = build_daily_report(day_key)
            asyncio.run(send_to_direction(text))
        elif self.path == "/monthly":
            text = build_monthly_report()
            asyncio.run(send_to_direction(text))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

def run_http_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()

# ─── STATS ───────────────────────────────────
def load_stats():
    if not os.path.exists(STATS_FILE):
        return {}
    with open(STATS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_stats(stats):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

def record_order(items):
    stats = load_stats()
    now = datetime.now()
    for key in [now.strftime("%Y-%m"), now.strftime("%Y-%m-%d")]:
        if key not in stats:
            stats[key] = {}
        for item_id in items:
            stats[key][item_id] = stats[key].get(item_id, 0) + 1
        if "__orders__" not in stats:
            stats["__orders__"] = {}
        if key not in stats["__orders__"]:
            stats["__orders__"][key] = 0
        stats["__orders__"][key] += 1
    save_stats(stats)

# ─── MENU & TABLES ───────────────────────────
def load_menu():
    with open("menu.json", "r", encoding="utf-8") as f:
        return json.load(f)

def load_tables():
    with open("tables.json", "r", encoding="utf-8") as f:
        return json.load(f)

def get_item_name(item_id):
    for item in load_menu()["items"]:
        if item["id"] == item_id:
            return item["name"]
    return item_id

def parse_items_from_message(text):
    """Extrait les item_ids depuis le texte du ticket cuisine (fallback si mémoire vide)"""
    menu = load_menu()
    name_to_id = {item["name"]: item["id"] for item in menu["items"]}
    result = []
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("•"):
            continue
        line = line[1:].strip()
        # Extraire la quantité
        qty = 1
        qty_match = re.search(r"[x×](\d+)$", line)
        if qty_match:
            qty = int(qty_match.group(1))
            line = line[:qty_match.start()].strip()
        # Chercher dans le menu
        for name, item_id in name_to_id.items():
            if name in line or line in name:
                for _ in range(qty):
                    result.append(item_id)
                break
    return result

# ─── RAPPORTS ────────────────────────────────
UA_MONTHS = {1:"Січень",2:"Лютий",3:"Березень",4:"Квітень",5:"Травень",6:"Червень",7:"Липень",8:"Серпень",9:"Вересень",10:"Жовтень",11:"Листопад",12:"Грудень"}
UA_MONTHS_GEN = {1:"Січня",2:"Лютого",3:"Березня",4:"Квітня",5:"Травня",6:"Червня",7:"Липня",8:"Серпня",9:"Вересня",10:"Жовтня",11:"Листопада",12:"Грудня"}

def build_period_report(key, title):
    stats = load_stats()
    data = stats.get(key, {})
    orders_count = stats.get("__orders__", {}).get(key, 0)
    if not data:
        return title + "\n\n_(немає даних)_"
    sorted_items = sorted(data.items(), key=lambda x: x[1], reverse=True)
    total = sum(data.values())
    lines = [title + "\n", "Замовлень: *" + str(orders_count) + "*", "Страв подано: *" + str(total) + "*\n"]
    for item_id, count in sorted_items:
        lines.append("• " + get_item_name(item_id) + " — " + str(count))
    return "\n".join(lines)

def build_daily_report(day_key=None):
    if not day_key:
        day_key = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    d = datetime.strptime(day_key, "%Y-%m-%d")
    title = "📋 Підсумок дня — " + str(d.day) + " " + UA_MONTHS_GEN.get(d.month, "") + " " + str(d.year)
    return build_period_report(day_key, title)

def build_monthly_report(month_key=None):
    if not month_key:
        now = datetime.now()
        month_key = str(now.year - 1) + "-12" if now.month == 1 else str(now.year) + "-" + str(now.month - 1).zfill(2)
    year, month = map(int, month_key.split("-"))
    title = "📊 Підсумок місяця — " + UA_MONTHS.get(month, "") + " " + str(year)
    return build_period_report(month_key, title)

# ─── ÉTAT ────────────────────────────────────
orders = {}
order_counter = {"n": 0}
order_waiter = {}
order_items = {}
partial_sel = {}

def next_order_id():
    order_counter["n"] += 1
    return order_counter["n"]

# ─── REMINDER ────────────────────────────────
async def send_reminder(order_id, waiter_id, table_label, bot):
    await asyncio.sleep(REMINDER_MINUTES * 60)
    if order_id in order_waiter:
        try:
            await bot.send_message(chat_id=waiter_id, text="⏳ Замовлення #" + str(order_id) + " — " + table_label + "\nКухня ще не підтвердила. Перевір!")
            await bot.send_message(chat_id=KITCHEN_CHAT_ID, text="⚠️ Замовлення #" + str(order_id) + " — " + table_label + "\nЧекає вже " + str(REMINDER_MINUTES) + " хвилини!")
        except Exception:
            pass

# ─── HELPERS ─────────────────────────────────
def format_cart(table_label, items):
    if not items:
        return "🪑 " + table_label + "\n\n_(кошик порожній)_"
    counts = {}
    for i in items:
        counts[i] = counts.get(i, 0) + 1
    lines = ["🪑 *" + table_label + "*\n"]
    for item_id, count in counts.items():
        name = get_item_name(item_id)
        lines.append("• " + name + " x" + str(count) if count > 1 else "• " + name)
    return "\n".join(lines)

def format_order_for_kitchen(order_id, table_label, items, waiter_name):
    counts = {}
    for i in items:
        counts[i] = counts.get(i, 0) + 1
    now = datetime.now().strftime("%H:%M")
    lines = ["🍷 *Замовлення #" + str(order_id) + "* — " + now, "🪑 " + table_label, "👤 " + waiter_name, ""]
    for item_id, count in counts.items():
        name = get_item_name(item_id)
        lines.append("• " + name + " x" + str(count) if count > 1 else "• " + name)
    return "\n".join(lines)

def format_partial_notif(order_id_str, ready_items, pending_items, now):
    lines = ["🍽 Замовлення #" + order_id_str + " — частково готово (" + now + ")", ""]
    lines.append("Готово до подачі:")
    counts_r = {}
    for i in ready_items:
        counts_r[i] = counts_r.get(i, 0) + 1
    for item_id, count in counts_r.items():
        name = get_item_name(item_id)
        lines.append("• " + name + " x" + str(count) if count > 1 else "• " + name)
    if pending_items:
        lines.append("")
        lines.append("Ще готується:")
        counts_p = {}
        for i in pending_items:
            counts_p[i] = counts_p.get(i, 0) + 1
        for item_id, count in counts_p.items():
            name = get_item_name(item_id)
            lines.append("• " + name + " x" + str(count) if count > 1 else "• " + name)
    return "\n".join(lines)

# ─── KEYBOARDS ───────────────────────────────
def build_zones_keyboard():
    rows = [[InlineKeyboardButton(z["name"], callback_data="ZONE_" + z["id"])] for z in load_tables()["zones"]]
    rows.append([InlineKeyboardButton("Скасувати", callback_data="CANCEL")])
    return InlineKeyboardMarkup(rows)

def build_tables_keyboard(zone_id):
    zone = next((z for z in load_tables()["zones"] if z["id"] == zone_id), None)
    if not zone:
        return build_zones_keyboard()
    rows = []
    row = []
    for t in zone["tables"]:
        row.append(InlineKeyboardButton(str(t), callback_data="TABLE_" + zone_id + "_" + str(t)))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("← Назад", callback_data="BACK_ZONES")])
    return InlineKeyboardMarkup(rows)

def build_menu_keyboard(page=0):
    items = load_menu()["items"]
    page_size = 8
    start = page * page_size
    total_pages = (len(items) + page_size - 1) // page_size
    rows = [[InlineKeyboardButton(item["name"], callback_data="ADD_" + item["id"])] for item in items[start:start+page_size]]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data="PAGE_" + str(page-1)))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data="PAGE_" + str(page+1)))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("Переглянути кошик", callback_data="SHOW_CART")])
    rows.append([InlineKeyboardButton("Скасувати", callback_data="CANCEL")])
    return InlineKeyboardMarkup(rows)

def build_cart_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Додати ще", callback_data="PAGE_0")],
        [InlineKeyboardButton("Прибрати страву", callback_data="REMOVE_LIST")],
        [InlineKeyboardButton("✓ Відправити замовлення", callback_data="SEND")],
        [InlineKeyboardButton("Скасувати", callback_data="CANCEL")],
    ])

def build_remove_keyboard(items_in_cart):
    item_lookup = {item["id"]: item["name"] for item in load_menu()["items"]}
    rows = []
    seen = []
    for item_id in items_in_cart:
        if item_id not in seen:
            seen.append(item_id)
            count = items_in_cart.count(item_id)
            label = item_lookup.get(item_id, item_id)
            if count > 1:
                label += " x" + str(count)
            rows.append([InlineKeyboardButton("− " + label, callback_data="REMOVE_" + item_id)])
    rows.append([InlineKeyboardButton("← Назад", callback_data="SHOW_CART")])
    return InlineKeyboardMarkup(rows)

def build_kitchen_keyboard(order_id):
    oid = str(order_id)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Готується", callback_data="STATUS_COOKING_" + oid),
            InlineKeyboardButton("Готово", callback_data="STATUS_READY_" + oid),
        ],
        [InlineKeyboardButton("🍽 Частково готово", callback_data="STATUS_PARTIAL_" + oid)],
    ])

def build_after_cooking_keyboard(order_id):
    oid = str(order_id)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Готово", callback_data="STATUS_READY_" + oid)],
        [InlineKeyboardButton("🍽 Частково готово", callback_data="STATUS_PARTIAL_" + oid)],
    ])

def build_partial_keyboard(order_id_str, items, selected):
    counts = {}
    for i in items:
        counts[i] = counts.get(i, 0) + 1
    rows = []
    seen = []
    for item_id in items:
        if item_id not in seen:
            seen.append(item_id)
            name = get_item_name(item_id)
            count = counts[item_id]
            check = "✅" if item_id in selected else "⬜"
            label = check + " " + name
            if count > 1:
                label += " x" + str(count)
            rows.append([InlineKeyboardButton(label, callback_data="PTOGGLE_" + order_id_str + "_" + item_id)])
    rows.append([
        InlineKeyboardButton("✓ Підтвердити", callback_data="PCONFIRM_" + order_id_str),
        InlineKeyboardButton("✕ Скасувати", callback_data="PCANCEL_" + order_id_str),
    ])
    return InlineKeyboardMarkup(rows)

# ─── HANDLERS ────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🍷 Ласкаво просимо до bandôl!\n\nНатисни /new щоб створити замовлення.")

async def new_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    orders[user_id] = {"table": None, "table_label": None, "items": [], "page": 0}
    await update.message.reply_text("Вибери зону:", reply_markup=build_zones_keyboard())

async def rapport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔️ Команда недоступна.")
        return
    text = build_monthly_report()
    await context.bot.send_message(chat_id=DIRECTION_CHAT_ID, text=text, parse_mode="Markdown")
    await update.message.reply_text("✅ Звіт відправлено.")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    waiter_name = query.from_user.first_name or "Офіціант"
    await query.answer()

    if data == "CANCEL":
        orders.pop(user_id, None)
        await query.edit_message_text("Замовлення скасовано.")
        return

    if data == "BACK_ZONES":
        if user_id not in orders:
            orders[user_id] = {"table": None, "table_label": None, "items": [], "page": 0}
        await query.edit_message_text("Вибери зону:", reply_markup=build_zones_keyboard())
        return

    if data.startswith("ZONE_"):
        if user_id not in orders:
            orders[user_id] = {"table": None, "table_label": None, "items": [], "page": 0}
        zone_id = data[5:]
        zone = next((z for z in load_tables()["zones"] if z["id"] == zone_id), None)
        orders[user_id]["zone_id"] = zone_id
        await query.edit_message_text(zone["name"] + " — вибери стіл:", reply_markup=build_tables_keyboard(zone_id))
        return

    if data.startswith("TABLE_"):
        if user_id not in orders:
            orders[user_id] = {"table": None, "table_label": None, "items": [], "page": 0}
        parts = data.split("_")
        zone_id, table_num = parts[1], parts[2]
        zone = next((z for z in load_tables()["zones"] if z["id"] == zone_id), None)
        table_label = zone["name"] + ", стіл " + table_num
        orders[user_id].update({"table": table_num, "table_label": table_label})
        await query.edit_message_text("🪑 *" + table_label + "*\n\nВибери страви:", reply_markup=build_menu_keyboard(0), parse_mode="Markdown")
        return

    if data.startswith("PAGE_"):
        if user_id not in orders:
            await query.edit_message_text("Сесія закінчилась. Натисни /new")
            return
        page = int(data[5:])
        orders[user_id]["page"] = page
        order = orders[user_id]
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        await query.edit_message_text(cart_text + "\n\nВибери страву:", reply_markup=build_menu_keyboard(page), parse_mode="Markdown")
        return

    if data.startswith("ADD_"):
        if user_id not in orders:
            await query.edit_message_text("Сесія закінчилась. Натисни /new")
            return
        item_id = data[4:]
        orders[user_id]["items"].append(item_id)
        order = orders[user_id]
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        await query.edit_message_text("✓ Додано\n\n" + cart_text + "\n\nДодай ще або переглянь кошик:", reply_markup=build_menu_keyboard(order.get("page", 0)), parse_mode="Markdown")
        return

    if data == "SHOW_CART":
        if user_id not in orders:
            await query.edit_message_text("Сесія закінчилась. Натисни /new")
            return
        order = orders[user_id]
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        await query.edit_message_text(cart_text + "\n\nПеревір замовлення:", reply_markup=build_cart_keyboard(), parse_mode="Markdown")
        return

    if data == "REMOVE_LIST":
        if user_id not in orders:
            await query.edit_message_text("Сесія закінчилась. Натисни /new")
            return
        order = orders[user_id]
        if not order["items"]:
            await query.answer("Кошик порожній", show_alert=True)
            return
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        await query.edit_message_text(cart_text + "\n\nЩо прибрати?", reply_markup=build_remove_keyboard(order["items"]), parse_mode="Markdown")
        return

    if data.startswith("REMOVE_"):
        if user_id not in orders:
            await query.edit_message_text("Сесія закінчилась. Натисни /new")
            return
        item_id = data[7:]
        order = orders[user_id]
        if item_id in order["items"]:
            order["items"].remove(item_id)
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        if not order["items"]:
            await query.edit_message_text("Кошик порожній. Вибери страви:", reply_markup=build_menu_keyboard(0), parse_mode="Markdown")
        else:
            await query.edit_message_text(cart_text + "\n\nЩе прибрати?", reply_markup=build_remove_keyboard(order["items"]), parse_mode="Markdown")
        return

    if data == "SEND":
        if user_id not in orders:
            await query.edit_message_text("Сесія закінчилась. Натисни /new")
            return
        order = orders[user_id]
        table_label = order.get("table_label", "?")
        items = order["items"]
        if not items:
            await query.answer("Кошик порожній! Додай страви.", show_alert=True)
            return
        order_id = next_order_id()
        kitchen_text = format_order_for_kitchen(order_id, table_label, items, waiter_name)
        record_order(items)
        order_waiter[order_id] = user_id
        order_items[order_id] = list(items)
        await context.bot.send_message(chat_id=KITCHEN_CHAT_ID, text=kitchen_text, reply_markup=build_kitchen_keyboard(order_id), parse_mode="Markdown")
        await query.edit_message_text("Замовлення #" + str(order_id) + " відправлено.\n\n" + format_cart(table_label, items), parse_mode="Markdown")
        orders.pop(user_id, None)
        asyncio.create_task(send_reminder(order_id, user_id, table_label, context.bot))
        return

    # ── PARTIAL: показати список страв ──
    if data.startswith("STATUS_PARTIAL_"):
        order_id_str = data[15:]
        try:
            oid = int(order_id_str)
        except ValueError:
            oid = None
        # Mémoire d'abord, sinon extraire du message
        items = order_items.get(oid, [])
        if not items and query.message and query.message.text:
            items = parse_items_from_message(query.message.text)
            if oid and items:
                order_items[oid] = items
        if not items:
            await query.answer("Немає даних про страви", show_alert=True)
            return
        partial_sel[order_id_str] = set()
        await query.edit_message_reply_markup(reply_markup=build_partial_keyboard(order_id_str, items, set()))
        return

    # ── PARTIAL: toggle ──
    if data.startswith("PTOGGLE_"):
        rest = data[8:]
        idx = rest.index("_")
        order_id_str = rest[:idx]
        item_id = rest[idx+1:]
        if order_id_str not in partial_sel:
            partial_sel[order_id_str] = set()
        if item_id in partial_sel[order_id_str]:
            partial_sel[order_id_str].discard(item_id)
        else:
            partial_sel[order_id_str].add(item_id)
        try:
            oid = int(order_id_str)
        except ValueError:
            oid = None
        items = order_items.get(oid, [])
        if not items and query.message and query.message.text:
            items = parse_items_from_message(query.message.text)
        await query.edit_message_reply_markup(reply_markup=build_partial_keyboard(order_id_str, items, partial_sel[order_id_str]))
        return

    # ── PARTIAL: annuler ──
    if data.startswith("PCANCEL_"):
        order_id_str = data[8:]
        partial_sel.pop(order_id_str, None)
        try:
            oid = int(order_id_str)
        except ValueError:
            oid = None
        # Restaurer le bon keyboard selon si cooking a été cliqué
        msg_text = query.message.text or ""
        if "Готується" in msg_text:
            kb = build_after_cooking_keyboard(oid or order_id_str)
        else:
            kb = build_kitchen_keyboard(oid or order_id_str)
        await query.edit_message_reply_markup(reply_markup=kb)
        return

    # ── PARTIAL: confirmer ──
    if data.startswith("PCONFIRM_"):
        order_id_str = data[9:]
        selected = partial_sel.get(order_id_str, set())
        partial_sel.pop(order_id_str, None)
        if not selected:
            await query.answer("Вибери хоча б одну страву", show_alert=True)
            return
        try:
            order_id_int = int(order_id_str)
        except ValueError:
            order_id_int = None
        items = order_items.get(order_id_int, [])
        if not items and query.message and query.message.text:
            items = parse_items_from_message(query.message.text)
        ready_items = [i for i in items if i in selected]
        pending_items = [i for i in items if i not in selected]
        now = datetime.now().strftime("%H:%M")
        notif_text = format_partial_notif(order_id_str, ready_items, pending_items, now)
        if order_id_int and order_id_int in order_waiter:
            try:
                await context.bot.send_message(chat_id=order_waiter[order_id_int], text=notif_text)
            except Exception:
                pass
        updated_text = query.message.text + "\n\n🍽 Частково готово (" + now + ")"
        msg_text = query.message.text or ""
        if "Готується" in msg_text:
            kb = build_after_cooking_keyboard(order_id_int or order_id_str)
        else:
            kb = build_kitchen_keyboard(order_id_int or order_id_str)
        await query.edit_message_text(updated_text, reply_markup=kb, parse_mode="Markdown")
        return

    # ── STATUS COOKING / READY ──
    if data.startswith("STATUS_"):
        parts = data.split("_")
        status = parts[1]
        order_id_str = parts[2] if len(parts) > 2 else "?"
        now = datetime.now().strftime("%H:%M")
        try:
            order_id_int = int(order_id_str)
        except ValueError:
            order_id_int = None

        if status == "COOKING":
            new_text = query.message.text + "\n\n— Готується (" + now + ")"
            await query.edit_message_text(new_text, reply_markup=build_after_cooking_keyboard(order_id_int or order_id_str), parse_mode="Markdown")
            if order_id_int and order_id_int in order_waiter:
                try:
                    await context.bot.send_message(chat_id=order_waiter[order_id_int], text="🍷 Замовлення #" + order_id_str + " — кухня прийняла, готується. (" + now + ")")
                except Exception:
                    pass

        elif status == "READY":
            new_text = query.message.text + "\n\n— Готово (" + now + ")"
            await query.edit_message_text(new_text, reply_markup=InlineKeyboardMarkup([]), parse_mode="Markdown")
            if order_id_int and order_id_int in order_waiter:
                waiter_id = order_waiter[order_id_int]
                try:
                    await context.bot.send_message(chat_id=waiter_id, text="ЗАМОВЛЕННЯ #" + order_id_str + " ГОТОВЕ\n\nМОЖНА ПОДАВАТИ (" + now + ")")
                    await context.bot.send_sticker(chat_id=waiter_id, sticker="CAACAgIAAxkBAAIBmGYV1c2v8gHSsV0Hx3hLSAAB0Ew2AAJoAQACIjaOC0rBHMJj3Ro1HgQ")
                except Exception:
                    pass
                order_waiter.pop(order_id_int, None)
                order_items.pop(order_id_int, None)
        return

# ─── MAIN ────────────────────────────────────
def main():
    t = threading.Thread(target=run_http_server, daemon=True)
    t.start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_order))
    app.add_handler(CommandHandler("rapport", rapport))
    app.add_handler(CallbackQueryHandler(button))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
