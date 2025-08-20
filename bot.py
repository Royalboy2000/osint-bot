import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
import jwt
import re

import config
import db_manager # Added missing import
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
    get_job_details
)
from telegram import error as telegram_error

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

SELECTING_CATEGORY, TYPING_QUERY = range(2)
SELECTING_BUY_OPTION, SELECTING_PLAN, SELECTING_TOKEN_AMOUNT, AWAITING_PAYMENT_CONFIRMATION, AWAITING_PROOF = range(10, 15)
AWAITING_USER_ID_TO_BAN, CONFIRM_BAN, AWAITING_USER_ID_TO_UNBAN, CONFIRM_UNBAN = range(20, 24)
ADMIN_GRANT_USER_ID, ADMIN_GRANT_TYPE, ADMIN_GRANT_SUB_PLAN, ADMIN_GRANT_TOKEN_AMOUNT, ADMIN_CONFIRM_GRANT = range(30, 35)
ADMIN_JWT_COMPANY_ID, ADMIN_JWT_EXPIRATION = range(40, 42)

USER_PAGE_SIZE = 5
RATE_LIMIT_SECONDS = 5

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
        [InlineKeyboardButton("🔑 Generate API Token", callback_data='admin_jwt_start')],
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
            query = update.callback_query
            # Try to answer, then edit. If edit fails, it's usually okay for a ban message.
            try: await query.answer(ban_message, show_alert=True)
            except Exception as e_ans: logger.debug(f"Error answering ban callback (already answered?): {e_ans}")

            try:
                if query.message:
                    await query.edit_message_text(ban_message, reply_markup=None)
            except Exception as e_edit:
                logger.debug(f"Failed to edit message for banned user in start: {e_edit}. Ban message alert was shown.")
                # If edit fails but alert was shown, it's often sufficient.
                # Optionally, send a new message if edit fails and alert wasn't possible/sufficient
                # await context.bot.send_message(chat_id=user_id, text=ban_message)
        return

    now = datetime.now()
    last_reset_time_str = user_db_data.get('last_free_search_reset_time')
    last_reset_time = datetime.fromisoformat(last_reset_time_str) if isinstance(last_reset_time_str, str) else (last_reset_time_str or (now - timedelta(days=1)))

    if last_reset_time < (now - timedelta(hours=config.FREE_SEARCH_RESET_HOURS)):
        update_user_free_searches(user_id, DEFAULT_FREE_SEARCHES, now)
        user_db_data = get_or_create_user(user_id)
        if update.callback_query:
             try:
                await context.bot.send_message(chat_id=user_id, text="Your daily free searches have been reset!")
             except Exception as e:
                logger.error(f"Failed to send free search reset notification to {user_id}: {e}")

    searches_left_display = user_db_data.get('free_searches_left', 0)
    tokens_left_display = user_db_data.get('tokens_left', 0)
    sub_type = user_db_data.get('subscription_type', 'None')
    sub_expiry_str = user_db_data.get('subscription_expiry_date')
    sub_status = "None"
    if sub_type and sub_type != 'None':
        if sub_type == 'lifetime': sub_status = "Lifetime"
        elif sub_expiry_str:
            sub_expiry_dt = datetime.fromisoformat(sub_expiry_str) if isinstance(sub_expiry_str, str) else sub_expiry_str
            if sub_expiry_dt and sub_expiry_dt > now:
                sub_status = f"{sub_type.replace('_', ' ').title()} (Expires: {sub_expiry_dt.strftime('%Y-%m-%d')})"
            else:
                sub_status = f"Expired {sub_type.replace('_', ' ').title()}"
        else:
            sub_status = sub_type.replace('_', ' ').title()

    final_welcome_text = message_text if message_text else f"👋 Hello {html.escape(user.first_name or 'User')}!\n\nI am your advanced search assistant."
    status_text = (
        f"\n\nSubscription: <b>{html.escape(sub_status)}</b>\nTokens: <b>{tokens_left_display}</b>\n"
        f"Free Searches Today: <b>{searches_left_display}</b>\n\nUse the buttons below to get started."
    )
    full_message = final_welcome_text + status_text
    main_menu_kb = get_main_menu_keyboard(user_id)

    if update.callback_query:
        query = update.callback_query
        try: await query.answer() # Answer callback query
        except Exception as e: logger.debug(f"Query answer failed in start (likely already answered): {e}")

        if not query.message or not query.message.text:
            logger.info(f"Original message for callback (user: {user_id}) has no text or is missing. Sending new message.")
            try:
                await context.bot.send_message(chat_id=user_id, text=full_message, reply_markup=main_menu_kb, parse_mode='HTML')
            except Exception as e:
                logger.error(f"Failed to send new message in start (callback fallback for no text): {e}")
        else:
            try:
                await query.edit_message_text(full_message, reply_markup=main_menu_kb, parse_mode='HTML')
            except BadRequest as e:
                if "message is not modified" in str(e).lower():
                    logger.debug(f"Message not modified in start (callback edit): {e}.")
                elif "message to edit not found" in str(e).lower() or \
                     "there is no text in the message to edit" in str(e).lower() or \
                     "message can't be edited" in str(e).lower():
                    logger.warning(f"Failed to edit message in start (callback: {e}). Sending new message.")
                    try:
                        await context.bot.send_message(chat_id=user_id, text=full_message, reply_markup=main_menu_kb, parse_mode='HTML')
                    except Exception as send_e:
                        logger.error(f"Failed to send new message after edit failure in start: {send_e}")
                else:
                    logger.error(f"Other BadRequest error editing message in start (callback): {e}. Sending new message as fallback.")
                    try:
                        await context.bot.send_message(chat_id=user_id, text=full_message, reply_markup=main_menu_kb, parse_mode='HTML')
                    except Exception as send_e:
                        logger.error(f"Failed to send new message after other BadRequest in start: {send_e}")
            except Exception as e:
                logger.error(f"Unexpected error editing message in start (callback): {e}. Sending new message.")
                try:
                    await context.bot.send_message(chat_id=user_id, text=full_message, reply_markup=main_menu_kb, parse_mode='HTML')
                except Exception as send_e:
                    logger.error(f"Failed to send new message after unexpected edit error in start: {send_e}")
    elif update.message:
        await update.message.reply_text(full_message, reply_markup=main_menu_kb, parse_mode='HTML')

async def start_again_in_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    welcome_message = "Returning to main menu."
    if update.callback_query:
        try: await update.callback_query.answer()
        except Exception as e: logger.debug(f"Query answer failed in start_again (likely already answered): {e}")

    await start(update, context, message_text=welcome_message)

    keys_to_clear_user = [
        'search_category', 'using_token', 'using_free_search',
        'last_search_init_time', 'last_search_button_click_time',
        'purchase_item_name', 'purchase_item_price'
    ]
    for key in keys_to_clear_user:
        context.user_data.pop(key, None)

    keys_to_clear_chat = ['user_id_to_modify', 'grant_user_id', 'grant_type',
                          'grant_plan_type', 'grant_token_amount']
    for key in keys_to_clear_chat:
        context.chat_data.pop(key, None)

    return ConversationHandler.END

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
    category_code = category.lower().replace(' ', '_')
    display_query_text = (query_text[:20] + '...') if len(query_text) > 20 else query_text
    keyboard = [
        [InlineKeyboardButton(f"🔎 Search '{html.escape(display_query_text)}' again", callback_data=f"search_again_{job_id}")],
        [InlineKeyboardButton(f"✨ New Search in '{html.escape(category)}'", callback_data=f"new_search_cat_{category_code}")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="show_main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    logger.error(f"Update: {json.dumps(update_str, indent=2, ensure_ascii=False)} caused error: {context.error}")

    user_message = "An unexpected error occurred. The admin has been notified. Please try again later."
    if isinstance(context.error, TimedOut):
        user_message = "Sorry, the bot timed out waiting for Telegram. This might be a temporary issue. Please try again in a moment."
    elif isinstance(context.error, NetworkError):
        user_message = "Sorry, there was a network problem connecting to Telegram. Please check your internet connection and try again."
    elif isinstance(context.error, BadRequest):
        error_str = str(context.error).lower()
        if "message to edit not found" in error_str or \
           "there is no text in the message to edit" in error_str or \
           "message can't be edited" in error_str or \
           "message is not modified" in error_str:
            logger.warning(f"Specific BadRequest: {context.error}. Suppressing user error message for this.")
            user_message = None
        else:
            user_message = "An error occurred while processing your request. Please try again."

    if user_message and isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=user_message)
        except Forbidden:
            logger.error(f"Failed to send error message to chat {update.effective_chat.id}: Bot blocked.")
        except Exception as e:
            logger.error(f"Failed to send error message to chat {update.effective_chat.id}: {e}")

# Health Check Constants
PROCESSING_STUCK_THRESHOLD_SECONDS = 240  # Based on user_client REPLY_TIMEOUT (120s) * 2
PENDING_STUCK_THRESHOLD_SECONDS = 60    # Based on user_client POLL_INTERVAL (10s) * 6
ADMIN_ALERT_COOLDOWN_SECONDS = 300      # 5 minutes

async def user_client_health_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Checks the health of user_client.py by monitoring job statuses in the database.
    Sends alerts to admins if issues are detected.
    """
    now = datetime.now()
    bot_data = context.bot_data

    # Check for stuck 'processing' jobs
    try:
        stuck_processing_jobs = db_manager.get_stuck_processing_jobs(PROCESSING_STUCK_THRESHOLD_SECONDS)
        if stuck_processing_jobs:
            last_alert_time = bot_data.get('last_stuck_processing_alert_time', datetime.min)
            if (now - last_alert_time).total_seconds() > ADMIN_ALERT_COOLDOWN_SECONDS:
                job_ids = [job['job_id'] for job in stuck_processing_jobs]
                message = (
                    f"⚠️ **User Client Alert: Stuck Processing Jobs** ⚠️\n\n"
                    f"The following jobs have been in 'processing' state for over {PROCESSING_STUCK_THRESHOLD_SECONDS // 60} minutes:\n"
                    f"- `{(', '.join(job_ids))}`\n\n"
                    f"This may indicate that `user_client.py` has crashed or is unresponsive while processing these tasks."
                )
                logger.warning(f"Health Check: Found stuck processing jobs: {job_ids}")
                for admin_id in config.ADMIN_IDS:
                    try:
                        await context.bot.send_message(chat_id=admin_id, text=message, parse_mode=ParseMode.MARKDOWN_V2)
                    except Exception as e:
                        logger.error(f"Failed to send stuck processing alert to admin {admin_id}: {e}")
                bot_data['last_stuck_processing_alert_time'] = now
    except Exception as e:
        logger.error(f"Error during health check (stuck processing jobs): {e}", exc_info=True)

    # Check for 'pending' jobs not being picked up
    try:
        num_processing = db_manager.count_processing_jobs()
        if num_processing == 0:
            oldest_pending_job = db_manager.get_oldest_pending_job()
            if oldest_pending_job:
                created_at_str = oldest_pending_job.get('created_at')
                if isinstance(created_at_str, str):
                    created_at = datetime.fromisoformat(created_at_str)
                    if (now - created_at).total_seconds() > PENDING_STUCK_THRESHOLD_SECONDS:
                        last_alert_time = bot_data.get('last_pending_stuck_alert_time', datetime.min)
                        if (now - last_alert_time).total_seconds() > ADMIN_ALERT_COOLDOWN_SECONDS:
                            message = (
                                f"⚠️ **User Client Alert: Pending Jobs Not Processed** ⚠️\n\n"
                                f"There are pending jobs, and the oldest one (ID: `{oldest_pending_job['job_id']}`) "
                                f"was created over {PENDING_STUCK_THRESHOLD_SECONDS // 60} minutes ago, "
                                f"but no jobs are currently being processed.\n\n"
                                f"This may indicate that `user_client.py` is not running or not polling for new jobs."
                            )
                            logger.warning(f"Health Check: Found old pending jobs not being processed. Oldest: {oldest_pending_job['job_id']}")
                            for admin_id in config.ADMIN_IDS:
                                try:
                                    await context.bot.send_message(chat_id=admin_id, text=message, parse_mode=ParseMode.MARKDOWN_V2)
                                except Exception as e:
                                    logger.error(f"Failed to send pending stuck alert to admin {admin_id}: {e}")
                            bot_data['last_pending_stuck_alert_time'] = now
    except Exception as e:
        logger.error(f"Error during health check (pending jobs): {e}", exc_info=True)


# --- Buy Conversation Handlers ---
async def buy_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    user_id = query.from_user.id
    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name)
    if user_db_data and user_db_data.get('is_banned'):
        await query.edit_message_text("You are banned."); return ConversationHandler.END
    await query.edit_message_text("🛍️ Welcome to the Shop!", reply_markup=get_buy_options_keyboard())
    return SELECTING_BUY_OPTION

async def select_buy_option(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    if query.data == 'buy_monthly_plans':
        await query.edit_message_text("Select a plan:", reply_markup=get_monthly_plans_keyboard())
        return SELECTING_PLAN
    elif query.data == 'buy_tokens':
        await query.edit_message_text("Select token amount:", reply_markup=get_token_options_keyboard())
        return SELECTING_TOKEN_AMOUNT
    return ConversationHandler.END

async def select_plan_or_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    item_details = query.data.split('_')
    item_type = item_details[1]
    item_name_parts = item_details[2:-1]
    item_price = item_details[-1]
    display_item_name = " ".join(item_name_parts).replace(item_type, "").strip().title()
    if item_type == "plan": full_item_name = f"{display_item_name} Plan"
    else: full_item_name = f"{display_item_name} Token(s)"
    context.user_data['purchase_item_name'] = full_item_name
    context.user_data['purchase_item_price'] = item_price
    payment_message = (
        f"You selected: <b>{full_item_name}</b> for <b>${item_price}</b>.\n"
        f"Please send exactly <code>{item_price}</code> USD equivalent in BTC to:\n"
        f"<code>{config.BTC_ADDRESS}</code>\n\n"
        "Have you made the payment?"
    )
    await query.edit_message_text(payment_message, reply_markup=get_payment_confirmation_keyboard(), parse_mode='HTML')
    return AWAITING_PAYMENT_CONFIRMATION

async def payment_confirmation_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    user_id = query.from_user.id
    if query.data == 'payment_paid_yes':
        item_name = context.user_data.get('purchase_item_name', 'Your item')
        await query.edit_message_text(f"To complete purchase of <b>{item_name}</b>, please send a screenshot or transaction ID of your payment.", parse_mode='HTML'); return AWAITING_PROOF
    elif query.data == 'payment_paid_no':
        await query.edit_message_text("Order cancelled. Returning to main menu.", reply_markup=get_main_menu_keyboard(user_id))
        context.user_data.pop('purchase_item_name', None)
        context.user_data.pop('purchase_item_price', None)
        return ConversationHandler.END
    return ConversationHandler.END

async def handle_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.message.from_user; user_id = user.id; username = user.username or user.first_name
    item = context.user_data.get('purchase_item_name', 'N/A'); price = context.user_data.get('purchase_item_price', 'N/A')
    admin_text = f"💰 Payment Proof from User!\nUser: {html.escape(str(username))} (ID: <code>{user_id}</code>)\nItem: {html.escape(item)} (${html.escape(str(price))})\n\nPlease verify the transaction. The proof message is above/below."
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.forward_message(chat_id=admin_id, from_chat_id=user_id, message_id=update.message.message_id)
            await context.bot.send_message(chat_id=admin_id, text=admin_text, parse_mode='HTML')
        except Exception as e:
            logger.error(f"Failed to forward payment proof to admin {admin_id}: {e}")
    await update.message.reply_text(
        "Thank you! Your payment proof has been submitted for verification. Access will be granted once confirmed.",
        reply_markup=get_main_menu_keyboard(user_id)
    )
    context.user_data.pop('purchase_item_name', None)
    context.user_data.pop('purchase_item_price', None)
    return ConversationHandler.END

async def back_to_buy_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.edit_message_text("🛍️ Welcome to the Shop!", reply_markup=get_buy_options_keyboard())
    return SELECTING_BUY_OPTION

async def back_to_main_from_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await start_again_in_conversation(update, context)

# --- Admin Panel & Related Handlers ---
async def admin_panel_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query; await query.answer()
    user_id = query.from_user.id
    if not is_admin(user_id):
        await query.edit_message_text("⚠️ Access Denied.", reply_markup=get_main_menu_keyboard(user_id))
        return
    await query.edit_message_text("👑 Admin Panel:", reply_markup=get_admin_panel_keyboard())

async def back_to_main_from_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await start_again_in_conversation(update, context)

async def admin_ban_user_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.edit_message_text("Enter User ID to ban:")
    return AWAITING_USER_ID_TO_BAN

async def received_user_id_for_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id_to_ban = int(update.message.text)
        context.chat_data['user_id_to_modify'] = user_id_to_ban
        await update.message.reply_text(f"Ban user {user_id_to_ban}?", reply_markup=get_confirmation_keyboard("ban", str(user_id_to_ban)))
        return CONFIRM_BAN
    except ValueError:
        await update.message.reply_text("Invalid User ID. Please send a numeric ID.")
        return AWAITING_USER_ID_TO_BAN

async def confirm_ban_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    action = query.data
    if action.startswith("ban_yes"):
        user_id_to_ban = context.chat_data.get('user_id_to_modify')
        if not user_id_to_ban:
             await query.edit_message_text("Error: User ID not found. Please try again.", reply_markup=get_admin_panel_keyboard())
             return ConversationHandler.END
        if ban_user(user_id_to_ban):
            await query.edit_message_text(f"User {user_id_to_ban} has been banned.", reply_markup=get_admin_panel_keyboard())
        else:
            await query.edit_message_text(f"Failed to ban user {user_id_to_ban}.", reply_markup=get_admin_panel_keyboard())
    else:
        await query.edit_message_text("Ban action cancelled.", reply_markup=get_admin_panel_keyboard())
    context.chat_data.pop('user_id_to_modify', None)
    return ConversationHandler.END

async def admin_unban_user_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.edit_message_text("Enter User ID to unban:")
    return AWAITING_USER_ID_TO_UNBAN

async def received_user_id_for_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id_to_unban = int(update.message.text)
        context.chat_data['user_id_to_modify'] = user_id_to_unban
        await update.message.reply_text(f"Unban user {user_id_to_unban}?", reply_markup=get_confirmation_keyboard("unban", str(user_id_to_unban)))
        return CONFIRM_UNBAN
    except ValueError:
        await update.message.reply_text("Invalid User ID. Please send a numeric ID.")
        return AWAITING_USER_ID_TO_UNBAN

async def confirm_unban_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    action = query.data
    if action.startswith("unban_yes"):
        user_id_to_unban = context.chat_data.get('user_id_to_modify')
        if not user_id_to_unban:
            await query.edit_message_text("Error: User ID not found.", reply_markup=get_admin_panel_keyboard())
            return ConversationHandler.END
        if unban_user(user_id_to_unban):
            await query.edit_message_text(f"User {user_id_to_unban} has been unbanned.", reply_markup=get_admin_panel_keyboard())
        else:
            await query.edit_message_text(f"Failed to unban user {user_id_to_unban}.", reply_markup=get_admin_panel_keyboard())
    else:
        await query.edit_message_text("Unban action cancelled.", reply_markup=get_admin_panel_keyboard())
    context.chat_data.pop('user_id_to_modify', None)
    return ConversationHandler.END

async def admin_grant_access_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.edit_message_text("Enter User ID to grant access to:")
    return ADMIN_GRANT_USER_ID

async def received_user_id_for_grant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        grant_user_id = int(update.message.text)
        user_data = get_or_create_user(grant_user_id)
        if not user_data:
            await update.message.reply_text(f"Could not find or create user {grant_user_id}.")
            return ADMIN_GRANT_USER_ID
        context.chat_data['grant_user_id'] = grant_user_id
        await update.message.reply_text(f"Granting access to user {grant_user_id}.\nSelect grant type:", reply_markup=get_grant_type_keyboard())
        return ADMIN_GRANT_TYPE
    except ValueError:
        await update.message.reply_text("Invalid User ID. Please send a numeric ID.")
        return ADMIN_GRANT_USER_ID

async def grant_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    grant_type = query.data.replace('grant_type_', '')
    context.chat_data['grant_type'] = grant_type
    if grant_type == 'subscription':
        await query.edit_message_text("Select subscription plan:", reply_markup=get_grant_subscription_plan_keyboard())
        return ADMIN_GRANT_SUB_PLAN
    elif grant_type == 'tokens':
        await query.edit_message_text("Enter number of tokens to grant:")
        return ADMIN_GRANT_TOKEN_AMOUNT
    return ConversationHandler.END

async def grant_subscription_plan_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    plan_type = query.data.replace('grant_plan_', '').replace('_', ' ')
    context.chat_data['grant_plan_type'] = plan_type
    user_id_to_grant = context.chat_data.get('grant_user_id')
    await query.edit_message_text(f"Grant {plan_type.title()} subscription to user {user_id_to_grant}?", reply_markup=get_confirmation_keyboard("grant_sub", f"{user_id_to_grant}_{plan_type.replace(' ','_')}"))
    return ADMIN_CONFIRM_GRANT

async def grant_token_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        token_amount = int(update.message.text)
        if token_amount <= 0:
            await update.message.reply_text("Token amount must be positive.")
            return ADMIN_GRANT_TOKEN_AMOUNT
        context.chat_data['grant_token_amount'] = token_amount
        user_id_to_grant = context.chat_data.get('grant_user_id')
        await update.message.reply_text(f"Grant {token_amount} tokens to user {user_id_to_grant}?", reply_markup=get_confirmation_keyboard("grant_tokens", f"{user_id_to_grant}_{token_amount}"))
        return ADMIN_CONFIRM_GRANT
    except ValueError:
        await update.message.reply_text("Invalid amount. Please enter a number.")
        return ADMIN_GRANT_TOKEN_AMOUNT

async def confirm_grant_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    action_parts = query.data.split('_')
    grant_category = action_parts[1]
    grant_confirmation = action_parts[2]
    user_id = context.chat_data.get('grant_user_id')
    if grant_confirmation == 'yes':
        if grant_category == 'sub':
            plan_type = context.chat_data.get('grant_plan_type')
            internal_plan_type = plan_type.replace(' ', '_')
            grant_subscription(user_id, internal_plan_type)
            update_user_free_searches(user_id, DEFAULT_FREE_SEARCHES, datetime.now())
            await query.edit_message_text(f"Granted {plan_type.title()} subscription to user {user_id}. Free searches reset.", reply_markup=get_admin_panel_keyboard())
        elif grant_category == 'tokens':
            amount = context.chat_data.get('grant_token_amount')
            grant_tokens(user_id, amount)
            update_user_free_searches(user_id, DEFAULT_FREE_SEARCHES, datetime.now())
            await query.edit_message_text(f"Granted {amount} tokens to user {user_id}. Free searches reset.", reply_markup=get_admin_panel_keyboard())
    else:
        await query.edit_message_text("Grant access cancelled.", reply_markup=get_admin_panel_keyboard())
    context.chat_data.pop('grant_user_id', None)
    context.chat_data.pop('grant_type', None)
    context.chat_data.pop('grant_plan_type', None)
    context.chat_data.pop('grant_token_amount', None)
    return ConversationHandler.END

async def grant_back_to_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.edit_message_text("Select grant type:", reply_markup=get_grant_type_keyboard())
    return ADMIN_GRANT_TYPE

async def cancel_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    message_text = "Admin action cancelled. Returning to Admin Panel."
    if query:
        await query.answer()
        await query.edit_message_text(message_text, reply_markup=get_admin_panel_keyboard())
    elif update.message:
        await update.message.reply_text(message_text, reply_markup=get_admin_panel_keyboard())
    context.chat_data.pop('grant_user_id', None)
    context.chat_data.pop('grant_type', None)
    context.chat_data.pop('grant_plan_type', None)
    context.chat_data.pop('grant_token_amount', None)
    context.chat_data.pop('user_id_to_modify', None)
    return ConversationHandler.END

async def admin_view_users_noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query: await query.answer()

async def admin_view_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query; await query.answer()
    user_id = query.from_user.id
    if not is_admin(user_id):
        await query.edit_message_text("⚠️ Access Denied.", reply_markup=get_main_menu_keyboard(user_id))
        return
    try: page = int(query.data.replace('admin_view_users_page_', ''))
    except ValueError: page = 1
    users_data, total_users = get_users_for_view(page=page, page_size=USER_PAGE_SIZE)
    if total_users == 0:
        message_text = "No users found in the database."
        keyboard = [[InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data='admin_panel_main')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        return
    message_lines = [f"👥 <b>Users List - Page {page}</b>\n"]
    now = datetime.now()
    for user_info in users_data:
        uid = user_info.get('user_id'); username = user_info.get('username', 'N/A'); first_name = user_info.get('first_name', 'N/A')
        display_name = f"@{username}" if username != 'N/A' else first_name
        free_searches = user_info.get('free_searches_left', 0); last_reset_str = user_info.get('last_free_search_reset_time', 'Never')
        last_reset_formatted = datetime.fromisoformat(last_reset_str).strftime('%Y-%m-%d %H:%M') if isinstance(last_reset_str, str) and last_reset_str != 'Never' else 'Never'
        tokens = user_info.get('tokens_left', 0); sub_type = user_info.get('subscription_type', 'None'); sub_expiry_str = user_info.get('subscription_expiry_date')
        sub_status_display = "None"
        if sub_type and sub_type != 'None':
            if sub_type == 'lifetime': sub_status_display = "Lifetime"
            elif sub_expiry_str:
                try:
                    sub_expiry_dt = datetime.fromisoformat(sub_expiry_str)
                    sub_status_display = f"{sub_type.replace('_',' ').title()} (Expires: {sub_expiry_dt.strftime('%Y-%m-%d')})" if sub_expiry_dt > now else f"Expired {sub_type.replace('_',' ').title()}"
                except ValueError: sub_status_display = f"{sub_type.replace('_',' ').title()} (Expiry: {sub_expiry_str})"
            else: sub_status_display = f"{sub_type.replace('_',' ').title()} (No expiry date)"
        is_banned_val = user_info.get('is_banned', False); banned_status = "Yes" if is_banned_val else "No"
        user_lines = [
            f"👤 <b>{html.escape(display_name)}</b> (ID: <code>{uid}</code>)",
            f"  🆓 Free Searches: {free_searches} (Reset: {last_reset_formatted})",
            f"  🪙 Tokens: {tokens}", f"  📜 Subscription: {html.escape(sub_status_display)}", f"  🚫 Banned: {banned_status}"
        ]
        message_lines.append("\n".join(user_lines))
    message_text = "\n\n".join(message_lines)
    total_pages = (total_users + USER_PAGE_SIZE - 1) // USER_PAGE_SIZE
    pagination_buttons = []
    if page > 1: pagination_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f'admin_view_users_page_{page - 1}'))
    if total_pages > 0 : pagination_buttons.append(InlineKeyboardButton(f"Page {page}/{total_pages}", callback_data='admin_view_users_noop'))
    if page < total_pages: pagination_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f'admin_view_users_page_{page + 1}'))
    keyboard = [pagination_buttons] if pagination_buttons else []
    keyboard.append([InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data='admin_panel_main')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text=message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

# --- JWT Generation Conversation Handlers ---
async def admin_jwt_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the JWT generation process."""
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.edit_message_text("⚠️ Access Denied.")
        return ConversationHandler.END

    await query.edit_message_text("Enter a unique identifier for the company or client (e.g., 'company-name-inc'):")
    return ADMIN_JWT_COMPANY_ID

async def received_company_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the company ID and asks for the expiration period."""
    company_id = update.message.text.strip()
    if not company_id:
        await update.message.reply_text("Company ID cannot be empty. Please try again.")
        return ADMIN_JWT_COMPANY_ID

    context.chat_data['jwt_company_id'] = company_id

    await update.message.reply_text(
        "Great. Now, enter the token's validity period.\n"
        "Examples: `30d` (30 days), `6m` (6 months), `1y` (1 year).\n"
        "Use 'd' for days, 'm' for months, 'y' for years."
    )
    return ADMIN_JWT_EXPIRATION

async def received_expiration_and_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Parses expiration, generates the JWT, and sends it."""
    expiration_str = update.message.text.strip().lower()
    match = re.match(r'^(\d+)([dmy])$', expiration_str)

    if not match:
        await update.message.reply_text("Invalid format. Please use a number followed by 'd', 'm', or 'y'.\nExample: `90d`")
        return ADMIN_JWT_EXPIRATION

    value, unit = int(match.group(1)), match.group(2)
    now = datetime.now()

    if unit == 'd':
        delta = timedelta(days=value)
    elif unit == 'm':
        # timedelta doesn't have months, so approximate as 30 days per month
        delta = timedelta(days=value * 30)
    elif unit == 'y':
        # Approximate as 365 days per year
        delta = timedelta(days=value * 365)

    expiration_time = now + delta
    company_id = context.chat_data.get('jwt_company_id')

    if not company_id:
        await update.message.reply_text("Error: Company ID was lost. Please start over.", reply_markup=get_admin_panel_keyboard())
        return ConversationHandler.END

    payload = {
        'sub': company_id,
        'exp': expiration_time,
        'iat': now
    }

    try:
        token = jwt.encode(payload, config.API_JWT_KEY, algorithm="HS256")

        await update.message.reply_text(
            f"✅ Token generated successfully for `{company_id}`!\n"
            f"Expires on: {expiration_time.strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
            f"Here is the token:"
        )
        # Send the token in a separate message so it's easy to copy
        await update.message.reply_text(f"<code>{token}</code>", parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"Failed to generate JWT: {e}")
        await update.message.reply_text("An internal error occurred while generating the token. The developer has been notified.")

    # Clean up context
    context.chat_data.pop('jwt_company_id', None)
    return ConversationHandler.END

# --- Helper function for search checks ---
async def _can_perform_search(user_id: int, context: ContextTypes.DEFAULT_TYPE, query_update: Update = None) -> tuple[bool, str, bool, bool]:
    current_time = time.time()
    last_search_action_time = context.user_data.get('last_search_init_time', 0)
    if (current_time - last_search_action_time) < RATE_LIMIT_SECONDS:
        wait_time = int(RATE_LIMIT_SECONDS - (current_time - last_search_action_time))
        return False, f"Please wait {wait_time} more seconds.", False, False
    context.user_data['last_search_init_time'] = current_time
    user_db_data = get_or_create_user(user_id); now = datetime.now(); has_active_sub = False
    sub_type = user_db_data.get('subscription_type'); sub_expiry_str = user_db_data.get('subscription_expiry_date')
    if sub_type and sub_type != 'None':
        if sub_type == 'lifetime': has_active_sub = True
        elif sub_expiry_str:
            sub_expiry_dt = datetime.fromisoformat(sub_expiry_str) if isinstance(sub_expiry_str, str) else sub_expiry_str
            if sub_expiry_dt and sub_expiry_dt > now: has_active_sub = True
    using_token = False; using_free_search = False; search_type_msg = ""; search_credit_info = ""
    if has_active_sub:
        search_type_msg = "Using active subscription."
        return True, search_type_msg, False, False
    elif user_db_data.get('tokens_left', 0) > 0:
        search_type_msg = f"Using a token. Tokens available: {user_db_data.get('tokens_left', 0)}."
        using_token = True
        return True, search_type_msg, using_token, False
    else:
        last_reset_time_str = user_db_data.get('last_free_search_reset_time')
        last_reset_time = datetime.fromisoformat(last_reset_time_str) if isinstance(last_reset_time_str, str) else (last_reset_time_str or (now - timedelta(days=1)))
        if last_reset_time < (now - timedelta(hours=config.FREE_SEARCH_RESET_HOURS)):
            update_user_free_searches(user_id, DEFAULT_FREE_SEARCHES, now)
            user_db_data = get_or_create_user(user_id)
            if query_update and query_update.effective_chat:
                 try: await context.bot.send_message(chat_id=query_update.effective_chat.id, text="Your daily free searches have been reset!")
                 except Exception as e: logger.info(f"Failed to send free search reset message to {user_id}: {e}")
        free_searches_left = user_db_data.get('free_searches_left', 0)
        if free_searches_left > 0:
            search_credit_info = f"Free searches left: {free_searches_left}"
            search_type_msg = f"Using a free search. {search_credit_info}"
            using_free_search = True
            return True, search_type_msg, False, using_free_search
        else: return False, "😔 No active subscription, tokens, or free searches available.", False, False

# --- Search Conversation Handlers ---
async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    user_id = query.from_user.id
    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name)
    if user_db_data and user_db_data.get('is_banned'):
        await query.answer("You are banned.", show_alert=True)
        try: await query.edit_message_text("You are banned.")
        except BadRequest: logger.debug(f"Could not edit message for banned user {user_id} in search_start.")
        return ConversationHandler.END
    current_time = time.time()
    last_search_button_click_time = context.user_data.get('last_search_button_click_time', 0)
    if (current_time - last_search_button_click_time) < RATE_LIMIT_SECONDS:
        wait_time = int(RATE_LIMIT_SECONDS - (current_time - last_search_button_click_time))
        await query.answer(f"Please wait {wait_time} more seconds.", show_alert=True)
        return ConversationHandler.END
    context.user_data['last_search_button_click_time'] = current_time
    context.user_data.pop('search_category', None); context.user_data.pop('using_token', None); context.user_data.pop('using_free_search', None)
    await query.edit_message_text(text="👇 Please select a search category:", reply_markup=get_search_categories_keyboard())
    return SELECTING_CATEGORY

async def select_search_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    user_id = query.from_user.id
    can_search, message, using_token, using_free_search = await _can_perform_search(user_id, context, query_update=query)
    if not can_search:
        if "Please wait" in message: await query.answer(message, show_alert=True)
        else: await query.edit_message_text(f"{message}", reply_markup=get_main_menu_keyboard(user_id))
        context.user_data.pop('search_category', None); context.user_data.pop('using_token', None); context.user_data.pop('using_free_search', None)
        return ConversationHandler.END
    context.user_data['using_token'] = using_token; context.user_data['using_free_search'] = using_free_search
    category_code = query.data.replace('search_category_', '')
    category_map = {'domains': 'Domain', 'emails': 'Email', 'phone': 'Phone Number', 'name': 'Name'}
    category_name = category_map.get(category_code, category_code.replace('_', ' ').title())
    context.user_data['search_category'] = category_name
    prompt_msg = f"{message}\n✍️ Enter {category_name} to search:"
    await query.edit_message_text(text=prompt_msg)
    return TYPING_QUERY

async def handle_search_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query_text = update.message.text; category = context.user_data.get('search_category', 'N/A'); user_id = update.message.from_user.id
    if category == 'N/A':
        logger.warning(f"User {user_id} reached handle_search_query without a category set.")
        await update.message.reply_text("⚠️ Error: category not selected. Start over.", reply_markup=get_main_menu_keyboard(user_id))
        context.user_data.pop('using_token', None); context.user_data.pop('using_free_search', None)
        return ConversationHandler.END
    if context.user_data.get('using_token'):
        if not decrement_tokens(user_id):
            await update.message.reply_text("⚠️ Error using token. Try again.", reply_markup=get_main_menu_keyboard(user_id))
            context.user_data.pop('search_category', None); context.user_data.pop('using_token', None); context.user_data.pop('using_free_search', None)
            return ConversationHandler.END
    elif context.user_data.get('using_free_search'):
        if not decrement_user_free_searches(user_id):
            await update.message.reply_text("⚠️ Error using free search. Try again.", reply_markup=get_main_menu_keyboard(user_id))
            context.user_data.pop('search_category', None); context.user_data.pop('using_token', None); context.user_data.pop('using_free_search', None)
            return ConversationHandler.END
    job_id = str(uuid.uuid4())[:8]
    # Changed to 'bot_only' as per new requirement
    if not create_search_job(job_id, user_id, category, query_text, search_type='bot_only'):
        await update.message.reply_text("⚠️ Failed to queue search. Try again later.", reply_markup=get_main_menu_keyboard(user_id))
        context.user_data.pop('search_category', None); context.user_data.pop('using_token', None); context.user_data.pop('using_free_search', None)
        return ConversationHandler.END
    confirm_msg = (f"✅ Your search for {html.escape(category)}: \"{html.escape(query_text)}\" (Job ID: <code>{job_id}</code>) has been queued.\n\n" # Message reverted
                   "Results sent when ready.")
    await update.message.reply_text(text=confirm_msg, parse_mode='HTML', reply_markup=get_main_menu_keyboard(user_id))
    context.user_data.pop('search_category', None); context.user_data.pop('using_token', None); context.user_data.pop('using_free_search', None)
    return ConversationHandler.END

async def back_to_main_menu_from_search_cats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await start_again_in_conversation(update, context)

async def handle_search_again(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query; await query.answer()
    user_id = query.from_user.id
    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name)
    if user_db_data and user_db_data.get('is_banned'):
        await query.answer("You are banned.", show_alert=True)
        try: await query.edit_message_text("You are banned.")
        except BadRequest: pass
        return
    if not get_job_details:
        logger.error("get_job_details not available.")
        await context.bot.send_message(chat_id=user_id, text="Internal error.", reply_markup=get_main_menu_keyboard(user_id))
        return
    job_id_to_repeat = query.data.replace("search_again_", "")
    original_job = get_job_details(job_id_to_repeat)
    if not original_job:
        await context.bot.send_message(chat_id=user_id, text=f"No details for Job ID: {job_id_to_repeat}.", reply_markup=get_main_menu_keyboard(user_id))
        return
    category = original_job.get('category'); query_text = original_job.get('query_text')
    if not category or not query_text:
        await context.bot.send_message(chat_id=user_id, text=f"Job {job_id_to_repeat} missing details.", reply_markup=get_main_menu_keyboard(user_id))
        return
    can_search, message, using_token, using_free_search = await _can_perform_search(user_id, context, query_update=query)
    if not can_search:
        if "Please wait" in message: await query.answer(message, show_alert=True)
        else: await context.bot.send_message(chat_id=user_id, text=message, reply_markup=get_main_menu_keyboard(user_id))
        return
    if using_token:
        if not decrement_tokens(user_id): await context.bot.send_message(chat_id=user_id, text="⚠️ Error using token.", reply_markup=get_main_menu_keyboard(user_id)); return
    elif using_free_search:
        if not decrement_user_free_searches(user_id): await context.bot.send_message(chat_id=user_id, text="⚠️ Error using free search.", reply_markup=get_main_menu_keyboard(user_id)); return
    new_job_id = str(uuid.uuid4())[:8]
    # Changed to 'bot_only' as per new requirement
    if not create_search_job(new_job_id, user_id, category, query_text, search_type='bot_only'):
        logger.error(f"Failed to create job {new_job_id} (type: bot_only) for user {user_id}.")
        await context.bot.send_message(chat_id=user_id, text="⚠️ Failed to queue search. Contact support.", reply_markup=get_main_menu_keyboard(user_id))
        return
    confirm_msg = (f"✅ Repeated search for {html.escape(category)}: \"{html.escape(query_text)}\" (New Job ID: <code>{new_job_id}</code>) queued.\n\nResults when ready.") # Message reverted
    await context.bot.send_message(chat_id=user_id, text=confirm_msg, parse_mode='HTML', reply_markup=get_main_menu_keyboard(user_id))

async def handle_new_search_in_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    user_id = query.from_user.id
    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name)
    if user_db_data and user_db_data.get('is_banned'):
        await query.answer("You are banned.", show_alert=True)
        try: await query.edit_message_text("You are banned.")
        except BadRequest: pass
        return ConversationHandler.END
    can_search, message, using_token, using_free_search = await _can_perform_search(user_id, context, query_update=query)
    if not can_search:
        if "Please wait" in message: await query.answer(message, show_alert=True)
        else:
            try: await query.edit_message_text(f"{message}", reply_markup=get_main_menu_keyboard(user_id))
            except BadRequest: await context.bot.send_message(chat_id=user_id, text=message, reply_markup=get_main_menu_keyboard(user_id))
        return ConversationHandler.END
    context.user_data['using_token'] = using_token; context.user_data['using_free_search'] = using_free_search
    category_code = query.data.replace('new_search_cat_', '')
    category_map = {'domains': 'Domain', 'emails': 'Email', 'phone': 'Phone Number', 'name': 'Name'}
    category_name = category_map.get(category_code, category_code.replace('_', ' ').title())
    context.user_data['search_category'] = category_name
    prompt_msg = f"{message}\n✍️ Enter {category_name} to search:"
    try: await query.edit_message_text(text=prompt_msg)
    except BadRequest: await context.bot.send_message(chat_id=user_id, text=prompt_msg)
    return TYPING_QUERY

async def handle_show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query: await query.answer()
    return await start_again_in_conversation(update, context)

async def deliver_results_background_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        undelivered_jobs = get_all_undelivered_jobs()
        if not undelivered_jobs: return
        for job in undelivered_jobs:
            job_id = job.get('job_id'); user_id = job.get('user_id'); status = job.get('status')
            result_file_path = job.get('result_file_path'); error_message_from_job = job.get('error_message')
            if not job_id or not user_id: logger.error(f"Job missing ID/user_id: {job}"); continue
            try:
                if status == 'completed':
                    if result_file_path and os.path.exists(result_file_path):
                        try:
                            with open(result_file_path, 'rb') as f_doc:
                                await context.bot.send_document(chat_id=user_id, document=f_doc, caption=f"✅ Results for job <code>{job_id}</code>.", parse_mode=ParseMode.HTML)
                            logger.info(f"Sent results job {job_id} to user {user_id}.")
                            mark_job_delivered(job_id)
                            try: os.remove(result_file_path); logger.info(f"Deleted file {result_file_path} for job {job_id}.")
                            except FileNotFoundError: logger.warning(f"File {result_file_path} not found for deletion (job {job_id}).")
                            except Exception as e_rem: logger.error(f"Error deleting file {result_file_path} (job {job_id}): {e_rem}")
                        except FileNotFoundError:
                            logger.error(f"File {result_file_path} not found for job {job_id} (pre-send).")
                            await context.bot.send_message(chat_id=user_id, text=f"⚠️ Error delivering results for job <code>{job_id}</code>: File missing.", parse_mode=ParseMode.HTML)
                            mark_job_delivered(job_id)
                        except telegram_error.Forbidden: logger.warning(f"User {user_id} blocked bot. Job {job_id} marked delivered."); mark_job_delivered(job_id)
                        except (telegram_error.BadRequest, telegram_error.NetworkError, TimedOut) as e_tel: logger.error(f"Telegram error sending job {job_id} to {user_id}: {type(e_tel).__name__}. Retrying.")
                        except Exception as e_send: logger.error(f"Unexpected error sending job {job_id} to {user_id}: {e_send}. Retrying.")
                    else:
                        logger.warning(f"Job {job_id} completed but file '{result_file_path}' missing.")
                        await context.bot.send_message(chat_id=user_id, text=f"⚠️ Job <code>{job_id}</code> completed, issue retrieving results. Contact support.", parse_mode=ParseMode.HTML)
                        mark_job_delivered(job_id)
                elif status == 'failed':
                    user_facing_error = "Issue with search."
                    if error_message_from_job:
                        if "Timeout: The target bot did not respond" in error_message_from_job: user_facing_error = html.escape(error_message_from_job)
                        else: logger.warning(f"Job {job_id} failed (user_client error): {error_message_from_job}. Generic msg shown."); user_facing_error = "Task encountered unexpected issue."
                    else: user_facing_error = "No specific error by search task."
                    await context.bot.send_message(chat_id=user_id, text=f"❌ Job <code>{job_id}</code> failed. {user_facing_error}", parse_mode=ParseMode.HTML)
                    mark_job_delivered(job_id)
                    logger.info(f"Sent failure for job {job_id} to {user_id}. Original error: {error_message_from_job or 'N/A'}")
                else: logger.warning(f"Job {job_id} unknown status '{status}'. Marked delivered."); mark_job_delivered(job_id)
            except telegram_error.Forbidden: logger.warning(f"User {user_id} blocked bot (outer job {job_id}). Marked delivered."); mark_job_delivered(job_id)
            except (telegram_error.BadRequest, telegram_error.NetworkError, TimedOut) as e_outer_tel: logger.error(f"Outer Telegram error for {user_id}, job {job_id}: {type(e_outer_tel).__name__}. Retrying.")
            except Exception as e_outer: logger.error(f"Outer error for job {job_id}, user {user_id}: {e_outer}. Retrying.")
    except Exception as e_crit: logger.error(f"Critical error in deliver_results_background_job: {e_crit}", exc_info=True)

def main() -> None:
    application = Application.builder().token(config.BOT_TOKEN).build()
    application.add_error_handler(error_handler)
    application.add_handler(CommandHandler("start", start))
    job_queue = application.job_queue
    job_queue.run_repeating(deliver_results_background_job, interval=30, first=10, name='result_delivery_job')
    logger.info("Background result delivery job scheduled.")

    # Health Check for user_client.py
    HEALTH_CHECK_INTERVAL_SECONDS = 30
    job_queue.run_repeating(user_client_health_check, interval=HEALTH_CHECK_INTERVAL_SECONDS, first=HEALTH_CHECK_INTERVAL_SECONDS, name='user_client_health_check_job')
    logger.info(f"User client health check job scheduled to run every {HEALTH_CHECK_INTERVAL_SECONDS} seconds.")

    application.add_handler(CallbackQueryHandler(handle_search_again, pattern=r'^search_again_'))
    application.add_handler(CallbackQueryHandler(handle_show_main_menu, pattern=r'^show_main_menu$'))
    search_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(search_start, pattern='^main_search$'), CallbackQueryHandler(handle_new_search_in_category, pattern=r'^new_search_cat_')],
        states={
            SELECTING_CATEGORY: [CallbackQueryHandler(select_search_category, pattern='^search_category_')],
            TYPING_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_query)],
        },
        fallbacks=[CallbackQueryHandler(back_to_main_menu_from_search_cats, pattern='^back_to_main_search_cats$'), CommandHandler('start', start_again_in_conversation), CallbackQueryHandler(handle_show_main_menu, pattern='^show_main_menu$')],
        per_message=False
    )
    application.add_handler(search_conv_handler)
    buy_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(buy_start, pattern='^main_buy$')],
        states={
            SELECTING_BUY_OPTION: [CallbackQueryHandler(select_buy_option, pattern='^(buy_monthly_plans|buy_tokens)$')],
            SELECTING_PLAN: [CallbackQueryHandler(select_plan_or_token, pattern='^buy_plan_'), CallbackQueryHandler(back_to_buy_options, pattern='^back_to_buy_options$')],
            SELECTING_TOKEN_AMOUNT: [CallbackQueryHandler(select_plan_or_token, pattern='^buy_token_'), CallbackQueryHandler(back_to_buy_options, pattern='^back_to_buy_options$')],
            AWAITING_PAYMENT_CONFIRMATION: [CallbackQueryHandler(payment_confirmation_prompt, pattern='^(payment_paid_yes|payment_paid_no)$')],
            AWAITING_PROOF: [MessageHandler(filters.TEXT | filters.PHOTO & ~filters.COMMAND, handle_payment_proof)]
        },
        fallbacks=[CallbackQueryHandler(back_to_main_from_buy, pattern='^back_to_main_from_buy$'), CommandHandler('start', start_again_in_conversation)],
        per_message=False
    )
    application.add_handler(buy_conv_handler)
    ban_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_ban_user_start_callback, pattern='^admin_ban_user_start$')],
        states={
            AWAITING_USER_ID_TO_BAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_user_id_for_ban)],
            CONFIRM_BAN: [CallbackQueryHandler(confirm_ban_action, pattern=r'^(ban_yes_|ban_no$)')]
        },
        fallbacks=[CallbackQueryHandler(cancel_admin_action, pattern='^admin_panel_main$'), CommandHandler('start', start_again_in_conversation)],
        map_to_parent={ConversationHandler.END: ConversationHandler.END}
    )
    unban_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_unban_user_start_callback, pattern='^admin_unban_user_start$')],
        states={
            AWAITING_USER_ID_TO_UNBAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_user_id_for_unban)],
            CONFIRM_UNBAN: [CallbackQueryHandler(confirm_unban_action, pattern=r'^(unban_yes_|unban_no$)')]
        },
        fallbacks=[CallbackQueryHandler(cancel_admin_action, pattern='^admin_panel_main$'), CommandHandler('start', start_again_in_conversation)],
        map_to_parent={ConversationHandler.END: ConversationHandler.END}
    )
    grant_access_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_grant_access_start_callback, pattern='^admin_grant_access_start$')],
        states={
            ADMIN_GRANT_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_user_id_for_grant)],
            ADMIN_GRANT_TYPE: [CallbackQueryHandler(grant_type_selected, pattern='^grant_type_')],
            ADMIN_GRANT_SUB_PLAN: [CallbackQueryHandler(grant_subscription_plan_selected, pattern='^grant_plan_'), CallbackQueryHandler(grant_back_to_type_selected, pattern='^grant_back_to_type$')],
            ADMIN_GRANT_TOKEN_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, grant_token_amount_received)],
            ADMIN_CONFIRM_GRANT: [CallbackQueryHandler(confirm_grant_action, pattern=r'^(grant_sub_yes_|grant_tokens_yes_|grant_sub_no|grant_tokens_no$)')]
        },
        fallbacks=[CallbackQueryHandler(cancel_admin_action, pattern='^grant_cancel_to_admin_panel$'), CallbackQueryHandler(cancel_admin_action, pattern='^admin_panel_main$'), CommandHandler('start', start_again_in_conversation)],
        map_to_parent={ConversationHandler.END: ConversationHandler.END}
    )
    application.add_handler(CallbackQueryHandler(admin_panel_main_callback, pattern='^admin_panel_main$'))
    application.add_handler(CallbackQueryHandler(back_to_main_from_admin_panel, pattern='^back_to_main_from_admin_panel$'))
    application.add_handler(CallbackQueryHandler(admin_view_users_callback, pattern=r'^admin_view_users_page_'))
    application.add_handler(CallbackQueryHandler(admin_view_users_noop_callback, pattern=r'^admin_view_users_noop$'))
    application.add_handler(ban_conv_handler)
    application.add_handler(unban_conv_handler)
    application.add_handler(grant_access_conv_handler)

    jwt_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_jwt_start, pattern='^admin_jwt_start$')],
        states={
            ADMIN_JWT_COMPANY_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_company_id)],
            ADMIN_JWT_EXPIRATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_expiration_and_generate)],
        },
        fallbacks=[CallbackQueryHandler(cancel_admin_action, pattern='^admin_panel_main$'), CommandHandler('start', start_again_in_conversation)],
        map_to_parent={ConversationHandler.END: ConversationHandler.END}
    )
    application.add_handler(jwt_conv_handler)

    logger.info("Bot starting...")
    application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
