import os
import json
import asyncio
import calendar
from datetime import datetime
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
KITCHEN_CHAT_ID = int(os.environ["KITCHEN_CHAT_ID"])
ADMIN_ID = int(os.environ["ADMIN_ID"])
# IDs des cuisiniers/restaurant séparés par virgule ex: "123456,789012"
KITCHEN_USER_IDS = set(
    int(x.strip()) for x in os.environ.get("KITCHEN_USER_IDS", "").split(",") if x.strip()
)
STATS_FILE = "stats.json"

app_flask = Flask(__name__)
application = ApplicationBuilder().token(BOT_TOKEN).build()

# ─────────────────────────────────────────────
#  STATS
# ─────────────────────────────────────────────
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
    month_key = datetime.now().strftime("%Y-%m")
    if month_key not in stats:
        stats[month_key] = {}
    for item_id in items:
        stats[month_key][item_id] = stats[month_key].get(item_id, 0) + 1
    save_stats(stats)

# ─────────────────────────────────────────────
#  MENU & TABLES
# ─────────────────────────────────────────────
def load_menu():
    with open("menu.json", "r", encoding="utf-8") as f:
        return json.load(f)

def load_tables():
    with open("tables.json", "r", encoding="utf-8") as f:
        return json.load(f)

def get_item_name(item_id):
    menu = load_menu()
    for item in menu["items"]:
        if item["id"] == item_id:
            return item["name"]
    return item_id

# ─────────────────────────────────────────────
#  RAPPORT
# ─────────────────────────────────────────────
def build_rapport(month_key):
    stats = load_stats()
    data = stats.get(month_key, {})
    year, month = map(int, month_key.split("-"))
    UA_MONTHS = {
        1: "Січень", 2: "Лютий", 3: "Березень", 4: "Квітень",
        5: "Травень", 6: "Червень", 7: "Липень", 8: "Серпень",
        9: "Вересень", 10: "Жовтень", 11: "Листопад", 12: "Грудень",
    }
    month_ua = UA_MONTHS.get(month, str(month))
    if not data:
        return f"📊 *Звіт за {month_ua} {year}*\n\n_(немає даних)_"
    sorted_items = sorted(data.items(), key=lambda x: x[1], reverse=True)
    total = sum(data.values())
    lines = [f"📊 *Звіт за {month_ua} {year}*\n"]
    for item_id, count in sorted_items:
        name = get_item_name(item_id)
        lines.append(f"• {name} — *{count}*")
    lines.append(f"\n🍽 Всього страв: *{total}*")
    return "\n".join(lines)

# ─────────────────────────────────────────────
#  ÉTAT
# ─────────────────────────────────────────────
orders = {}
order_counter = {"n": 0}
# Stocke waiter_id par order_id pour la notification
order_waiter = {}

def next_order_id():
    order_counter["n"] += 1
    return order_counter["n"]

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
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
    lines = [
        f"🍷 *Замовлення #{order_id}* — {now}",
        f"🪑 {table_label}",
        f"👤 {waiter_name}",
        "",
    ]
    for item_id, count in counts.items():
        name = get_item_name(item_id)
        lines.append(f"• {name} ×{count}" if count > 1 else f"• {name}")
    return "\n".join(lines)

# ─────────────────────────────────────────────
#  KEYBOARDS
# ─────────────────────────────────────────────
def build_zones_keyboard():
    tables_data = load_tables()
    rows = []
    for zone in tables_data["zones"]:
        rows.append([InlineKeyboardButton(zone["name"], callback_data=f"ZONE_{zone['id']}")])
    rows.append([InlineKeyboardButton("Скасувати", callback_data="CANCEL")])
    return InlineKeyboardMarkup(rows)

def build_tables_keyboard(zone_id):
    tables_data = load_tables()
    zone = next((z for z in tables_data["zones"] if z["id"] == zone_id), None)
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
    menu = load_menu()
    items = menu["items"]
    page_size = 8
    start = page * page_size
    end = min(start + page_size, len(items))
    page_items = items[start:end]
    total_pages = (len(items) + page_size - 1) // page_size

    rows = []
    for item in page_items:
        rows.append([InlineKeyboardButton(item["name"], callback_data=f"ADD_{item['id']}")])

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
    menu = load_menu()
    item_lookup = {item["id"]: item["name"] for item in menu["items"]}
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

# ─────────────────────────────────────────────
#  HANDLERS
# ─────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍷 Ласкаво просимо до bandôl!\n\nНатисни /new щоб створити замовлення."
    )

async def new_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    orders[user_id] = {"table": None, "table_label": None, "items": [], "page": 0}
    await update.message.reply_text(
        "Вибери зону:",
        reply_markup=build_zones_keyboard()
    )

async def rapport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔️ Команда недоступна.")
        return
    now = datetime.now()
    month_key = f"{now.year - 1}-12" if now.month == 1 else f"{now.year}-{now.month - 1:02d}"
    text = build_rapport(month_key)
    await context.bot.send_message(chat_id=KITCHEN_CHAT_ID, text=text, parse_mode="Markdown")
    await update.message.reply_text("✅ Звіт відправлено.")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    waiter_name = query.from_user.first_name or "Офіціант"
    await query.answer()

    # CANCEL
    if data == "CANCEL":
        orders.pop(user_id, None)
        await query.edit_message_text("Замовлення скасовано.")
        return

    # BACK TO ZONES
    if data == "BACK_ZONES":
        if user_id not in orders:
            orders[user_id] = {"table": None, "table_label": None, "items": [], "page": 0}
        await query.edit_message_text("Вибери зону:", reply_markup=build_zones_keyboard())
        return

    # ZONE SELECTED
    if data.startswith("ZONE_"):
        if user_id not in orders:
            orders[user_id] = {"table": None, "table_label": None, "items": [], "page": 0}
        zone_id = data.replace("ZONE_", "")
        tables_data = load_tables()
        zone = next((z for z in tables_data["zones"] if z["id"] == zone_id), None)
        zone_name = zone["name"] if zone else zone_id
        orders[user_id]["zone_id"] = zone_id
        await query.edit_message_text(
            f"{zone_name} — вибери стіл:",
            reply_markup=build_tables_keyboard(zone_id)
        )
        return

    # TABLE SELECTED
    if data.startswith("TABLE_"):
        if user_id not in orders:
            orders[user_id] = {"table": None, "table_label": None, "items": [], "page": 0}
        parts = data.split("_")
        zone_id = parts[1]
        table_num = parts[2]
        tables_data = load_tables()
        zone = next((z for z in tables_data["zones"] if z["id"] == zone_id), None)
        zone_name = zone["name"] if zone else zone_id
        table_label = f"{zone_name}, стіл {table_num}"
        orders[user_id]["table"] = table_num
        orders[user_id]["table_label"] = table_label
        await query.edit_message_text(
            f"🪑 *{table_label}*\n\nВибери страви:",
            reply_markup=build_menu_keyboard(0),
            parse_mode="Markdown"
        )
        return

    # PAGINATION
    if data.startswith("PAGE_"):
        if user_id not in orders:
            await query.edit_message_text("Сесія закінчилась. Натисни /new")
            return
        page = int(data.replace("PAGE_", ""))
        orders[user_id]["page"] = page
        order = orders[user_id]
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        await query.edit_message_text(
            f"{cart_text}\n\nВибери страву:",
            reply_markup=build_menu_keyboard(page),
            parse_mode="Markdown"
        )
        return

    # ADD ITEM
    if data.startswith("ADD_"):
        if user_id not in orders:
            await query.edit_message_text("Сесія закінчилась. Натисни /new")
            return
        item_id = data.replace("ADD_", "")
        orders[user_id]["items"].append(item_id)
        order = orders[user_id]
        page = order.get("page", 0)
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        await query.edit_message_text(
            f"✓ Додано\n\n{cart_text}\n\nДодай ще або переглянь кошик:",
            reply_markup=build_menu_keyboard(page),
            parse_mode="Markdown"
        )
        return

    # SHOW CART
    if data == "SHOW_CART":
        if user_id not in orders:
            await query.edit_message_text("Сесія закінчилась. Натисни /new")
            return
        order = orders[user_id]
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        await query.edit_message_text(
            f"{cart_text}\n\nПеревір замовлення:",
            reply_markup=build_cart_keyboard(),
            parse_mode="Markdown"
        )
        return

    # REMOVE LIST
    if data == "REMOVE_LIST":
        if user_id not in orders:
            await query.edit_message_text("Сесія закінчилась. Натисни /new")
            return
        order = orders[user_id]
        if not order["items"]:
            await query.answer("Кошик порожній", show_alert=True)
            return
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        await query.edit_message_text(
            f"{cart_text}\n\nЩо прибрати?",
            reply_markup=build_remove_keyboard(order["items"]),
            parse_mode="Markdown"
        )
        return

    # REMOVE ONE ITEM
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
            await query.edit_message_text(
                "Кошик порожній. Вибери страви:",
                reply_markup=build_menu_keyboard(0),
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(
                f"{cart_text}\n\nЩе прибрати?",
                reply_markup=build_remove_keyboard(order["items"]),
                parse_mode="Markdown"
            )
        return

    # SEND ORDER
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
        # Mémoriser le waiter pour la notification
        order_waiter[order_id] = user_id
        await context.bot.send_message(
            chat_id=KITCHEN_CHAT_ID,
            text=kitchen_text,
            reply_markup=build_kitchen_keyboard(order_id),
            parse_mode="Markdown"
        )
        await query.edit_message_text(
            f"Замовлення #{order_id} відправлено.\n\n{format_cart(table_label, items)}",
            parse_mode="Markdown"
        )
        orders.pop(user_id, None)
        return

    # KITCHEN STATUS — réservé aux cuisiniers
    if data.startswith("STATUS_"):
        # Vérifier que c'est bien un cuisinier
        if KITCHEN_USER_IDS and user_id not in KITCHEN_USER_IDS:
            await query.answer("⛔️ Тільки для кухні.", show_alert=True)
            return

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
            new_keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("Готово", callback_data=f"STATUS_READY_{order_id_str}")
            ]])
            await query.edit_message_text(new_text, reply_markup=new_keyboard, parse_mode="Markdown")
            # Notifier le serveur
            if order_id_int and order_id_int in order_waiter:
                waiter_id = order_waiter[order_id_int]
                try:
                    await context.bot.send_message(
                        chat_id=waiter_id,
                        text=f"🍷 Замовлення #{order_id_str} — кухня прийняла, готується. ({now})"
                    )
                except Exception:
                    pass

        elif status == "READY":
            new_text = query.message.text + f"\n\n— Готово ({now})"
            await query.edit_message_text(new_text, reply_markup=InlineKeyboardMarkup([]), parse_mode="Markdown")
            # Notifier le serveur
            if order_id_int and order_id_int in order_waiter:
                waiter_id = order_waiter[order_id_int]
                try:
                    await context.bot.send_message(
                        chat_id=waiter_id,
                        text=f"✓ Замовлення #{order_id_str} — готово, можна подавати. ({now})"
                    )
                except Exception:
                    pass
                order_waiter.pop(order_id_int, None)
        return

# ─────────────────────────────────────────────
#  REGISTRATION
# ─────────────────────────────────────────────
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("new", new_order))
application.add_handler(CommandHandler("rapport", rapport))
application.add_handler(CallbackQueryHandler(button))

# ─────────────────────────────────────────────
#  FLASK WEBHOOK
# ─────────────────────────────────────────────
@app_flask.route("/", methods=["POST"])
async def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return "ok"

@app_flask.route("/", methods=["GET"])
def health():
    return "bandôl — alive"

if __name__ == "__main__":
    asyncio.run(application.initialize())
    asyncio.run(application.start())
    app_flask.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
