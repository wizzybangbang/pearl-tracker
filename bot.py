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
    int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()
]

ORDERS_FILE = "orders.json"
SETTINGS_FILE = "settings.json"

ASKING_CATEGORY, ASKING_ITEM, ASKING_RECEIPT, ASKING_TRACK, WAITING_MEDIA = range(5)

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
        "Welcome to Pearl Tracker.\n\nUse /order to place an order.\nUse /track to check your order status."
    )


async def order_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["DGC", "Prems"], ["Skins", "Pilot"]]
    await update.message.reply_text(
        "Choose what you want to order:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
    )
    return ASKING_CATEGORY


async def choose_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text

    if choice not in CATEGORIES:
        await update.message.reply_text("Please choose from the buttons.")
        return ASKING_CATEGORY

    key = CATEGORIES[choice]
    context.user_data["category"] = choice

    settings = load_json(SETTINGS_FILE, {
        "prices": {"dgc": None, "prems": None, "skins": None, "pilot": None},
        "qr": None
    })

    price_file_id = settings["prices"].get(key)

    if price_file_id:
        await update.message.reply_photo(photo=price_file_id, caption=f"{choice} Pricelist")
    else:
        await update.message.reply_text(f"No {choice} pricelist has been set yet.")

    await update.message.reply_text(
        "What item/package do you want to order?\nExample: 301 diamonds",
        reply_markup=ReplyKeyboardRemove(),
    )

    return ASKING_ITEM


async def get_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["item"] = update.message.text

    settings = load_json(SETTINGS_FILE, {
        "prices": {"dgc": None, "prems": None, "skins": None, "pilot": None},
        "qr": None
    })

    qr = settings.get("qr")

    if qr:
        await update.message.reply_photo(photo=qr, caption="Please send your payment here, then upload your receipt.")
    else:
        await update.message.reply_text("QR payment image has not been set yet. Please wait for admin assistance.")

    await update.message.reply_text("After payment, please send your receipt screenshot/photo here.")
    return ASKING_RECEIPT


async def get_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    orders = load_json(ORDERS_FILE, {})
    order_id = next_order_id(orders)

    category = context.user_data.get("category", "Unknown")
    item = context.user_data.get("item", "Unknown")

    orders[str(order_id)] = {
        "user_id": user.id,
        "username": user.username or "no username",
        "category": category,
        "item": item,
        "status": "pending",
    }

    save_json(ORDERS_FILE, orders)

    admin_text = (
        f"🛒 NEW ORDER #{order_id}\n\n"
        f"👤 Customer: @{user.username or 'no username'}\n"
        f"🆔 User ID: {user.id}\n"
        f"📦 Category: {category}\n"
        f"📝 Item: {item}\n"
        f"📌 Status: pending"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Process", callback_data=f"process:{order_id}"),
            InlineKeyboardButton("Done", callback_data=f"done:{order_id}"),
        ]
    ])

    await context.bot.send_message(
        chat_id=ADMIN_GROUP_ID,
        text=admin_text,
        reply_markup=keyboard,
    )

    if update.message.photo or update.message.document:
        await context.bot.forward_message(
            chat_id=ADMIN_GROUP_ID,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
    else:
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"Receipt/message for order #{order_id}:\n{update.message.text or '[no text]'}"
        )

    await update.message.reply_text(
        f"✅ Order #{order_id} has been submitted.\nStatus: pending\n\nUse /track to check your order."
    )

    context.user_data.clear()
    return ConversationHandler.END


async def track_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("What's your order number?")
    return ASKING_TRACK


async def get_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.replace("#", "").replace("order", "").strip()
    orders = load_json(ORDERS_FILE, {})

    if raw not in orders:
        await update.message.reply_text("Order not found. Please check your order number.")
        return ConversationHandler.END

    order = orders[raw]

    if update.effective_user.id != order["user_id"] and not is_admin(update.effective_user.id):
        await update.message.reply_text("You can only track your own order.")
        return ConversationHandler.END

    await update.message.reply_text(
        f"Order #{raw}\n"
        f"Category: {order['category']}\n"
        f"Item: {order['item']}\n"
        f"Status: {order['status']}"
    )

    return ConversationHandler.END


async def setprice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("You are not allowed to change pricelists.")
        return ConversationHandler.END

    if not context.args or context.args[0].lower() not in ["dgc", "prems", "skins", "pilot"]:
        await update.message.reply_text("Usage: /setprice dgc")
        return ConversationHandler.END

    context.user_data["setting_type"] = "price"
    context.user_data["setting_key"] = context.args[0].lower()

    await update.message.reply_text("Send the new pricelist image now.")
    return WAITING_MEDIA


async def setqr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("You are not allowed to change the QR code.")
        return ConversationHandler.END

    context.user_data["setting_type"] = "qr"
    await update.message.reply_text("Send the new QR code image now.")
    return WAITING_MEDIA


async def save_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Please send an image/photo.")
        return WAITING_MEDIA

    file_id = update.message.photo[-1].file_id

    settings = load_json(SETTINGS_FILE, {
        "prices": {"dgc": None, "prems": None, "skins": None, "pilot": None},
        "qr": None
    })

    setting_type = context.user_data.get("setting_type")

    if setting_type == "price":
        key = context.user_data.get("setting_key")
        settings["prices"][key] = file_id
        save_json(SETTINGS_FILE, settings)
        await update.message.reply_text(f"✅ {key.upper()} pricelist updated.")

    elif setting_type == "qr":
        settings["qr"] = file_id
        save_json(SETTINGS_FILE, settings)
        await update.message.reply_text("✅ QR code updated.")

    context.user_data.clear()
    return ConversationHandler.END


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("You are not allowed to manage orders.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /status 13 pending")
        return

    order_id = context.args[0].replace("#", "")
    new_status = " ".join(context.args[1:])

    await update_order_status(update, context, order_id, new_status)


async def process_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("Usage: /process 13")
        return

    await update_order_status(update, context, context.args[0].replace("#", ""), "processing")


async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("Usage: /done 13")
        return

    await update_order_status(update, context, context.args[0].replace("#", ""), "done")


async def update_order_status(update, context, order_id, new_status):
    orders = load_json(ORDERS_FILE, {})

    if order_id not in orders:
        await update.message.reply_text("Order not found.")
        return

    orders[order_id]["status"] = new_status
    save_json(ORDERS_FILE, orders)

    user_id = orders[order_id]["user_id"]

    await update.message.reply_text(f"✅ Order #{order_id} updated to {new_status}.")

    if new_status.lower() == "done":
        msg = f"✅ Order #{order_id} is done.\nThank you for ordering from Pearl!"
    else:
        msg = f"📌 Order #{order_id} status updated: {new_status}"

    await context.bot.send_message(chat_id=user_id, text=msg)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.edit_message_text("You are not allowed to manage orders.")
        return

    action, order_id = query.data.split(":")
    status = "processing" if action == "process" else "done"

    orders = load_json(ORDERS_FILE, {})

    if order_id not in orders:
        await query.edit_message_text("Order not found.")
        return

    orders[order_id]["status"] = status
    save_json(ORDERS_FILE, orders)

    user_id = orders[order_id]["user_id"]

    await query.edit_message_text(f"✅ Order #{order_id} updated to {status}.")

    if status == "done":
        msg = f"✅ Order #{order_id} is done.\nThank you for ordering from Pearl!"
    else:
        msg = f"📌 Order #{order_id} is now processing."

    await context.bot.send_message(chat_id=user_id, text=msg)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove())
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
            ASKING_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_category)],
            ASKING_ITEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_item)],
            ASKING_RECEIPT: [MessageHandler(filters.ALL & ~filters.COMMAND, get_receipt)],
            ASKING_TRACK: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_track)],
            WAITING_MEDIA: [MessageHandler(filters.PHOTO & ~filters.COMMAND, save_media)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("process", process_cmd))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("Pearl Tracker is running...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    Thread(target=start_ping_server, daemon=True).start()
    run_bot()