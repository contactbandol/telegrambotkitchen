from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

BOT_TOKEN = "8734988502:AAG38XIbi5qMaS-tjDDhCTktC-XdUwRtc3k"
KITCHEN_CHAT_ID = -1003934853491

orders = {}

TABLES = ["11", "12", "13"]

MENU = {
    "VITELLO": "Вітелло Тонато",
    "SKU": "Скумбрія с овочами",
    "OLI": "Оливки Мариновани",
}


def build_tables_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Стол 11", callback_data="TABLE_11")],
        [InlineKeyboardButton("Стол 12", callback_data="TABLE_12")],
        [InlineKeyboardButton("Стол 13", callback_data="TABLE_13")],
        [InlineKeyboardButton("Отмена", callback_data="CANCEL")],
    ])


def build_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Вітелло Тонато", callback_data="ADD_VITELLO")],
        [InlineKeyboardButton("Скумбрія с овочами", callback_data="ADD_SKU")],
        [InlineKeyboardButton("Оливки Мариновани", callback_data="ADD_OLI")],
        [InlineKeyboardButton("Отправить", callback_data="SEND")],
        [InlineKeyboardButton("Отмена", callback_data="CANCEL")],
    ])


def build_kitchen_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("ГОТОВИТСЯ", callback_data="STATUS_COOKING"),
            InlineKeyboardButton("ГОТОВО", callback_data="STATUS_READY"),
        ]
    ])


def format_order_text(table, items):
    lines = [f"Стол {table}"]
    for code in items:
        lines.append(f"• {MENU[code]}")
    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Бот готов.\nНажми /new чтобы создать заказ."
    )


async def new_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    orders[user_id] = {"table": None, "items": []}

    await update.message.reply_text(
        "Выбери стол:",
        reply_markup=build_tables_keyboard(),
    )


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    await query.answer()

    if data == "CANCEL":
        if user_id in orders:
            del orders[user_id]
        await query.edit_message_text("Заказ отменён.")
        return

    if data.startswith("TABLE_"):
        if user_id not in orders:
            await query.edit_message_text("Сессия истекла. Нажми /new")
            return

        table = data.replace("TABLE_", "")
        orders[user_id]["table"] = table

        await query.edit_message_text(
            f"Стол {table} выбран.\nВыбери блюда:",
            reply_markup=build_menu_keyboard(),
        )
        return

    if data.startswith("ADD_"):
        if user_id not in orders:
            await query.edit_message_text("Сессия истекла. Нажми /new")
            return

        code = data.replace("ADD_", "")
        orders[user_id]["items"].append(code)

        table = orders[user_id]["table"]
        items = orders[user_id]["items"]

        if items:
            recap = format_order_text(table, items)
        else:
            recap = f"Стол {table}\nНет блюд"

        await query.edit_message_text(
            f"{recap}\n\nДобавь ещё или нажми Отправить.",
            reply_markup=build_menu_keyboard(),
        )
        return

    if data == "SEND":
        if user_id not in orders:
            await query.edit_message_text("Сессия истекла. Нажми /new")
            return

        order = orders[user_id]
        table = order["table"]
        items = order["items"]

        if not table:
            await query.edit_message_text("Сначала выбери стол.")
            return

        if not items:
            await query.edit_message_text(
import os
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
KITCHEN_CHAT_ID = -1003934853491

app_flask = Flask(__name__)
application = ApplicationBuilder().token(BOT_TOKEN).build()

orders = {}

MENU = {
    "VITELLO": "Вітелло Тонато",
    "SKU": "Скумбрія с овочами",
    "OLI": "Оливки Мариновани",
}

def build_tables_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Стол 11", callback_data="TABLE_11")],
        [InlineKeyboardButton("Стол 12", callback_data="TABLE_12")],
        [InlineKeyboardButton("Стол 13", callback_data="TABLE_13")],
        [InlineKeyboardButton("Отмена", callback_data="CANCEL")],
    ])

def build_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Вітелло Тонато", callback_data="ADD_VITELLO")],
        [InlineKeyboardButton("Скумбрія с овочами", callback_data="ADD_SKU")],
        [InlineKeyboardButton("Оливки Мариновани", callback_data="ADD_OLI")],
        [InlineKeyboardButton("Отправить", callback_data="SEND")],
        [InlineKeyboardButton("Отмена", callback_data="CANCEL")],
    ])

def build_kitchen_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("ГОТОВИТСЯ", callback_data="STATUS_COOKING"),
            InlineKeyboardButton("ГОТОВО", callback_data="STATUS_READY"),
        ]
    ])

def format_order_text(table, items):
    lines = [f"Стол {table}"]
    for code in items:
        lines.append(f"• {MENU[code]}")
    return "\n".join(lines)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот готов. Нажми /new")

async def new_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    orders[user_id] = {"table": None, "items": []}

    await update.message.reply_text("Выбери стол:", reply_markup=build_tables_keyboard())

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    await query.answer()

    if data == "CANCEL":
        orders.pop(user_id, None)
        await query.edit_message_text("Заказ отменён.")
        return

    if data.startswith("TABLE_"):
        orders[user_id]["table"] = data.replace("TABLE_", "")
        await query.edit_message_text("Выбери блюда:", reply_markup=build_menu_keyboard())
        return

    if data.startswith("ADD_"):
        orders[user_id]["items"].append(data.replace("ADD_", ""))
        table = orders[user_id]["table"]
        items = orders[user_id]["items"]
        recap = format_order_text(table, items)
        await query.edit_message_text(f"{recap}", reply_markup=build_menu_keyboard())
        return

    if data == "SEND":
        order = orders[user_id]
        text = format_order_text(order["table"], order["items"])

        await context.bot.send_message(
            chat_id=KITCHEN_CHAT_ID,
            text=text,
            reply_markup=build_kitchen_keyboard(),
        )

        await query.edit_message_text("Отправлено")
        orders.pop(user_id, None)

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("new", new_order))
application.add_handler(CallbackQueryHandler(button))

@app_flask.route("/", methods=["POST"])
async def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return "ok"

@app_flask.route("/", methods=["GET"])
def health():
    return "Bot is alive"

if __name__ == "__main__":
    application.initialize()
    application.start()
    app_flask.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
