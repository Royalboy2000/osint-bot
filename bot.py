import asyncio
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters
)
from telegram.error import Forbidden, BadRequest, NetworkError, TimedOut
from telegram.constants import ParseMode
import uuid
from datetime import datetime, timedelta
import os
import time
import html
import json

import config
from db_manager import (
    get_or_create_user,
    decrement_user_free_searches,
    update_user_free_searches,
    DEFAULT_FREE_SEARCHES,
    ban_user,
    unban_user,
    grant_subscription,
    grant_tokens,
    decrement_tokens,
    get_users_for_view,
    create_search_job,
    get_completed_jobs_for_user,
    mark_job_delivered,
    get_all_undelivered_jobs,
    get_job_details # Added
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

SELECTING_CATEGORY, TYPING_QUERY = range(2)
SELECTING_BUY_OPTION, SELECTING_PLAN, SELECTING_TOKEN_AMOUNT, AWAITING_PAYMENT_CONFIRMATION, AWAITING_PROOF = range(10, 15)
AWAITING_USER_ID_TO_BAN, CONFIRM_BAN, AWAITING_USER_ID_TO_UNBAN, CONFIRM_UNBAN = range(20, 24)
ADMIN_GRANT_USER_ID, ADMIN_GRANT_TYPE, ADMIN_GRANT_SUB_PLAN, ADMIN_GRANT_TOKEN_AMOUNT, ADMIN_CONFIRM_GRANT = range(30, 35)

USER_PAGE_SIZE = 5
RATE_LIMIT_SECONDS = 5 # Moved here for global access if needed by multiple functions

def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS

# --- Keyboards ---
def get_main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("🔍 Search", callback_data='main_search'),
            InlineKeyboardButton("💰 Buy", callback_data='main_buy'),
        ],
        [
            InlineKeyboardButton("📞 Support", url=config.SUPPORT_URL),
            InlineKeyboardButton("📢 Channel", url=config.CHANNEL_URL),
        ]
    ]
    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data='admin_panel_main')])
    return InlineKeyboardMarkup(keyboard)

def get_search_categories_keyboard():
    keyboard = [
        [InlineKeyboardButton("🌐 Domains", callback_data='search_category_domains')],
        [InlineKeyboardButton("📧 Emails", callback_data='search_category_emails')],
        [InlineKeyboardButton("📞 Phone Numbers", callback_data='search_category_phone')],
        [InlineKeyboardButton("👤 Names", callback_data='search_category_name')],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data='back_to_main_search_cats')],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_follow_up_keyboard(job_id: str, category: str, query_text: str) -> InlineKeyboardMarkup:
    # Sanitize category for callback data if needed, e.g. lowercasing, replacing spaces
    category_code = category.lower().replace(' ', '_')
    keyboard = [
        [InlineKeyboardButton(f"🔎 Search '{query_text}' again", callback_data=f"search_again_{job_id}")],
        [InlineKeyboardButton(f"✨ New Search in '{category}'", callback_data=f"new_search_cat_{category_code}")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="show_main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

# (Other keyboard functions remain the same)
def get_buy_options_keyboard():
    keyboard = [
        [InlineKeyboardButton("📅 Monthly Plans", callback_data='buy_monthly_plans')],
        [InlineKeyboardButton("🪙 Buy Tokens", callback_data='buy_tokens')],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data='back_to_main_from_buy')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_monthly_plans_keyboard():
    keyboard = [
        [InlineKeyboardButton("1 Month - $150", callback_data='buy_plan_1month_150')],
        [InlineKeyboardButton("3 Months - $300", callback_data='buy_plan_3months_300')],
        [InlineKeyboardButton("Lifetime - $500", callback_data='buy_plan_lifetime_500')],
        [InlineKeyboardButton("⬅️ Back", callback_data='back_to_buy_options')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_token_options_keyboard():
    keyboard = [
        [InlineKeyboardButton("1 Token (1 Search) - $1", callback_data='buy_token_1_1')],
        [InlineKeyboardButton("⬅️ Back", callback_data='back_to_buy_options')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_payment_confirmation_keyboard():
    keyboard = [
        [InlineKeyboardButton("✅ Yes, I have paid", callback_data='payment_paid_yes')],
        [InlineKeyboardButton("❌ No, I haven't paid yet / Cancel", callback_data='payment_paid_no')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_panel_keyboard():
    keyboard = [
        [InlineKeyboardButton("🚫 Ban User", callback_data='admin_ban_user_start')],
        [InlineKeyboardButton("✅ Unban User", callback_data='admin_unban_user_start')],
        [InlineKeyboardButton("🎁 Grant Access", callback_data='admin_grant_access_start')],
        [InlineKeyboardButton("👥 View Users", callback_data='admin_view_users_page_1')],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data='back_to_main_from_admin_panel')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_confirmation_keyboard(action_prefix: str, item_info: str = ""):
    yes_callback = f'{action_prefix}_yes'
    no_callback = f'{action_prefix}_no'
    if item_info:
        yes_callback += f"_{item_info}"
    keyboard = [
        [
            InlineKeyboardButton(f"Yes, {action_prefix.replace('_',' ').title()}", callback_data=yes_callback),
            InlineKeyboardButton(f"No, Cancel", callback_data=no_callback)
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_grant_type_keyboard():
    keyboard = [
        [InlineKeyboardButton("📜 Subscription", callback_data='grant_type_subscription')],
        [InlineKeyboardButton("🪙 Tokens", callback_data='grant_type_tokens')],
        [InlineKeyboardButton("⬅️ Cancel to Admin Panel", callback_data='grant_cancel_to_admin_panel')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_grant_subscription_plan_keyboard():
    keyboard = [
        [InlineKeyboardButton("1 Month", callback_data='grant_plan_1_month')],
        [InlineKeyboardButton("3 Months", callback_data='grant_plan_3_months')],
        [InlineKeyboardButton("Lifetime", callback_data='grant_plan_lifetime')],
        [InlineKeyboardButton("⬅️ Back to Grant Type", callback_data='grant_back_to_type')]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Start Command & Generic Reset ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str = None) -> None:
    user = update.effective_user
    user_id = update.effective_user.id
    user_db_data = get_or_create_user(user_id, user.username, user.first_name)

    if user_db_data and user_db_data.get('is_banned'):
        ban_message = "You are banned from using this bot."
        if update.message: await update.message.reply_text(ban_message)
        elif update.callback_query:
            await update.callback_query.answer(ban_message, show_alert=True)
            try: await update.callback_query.edit_message_text(ban_message)
            except Exception as e: logger.debug(f"Failed to edit message for banned user in start: {e}"); await context.bot.send_message(chat_id=user_id, text=ban_message)
        return

    now = datetime.now()
    last_reset_time_str = user_db_data.get('last_free_search_reset_time')
    last_reset_time = datetime.fromisoformat(last_reset_time_str) if isinstance(last_reset_time_str, str) else (last_reset_time_str or (now - timedelta(days=1)))

    if last_reset_time < (now - timedelta(hours=config.FREE_SEARCH_RESET_HOURS)):
        update_user_free_searches(user_id, DEFAULT_FREE_SEARCHES, now)
        user_db_data = get_or_create_user(user_id)
        if update.callback_query: await context.bot.send_message(chat_id=user_id, text="Your daily free searches have been reset!")

    searches_left_display = user_db_data.get('free_searches_left', 0)
    tokens_left_display = user_db_data.get('tokens_left', 0)
    sub_type = user_db_data.get('subscription_type', 'None')
    sub_expiry_str = user_db_data.get('subscription_expiry_date')
    sub_status = "None"
    if sub_type and sub_type != 'None':
        if sub_type == 'lifetime': sub_status = "Lifetime"
        elif sub_expiry_str:
            sub_expiry_dt = datetime.fromisoformat(sub_expiry_str) if isinstance(sub_expiry_str, str) else sub_expiry_str
            if sub_expiry_dt and sub_expiry_dt > now: sub_status = f"{sub_type.replace('_', ' ').title()} (Expires: {sub_expiry_dt.strftime('%Y-%m-%d')})"
            else: sub_status = f"Expired {sub_type.replace('_', ' ').title()}"

    final_welcome_text = message_text if message_text else f"👋 Hello {user.first_name}!\n\nI am your advanced search assistant."
    status_text = (
        f"\n\nSubscription: <b>{sub_status}</b>\nTokens: <b>{tokens_left_display}</b>\n"
        f"Free Searches Today: <b>{searches_left_display}</b>\n\nUse the buttons below to get started."
    )
    full_message = final_welcome_text + status_text

    if update.callback_query:
        try: await update.callback_query.edit_message_text(full_message, reply_markup=get_main_menu_keyboard(user_id), parse_mode='HTML')
        except Exception as e:
            logger.error(f"Error editing message in start callback: {e}")
            await context.bot.send_message(chat_id=user_id, text=full_message, reply_markup=get_main_menu_keyboard(user_id), parse_mode='HTML')
    else:
        await update.message.reply_text(full_message, reply_markup=get_main_menu_keyboard(user_id), parse_mode='HTML')

async def start_again_in_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await start(update, context, message_text="Returning to main menu.")
    context.user_data.clear(); context.chat_data.clear()
    return ConversationHandler.END

# --- Search Conversation Handlers ---
async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # (Rate limit and ban check logic as before)
    query = update.callback_query
    user_id = query.from_user.id
    current_time = time.time()
    last_search_time = context.user_data.get('last_search_init_time', 0)
    if (current_time - last_search_time) < RATE_LIMIT_SECONDS:
        await query.answer(f"Please wait {int(RATE_LIMIT_SECONDS - (current_time - last_search_time))} more seconds.", show_alert=True)
        return ConversationHandler.END
    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name)
    if user_db_data and user_db_data.get('is_banned'):
        await query.answer("You are banned.", show_alert=True)
        await query.edit_message_text("You are banned from using this bot.")
        return ConversationHandler.END
    await query.answer()
    context.user_data['last_search_init_time'] = current_time
    await query.edit_message_text(text="👇 Please select a search category:", reply_markup=get_search_categories_keyboard())
    return SELECTING_CATEGORY

async def select_search_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # (Logic for checking credits and prompting for query text as before)
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name)
    now = datetime.now()
    sub_type = user_db_data.get('subscription_type')
    sub_expiry_str = user_db_data.get('subscription_expiry_date')
    has_active_sub = False
    if sub_type and sub_type != 'None':
        if sub_type == 'lifetime': has_active_sub = True
        elif sub_expiry_str:
            sub_expiry_dt = datetime.fromisoformat(sub_expiry_str) if isinstance(sub_expiry_str, str) else sub_expiry_str
            if sub_expiry_dt and sub_expiry_dt > now: has_active_sub = True
    search_type_message = ""; can_search = False; search_credit_info = ""
    if has_active_sub:
        search_type_message = "Using active subscription."; can_search = True
    elif user_db_data.get('tokens_left', 0) > 0:
        if decrement_tokens(user_id):
            user_db_data = get_or_create_user(user_id)
            search_type_message = f"Using a token. Tokens remaining: {user_db_data.get('tokens_left', 0)}."; can_search = True
        else: search_type_message = "Tried to use a token, but failed."
    else:
        last_reset_time_str = user_db_data.get('last_free_search_reset_time')
        last_reset_time = datetime.fromisoformat(last_reset_time_str) if isinstance(last_reset_time_str, str) else (last_reset_time_str or (now - timedelta(days=1)))
        if last_reset_time < (now - timedelta(hours=config.FREE_SEARCH_RESET_HOURS)):
            update_user_free_searches(user_id, DEFAULT_FREE_SEARCHES, now)
            user_db_data = get_or_create_user(user_id)
            await query.message.reply_text("Your daily free searches have been reset!", quote=False)
        free_searches_left = user_db_data.get('free_searches_left', 0)
        if free_searches_left > 0:
            search_type_message = f"Using a free search."; search_credit_info = f"Free searches left: {free_searches_left}"
            context.user_data['using_free_search'] = True; can_search = True
        else: search_type_message = "No active subscription, tokens, or free searches available."
    if can_search:
        category_code = query.data.replace('search_category_', '')
        category_map = {'domains': 'Domain', 'emails': 'Email', 'phone': 'Phone Number', 'name': 'Name'}
        category_name = category_map.get(category_code, 'Unknown Category')
        context.user_data['search_category'] = category_name
        prompt_message = f"{search_type_message}\n"
        if search_credit_info: prompt_message += f"{search_credit_info}\n"
        prompt_message += f"✍️ Please enter the {category_name} you want to search for:"
        await query.edit_message_text(text=prompt_message)
        return TYPING_QUERY
    else:
        await query.edit_message_text(text=f"😔 {search_type_message}\n\nPlease /buy a subscription or tokens.", reply_markup=get_main_menu_keyboard(user_id))
        context.user_data.clear(); return ConversationHandler.END

async def handle_search_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # (Logic for creating job and confirming to user as before)
    query_text = update.message.text
    category = context.user_data.get('search_category', 'Unknown Category')
    user_id = update.message.from_user.id
    if context.user_data.get('using_free_search'):
        if not decrement_user_free_searches(user_id):
            await update.message.reply_text("⚠️ An issue occurred with your free search credits.", reply_markup=get_main_menu_keyboard(user_id))
            context.user_data.clear(); return ConversationHandler.END
        context.user_data.pop('using_free_search', None)
    job_id = str(uuid.uuid4())[:8]
    if not create_search_job(job_id, user_id, category, query_text):
        await update.message.reply_text("⚠️ Failed to queue your search.", reply_markup=get_main_menu_keyboard(user_id))
        context.user_data.clear(); return ConversationHandler.END
    reply_timeout_from_config = getattr(config, 'REPLY_TIMEOUT', 10) # Use actual REPLY_TIMEOUT from user_client via config
    confirmation_message = (
        f"✅ Your search for {category}: \"{query_text}\" (Job ID: `{job_id}`) has been queued.\n"
        f"The external bot has up to {reply_timeout_from_config} seconds to respond.\n"
        "Results will be sent automatically when ready."
    )
    await update.message.reply_text(text=confirmation_message, parse_mode='MarkdownV2')
    await context.bot.send_message(chat_id=user_id, text="👋 Main Menu:", reply_markup=get_main_menu_keyboard(user_id))
    context.user_data.clear(); return ConversationHandler.END

async def back_to_main_menu_from_search_cats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await start(update, context); context.user_data.clear(); return ConversationHandler.END

# --- Buy Conversation Handlers ---
# (Largely remain the same, focusing on edit_message_text where appropriate)
async def buy_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = query.from_user.id
    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name)
    if user_db_data and user_db_data.get('is_banned'):
        await query.answer("You are banned.", show_alert=True); await query.edit_message_text("You are banned.")
        return ConversationHandler.END
    await query.answer()
    await query.edit_message_text("🛍️ Welcome to the Shop!", reply_markup=get_buy_options_keyboard())
    return SELECTING_BUY_OPTION
# (Other buy handlers like select_buy_option, select_plan_or_token, payment_confirmation_prompt,
#  handle_payment_proof, back_to_buy_options, back_to_main_from_buy adapted similarly for edit_message_text)
async def select_buy_option(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    if query.data == 'buy_monthly_plans':
        await query.edit_message_text(text="🗓️ Select a Monthly Plan:", reply_markup=get_monthly_plans_keyboard()); return SELECTING_PLAN
    elif query.data == 'buy_tokens':
        await query.edit_message_text(text="🪙 Select Token Amount:", reply_markup=get_token_options_keyboard()); return SELECTING_TOKEN_AMOUNT
    await start(update, context); return ConversationHandler.END
async def select_plan_or_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer(); parts = query.data.split('_')
    item_type, item_details, item_price = parts[1], parts[2], parts[3]
    name_map = {'plan': {'1month': "1 Month Plan", '3months': "3 Months Plan", 'lifetime': "Lifetime Plan"}, 'token': {parts[2]: f"{parts[2]} Token(s)"}}
    full_item_name = name_map[item_type][item_details]
    context.user_data.update({'purchase_item_name': full_item_name, 'purchase_item_price': item_price})
    payment_msg = f"Selected: <b>{full_item_name}</b> for <b>${item_price}</b>.\n\nSend <code>${item_price}</code> BTC to:\n<code>{config.BTC_ADDRESS}</code>\n\nThen click '<b>Yes, I have paid</b>'."
    await query.edit_message_text(text=payment_msg, reply_markup=get_payment_confirmation_keyboard(), parse_mode='HTML')
    return AWAITING_PAYMENT_CONFIRMATION
async def payment_confirmation_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer(); user_id = query.from_user.id
    if query.data == 'payment_paid_yes':
        item_name = context.user_data.get('purchase_item_name', 'Your item')
        await query.edit_message_text(f"To complete purchase of <b>{item_name}</b>, send transaction ID/screenshot.", parse_mode='HTML'); return AWAITING_PROOF
    elif query.data == 'payment_paid_no':
        await query.edit_message_text("Order pending. Returning to main menu.")
        await start(update, context); context.user_data.clear(); return ConversationHandler.END
async def handle_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # Remains as MessageHandler
    user = update.message.from_user; user_id = user.id; username = user.username or user.first_name
    item = context.user_data.get('purchase_item_name', 'N/A'); price = context.user_data.get('purchase_item_price', 'N/A')
    admin_text = f"💰 Payment Proof!\nUser: @{username} ({user_id})\nItem: {item} (${price})\nVerify message above/below."
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.forward_message(admin_id, user_id, update.message.message_id)
            await context.bot.send_message(admin_id, admin_text)
        except Exception as e: logger.error(f"Admin forward failed for {admin_id}: {e}")
    await update.message.reply_text("Proof sent for verification. Returning to main menu.")
    await context.bot.send_message(chat_id=user_id, text="👋 Main Menu:", reply_markup=get_main_menu_keyboard(user_id))
    context.user_data.clear(); return ConversationHandler.END
async def back_to_buy_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.edit_message_text("🛍️ Shop Options:", reply_markup=get_buy_options_keyboard()); return SELECTING_BUY_OPTION
async def back_to_main_from_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await start(update, context); context.user_data.clear(); return ConversationHandler.END

# --- Admin Panel & Related Handlers (adapted for edit_message_text) ---
async def admin_panel_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query; user_id = query.from_user.id
    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name)
    if user_db_data and user_db_data.get('is_banned'):
        await query.answer("You are banned.", show_alert=True); await query.edit_message_text("You are banned.")
        return
    await query.answer()
    if not is_admin(user_id):
        await query.edit_message_text("⚠️ Access Denied.", reply_markup=get_main_menu_keyboard(user_id)); return
    await query.edit_message_text("👑 Admin Panel:", reply_markup=get_admin_panel_keyboard())

async def back_to_main_from_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await start(update, context); return ConversationHandler.END

async def admin_view_users_noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()

async def admin_view_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Logic to fetch and format users, then edit_message_text as before)
    query = update.callback_query; await query.answer()
    if not is_admin(query.from_user.id):
        await query.edit_message_text("Access Denied.", reply_markup=get_main_menu_keyboard(query.from_user.id)); return
    page = int(query.data.split('_')[-1]) if query.data.startswith('admin_view_users_page_') else 1
    users, total = get_users_for_view(page=page, page_size=USER_PAGE_SIZE)
    text = f"👥 <b>Users - Page {page}</b>\n\n" + ("".join([
        f"👤<code>{u['user_id']}</code> (@{u.get('username','N/A')})\n"
        f"  F:{u['free_searches_left']} T:{u['tokens_left']} S:{u.get('subscription_type','N/A')}\n"
        f"  Exp:{u.get('subscription_expiry_date','N/A')[:10] if u.get('subscription_expiry_date') else 'N/A'} Banned:{'Y' if u.get('is_banned') else 'N'}\n\n"
        for u in users]) if users else "No users on this page.")
    # (Pagination keyboard logic as before)
    kb = []; pr = []; tp = (total + USER_PAGE_SIZE -1) // USER_PAGE_SIZE if total > 0 else 1
    if page>1: pr.append(InlineKeyboardButton("⬅️",callback_data=f"admin_view_users_page_{page-1}"))
    if total>0: pr.append(InlineKeyboardButton(f"{page}/{tp}",callback_data="admin_view_users_noop"))
    if page<tp: pr.append(InlineKeyboardButton("➡️",callback_data=f"admin_view_users_page_{page+1}"))
    if pr: kb.append(pr)
    kb.append([InlineKeyboardButton("⬅️ Admin Panel", callback_data='admin_panel_main')])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')


async def admin_ban_user_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.edit_message_text("🚫 Enter User ID to ban:"); return AWAITING_USER_ID_TO_BAN
async def received_user_id_for_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # MessageHandler
    try: uid = int(update.message.text); user = get_or_create_user(uid); context.chat_data['user_id_to_modify'] = uid
    except ValueError: await update.message.reply_text("Invalid ID."); return AWAITING_USER_ID_TO_BAN
    if user: uname = f"(@{user['username']})" if user.get('username') else ""; stat = "Banned" if user.get('is_banned') else "Not Banned"
    await update.message.reply_text(f"Ban {uid} {uname} (Status: {stat})?", reply_markup=get_confirmation_keyboard("ban")); return CONFIRM_BAN
async def confirm_ban_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # Callback
    query = update.callback_query; await query.answer(); uid = context.chat_data.get('user_id_to_modify'); msg = ""
    if query.data == 'ban_yes': msg = f"✅ User {uid} banned." if ban_user(uid) else f"⚠️ Failed to ban {uid}."
    elif query.data == 'ban_no': msg = "🚫 Ban cancelled."
    await query.edit_message_text(msg, reply_markup=get_admin_panel_keyboard()); context.chat_data.clear(); return ConversationHandler.END
async def admin_unban_user_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.edit_message_text("✅ Enter User ID to unban:"); return AWAITING_USER_ID_TO_UNBAN
async def received_user_id_for_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # MessageHandler
    try: uid = int(update.message.text); user = get_or_create_user(uid); context.chat_data['user_id_to_modify'] = uid
    except ValueError: await update.message.reply_text("Invalid ID."); return AWAITING_USER_ID_TO_UNBAN
    if user: uname = f"(@{user['username']})" if user.get('username') else ""; stat = "Banned" if user.get('is_banned') else "Not Banned"
    await update.message.reply_text(f"Unban {uid} {uname} (Status: {stat})?", reply_markup=get_confirmation_keyboard("unban")); return CONFIRM_UNBAN
async def confirm_unban_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # Callback
    query = update.callback_query; await query.answer(); uid = context.chat_data.get('user_id_to_modify'); msg = ""
    if query.data == 'unban_yes': msg = f"✅ User {uid} unbanned." if unban_user(uid) else f"⚠️ Failed to unban {uid}."
    elif query.data == 'unban_no': msg = "🚫 Unban cancelled."
    await query.edit_message_text(msg, reply_markup=get_admin_panel_keyboard()); context.chat_data.clear(); return ConversationHandler.END
async def admin_grant_access_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.edit_message_text("🎁 Enter User ID to grant access:"); return ADMIN_GRANT_USER_ID
async def received_user_id_for_grant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # MessageHandler
    try: uid = int(update.message.text); user = get_or_create_user(uid)
    except ValueError: await update.message.reply_text("Invalid ID."); return ADMIN_GRANT_USER_ID
    if not user: await update.message.reply_text(f"User {uid} not found/created."); return ConversationHandler.END
    context.chat_data['grant_user_id'] = uid; uname = f"(@{user.get('username')})" if user.get('username') else ""
    await update.message.reply_text(f"Grant access to {uid} {uname}.\nSelect type:", reply_markup=get_grant_type_keyboard()); return ADMIN_GRANT_TYPE
async def grant_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # Callback
    query = update.callback_query; await query.answer(); context.chat_data['grant_type'] = query.data
    if query.data == 'grant_type_subscription':
        await query.edit_message_text("📜 Select plan:", reply_markup=get_grant_subscription_plan_keyboard()); return ADMIN_GRANT_SUB_PLAN
    elif query.data == 'grant_type_tokens':
        await query.edit_message_text("🪙 Enter token amount:"); return ADMIN_GRANT_TOKEN_AMOUNT
    return ConversationHandler.END
async def grant_subscription_plan_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # Callback
    query = update.callback_query; await query.answer(); plan = query.data.replace('grant_plan_', '')
    context.chat_data['grant_plan_type'] = plan; uid = context.chat_data['grant_user_id']
    await query.edit_message_text(f"Grant <b>{plan.replace('_',' ').title()}</b> to <b>{uid}</b>?", reply_markup=get_confirmation_keyboard("grant_sub"), parse_mode='HTML'); return ADMIN_CONFIRM_GRANT
async def grant_token_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # MessageHandler
    try: amt = int(update.message.text); assert amt > 0
    except (ValueError, AssertionError): await update.message.reply_text("Invalid amount."); return ADMIN_GRANT_TOKEN_AMOUNT
    context.chat_data['grant_token_amount'] = amt; uid = context.chat_data['grant_user_id']
    await update.message.reply_text(f"Grant <b>{amt}</b> tokens to <b>{uid}</b>?", reply_markup=get_confirmation_keyboard("grant_tokens"), parse_mode='HTML'); return ADMIN_CONFIRM_GRANT
async def confirm_grant_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # Callback
    query = update.callback_query; await query.answer(); uid = context.chat_data.get('grant_user_id'); gtype = context.chat_data.get('grant_type'); msg = ""
    if query.data.startswith('grant_sub_yes') and gtype == 'grant_type_subscription':
        plan = context.chat_data.get('grant_plan_type')
        msg = f"✅ Sub ({plan}) granted to {uid}." if grant_subscription(uid, plan) else f"⚠️ Failed sub grant for {uid}."
    elif query.data.startswith('grant_tokens_yes') and gtype == 'grant_type_tokens':
        amt = context.chat_data.get('grant_token_amount')
        msg = f"✅ {amt} tokens granted to {uid}." if grant_tokens(uid, amt) else f"⚠️ Failed token grant for {uid}."
    elif query.data.endswith('_no'): msg = "🚫 Grant cancelled."
    else: msg = "Unknown grant confirmation."
    await query.edit_message_text(msg, reply_markup=get_admin_panel_keyboard()); context.chat_data.clear(); return ConversationHandler.END
async def grant_back_to_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # Callback
    query = update.callback_query; await query.answer(); uid = context.chat_data['grant_user_id']; user = get_or_create_user(uid)
    uname = f"(@{user.get('username')})" if user and user.get('username') else ""
    await query.edit_message_text(f"Grant access to {uid} {uname}.\nSelect type:", reply_markup=get_grant_type_keyboard()); return ADMIN_GRANT_TYPE
async def cancel_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # Command or Callback
    uid = update.effective_user.id; text = "Admin action cancelled. Returning to Admin Panel."; markup = get_admin_panel_keyboard()
    if not is_admin(uid):
        if update.message: await update.message.reply_text("Not admin."); return ConversationHandler.END
        if update.callback_query: await update.callback_query.answer("Not admin.", show_alert=True); return ConversationHandler.END
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=markup)
    else: await update.message.reply_text(text, reply_markup=markup) # From /cancel_admin_action command
    context.chat_data.clear(); context.user_data.clear(); return ConversationHandler.END

# --- Result Delivery & Background Job ---
# (deliver_results_for_user and deliver_results_background_job remain the same)
async def deliver_results_for_user(user_id: int, jobs_to_process: list[dict], context: ContextTypes.DEFAULT_TYPE) -> None:
    if not jobs_to_process: logger.info(f"deliver_results_for_user called for user {user_id} with no jobs."); return
    logger.info(f"Attempting to deliver {len(jobs_to_process)} job results to user {user_id}.")
    for job in jobs_to_process:
        job_id, category, query_text, status = job['job_id'], job['search_category'], job['query_text'], job['status']
        try:
            if status == 'completed':
                path = job.get('result_file_path')
                if path and os.path.exists(path):
                    cap = f"✅ Results for '{category}' search: '{query_text}'\nJob ID: <code>{job_id}</code>"
                    with open(path, 'rb') as f: await context.bot.send_document(user_id, f, filename=os.path.basename(path), caption=cap, parse_mode='HTML')
                    logger.info(f"Delivered job {job_id} to user {user_id}.")
                else:
                    logger.error(f"File missing for job {job_id} (Path: {path}). Notifying user {user_id}.")
                    await context.bot.send_message(user_id, f"⚠️ Error Job ID <code>{job_id}</code>: Result file missing. Contact support.", parse_mode='HTML')
            elif status == 'failed':
                err_msg = job.get('error_message', 'Unknown error')
                logger.info(f"Notifying user {user_id} of failed job {job_id}: {err_msg}")
                await context.bot.send_message(user_id, f"❌ Search for '{category}': '{query_text}' (Job ID: <code>{job_id}</code>) failed. Reason: {err_msg}", parse_mode='HTML')
            mark_job_delivered(job_id)
        except Forbidden: logger.warning(f"User {user_id} blocked bot. Job {job_id} marked delivered."); mark_job_delivered(job_id)
        except BadRequest as e: logger.error(f"BadRequest job {job_id} for user {user_id}: {e}. Marked delivered."); mark_job_delivered(job_id)
        except Exception as e: logger.error(f"Error processing job {job_id} for user {user_id}: {e}", exc_info=True); mark_job_delivered(job_id)
async def deliver_results_background_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Background job: Checking undelivered jobs...")
    jobs = get_all_undelivered_jobs()
    if not jobs: logger.info("Background job: No jobs for delivery."); return
    logger.info(f"Background job: Found {len(jobs)} undelivered jobs.")
    user_map = {}; [user_map.setdefault(j['user_id'], []).append(j) for j in jobs]
    for uid, u_jobs in user_map.items():
        logger.info(f"Background job: Processing {len(u_jobs)} for user {uid}.")
        udata = get_or_create_user(uid)
        if udata and udata.get('is_banned'):
            logger.warning(f"User {uid} banned. Skipping delivery for {len(u_jobs)} jobs, marking delivered.")
            [mark_job_delivered(j['job_id']) for j in u_jobs]; continue
        await deliver_results_for_user(uid, u_jobs, context)

# --- Global Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    logger.error(f"Update: {json.dumps(update_str, indent=2, ensure_ascii=False)} caused error: {context.error}")
    user_msg = "Unexpected error. Admin notified."
    if isinstance(context.error, TimedOut): user_msg = "Bot timed out. Try again."
    elif isinstance(context.error, NetworkError): user_msg = "Network error. Check connection and try again."
    if isinstance(update, Update) and update.effective_chat:
        try: await context.bot.send_message(chat_id=update.effective_chat.id, text=user_msg)
        except Forbidden: logger.error(f"Cannot send error to chat {update.effective_chat.id}: Bot blocked.")
        except Exception as e: logger.error(f"Cannot send error to chat {update.effective_chat.id}: {e}")

# --- Main Application Setup ---
def main() -> None:
    application = Application.builder().token(config.BOT_TOKEN).build()
    application.add_error_handler(error_handler)
    application.add_handler(CommandHandler("start", start))
    # Removed /myresults handler
    job_queue = application.job_queue
    job_queue.run_repeating(deliver_results_background_job, interval=30, first=10, name='result_delivery_job')
    logger.info("Background result delivery job scheduled.")

    # Follow-up action handlers (standalone)
    application.add_handler(CallbackQueryHandler(handle_search_again, pattern=r'^search_again_'))
    application.add_handler(CallbackQueryHandler(handle_show_main_menu, pattern=r'^show_main_menu$'))

    # Search Conversation
    search_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(search_start, pattern='^main_search$'),
            CallbackQueryHandler(handle_new_search_in_category, pattern=r'^new_search_cat_') # New entry point
        ],
        states={
            SELECTING_CATEGORY: [CallbackQueryHandler(select_search_category, pattern='^search_category_')],
            TYPING_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_query)],
        },
        fallbacks=[
            CallbackQueryHandler(back_to_main_menu_from_search_cats, pattern='^back_to_main_search_cats$'),
            CommandHandler('start', start_again_in_conversation)
        ], per_message=False
    )
    # (Other conversation handlers: buy, ban, unban, grant_access remain the same)
    buy_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(buy_start, pattern='^main_buy$')],
        states={
            SELECTING_BUY_OPTION: [CallbackQueryHandler(select_buy_option, pattern='^(buy_monthly_plans|buy_tokens)$')],
            SELECTING_PLAN: [CallbackQueryHandler(select_plan_or_token, pattern='^buy_plan_'), CallbackQueryHandler(back_to_buy_options, pattern='^back_to_buy_options$')],
            SELECTING_TOKEN_AMOUNT: [CallbackQueryHandler(select_plan_or_token, pattern='^buy_token_'), CallbackQueryHandler(back_to_buy_options, pattern='^back_to_buy_options$')],
            AWAITING_PAYMENT_CONFIRMATION: [CallbackQueryHandler(payment_confirmation_prompt, pattern='^(payment_paid_yes|payment_paid_no)$')],
            AWAITING_PROOF: [MessageHandler(filters.TEXT | filters.PHOTO & ~filters.COMMAND, handle_payment_proof)]
        },
        fallbacks=[CallbackQueryHandler(back_to_main_from_buy, pattern='^back_to_main_from_buy$'), CommandHandler('start', start_again_in_conversation)], per_message=False
    )
    ban_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_ban_user_start_callback, pattern='^admin_ban_user_start$')],
        states={
            AWAITING_USER_ID_TO_BAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_user_id_for_ban)],
            CONFIRM_BAN: [CallbackQueryHandler(confirm_ban_action, pattern='^(ban_yes|ban_no)$')]
        },
        fallbacks=[CommandHandler('start', start_again_in_conversation), CommandHandler('cancel_admin_action', cancel_admin_action), CallbackQueryHandler(admin_panel_main_callback, pattern='^admin_panel_main$') ], map_to_parent={ConversationHandler.END: -1}, per_message=False
    )
    unban_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_unban_user_start_callback, pattern='^admin_unban_user_start$')],
        states={
            AWAITING_USER_ID_TO_UNBAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_user_id_for_unban)],
            CONFIRM_UNBAN: [CallbackQueryHandler(confirm_unban_action, pattern='^(unban_yes|unban_no)$')]
        },
        fallbacks=[CommandHandler('start', start_again_in_conversation), CommandHandler('cancel_admin_action', cancel_admin_action), CallbackQueryHandler(admin_panel_main_callback, pattern='^admin_panel_main$')], map_to_parent={ConversationHandler.END: -1}, per_message=False
    )
    grant_access_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_grant_access_start_callback, pattern='^admin_grant_access_start$')],
        states={
            ADMIN_GRANT_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_user_id_for_grant)],
            ADMIN_GRANT_TYPE: [CallbackQueryHandler(grant_type_selected, pattern='^grant_type_')],
            ADMIN_GRANT_SUB_PLAN: [CallbackQueryHandler(grant_subscription_plan_selected, pattern='^grant_plan_'), CallbackQueryHandler(grant_back_to_type_selected, pattern='^grant_back_to_type$') ],
            ADMIN_GRANT_TOKEN_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, grant_token_amount_received)],
            ADMIN_CONFIRM_GRANT: [CallbackQueryHandler(confirm_grant_action, pattern='^(grant_sub_yes|grant_sub_no|grant_tokens_yes|grant_tokens_no)$')]
        },
        fallbacks=[CommandHandler('start', start_again_in_conversation), CommandHandler('cancel_admin_action', cancel_admin_action), CallbackQueryHandler(admin_panel_main_callback, pattern='^grant_cancel_to_admin_panel$'), CallbackQueryHandler(back_to_main_from_admin_panel, pattern='^back_to_main_from_admin_panel$')], map_to_parent={ConversationHandler.END: -1}, per_message=False
    )

    application.add_handler(search_conv_handler)
    application.add_handler(buy_conv_handler)
    application.add_handler(CallbackQueryHandler(admin_panel_main_callback, pattern='^admin_panel_main$'))
    application.add_handler(CallbackQueryHandler(back_to_main_from_admin_panel, pattern='^back_to_main_from_admin_panel$'))
    application.add_handler(CallbackQueryHandler(admin_view_users_callback, pattern=r'^admin_view_users_page_'))
    application.add_handler(CallbackQueryHandler(admin_view_users_noop_callback, pattern=r'^admin_view_users_noop$'))
    application.add_handler(ban_conv_handler)
    application.add_handler(unban_conv_handler)
    application.add_handler(grant_access_conv_handler)

    logger.info("Bot starting...")
    application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())

# --- Follow-up Action Handlers (New) ---
async def handle_search_again(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    job_id_to_repeat = query.data.replace("search_again_", "")
    original_job = get_job_details(job_id_to_repeat)

    if not original_job:
        await query.edit_message_text("Error: Could not find details of the original job. Please start a new search.", reply_markup=get_main_menu_keyboard(user_id))
        return ConversationHandler.END # End if part of a conversation, or just return if standalone

    category = original_job['search_category']
    query_text = original_job['query_text']
    context.user_data.clear() # Clear any previous user_data from other interactions
    context.user_data['search_category'] = category
    context.user_data['current_query_text'] = query_text # Store for confirmation

    # --- Re-apply search initiation logic (Rate limit, Ban check, Credit check) ---
    current_time = time.time()
    last_search_time = context.user_data.get('last_search_init_time', 0)
    if (current_time - last_search_time) < RATE_LIMIT_SECONDS:
        await context.bot.send_message(chat_id=user_id, text=f"Please wait {int(RATE_LIMIT_SECONDS - (current_time - last_search_time))} more seconds.")
        return ConversationHandler.END

    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name)
    if user_db_data and user_db_data.get('is_banned'):
        await context.bot.send_message(chat_id=user_id, text="You are banned from using this bot.")
        return ConversationHandler.END

    context.user_data['last_search_init_time'] = current_time

    # Credit Check Logic (copied and adapted from select_search_category)
    now = datetime.now()
    sub_type = user_db_data.get('subscription_type'); sub_expiry_str = user_db_data.get('subscription_expiry_date')
    has_active_sub = (sub_type == 'lifetime') or (sub_expiry_str and (datetime.fromisoformat(sub_expiry_str) if isinstance(sub_expiry_str, str) else sub_expiry_str) > now)

    can_search_credit_type = "None"
    if has_active_sub: can_search_credit_type = "Subscription"
    elif user_db_data.get('tokens_left', 0) > 0:
        if decrement_tokens(user_id): can_search_credit_type = "Token"
        else: await context.bot.send_message(user_id, "Failed to use a token."); return ConversationHandler.END
    else:
        last_reset = datetime.fromisoformat(user_db_data['last_free_search_reset_time']) if isinstance(user_db_data['last_free_search_reset_time'], str) else (user_db_data['last_free_search_reset_time'] or (now - timedelta(days=1)))
        if last_reset < (now - timedelta(hours=config.FREE_SEARCH_RESET_HOURS)):
            update_user_free_searches(user_id, DEFAULT_FREE_SEARCHES, now); user_db_data = get_or_create_user(user_id)
            await context.bot.send_message(user_id, "Daily free searches reset.")
        if user_db_data.get('free_searches_left', 0) > 0:
            if decrement_user_free_searches(user_id): can_search_credit_type = "Free" # Actual decrement for free search
            else: await context.bot.send_message(user_id, "Failed to use a free search."); return ConversationHandler.END
        else:
             await context.bot.send_message(user_id, "No search credits available (no sub, tokens, or free searches).", reply_markup=get_main_menu_keyboard(user_id))
             return ConversationHandler.END

    if can_search_credit_type == "None": # Should have been caught by logic above
        await context.bot.send_message(user_id, "Error: No valid search credit found. Please contact support.", reply_markup=get_main_menu_keyboard(user_id))
        return ConversationHandler.END

    # --- Create and confirm new job ---
    new_job_id = str(uuid.uuid4())[:8]
    if create_search_job(new_job_id, user_id, category, query_text):
        reply_timeout_from_config = getattr(config, 'REPLY_TIMEOUT', 10)
        confirm_message = (
            f"✅ Repeating search for {category}: \"{query_text}\" (New Job ID: `{new_job_id}`).\n"
            f"Used a {can_search_credit_type.lower()} credit.\n"
            f"The external bot has up to {reply_timeout_from_config} seconds to respond.\n"
            "Results will be sent automatically."
        )
        await query.edit_message_text(text=confirm_message, parse_mode='MarkdownV2', reply_markup=None) # Remove original buttons
    else:
        await query.edit_message_text("Error: Failed to queue the repeated search. Please try again.", reply_markup=get_main_menu_keyboard(user_id))

    return ConversationHandler.END # This is a one-off action, not starting a new conversation state from here.

async def handle_new_search_in_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = query.from_user.id

    # Rate Limit Check (copied from search_start)
    current_time = time.time()
    last_search_time = context.user_data.get('last_search_init_time', 0)
    if (current_time - last_search_time) < RATE_LIMIT_SECONDS:
        await query.answer(f"Please wait {int(RATE_LIMIT_SECONDS - (current_time - last_search_time))} more seconds.", show_alert=True)
        return ConversationHandler.END # End this attempt

    await query.answer()
    context.user_data['last_search_init_time'] = current_time # Update for this new interaction

    category_code = query.data.replace("new_search_cat_", "")
    # Convert code to human-readable name (ensure this map is comprehensive or use a helper)
    category_map = {'domains': 'Domain', 'emails': 'Email', 'phone': 'Phone Number', 'name': 'Name',
                    'domain': 'Domain'} # Added 'domain' just in case
    category_name = category_map.get(category_code, category_code.replace('_', ' ').title())

    context.user_data['search_category'] = category_name

    # Credit check logic simplified for this entry point - full check happens in select_search_category
    # This function's purpose is to set the category and go to TYPING_QUERY
    # The credit check will happen if the user actually types something.
    # For now, assume select_search_category handles credits properly when called.
    # However, this handler IS an entry point to the search_conv_handler.
    # It needs to perform the credit check itself before going to TYPING_QUERY.

    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name)
    if user_db_data and user_db_data.get('is_banned'):
        await query.edit_message_text("You are banned from using this bot.")
        return ConversationHandler.END

    now = datetime.now()
    sub_type = user_db_data.get('subscription_type'); sub_expiry_str = user_db_data.get('subscription_expiry_date')
    has_active_sub = (sub_type == 'lifetime') or (sub_expiry_str and (datetime.fromisoformat(sub_expiry_str) if isinstance(sub_expiry_str, str) else sub_expiry_str) > now)

    search_type_msg = ""
    can_search = False
    search_credit_details = ""

    if has_active_sub:
        search_type_msg = "Using active subscription."; can_search = True
    elif user_db_data.get('tokens_left', 0) > 0:
        # Token decrement will happen when query is submitted, just check if > 0
        search_type_msg = f"Using a token. Tokens available: {user_db_data.get('tokens_left', 0)}."; can_search = True
        # Do not set 'using_free_search' or decrement here. select_search_category/handle_search_query handles it.
    else:
        last_reset = datetime.fromisoformat(user_db_data['last_free_search_reset_time']) if isinstance(user_db_data['last_free_search_reset_time'], str) else (user_db_data['last_free_search_reset_time'] or (now - timedelta(days=1)))
        if last_reset < (now - timedelta(hours=config.FREE_SEARCH_RESET_HOURS)):
            update_user_free_searches(user_id, DEFAULT_FREE_SEARCHES, now); user_db_data = get_or_create_user(user_id)
            await context.bot.send_message(user_id, "Your daily free searches have been reset!") # New message

        free_searches_left = user_db_data.get('free_searches_left', 0)
        if free_searches_left > 0:
            search_type_msg = "Using a free search."
            search_credit_details = f"Free searches left: {free_searches_left}"
            can_search = True # Flag that free search will be used, actual decrement later
        else:
            search_type_msg = "No search credits available."

    if can_search:
        prompt = f"{search_type_msg}\n"
        if search_credit_details: prompt += f"{search_credit_details}\n"
        prompt += f"✍️ Please enter the {category_name} you want to search for:"
        await query.edit_message_text(text=prompt)
        return TYPING_QUERY
    else:
        await query.edit_message_text(text=f"😔 {search_type_msg}\nPlease /buy credits.", reply_markup=get_main_menu_keyboard(user_id))
        return ConversationHandler.END


async def handle_show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await start(update, context) # Use the main start function to display the menu by editing the message
    return ConversationHandler.END # End any conversation this button might be part of (if it's a fallback)
                                   # If it's a standalone handler, END has no effect here.
                                   # The goal is just to show the main menu.
