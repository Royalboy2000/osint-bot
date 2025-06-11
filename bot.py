import telebot
from telebot import types
import config
import db_manager
import os
from datetime import datetime, timedelta
import sqlite3
import re
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

db_manager.initialize_db()
bot = telebot.TeleBot(config.BOT_TOKEN, parse_mode='HTML')
user_states = {}
ADMIN_IDS = [6647420637, 123455]  # Admin user IDs
BTC_ADDRESS = "12345678"
SUPPORT_URL = "https://t.me/Lynch1TS"
CHANNEL_URL = "https://t.me/RedTeamArchives"
RESULTS_DIR = "search_results"

PRICES = {
    "monthly": 150,
    "tri-monthly": 300,
    "lifetime": 500,
    "token_1": 1
}

if not os.path.exists(RESULTS_DIR):
    os.makedirs(RESULTS_DIR)

class ResultFileHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        filename = os.path.basename(event.src_path)
        if filename.endswith('.txt'):  # Adjust for your file type
            job_id = filename.split('.')[0]
            with sqlite3.connect(db_manager.DB_FILE) as conn:
                c = conn.cursor()
                c.execute("SELECT user_id FROM jobs WHERE job_id = ?", (job_id,))
                row = c.fetchone()
            if row:
                user_id = row[0]
                bot.send_message(user_id, "✅ Here is the result for your search:")
                with open(event.src_path, 'rb') as f:
                    bot.send_document(user_id, f)
                db_manager.delete_job(job_id, user_id)
                os.remove(event.src_path)

def start_file_watcher():
    observer = Observer()
    observer.schedule(ResultFileHandler(), RESULTS_DIR, recursive=False)
    observer.start()

def main_menu_keyboard(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    search_btn = types.InlineKeyboardButton("🔍 Search", callback_data="menu_search")
    buy_btn = types.InlineKeyboardButton("💰 Buy Plan / Tokens", callback_data="menu_buy")
    support_btn = types.InlineKeyboardButton("📞 Support", url=SUPPORT_URL)
    channel_btn = types.InlineKeyboardButton("📢 Channel", url=CHANNEL_URL)
    markup.add(search_btn, buy_btn, support_btn, channel_btn)
    if user_id in ADMIN_IDS:
        admin_btn = types.InlineKeyboardButton("👑 Admin Panel", callback_data="menu_admin")
        markup.add(admin_btn)
    return markup

def admin_menu_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    list_users_btn = types.InlineKeyboardButton("📋 List Users", callback_data="admin_list_users_page_0")
    ban_btn = types.InlineKeyboardButton("🚫 Ban User", callback_data="admin_ban")
    give_plan_btn = types.InlineKeyboardButton("🎁 Give Plan", callback_data="admin_give_plan")
    give_tokens_btn = types.InlineKeyboardButton("🪙 Give Tokens", callback_data="admin_give_tokens")
    back_btn = types.InlineKeyboardButton("🔙 Back", callback_data="main_menu")
    markup.add(list_users_btn, ban_btn, give_plan_btn, give_tokens_btn, back_btn)
    return markup

def user_list_keyboard(current_page, total_users, limit=5):
    markup = types.InlineKeyboardMarkup()
    total_pages = (total_users + limit - 1) // limit
    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(types.InlineKeyboardButton("⬅️ Back", callback_data=f"admin_list_users_page_{current_page-1}"))
    if current_page < total_pages - 1:
        nav_buttons.append(types.InlineKeyboardButton("Next ➡️", callback_data=f"admin_list_users_page_{current_page+1}"))
    markup.row(*nav_buttons)
    return markup

def search_menu_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    domain_btn = types.InlineKeyboardButton("🌐 Domain", callback_data="search_domain")
    email_btn = types.InlineKeyboardButton("✉️ Email", callback_data="search_email")
    phone_btn = types.InlineKeyboardButton("📱 Phone Number", callback_data="search_phone")
    name_btn = types.InlineKeyboardButton("👤 Name", callback_data="search_name")
    back_btn = types.InlineKeyboardButton("🔙 Back", callback_data="main_menu")
    markup.add(domain_btn, email_btn, phone_btn, name_btn, back_btn)
    return markup

def buy_menu_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    plans_btn = types.InlineKeyboardButton("💎 Monthly Plans", callback_data="buy_plans")
    tokens_btn = types.InlineKeyboardButton("🪙 Search Tokens", callback_data="buy_tokens")
    back_btn = types.InlineKeyboardButton("🔙 Back", callback_data="main_menu")
    markup.add(plans_btn, tokens_btn, back_btn)
    return markup

def plans_menu_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    month1_btn = types.InlineKeyboardButton(f"1 Month - ${PRICES['monthly']}", callback_data="order_plan_monthly")
    month3_btn = types.InlineKeyboardButton(f"3 Months - ${PRICES['tri-monthly']}", callback_data="order_plan_tri-monthly")
    Hawkins_btn = types.InlineKeyboardButton(f"Lifetime - ${PRICES['lifetime']}", callback_data="order_plan_lifetime")
    back_btn = types.InlineKeyboardButton("🔙 Back", callback_data="menu_buy")
    markup.add(month1_btn, month3_btn, Hawkins_btn, back_btn)
    return markup

def tokens_menu_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    token1_btn = types.InlineKeyboardButton(f"1 Search Token - ${PRICES['token_1']}", callback_data="order_token_1")
    back_btn = types.InlineKeyboardButton("🔙 Back", callback_data="menu_buy")
    markup.add(token1_btn, back_btn)
    return markup

def admin_ban_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    back_btn = types.InlineKeyboardButton("🔙 Back", callback_data="menu_admin")
    markup.add(back_btn)
    return markup

def admin_give_plan_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    monthly_btn = types.InlineKeyboardButton("Monthly", callback_data="give_plan_monthly")
    tri_monthly_btn = types.InlineKeyboardButton("Tri-monthly", callback_data="give_plan_tri-monthly")
    lifetime_btn = types.InlineKeyboardButton("Lifetime", callback_data="give_plan_lifetime")
    back_btn = types.InlineKeyboardButton("🔙 Back", callback_data="menu_admin")
    markup.add(monthly_btn, tri_monthly_btn, lifetime_btn, back_btn)
    return markup

def admin_give_tokens_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    back_btn = types.InlineKeyboardButton("🔙 Back", callback_data="menu_admin")
    markup.add(back_btn)
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    username = message.from_user.username or "N/A"
    user_data = db_manager.get_or_create_user(user_id, username)
    welcome_text = (
        "<b>Welcome to the Data Leak Search Bot!</b> 🕵️‍♂️\n\n"
        "Search for your info (email, phone, etc.) in data breaches.\n\n"
        f"You have <b>{user_data[2]} free searches</b>, resetting daily. Buy plans/tokens for more.\n\n"
        "Use the buttons below to start."
    )
    bot.send_message(user_id, welcome_text, reply_markup=main_menu_keyboard(user_id))

@bot.message_handler(func=lambda message: user_states.get(message.from_user.id, {}).get('state') == 'awaiting_search')
def handle_search_query(message):
    user_id = message.from_user.id
    if not db_manager.can_user_search(user_id):
        bot.send_message(user_id, "❌ No searches left. Buy a plan or tokens.")
        del user_states[user_id]
        return
    try:
        job_id = db_manager.add_job(user_id, message.text)
        bot.send_message(user_id, f"✅ Search for '<code>{message.text}</code>' submitted. Please wait.")
        db_manager.use_search_credit(user_id)
    except Exception as e:
        bot.send_message(user_id, "❌ Error submitting job.")
        print(f"Error in handle_search_query: {e}")
    finally:
        if user_id in user_states:
            del user_states[user_id]

@bot.message_handler(commands=['admin'])
def handle_admin_command(message):
    user_id = message.from_user.id
    if user_id in ADMIN_IDS:
        bot.send_message(user_id, "👑 Welcome to the Admin Panel.", reply_markup=admin_menu_keyboard())

@bot.message_handler(func=lambda message: user_states.get(message.from_user.id, {}).get('state') == 'awaiting_ban_user_id')
def handle_ban_user_id(message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        return
    try:
        target_user_id = int(message.text)
        db_manager.ban_user(target_user_id)
        bot.send_message(user_id, f"User {target_user_id} banned.")
    except Exception as e:
        bot.send_message(user_id, "❌ Error banning user.")
        print(f"Error in handle_ban_user_id: {e}")
    finally:
        if user_id in user_states:
            del user_states[user_id]

@bot.message_handler(func=lambda message: user_states.get(message.from_user.id, {}).get('state') == 'awaiting_give_plan_user_id')
def handle_give_plan_user_id(message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        return
    try:
        target_user_id = int(message.text)
        user_states[user_id] = {'state': 'awaiting_give_plan_type', 'target_user_id': target_user_id}
        bot.send_message(user_id, "Select plan type:", reply_markup=admin_give_plan_keyboard())
    except Exception as e:
        bot.send_message(user_id, "❌ Error processing user ID.")
        print(f"Error in handle_give_plan_user_id: {e}")

@bot.message_handler(func=lambda message: user_states.get(message.from_user.id, {}).get('state') == 'awaiting_give_tokens_user_id')
def handle_give_tokens_user_id(message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        return
    try:
        target_user_id = int(message.text)
        user_states[user_id] = {'state': 'awaiting_give_tokens_amount', 'target_user_id': target_user_id}
        bot.send_message(user_id, "Enter number of tokens to give:")
    except Exception as e:
        bot.send_message(user_id, "❌ Error processing user ID.")
        print(f"Error in handle_give_tokens_user_id: {e}")

@bot.message_handler(func=lambda message: user_states.get(message.from_user.id, {}).get('state') == 'awaiting_give_tokens_amount')
def handle_give_tokens_amount(message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        return
    try:
        target_user_id = user_states[user_id]['target_user_id']
        tokens = int(message.text)
        db_manager.give_tokens(target_user_id, tokens)
        bot.send_message(user_id, f"Added {tokens} tokens to user {target_user_id}.")
    except Exception as e:
        bot.send_message(user_id, "❌ Error adding tokens.")
        print(f"Error in handle_give_tokens_amount: {e}")
    finally:
        if user_id in user_states:
            del user_states[user_id]

@bot.callback_query_handler(func=lambda call: True)
def handle_callback_query(call):
    user_id = call.from_user.id
    data = call.data
    if data == "main_menu":
        bot.edit_message_text("Main Menu", user_id, call.message.message_id, reply_markup=main_menu_keyboard(user_id))
    elif data == "menu_admin" and user_id in ADMIN_IDS:
        bot.edit_message_text("👑 Admin Panel", user_id, call.message.message_id, reply_markup=admin_menu_keyboard())
    elif data == "menu_search":
        bot.edit_message_text("Select search type:", user_id, call.message.message_id, reply_markup=search_menu_keyboard())
    elif data == "menu_buy":
        bot.edit_message_text("Choose a purchase option:", user_id, call.message.message_id, reply_markup=buy_menu_keyboard())
    elif data == "buy_plans":
        bot.edit_message_text("Select a plan:", user_id, call.message.message_id, reply_markup=plans_menu_keyboard())
    elif data == "buy_tokens":
        bot.edit_message_text("Select tokens:", user_id, call.message.message_id, reply_markup=tokens_menu_keyboard())
    elif data.startswith("admin_list_users_page_"):
        page = int(data.split('_')[-1])
        users, total_users = db_manager.get_users_paginated(page=page)
        message_text = f"<b>Bot Users (Page {page + 1} / {(total_users + 4) // 5})</b>\n\n"
        if not users:
            message_text += "No users on this page."
        for user in users:
            expiry_str = "N/A"
            if user[4]:
                try:
                    expiry_str = datetime.strptime(user[4], '%Y-%m-%d %H:%M:%S.%f').strftime('%Y-%m-%d')
                except ValueError:
                    expiry_str = "Invalid Date"
            message_text += (
                f"👤 <code>{user[0]}</code> (@{user[1]})\n"
                f"   - Searches: {user[2]}, Plan: {user[3]}, Expires: {expiry_str}\n"
                f"   - Banned: {'Yes' if user[5] else 'No'}\n\n"
            )
        bot.edit_message_text(message_text, user_id, call.message.message_id, reply_markup=user_list_keyboard(page, total_users))
    elif data.startswith("search_"):
        user_states[user_id] = {'state': 'awaiting_search', 'type': data.split('_')[1]}
        bot.send_message(user_id, f"Please enter the <b>{data.split('_')[1]}</b> to search:")
        bot.answer_callback_query(call.id)
    elif data.startswith("order_"):
        item_key_parts = data.split('_')[1:]
        item_key = "_".join(item_key_parts)
        price = PRICES.get(item_key)
        if not price:
            bot.answer_callback_query(call.id, "Error: Item not found.", show_alert=True)
            return
        payment_text = (
            "<b>Payment Details</b>\n\n"
            f"Please pay <b>${price}</b> in BTC to:\n\n"
            f"<code>{BTC_ADDRESS}</code>\n\n"
            "⚠️ Send exact amount. Click below after paying."
        )
        markup = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("✅ I Have Paid", callback_data=f"paid_{item_key}")
        )
        bot.edit_message_text(payment_text, user_id, call.message.message_id, reply_markup=markup)
    elif data.startswith("paid_"):
        item_key = data.replace("paid_", "", 1)
        user_states[user_id] = {'state': 'awaiting_proof', 'item': item_key}
        bot.edit_message_text(
            "Send your <b>transaction hash</b> or payment screenshot.",
            user_id,
            call.message.message_id
        )
    elif data == "admin_ban":
        user_states[user_id] = {'state': 'awaiting_ban_user_id'}
        bot.edit_message_text("Enter user ID to ban:", user_id, call.message.message_id, reply_markup=admin_ban_keyboard())
    elif data == "admin_give_plan":
        user_states[user_id] = {'state': 'awaiting_give_plan_user_id'}
        bot.edit_message_text("Enter user ID to give plan:", user_id, call.message.message_id, reply_markup=admin_give_plan_keyboard())
    elif data == "admin_give_tokens":
        user_states[user_id] = {'state': 'awaiting_give_tokens_user_id'}
        bot.edit_message_text("Enter user ID to give tokens:", user_id, call.message.message_id, reply_markup=admin_give_tokens_keyboard())
    elif data.startswith("give_plan_"):
        plan_type = data.split('_')[-1]
        target_user_id = user_states[user_id]['target_user_id']
        expiry = datetime.now() + timedelta(days=30 if plan_type == "monthly" else 90 if plan_type == "tri-monthly" else 36500)
        db_manager.give_plan(target_user_id, plan_type, expiry)
        bot.edit_message_text(f"Assigned {plan_type} plan to user {target_user_id}.", user_id, call.message.message_id)
        del user_states[user_id]

@bot.message_handler(content_types=['text', 'photo'], func=lambda message: user_states.get(message.from_user.id, {}).get('state') == 'awaiting_proof')
def handle_payment_proof(message):
    user_id = message.from_user.id
    user_info = f"👤 User: @{message.from_user.username} (<code>{user_id}</code>)"
    item = user_states[user_id]['item']
    price = PRICES.get(item)
    alert_text = (
        f"🚨 <b>Payment Proof Received</b> 🚨\n\n"
        f"{user_info}\n"
        f"Ordered: <b>{item.replace('_', ' ').title()}</b> for ${price}"
    )
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, alert_text)
            bot.forward_message(admin_id, user_id, message.message_id)
        except Exception as e:
            print(f"Failed to forward proof to admin {admin_id}: {e}")
    bot.send_message(user_id, "✅ Payment proof sent to admins. Awaiting verification.")
    del user_states[user_id]

start_file_watcher()
print("Bot is running...")
bot.polling()
