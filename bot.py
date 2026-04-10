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

BOT_TOKEN = os.environ["BOT_TOKEN"]
KITCHEN_CHAT_ID = int(os.environ["KITCHEN_CHAT_ID"])
ADMIN_ID = int(os.environ["ADMIN_ID"])
STATS_FILE = "stats.json"

app_flask = Flask(__name__)
application = ApplicationBuilder().token(BOT_TOKEN).build()

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

def get_item_name(item_id):
    menu = load_menu()
    for cat in menu["categories"]:
        for item in cat["items"]:
            if item["id"] == item_id:
                return item["name"]
    return item_id

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

def load_menu():
    with open("menu.json", "r", encoding="utf-8") as f:
        return json.load(f)

def load_tables():
    with open("tables.json", "r", encoding="utf-8") as f:
        return json.load(f)

orders = {}
order_counter = {"n": 0}

def next_order_id():
    order_counter["n"] += 1
    return order_counter["n"]

def format_cart(table_label, items):
    if not items:
        return f"🪑 Стол {table_label}\n\n_(корзина пуста)_"
    counts = {}
    for i in items:
        counts[i] = counts.get(i, 0) + 1
    lines = [f"🪑 *Стол {table_label}*\n"]
    for item_id, count in counts.items():
        name = get_item_name(item_id)
        lines.append(f"• {name} ×{count}" if count > 1 else f"• {name}")
    return "\n".join(lines)

def format_order_for_kitchen(order_id, table_label, items, waiter_name):
    counts = {}
    for i in items:
        counts[i] = counts.get(i, 0) + 1
    now = datetime.now().strftime("%H:%M")
    lines = [f"🔔 *Заказ #{order_id}* — {now}", f"🪑 Стол {table_label}", f"👤 {waiter_name}", ""]
    for item_id, count in counts.items():
        name = get_item_name(item_id)
        lines.append(f"• {name} ×{count}" if count > 1 else f"• {name}")
    return "\n".join(lines)

def build_tables_keyboard():
    tables = load_tables()
    rows = []
    row = []
    for t in tables:
        row.append(InlineKeyboardButton(t["label"], callback_data=f"TABLE_{t['id']}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="CANCEL")])
    return InlineKeyboardMarkup(rows)

def build_categories_keyboard():
    menu = load_menu()
    rows = []
    for cat in menu["categories"]:
        rows.append([InlineKeyboardButton(f"📂 {cat['name']}", callback_data=f"CAT_{cat['id']}")])
    rows.append([InlineKeyboardButton("✅ Отправить заказ", callback_data="SEND")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="CANCEL")])
    return InlineKeyboardMarkup(rows)

def build_items_keyboard(cat_id):
    menu = load_menu()
    cat = next((c for c in menu["categories"] if c["id"] == cat_id), None)
    if not cat:
        return build_categories_keyboard()
    rows = []
    for item in cat["items"]:
        rows.append([InlineKeyboardButton(item["name"], callback_data=f"ADD_{item['id']}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="BACK_CAT")])
    return InlineKeyboardMarkup(rows)

def build_cart_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Добавить ещё", callback_data="BACK_CAT")],
        [InlineKeyboardButton("🗑 Убрать блюдо", callback_data="REMOVE_LIST")],
        [InlineKeyboardButton("✅ Отправить заказ", callback_data="SEND")],
        [InlineKeyboardButton("❌ Отмена", callback_data="CANCEL")],
    ])

def build_remove_keyboard(items_in_cart):
    menu = load_menu()
    item_lookup = {}
    for cat in menu["categories"]:
        for item in cat["items"]:
            item_lookup[item["id"]] = item["name"]
    rows = []
    seen = []
    for item_id in items_in_cart:
        if item_id not in seen:
            seen.append(item_id)
            count = items_in_cart.count(item_id)
            label = f"🗑 {item_lookup.get(item_id, item_id)}"
            if count > 1:
                label += f" ×{count}"
            rows.append([InlineKeyboardButton(label, callback_data=f"REMOVE_{item_id}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="SHOW_CART")])
    return InlineKeyboardMarkup(rows)

def build_kitchen_keyboard(order_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👨‍🍳 ГОТОВИТСЯ", callback_data=f"STATUS_COOKING_{order_id}"),
        InlineKeyboardButton("✅ ГОТОВО", callback_data=f"STATUS_READY_{order_id}"),
    ]])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Привет! Бот bandôl готов к работе.\n\nНажми /new чтобы создать заказ.")

async def new_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    orders[user_id] = {"table": None, "table_label": None, "items": []}
    await update.message.reply_text("🪑 Выбери стол:", reply_markup=build_tables_keyboard())

async def rapport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔️ Команда недоступна.")
        return
    now = datetime.now()
    month_key = f"{now.year - 1}-12" if now.month == 1 else f"{now.year}-{now.month - 1:02d}"
    text = build_rapport(month_key)
    await context.bot.send_message(chat_id=KITCHEN_CHAT_ID, text=text, parse_mode="Markdown")
    await update.message.reply_text("✅ Звіт відправлено на кухню.")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    waiter_name = query.from_user.first_name or "Официант"
    await query.answer()

    if data == "CANCEL":
        orders.pop(user_id, None)
        await query.edit_message_text("❌ Заказ отменён.")
        return

    if data.startswith("TABLE_"):
        if user_id not in orders:
            orders[user_id] = {"table": None, "table_label": None, "items": []}
        table_id = data.replace("TABLE_", "")
        tables = load_tables()
        table = next((t for t in tables if t["id"] == table_id), None)
        table_label = table["label"].replace("Стол ", "") if table else table_id
        orders[user_id]["table"] = table_id
        orders[user_id]["table_label"] = table_label
        await query.edit_message_text(
            f"🪑 *Стол {table_label}* выбран.\n\nВыбери категорию:",
            reply_markup=build_categories_keyboard(), parse_mode="Markdown")
        return

    if data.startswith("CAT_"):
        if user_id not in orders:
            await query.edit_message_text("Сессия истекла. Нажми /new")
            return
        cat_id = data.replace("CAT_", "")
        menu = load_menu()
        cat = next((c for c in menu["categories"] if c["id"] == cat_id), None)
        cat_name = cat["name"] if cat else cat_id
        await query.edit_message_text(
            f"📂 *{cat_name}* — выбери блюдо:",
            reply_markup=build_items_keyboard(cat_id), parse_mode="Markdown")
        return

    if data == "BACK_CAT":
        if user_id not in orders:
            await query.edit_message_text("Сессия истекла. Нажми /new")
            return
        order = orders[user_id]
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        await query.edit_message_text(
            f"{cart_text}\n\n📂 Выбери категорию:",
            reply_markup=build_categories_keyboard(), parse_mode="Markdown")
        return

    if data.startswith("ADD_"):
        if user_id not in orders:
            await query.edit_message_text("Сессия истекла. Нажми /new")
            return
        item_id = data.replace("ADD_", "")
        orders[user_id]["items"].append(item_id)
        order = orders[user_id]
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        item_name = get_item_name(item_id)
        await query.edit_message_text(
            f"✅ *{item_name}* добавлен!\n\n{cart_text}\n\nДобавь ещё или отправь заказ:",
            reply_markup=build_cart_keyboard(), parse_mode="Markdown")
        return

    if data == "SHOW_CART":
        if user_id not in orders:
            await query.edit_message_text("Сессия истекла. Нажми /new")
            return
        order = orders[user_id]
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        await query.edit_message_text(
            f"{cart_text}\n\nДобавь ещё или отправь заказ:",
            reply_markup=build_cart_keyboard(), parse_mode="Markdown")
        return

    if data == "REMOVE_LIST":
        if user_id not in orders:
            await query.edit_message_text("Сессия истекла. Нажми /new")
            return
        order = orders[user_id]
        if not order["items"]:
            await query.answer("Корзина пуста", show_alert=True)
            return
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        await query.edit_message_text(
            f"{cart_text}\n\n🗑 Что убрать?",
            reply_markup=build_remove_keyboard(order["items"]), parse_mode="Markdown")
        return

    if data.startswith("REMOVE_"):
        if user_id not in orders:
            await query.edit_message_text("Сессия истекла. Нажми /new")
            return
        item_id = data.replace("REMOVE_", "")
        order = orders[user_id]
        if item_id in order["items"]:
            order["items"].remove(item_id)
        cart_text = format_cart(order.get("table_label", "?"), order["items"])
        if not order["items"]:
            await query.edit_message_text(
                "🛒 Корзина пуста.\n\nДобавь блюда:",
                reply_markup=build_categories_keyboard(), parse_mode="Markdown")
        else:
            await query.edit_message_text(
                f"{cart_text}\n\n🗑 Ещё убрать?",
                reply_markup=build_remove_keyboard(order["items"]), parse_mode="Markdown")
        return

    if data == "SEND":
        if user_id not in orders:
            await query.edit_message_text("Сессия истекла. Нажми /new")
            return
        order = orders[user_id]
        table_label = order.get("table_label", "?")
        items = order["items"]
        if not items:
            await query.answer("Корзина пуста! Добавь блюда.", show_alert=True)
            return
        order_id = next_order_id()
        kitchen_text = format_order_for_kitchen(order_id, table_label, items, waiter_name)
        record_order(items)
        await context.bot.send_message(
            chat_id=KITCHEN_CHAT_ID, text=kitchen_text,
            reply_markup=build_kitchen_keyboard(order_id), parse_mode="Markdown")
        await query.edit_message_text(
            f"✅ Заказ #{order_id} отправлен на кухню!\n\n{format_cart(table_label, items)}",
            parse_mode="Markdown")
        orders.pop(user_id, None)
        return

    if data.startswith("STATUS_"):
        parts = data.split("_")
        status = parts[1]
        order_id = parts[2] if len(parts) > 2 else "?"
        now = datetime.now().strftime("%H:%M")
        if status == "COOKING":
            new_text = query.message.text + f"\n\n👨‍🍳 *ГОТОВИТСЯ* — {now}"
            new_keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ ГОТОВО", callback_data=f"STATUS_READY_{order_id}")
            ]])
        elif status == "READY":
            new_text = query.message.text + f"\n\n✅ *ГОТОВО* — {now}"
            new_keyboard = InlineKeyboardMarkup([])
        else:
            return
        await query.edit_message_text(new_text, reply_markup=new_keyboard, parse_mode="Markdown")
        return

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("new", new_order))
application.add_handler(CommandHandler("rapport", rapport))
application.add_handler(CallbackQueryHandler(button))

@app_flask.route("/", methods=["POST"])
async def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return "ok"

@app_flask.route("/", methods=["GET"])
def health():
    return "bandôl kitchen bot — alive ✓"

if __name__ == "__main__":
    asyncio.run(application.initialize())
    asyncio.run(application.start())
    app_flask.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
