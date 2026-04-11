import os
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

# ─── MINI HTTP SERVER (pour Render) ──────────
class HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"bandol alive")

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

# ─── RAPPORTS ────────────────────────────────
UA_MONTHS = {1:"Січень",2:"Лютий",3:"Березень",4:"Квітень",5:"Травень",6:"Червень",7:"Липень",8:"Серпень",9:"Вересень",10:"Жовтень",11:"Листопад",12:"Грудень"}
UA_MONTHS_GEN = {1:"Січня",2:"Лютого",3:"Березня",4:"Квітня",5:"Травня",6:"Червня",7:"Липня",8:"Серпня",9:"Вересня",10:"Жовтня",11:"Листопада",12:"Грудня"}

def build_period_report(key, title):
    stats = load_stats()
    data = stats.get(key, {})
    orders_count = stats.get("__orders__", {}).get(key, 0)
    if not data:
        return f"{title}\n\n_(немає даних)_"
    sorted_items = sorted(data.items(), key=lambda x: x[1], reverse=True)
    total = sum(data.values())
    lines = [f"{title}\n", f"Замовлень: *{orders_count}*", f"Страв подано: *{total}*\n"]
    for item_id, count in sorted_items:
        lines.append(f"• {get_item_name(item_id)} — {count}")
    return "\n".join(lines)

def build_daily_report(day_key=None):
    if not day_key:
        day_key = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    d = datetime.strptime(day_key, "%Y-%m-%d")
    return build_period_report(day_key, f"📋 Підсумок дня — {d.day} {UA_MONTHS_GEN.get(d.month,'')} {d.year}")

def build_monthly_report(month_key=None):
    if not month_key:
        now = datetime.now()
        month_key = f"{now.year-1}-12" if now.month==1 else f"{now.year}-{now.month-1:02d}"
    year, month = map(int, month_key.split("-"))
    return build_period_report(month_key, f"📊 Підсумок місяця — {UA_MONTHS.get(month,'')} {year}")

# ─── ÉTAT ────────────────────────────────────
orders = {}
order_counter = {"n": 0}
order_waiter = {}

def next_order_id():
    order_counter["n"] += 1
    return order_counter["n"]

# ─── REMINDER ────────────────────────────────
async def send_reminder(order_id, waiter_id, table_label, bot):
    await asyncio.sleep(REMINDER_MINUTES * 60)
    if order_id in order_waiter:
        try:
            await bot.send_message(chat_id=waiter_id, text=f"⏳ Замовлення #{order_id} — {table_label}\nКухня ще не підтвердила. Перевір!")
            await bot.send_message(chat_id=KITCHEN_CHAT_ID, text=f"⚠️ Замовлення #{order_id} — {table_label}\nЧекає вже {REMINDER_MINUTES} хвилини!")
        except Exception:
            pass

# ─── HELPERS ─────────────────────────────────
def format_cart(table_label, items):
    if not items:
        return f"🪑 {table_label}\n\n_(кошик порожній)_"
    counts = {}
    for i in items:
        counts[i] = counts.get(i, 0) + 1
    lines = [f"🪑 *{table_label}*\n"]
    for item_id, count in counts.items():
        name = get_item_name(item_id)
        lines.append(f"• {name} ×{count}" if count > 1 else f"• {name}")
    return "\n".join(lines)

def format_order_for_kitchen(order_id, table_label, items, waiter_name):
    counts = {}
    for i in items:
        counts[i] = counts.get(i, 0) + 1
    now = datetime.now().strftime("%H:%M")
    lines = [f"🍷 *Замовлення #{order_id}* — {now}", f"🪑 {table_label}", f"👤 {waiter_name}", ""]
    for item_id, count in counts.items():
        name = get_item_name(item_id)
        lines.append(f"• {name} ×{count}" if count > 1 else f"• {name}")
    return "\n".join(lines)

# ─── KEYBOARDS ───────────────────────────────
def build_zones_keyboard():
    rows = [[InlineKeyboardButton(z["name"], callback_data=f"ZONE_{z['id']}")] for z in load_tables()["zones"]]
    rows.append([InlineKeyboardButton("Скасувати", callback_data="CANCEL")])
    return InlineKeyboardMarkup(rows)

def build_tables_keyboard(zone_id):
    zone = next((z for z in load_tables()["zones"] if z["id"] == zone_id), None)
    if not zone:
        return build_zones_keyboard()
    rows = []
    row = []
    for t in zone["tables"]:
        row.append(InlineKeyboardButton(str(t), callback_data=f"TABLE_{zone_id}_{t}"))
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
    rows = [[InlineKeyboardButton(item["name"], callback_data=f"ADD_{item['id']}")] for item in items[start:start+page_size]]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"PAGE_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"PAGE_{page+1}"))
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
                label += f" ×{count}"
            rows.append([InlineKeyboardButton(f"− {label}", callback_data=f"REMOVE_{item_id}")])
    rows.append([InlineKeyboardButton("← Назад", callback_data="SHOW_CART")])
    return InlineKeyboardMarkup(rows)

def build_kitchen_keyboard(order_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Готується", callback_data=f"STATUS_COOKING_{order_id}"),
        InlineKeyboardButton("Готово", callback_data=f"STATUS_READY_{order_id}"),
    ]])

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
        zone_id = data.replace("ZONE_", "")
        zone = next((z for z in load_tables()["zones"] if z["id"] == zone_id), None)
        orders[user_id]["zone_id"] = zone_id
        await query.edit_message_text(f"{zone['name']} — вибери стіл:", reply_markup=build_tables_keyboard(zone_id))
        return

    if data.startswith("TABLE_"):
        if user_id not in orders:
            orders[user_id] = {"table": None, "table_label": None, "items": [], "page": 0}
        parts = data.split("_")
        zone_id, table_num = parts[1], parts[2]
        zone = next((z for z in load_tables()["zones"] if z["id"] == zone_id), None)
        table_label = f"{zone['name']}, стіл {table_num}"
        orders[user_id].update({"table": table_num, "table_label": table_label})
        await query.edit_message_text(f"🪑 *{table_label}*\n\nВибери страви:", reply_markup=build_menu_keyboard(0), parse_mode="Markdown")
        return

    if data.startswith("PAGE_"):
        if user_id not in orders:
            await query.edit_message_text("Сесія закінчилась. Натисни /new")
            return
        page = int(data.replace("PAGE_", ""))
        orders[user_id]["page"] = page
        order = orders[user_id]
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        await query.edit_message_text(f"{cart_text}\n\nВибери страву:", reply_markup=build_menu_keyboard(page), parse_mode="Markdown")
        return

    if data.startswith("ADD_"):
        if user_id not in orders:
            await query.edit_message_text("Сесія закінчилась. Натисни /new")
            return
        item_id = data.replace("ADD_", "")
        orders[user_id]["items"].append(item_id)
        order = orders[user_id]
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        await query.edit_message_text(f"✓ Додано\n\n{cart_text}\n\nДодай ще або переглянь кошик:", reply_markup=build_menu_keyboard(order.get("page", 0)), parse_mode="Markdown")
        return

    if data == "SHOW_CART":
        if user_id not in orders:
            await query.edit_message_text("Сесія закінчилась. Натисни /new")
            return
        order = orders[user_id]
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        await query.edit_message_text(f"{cart_text}\n\nПеревір замовлення:", reply_markup=build_cart_keyboard(), parse_mode="Markdown")
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
        await query.edit_message_text(f"{cart_text}\n\nЩо прибрати?", reply_markup=build_remove_keyboard(order["items"]), parse_mode="Markdown")
        return

    if data.startswith("REMOVE_"):
        if user_id not in orders:
            await query.edit_message_text("Сесія закінчилась. Натисни /new")
            return
        item_id = data.replace("REMOVE_", "")
        order = orders[user_id]
        if item_id in order["items"]:
            order["items"].remove(item_id)
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        if not order["items"]:
            await query.edit_message_text("Кошик порожній. Вибери страви:", reply_markup=build_menu_keyboard(0), parse_mode="Markdown")
        else:
            await query.edit_message_text(f"{cart_text}\n\nЩе прибрати?", reply_markup=build_remove_keyboard(order["items"]), parse_mode="Markdown")
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
        await context.bot.send_message(chat_id=KITCHEN_CHAT_ID, text=kitchen_text, reply_markup=build_kitchen_keyboard(order_id), parse_mode="Markdown")
        await query.edit_message_text(f"Замовлення #{order_id} відправлено.\n\n{format_cart(table_label, items)}", parse_mode="Markdown")
        orders.pop(user_id, None)
        asyncio.create_task(send_reminder(order_id, user_id, table_label, context.bot))
        return

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
            new_text = query.message.text + f"\n\n— Готується ({now})"
            await query.edit_message_text(new_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Готово", callback_data=f"STATUS_READY_{order_id_str}")]]), parse_mode="Markdown")
            if order_id_int and order_id_int in order_waiter:
                try:
                    await context.bot.send_message(chat_id=order_waiter[order_id_int], text=f"🍷 Замовлення #{order_id_str} — кухня прийняла, готується. ({now})")
                except Exception:
                    pass

        elif status == "READY":
            new_text = query.message.text + f"\n\n— Готово ({now})"
            await query.edit_message_text(new_text, reply_markup=InlineKeyboardMarkup([]), parse_mode="Markdown")
            if order_id_int and order_id_int in order_waiter:
                waiter_id = order_waiter[order_id_int]
                try:
                    await context.bot.send_message(chat_id=waiter_id, text=f"ЗАМОВЛЕННЯ #{order_id_str} ГОТОВЕ\n\nМОЖНА ПОДАВАТИ ({now})")
                    await context.bot.send_sticker(chat_id=waiter_id, sticker="CAACAgIAAxkBAAIBmGYV1c2v8gHSsV0Hx3hLSAAB0Ew2AAJoAQACIjaOC0rBHMJj3Ro1HgQ")
                except Exception:
                    pass
                order_waiter.pop(order_id_int, None)
        return

# ─── MAIN ────────────────────────────────────
def main():
    # Démarrer le serveur HTTP dans un thread séparé
    t = threading.Thread(target=run_http_server, daemon=True)
    t.start()

    # Démarrer le bot en long polling
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_order))
    app.add_handler(CommandHandler("rapport", rapport))
    app.add_handler(CallbackQueryHandler(button))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
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

# ─── RAPPORTS ────────────────────────────────
UA_MONTHS = {1:"Січень",2:"Лютий",3:"Березень",4:"Квітень",5:"Травень",6:"Червень",7:"Липень",8:"Серпень",9:"Вересень",10:"Жовтень",11:"Листопад",12:"Грудень"}
UA_MONTHS_GEN = {1:"Січня",2:"Лютого",3:"Березня",4:"Квітня",5:"Травня",6:"Червня",7:"Липня",8:"Серпня",9:"Вересня",10:"Жовтня",11:"Листопада",12:"Грудня"}

def build_period_report(key, title):
    stats = load_stats()
    data = stats.get(key, {})
    orders_count = stats.get("__orders__", {}).get(key, 0)
    if not data:
        return f"{title}\n\n_(немає даних)_"
    sorted_items = sorted(data.items(), key=lambda x: x[1], reverse=True)
    total = sum(data.values())
    lines = [f"{title}\n", f"Замовлень: *{orders_count}*", f"Страв подано: *{total}*\n"]
    for item_id, count in sorted_items:
        lines.append(f"• {get_item_name(item_id)} — {count}")
    return "\n".join(lines)

def build_daily_report(day_key=None):
    if not day_key:
        day_key = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    d = datetime.strptime(day_key, "%Y-%m-%d")
    return build_period_report(day_key, f"📋 Підсумок дня — {d.day} {UA_MONTHS_GEN.get(d.month,'')} {d.year}")

def build_monthly_report(month_key=None):
    if not month_key:
        now = datetime.now()
        month_key = f"{now.year-1}-12" if now.month==1 else f"{now.year}-{now.month-1:02d}"
    year, month = map(int, month_key.split("-"))
    return build_period_report(month_key, f"📊 Підсумок місяця — {UA_MONTHS.get(month,'')} {year}")

# ─── ÉTAT ────────────────────────────────────
orders = {}
order_counter = {"n": 0}
order_waiter = {}

def next_order_id():
    order_counter["n"] += 1
    return order_counter["n"]

# ─── REMINDER ────────────────────────────────
async def send_reminder(order_id, waiter_id, table_label, bot):
    await asyncio.sleep(REMINDER_MINUTES * 60)
    if order_id in order_waiter:
        try:
            await bot.send_message(chat_id=waiter_id, text=f"⏳ Замовлення #{order_id} — {table_label}\nКухня ще не підтвердила. Перевір!")
            await bot.send_message(chat_id=KITCHEN_CHAT_ID, text=f"⚠️ Замовлення #{order_id} — {table_label}\nЧекає вже {REMINDER_MINUTES} хвилини!")
        except Exception:
            pass

# ─── HELPERS ─────────────────────────────────
def format_cart(table_label, items):
    if not items:
        return f"🪑 {table_label}\n\n_(кошик порожній)_"
    counts = {}
    for i in items:
        counts[i] = counts.get(i, 0) + 1
    lines = [f"🪑 *{table_label}*\n"]
    for item_id, count in counts.items():
        name = get_item_name(item_id)
        lines.append(f"• {name} ×{count}" if count > 1 else f"• {name}")
    return "\n".join(lines)

def format_order_for_kitchen(order_id, table_label, items, waiter_name):
    counts = {}
    for i in items:
        counts[i] = counts.get(i, 0) + 1
    now = datetime.now().strftime("%H:%M")
    lines = [f"🍷 *Замовлення #{order_id}* — {now}", f"🪑 {table_label}", f"👤 {waiter_name}", ""]
    for item_id, count in counts.items():
        name = get_item_name(item_id)
        lines.append(f"• {name} ×{count}" if count > 1 else f"• {name}")
    return "\n".join(lines)

# ─── KEYBOARDS ───────────────────────────────
def build_zones_keyboard():
    rows = [[InlineKeyboardButton(z["name"], callback_data=f"ZONE_{z['id']}")] for z in load_tables()["zones"]]
    rows.append([InlineKeyboardButton("Скасувати", callback_data="CANCEL")])
    return InlineKeyboardMarkup(rows)

def build_tables_keyboard(zone_id):
    zone = next((z for z in load_tables()["zones"] if z["id"] == zone_id), None)
    if not zone:
        return build_zones_keyboard()
    rows = []
    row = []
    for t in zone["tables"]:
        row.append(InlineKeyboardButton(str(t), callback_data=f"TABLE_{zone_id}_{t}"))
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
    rows = [[InlineKeyboardButton(item["name"], callback_data=f"ADD_{item['id']}")] for item in items[start:start+page_size]]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"PAGE_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"PAGE_{page+1}"))
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
                label += f" ×{count}"
            rows.append([InlineKeyboardButton(f"− {label}", callback_data=f"REMOVE_{item_id}")])
    rows.append([InlineKeyboardButton("← Назад", callback_data="SHOW_CART")])
    return InlineKeyboardMarkup(rows)

def build_kitchen_keyboard(order_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Готується", callback_data=f"STATUS_COOKING_{order_id}"),
        InlineKeyboardButton("Готово", callback_data=f"STATUS_READY_{order_id}"),
    ]])

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
        zone_id = data.replace("ZONE_", "")
        zone = next((z for z in load_tables()["zones"] if z["id"] == zone_id), None)
        orders[user_id]["zone_id"] = zone_id
        await query.edit_message_text(f"{zone['name']} — вибери стіл:", reply_markup=build_tables_keyboard(zone_id))
        return

    if data.startswith("TABLE_"):
        if user_id not in orders:
            orders[user_id] = {"table": None, "table_label": None, "items": [], "page": 0}
        parts = data.split("_")
        zone_id, table_num = parts[1], parts[2]
        zone = next((z for z in load_tables()["zones"] if z["id"] == zone_id), None)
        table_label = f"{zone['name']}, стіл {table_num}"
        orders[user_id].update({"table": table_num, "table_label": table_label})
        await query.edit_message_text(f"🪑 *{table_label}*\n\nВибери страви:", reply_markup=build_menu_keyboard(0), parse_mode="Markdown")
        return

    if data.startswith("PAGE_"):
        if user_id not in orders:
            await query.edit_message_text("Сесія закінчилась. Натисни /new")
            return
        page = int(data.replace("PAGE_", ""))
        orders[user_id]["page"] = page
        order = orders[user_id]
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        await query.edit_message_text(f"{cart_text}\n\nВибери страву:", reply_markup=build_menu_keyboard(page), parse_mode="Markdown")
        return

    if data.startswith("ADD_"):
        if user_id not in orders:
            await query.edit_message_text("Сесія закінчилась. Натисни /new")
            return
        item_id = data.replace("ADD_", "")
        orders[user_id]["items"].append(item_id)
        order = orders[user_id]
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        await query.edit_message_text(f"✓ Додано\n\n{cart_text}\n\nДодай ще або переглянь кошик:", reply_markup=build_menu_keyboard(order.get("page", 0)), parse_mode="Markdown")
        return

    if data == "SHOW_CART":
        if user_id not in orders:
            await query.edit_message_text("Сесія закінчилась. Натисни /new")
            return
        order = orders[user_id]
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        await query.edit_message_text(f"{cart_text}\n\nПеревір замовлення:", reply_markup=build_cart_keyboard(), parse_mode="Markdown")
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
        await query.edit_message_text(f"{cart_text}\n\nЩо прибрати?", reply_markup=build_remove_keyboard(order["items"]), parse_mode="Markdown")
        return

    if data.startswith("REMOVE_"):
        if user_id not in orders:
            await query.edit_message_text("Сесія закінчилась. Натисни /new")
            return
        item_id = data.replace("REMOVE_", "")
        order = orders[user_id]
        if item_id in order["items"]:
            order["items"].remove(item_id)
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        if not order["items"]:
            await query.edit_message_text("Кошик порожній. Вибери страви:", reply_markup=build_menu_keyboard(0), parse_mode="Markdown")
        else:
            await query.edit_message_text(f"{cart_text}\n\nЩе прибрати?", reply_markup=build_remove_keyboard(order["items"]), parse_mode="Markdown")
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
        await context.bot.send_message(chat_id=KITCHEN_CHAT_ID, text=kitchen_text, reply_markup=build_kitchen_keyboard(order_id), parse_mode="Markdown")
        await query.edit_message_text(f"Замовлення #{order_id} відправлено.\n\n{format_cart(table_label, items)}", parse_mode="Markdown")
        orders.pop(user_id, None)
        asyncio.create_task(send_reminder(order_id, user_id, table_label, context.bot))
        return

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
            new_text = query.message.text + f"\n\n— Готується ({now})"
            await query.edit_message_text(new_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Готово", callback_data=f"STATUS_READY_{order_id_str}")]]), parse_mode="Markdown")
            if order_id_int and order_id_int in order_waiter:
                try:
                    await context.bot.send_message(chat_id=order_waiter[order_id_int], text=f"🍷 Замовлення #{order_id_str} — кухня прийняла, готується. ({now})")
                except Exception:
                    pass

        elif status == "READY":
            new_text = query.message.text + f"\n\n— Готово ({now})"
            await query.edit_message_text(new_text, reply_markup=InlineKeyboardMarkup([]), parse_mode="Markdown")
            if order_id_int and order_id_int in order_waiter:
                waiter_id = order_waiter[order_id_int]
                try:
                    await context.bot.send_message(chat_id=waiter_id, text=f"‼️ ЗАМОВЛЕННЯ #{order_id_str} ГОТОВЕ ‼️\n\nМОЖНА ПОДАВАТИ ({now})")
                    await context.bot.send_sticker(chat_id=waiter_id, sticker="CAACAgIAAxkBAAIBmGYV1c2v8gHSsV0Hx3hLSAAB0Ew2AAJoAQACIjaOC0rBHMJj3Ro1HgQ")
                except Exception:
                    pass
                order_waiter.pop(order_id_int, None)
        return

# ─── MAIN — long polling ──────────────────────
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_order))
    app.add_handler(CommandHandler("rapport", rapport))
    app.add_handler(CallbackQueryHandler(button))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
