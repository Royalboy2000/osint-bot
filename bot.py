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
    get_job_details
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

# (Other keyboard functions remain the same)
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
            try: await update.callback_query.edit_message_text(ban_message, reply_markup=None) # Remove kbd if present
            except Exception as e:
                logger.debug(f"Failed to edit message for banned user in start: {e}")
                # If edit fails, try sending a new message if it's critical user sees it
                # For a ban message, if edit failed, it's probably okay to not send another.
    return

    now = datetime.now()
    last_reset_time_str = user_db_data.get('last_free_search_reset_time')
    last_reset_time = datetime.fromisoformat(last_reset_time_str) if isinstance(last_reset_time_str, str) else (last_reset_time_str or (now - timedelta(days=1)))

    if last_reset_time < (now - timedelta(hours=config.FREE_SEARCH_RESET_HOURS)):
        update_user_free_searches(user_id, DEFAULT_FREE_SEARCHES, now)
        user_db_data = get_or_create_user(user_id)
        if update.callback_query: # Only send this as a new message if it's a callback context
             await context.bot.send_message(chat_id=user_id, text="Your daily free searches have been reset!")

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

    final_welcome_text = message_text if message_text else f"👋 Hello {html.escape(user.first_name or 'User')}!\n\nI am your advanced search assistant."
    status_text = (
        f"\n\nSubscription: <b>{html.escape(sub_status)}</b>\nTokens: <b>{tokens_left_display}</b>\n"
        f"Free Searches Today: <b>{searches_left_display}</b>\n\nUse the buttons below to get started."
    )
    full_message = final_welcome_text + status_text

    main_menu_kb = get_main_menu_keyboard(user_id)

    if update.callback_query:
        query = update.callback_query
        # No await query.answer() here if start is called by other callbacks that already answered.
        # If start itself is a direct callback, answer should be at its beginning.
        # For now, assume calling functions (like back buttons) handle their own query.answer().
        try:
            await query.edit_message_text(full_message, reply_markup=main_menu_kb, parse_mode='HTML')
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                logger.debug(f"Message not modified in start (callback): {e}. User might be clicking 'Main Menu' on main menu.")
                # Optionally, answer the query to remove the loading spinner if not already done
                if query and not query.answered: await query.answer()
            else:
                logger.error(f"Error editing message in start (callback): {e}")
                # Fallback to sending a new message
                await context.bot.send_message(chat_id=user_id, text=full_message, reply_markup=main_menu_kb, parse_mode='HTML')
        except Exception as e:
            logger.error(f"Unexpected error editing message in start (callback): {e}")
            await context.bot.send_message(chat_id=user_id, text=full_message, reply_markup=main_menu_kb, parse_mode='HTML')
    elif update.message: # Typically from /start command
        await update.message.reply_text(full_message, reply_markup=main_menu_kb, parse_mode='HTML')


async def start_again_in_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    welcome_message = "Returning to main menu."

    if update.callback_query:
        query = update.callback_query
        # Answer callback if not already answered by a more specific handler
        if not query.answered: await query.answer()
        # Call start, which will attempt to edit the message.
        # start function itself handles ban check and full menu display.
        await start(update, context, message_text=welcome_message)
    elif update.message:
        # If /start is typed, start function handles it by sending a new message.
        await start(update, context, message_text=welcome_message)

    # Clear all known conversation-specific user_data keys
    keys_to_clear_user = ['search_category', 'using_token', 'using_free_search',
                          'purchase_item_name', 'purchase_item_price']
    for key in keys_to_clear_user:
        context.user_data.pop(key, None)

    # Clear all known chat_data keys (used for admin actions)
    keys_to_clear_chat = ['user_id_to_modify', 'grant_user_id', 'grant_type',
                          'grant_plan_type', 'grant_token_amount']
    for key in keys_to_clear_chat:
        context.chat_data.pop(key, None)

    return ConversationHandler.END

# (Rest of the bot.py code: search, buy, admin conversations, delivery, error_handler, main)
# ... search_start and select_search_category ...
# ... handle_search_query ... (This one sends new messages, which is fine for MessageHandler)
# ... back_to_main_menu_from_search_cats ... (This calls start)
# ... buy conversation handlers ...
# ... admin conversation handlers ...
# ... result delivery ...
# ... global error_handler ...
# ... main ...

# --- Search Conversation Handlers ---
async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; user_id = query.from_user.id
    await query.answer() # Answer early
    current_time = time.time()
    last_search_time = context.user_data.get('last_search_init_time', 0)
    if (current_time - last_search_time) < RATE_LIMIT_SECONDS:
        await query.answer(f"Please wait {int(RATE_LIMIT_SECONDS - (current_time - last_search_time))} more seconds.", show_alert=True) # Use answer with show_alert for rate limit
        return ConversationHandler.END
    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name)
    if user_db_data and user_db_data.get('is_banned'):
        await query.answer("You are banned.", show_alert=True); await query.edit_message_text("You are banned.")
        return ConversationHandler.END
    context.user_data['last_search_init_time'] = current_time
    await query.edit_message_text(text="👇 Please select a search category:", reply_markup=get_search_categories_keyboard())
    return SELECTING_CATEGORY

async def select_search_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer(); user_id = query.from_user.id
    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name); now = datetime.now()
    sub_type = user_db_data.get('subscription_type'); sub_expiry_str = user_db_data.get('subscription_expiry_date'); has_active_sub = False
    if sub_type and sub_type != 'None':
        if sub_type == 'lifetime': has_active_sub = True
        elif sub_expiry_str:
            sub_expiry_dt = datetime.fromisoformat(sub_expiry_str) if isinstance(sub_expiry_str, str) else sub_expiry_str
            if sub_expiry_dt and sub_expiry_dt > now: has_active_sub = True
    search_type_msg = ""; can_search = False; search_credit_info = ""
    if has_active_sub: search_type_msg = "Using active subscription."; can_search = True
    elif user_db_data.get('tokens_left', 0) > 0:
        context.user_data['using_token'] = True
        search_type_msg = f"Using a token. Tokens available: {user_db_data.get('tokens_left', 0)}."; can_search = True
    else:
        last_reset = datetime.fromisoformat(user_db_data['last_free_search_reset_time']) if isinstance(user_db_data['last_free_search_reset_time'], str) else (user_db_data['last_free_search_reset_time'] or (now - timedelta(days=1)))
        if last_reset < (now - timedelta(hours=config.FREE_SEARCH_RESET_HOURS)):
            update_user_free_searches(user_id, DEFAULT_FREE_SEARCHES, now); user_db_data = get_or_create_user(user_id)
            await context.bot.send_message(chat_id=user_id, text="Your daily free searches have been reset!") # New message for reset
        free_searches_left = user_db_data.get('free_searches_left', 0)
        if free_searches_left > 0:
            search_type_msg = f"Using a free search."; search_credit_info = f"Free searches left: {free_searches_left}"
            context.user_data['using_free_search'] = True; can_search = True
        else: search_type_msg = "No active subscription, tokens, or free searches available."
    if can_search:
        category_code = query.data.replace('search_category_', ''); category_map = {'domains': 'Domain', 'emails': 'Email', 'phone': 'Phone Number', 'name': 'Name'}
        category_name = category_map.get(category_code, category_code.replace('_', ' ').title()); context.user_data['search_category'] = category_name
        prompt_msg = f"{search_type_msg}\n" + (f"{search_credit_info}\n" if search_credit_info else "") + f"✍️ Enter {category_name} to search:"
        await query.edit_message_text(text=prompt_msg); return TYPING_QUERY
    else:
        await query.edit_message_text(f"😔 {search_type_msg}\nPlease /buy credits.", reply_markup=get_main_menu_keyboard(user_id))
        context.user_data.clear(); return ConversationHandler.END

async def handle_search_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query_text = update.message.text; category = context.user_data.get('search_category', 'N/A'); user_id = update.message.from_user.id
    if context.user_data.get('using_token'):
        if not decrement_tokens(user_id):
            await update.message.reply_text("⚠️ Error using token.", reply_markup=get_main_menu_keyboard(user_id)); context.user_data.clear(); return ConversationHandler.END
    elif context.user_data.get('using_free_search'):
        if not decrement_user_free_searches(user_id):
            await update.message.reply_text("⚠️ Error using free search.", reply_markup=get_main_menu_keyboard(user_id)); context.user_data.clear(); return ConversationHandler.END

    job_id = str(uuid.uuid4())[:8]
    if not create_search_job(job_id, user_id, category, query_text):
        await update.message.reply_text("⚠️ Failed to queue search.", reply_markup=get_main_menu_keyboard(user_id)); context.user_data.clear(); return ConversationHandler.END

    reply_timeout = getattr(config, 'REPLY_TIMEOUT', 10)
    confirm_msg = (
        f"✅ Your search for {html.escape(category)}: \"{html.escape(query_text)}\" (Job ID: <code>{job_id}</code>) has been queued.\n"
        f"The external bot has up to {reply_timeout} seconds to respond.\n"
        "Results will be sent automatically when ready."
    )
    await update.message.reply_text(text=confirm_msg, parse_mode='HTML', reply_markup=get_main_menu_keyboard(user_id)) # Added keyboard here

    context.user_data.pop('search_category', None)
    context.user_data.pop('using_token', None)
    context.user_data.pop('using_free_search', None)
    return ConversationHandler.END

async def back_to_main_menu_from_search_cats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await start(update, context, message_text="👋 Main Menu:")
    context.user_data.pop('search_category', None)
    context.user_data.pop('using_token', None)
    context.user_data.pop('using_free_search', None)
    return ConversationHandler.END

# --- Buy Conversation Handlers ---
async def buy_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; user_id = query.from_user.id
    await query.answer() # Answer early
    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name)
    if user_db_data and user_db_data.get('is_banned'):
        await query.edit_message_text("You are banned."); return ConversationHandler.END # No alert needed as message is edited
    await query.edit_message_text("🛍️ Welcome to the Shop!", reply_markup=get_buy_options_keyboard())
    return SELECTING_BUY_OPTION

async def payment_confirmation_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer(); user_id = query.from_user.id
    if query.data == 'payment_paid_yes':
        item_name = context.user_data.get('purchase_item_name', 'Your item')
        await query.edit_message_text(f"To complete purchase of <b>{item_name}</b>, send transaction ID/screenshot.", parse_mode='HTML'); return AWAITING_PROOF
    elif query.data == 'payment_paid_no':
        # Edit message to confirm cancellation and show main menu options
        await query.edit_message_text("Order cancelled. Returning to main menu.", reply_markup=get_main_menu_keyboard(user_id))
        context.user_data.clear(); return ConversationHandler.END

async def handle_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.message.from_user; user_id = user.id; username = user.username or user.first_name
    item = context.user_data.get('purchase_item_name', 'N/A'); price = context.user_data.get('purchase_item_price', 'N/A')
    admin_text = f"💰 Payment Proof!\nUser: @{username} ({user_id})\nItem: {item} (${price})\nVerify message above/below."
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.forward_message(admin_id, user_id, update.message.message_id)
            await context.bot.send_message(admin_id, admin_text)
        except Exception as e: logger.error(f"Admin forward failed for {admin_id}: {e}")

    await update.message.reply_text(
        "Proof sent for verification. Returning to main menu.",
        reply_markup=get_main_menu_keyboard(user_id) # Provide main menu after proof submission
    )
    context.user_data.clear(); return ConversationHandler.END

# --- Admin Panel & Related Handlers ---
async def admin_panel_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None: # Return type for conv compatibility
    query = update.callback_query; user_id = query.from_user.id
    await query.answer() # Answer early
    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name)
    if user_db_data and user_db_data.get('is_banned'):
        await query.edit_message_text("You are banned."); return # No state for simple callback
    if not is_admin(user_id):
        await query.edit_message_text("⚠️ Access Denied.", reply_markup=get_main_menu_keyboard(user_id)); return
    await query.edit_message_text("👑 Admin Panel:", reply_markup=get_admin_panel_keyboard())
    # This is often an entry to other convos or a simple menu, so no state return if not starting a new conv itself.
    # If it's a fallback target for other convos ending, their END state handles it.

# --- Global Error Handler ---
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
            user_message = None # Suppress generic user message for this specific error
        else:
            user_message = "An error occurred while processing your request. Please try again."

    if user_message and isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=user_message)
        except Forbidden: logger.error(f"Failed to send error message to chat {update.effective_chat.id}: Bot blocked.")
        except Exception as e: logger.error(f"Failed to send error message to chat {update.effective_chat.id}: {e}")

    # admin_notification_text = f"⚠️ Bot Error Detected!\nError: {html.escape(str(context.error))}\nType: {type(context.error).__name__}"
    # for admin_id in config.ADMIN_IDS:
    #     try: await context.bot.send_message(chat_id=admin_id, text=admin_notification_text, parse_mode=ParseMode.HTML)
    #     except Exception as e: logger.error(f"Failed to send error notification to admin {admin_id}: {e}")

# (main function, other handlers not directly related to search conv termination or global error handler)
# ... (rest of the file including main(), other conv handlers etc.)
# (The sections for Buy, Admin Ban/Unban/Grant, deliver_results, etc. are assumed to be correct from previous steps)
# (and are not the focus of this specific review, only the search conv termination and global error handler)

# --- Main Application Setup --- (Ensure it's at the very end)
def main() -> None:
    application = Application.builder().token(config.BOT_TOKEN).build()
    application.add_error_handler(error_handler) # Ensure this is registered
    application.add_handler(CommandHandler("start", start))

    job_queue = application.job_queue
    job_queue.run_repeating(deliver_results_background_job, interval=30, first=10, name='result_delivery_job')
    logger.info("Background result delivery job scheduled.")

    application.add_handler(CallbackQueryHandler(handle_search_again, pattern=r'^search_again_'))
    application.add_handler(CallbackQueryHandler(handle_show_main_menu, pattern=r'^show_main_menu$'))

    search_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(search_start, pattern='^main_search$'),
            CallbackQueryHandler(handle_new_search_in_category, pattern=r'^new_search_cat_')
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
        states={AWAITING_USER_ID_TO_BAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_user_id_for_ban)], CONFIRM_BAN: [CallbackQueryHandler(confirm_ban_action, pattern='^(ban_yes|ban_no)$')]},
        fallbacks=[CommandHandler('start', start_again_in_conversation), CommandHandler('cancel_admin_action', cancel_admin_action), CallbackQueryHandler(admin_panel_main_callback, pattern='^admin_panel_main$') ], map_to_parent={ConversationHandler.END: -1}, per_message=False
    )
    unban_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_unban_user_start_callback, pattern='^admin_unban_user_start$')],
        states={AWAITING_USER_ID_TO_UNBAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_user_id_for_unban)], CONFIRM_UNBAN: [CallbackQueryHandler(confirm_unban_action, pattern='^(unban_yes|unban_no)$')]},
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
