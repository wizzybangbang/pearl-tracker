import os
import json
import asyncio
import logging
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID"))
ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_ID"))

ADMIN_IDS = [
    int(x.strip())
    for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip()
]

ORDERS_FILE = "orders.json"
SETTINGS_FILE = "settings.json"

ASKING_CATEGORY, ASKING_ITEM, ASKING_RECEIPT, ASKING_TRACK, WAITING_PRICE, WAITING_QR = range(6)

CATEGORIES = {
    "DGC": "dgc",
    "Prems": "prems",
    "Skins": "skins",
    "Pilot": "pilot",
}


class Ping(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"alive")

    def log_message(self, *args):
        pass


def start_ping_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("", port), Ping).serve_forever()


def load_json(path, default):
    if not os.path.exists(path):
        save_json(path, default)
        return default

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def is_owner(user_id):
    return user_id == OWNER_ID


def is_admin(user_id):
    return user_id == OWNER_ID or user_id in ADMIN_IDS


def next_order_id(orders):
    if not orders:
        return 1
    return max(int(k) for k in orders.keys()) + 1


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💎 Welcome to Pearl Tracker\n\n"
        "Use /order to place an order.\n"
        "Use /track to check order status."
    )


async def order_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["DGC", "Prems"],
        ["Skins", "Pilot"]
    ]

    await update.message.reply_text(
        "Choose category:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )

    return ASKING_CATEGORY


async def choose_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text

    if choice not in CATEGORIES:
        await update.message.reply_text("Please choose from buttons.")
        return ASKING_CATEGORY

    key = CATEGORIES[choice]

    context.user_data["category"] = choice

    settings = load_json(SETTINGS_FILE, {
        "prices": {},
        "qr": None
    })

    pricelist = settings["prices"].get(key)

    if pricelist:
        await update.message.reply_text(pricelist)
    else:
        await update.message.reply_text("No pricelist yet.")

    await update.message.reply_text(
        "What item/package do you want to order?\n\nExample:\n301 Diamonds"
    )

    return ASKING_ITEM


async def get_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["item"] = update.message.text

    settings = load_json(SETTINGS_FILE, {
        "prices": {},
        "qr": None
    })

    qr = settings.get("qr")

    if qr:
        await update.message.reply_photo(
            photo=qr,
            caption="💳 Please send payment here.\n\nAfter payment, upload your receipt."
        )
    else:
        await update.message.reply_text(
            "QR payment image not set yet."
        )

    return ASKING_RECEIPT


async def get_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    orders = load_json(ORDERS_FILE, {})

    order_id = next_order_id(orders)

    category = context.user_data.get("category")
    item = context.user_data.get("item")

    orders[str(order_id)] = {
        "user_id": user.id,
        "username": user.username or "none",
        "category": category,
        "item": item,
        "status": "pending"
    }

    save_json(ORDERS_FILE, orders)

    admin_text = (
        f"🛒 NEW ORDER #{order_id}\n\n"
        f"👤 @{user.username or 'none'}\n"
        f"🆔 {user.id}\n"
        f"📦 {category}\n"
        f"📝 {item}\n"
        f"📌 pending"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Process",
                callback_data=f"process:{order_id}"
            ),
            InlineKeyboardButton(
                "Done",
                callback_data=f"done:{order_id}"
            ),
        ]
    ])

    await context.bot.send_message(
        chat_id=ADMIN_GROUP_ID,
        text=admin_text,
        reply_markup=keyboard
    )

    await context.bot.forward_message(
        chat_id=ADMIN_GROUP_ID,
        from_chat_id=update.effective_chat.id,
        message_id=update.message.message_id,
    )

    await update.message.reply_text(
        f"✅ Order #{order_id} submitted successfully.\n\n"
        f"Use /track to check your order."
    )

    context.user_data.clear()

    return ConversationHandler.END


async def track_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Enter your order number.\n\nExample:\n13"
    )

    return ASKING_TRACK


async def get_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.replace("#", "").strip()

    orders = load_json(ORDERS_FILE, {})

    if raw not in orders:
        await update.message.reply_text("Order not found.")
        return ConversationHandler.END

    order = orders[raw]

    if (
        update.effective_user.id != order["user_id"]
        and not is_admin(update.effective_user.id)
    ):
        await update.message.reply_text(
            "You can only track your own order."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"📦 Order #{raw}\n\n"
        f"Category: {order['category']}\n"
        f"Item: {order['item']}\n"
        f"Status: {order['status']}"
    )

    return ConversationHandler.END


async def setprice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/setprice dgc"
        )
        return ConversationHandler.END

    category = context.args[0].lower()

    if category not in ["dgc", "prems", "skins", "pilot"]:
        await update.message.reply_text("Invalid category.")
        return ConversationHandler.END

    context.user_data["price_category"] = category

    await update.message.reply_text(
        f"Send new {category.upper()} pricelist text now."
    )

    return WAITING_PRICE


async def save_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    category = context.user_data["price_category"]

    settings = load_json(SETTINGS_FILE, {
        "prices": {},
        "qr": None
    })

    settings["prices"][category] = update.message.text

    save_json(SETTINGS_FILE, settings)

    await update.message.reply_text(
        f"✅ {category.upper()} pricelist updated."
    )

    return ConversationHandler.END


async def setqr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return

    await update.message.reply_text(
        "Send new QR image now."
    )

    return WAITING_QR


async def save_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Please send image.")
        return WAITING_QR

    file_id = update.message.photo[-1].file_id

    settings = load_json(SETTINGS_FILE, {
        "prices": {},
        "qr": None
    })

    settings["qr"] = file_id

    save_json(SETTINGS_FILE, settings)

    await update.message.reply_text("✅ QR updated.")

    return ConversationHandler.END


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    if not is_admin(query.from_user.id):
        return

    action, order_id = query.data.split(":")

    orders = load_json(ORDERS_FILE, {})

    if order_id not in orders:
        return

    if action == "process":
        orders[order_id]["status"] = "processing"
        status_text = "processing"

    else:
        orders[order_id]["status"] = "done"
        status_text = "done"

    save_json(ORDERS_FILE, orders)

    user_id = orders[order_id]["user_id"]

    if status_text == "done":
        msg = (
            f"✅ Order #{order_id} completed.\n\n"
            f"Thank you for ordering from Pearl Tracker!"
        )
    else:
        msg = f"📌 Order #{order_id} is now processing."

    await context.bot.send_message(
        chat_id=user_id,
        text=msg
    )

    await query.edit_message_text(
        f"✅ Order #{order_id} updated to {status_text}."
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "Cancelled.",
        reply_markup=ReplyKeyboardRemove()
    )

    return ConversationHandler.END


def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("order", order_cmd),
            CommandHandler("track", track_cmd),
            CommandHandler("setprice", setprice_cmd),
            CommandHandler("setqr", setqr_cmd),
        ],
        states={
            ASKING_CATEGORY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, choose_category)
            ],

            ASKING_ITEM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_item)
            ],

            ASKING_RECEIPT: [
                MessageHandler(filters.ALL & ~filters.COMMAND, get_receipt)
            ],

            ASKING_TRACK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_track)
            ],

            WAITING_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_price)
            ],

            WAITING_QR: [
                MessageHandler(filters.PHOTO & ~filters.COMMAND, save_qr)
            ],
        },

        fallbacks=[
            CommandHandler("cancel", cancel)
        ],

        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))

    app.add_handler(conv)

    app.add_handler(
        CallbackQueryHandler(button_handler)
    )

    print("Pearl Tracker running...")

    app.run_polling(close_loop=False)


if __name__ == "__main__":
    Thread(
        target=start_ping_server,
        daemon=True
    ).start()

    run_bot()
