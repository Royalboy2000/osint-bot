import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup # Added for keyboards if used by other funcs
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler, # Added
    ContextTypes,
    ConversationHandler, # Added
    MessageHandler, # Added
    filters # Added
)
from telegram.error import Forbidden, BadRequest, NetworkError, TimedOut # Added
from telegram.constants import ParseMode # Added
import uuid # Added
from datetime import datetime, timedelta # Added
import os # Added
import time # Added
import html # Added
import json # Added

import config
from db_manager import (
    get_or_create_user,
    decrement_user_free_searches, # Assuming these might be needed by other parts later
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
    mark_job_delivered,      # For deliver_results_background_job
    get_all_undelivered_jobs, # For deliver_results_background_job
    get_job_details
)
from telegram import error as telegram_error # Added

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# State definitions (assuming they might be used by other parts, keep them for now)
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
        [InlineKeyboardButton("👥 View Users", callback_data='admin_view_users_page_1')], # Already implemented
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data='back_to_main_from_admin_panel')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_confirmation_keyboard(action_prefix: str, item_info: str = ""): # Used by admin ban/unban
    yes_callback = f'{action_prefix}_yes'
    no_callback = f'{action_prefix}_no'
    if item_info:
        yes_callback += f"_{item_info}" # e.g. ban_yes_12345 / unban_yes_12345
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
            # Ensure query is answered before potentially failing to edit
            if not update.callback_query.answered: await update.callback_query.answer(ban_message, show_alert=True)
            else: await context.bot.send_message(chat_id=user_id, text=ban_message) # If already answered, send new message
            try:
                if update.callback_query.message: # Check if message exists
                    await update.callback_query.edit_message_text(ban_message, reply_markup=None)
            except Exception as e:
                logger.debug(f"Failed to edit message for banned user in start: {e}")
        return # Important to return after handling banned user

    now = datetime.now()
    last_reset_time_str = user_db_data.get('last_free_search_reset_time')
    last_reset_time = datetime.fromisoformat(last_reset_time_str) if isinstance(last_reset_time_str, str) else (last_reset_time_str or (now - timedelta(days=1)))

    if last_reset_time < (now - timedelta(hours=config.FREE_SEARCH_RESET_HOURS)):
        update_user_free_searches(user_id, DEFAULT_FREE_SEARCHES, now)
        user_db_data = get_or_create_user(user_id) # refresh data
        if update.callback_query:
             try: # Send as new message if it's a callback context and reset occurred
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
        else: # Should not happen for non-lifetime if data is consistent
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
        if not query.answered: # Ensure answer before any potential message operations
            await query.answer()

        if not query.message or not query.message.text: # Message might be media or only keyboard
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
                else: # Other BadRequest errors
                    logger.error(f"Other BadRequest error editing message in start (callback): {e}. Sending new message as fallback.")
                    try:
                        await context.bot.send_message(chat_id=user_id, text=full_message, reply_markup=main_menu_kb, parse_mode='HTML')
                    except Exception as send_e:
                        logger.error(f"Failed to send new message after other BadRequest in start: {send_e}")
            except Exception as e: # Catch other non-BadRequest errors during edit
                logger.error(f"Unexpected error editing message in start (callback): {e}. Sending new message.")
                try:
                    await context.bot.send_message(chat_id=user_id, text=full_message, reply_markup=main_menu_kb, parse_mode='HTML')
                except Exception as send_e:
                    logger.error(f"Failed to send new message after unexpected edit error in start: {send_e}")
    elif update.message:
        await update.message.reply_text(full_message, reply_markup=main_menu_kb, parse_mode='HTML')

async def start_again_in_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id # type: ignore
    welcome_message = "Returning to main menu."

    # Call start, which will attempt to edit the message or send a new one.
    # start function itself handles ban check and full menu display.
    await start(update, context, message_text=welcome_message)

    # Clear all known conversation-specific user_data keys
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

# Placeholder for other keyboard functions if they were part of the original step 4 application
# For example: get_buy_options_keyboard, get_monthly_plans_keyboard, etc.
# These are not strictly part of search, but their definitions might be around this area.
# For now, focusing on search-related keyboards.

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

    # Admin notification (optional, can be noisy)
    # admin_notification_text = f"⚠️ Bot Error Detected!\nError: {html.escape(str(context.error))}\nType: {type(context.error).__name__}"
    # for admin_id in config.ADMIN_IDS:
    #     try: await context.bot.send_message(chat_id=admin_id, text=admin_notification_text, parse_mode=ParseMode.HTML)
    #     except Exception as e: logger.error(f"Failed to send error notification to admin {admin_id}: {e}")

# --- Buy Conversation Handlers ---
async def buy_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; user_id = query.from_user.id
    await query.answer()
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
    # Dummy prices and item names for now
    # Example: query.data could be 'buy_plan_1month_150' or 'buy_token_1_1'
    item_details = query.data.split('_')
    item_type = item_details[1] # plan or token
    item_name_parts = item_details[2:-1] # e.g. 1month or 1
    item_price = item_details[-1]

    # Reconstruct item name for display
    display_item_name = " ".join(item_name_parts).replace(item_type, "").strip().title() # e.g. "1 Month" or "1"
    if item_type == "plan":
        full_item_name = f"{display_item_name} Plan"
    else: # token
        full_item_name = f"{display_item_name} Token(s)"


    context.user_data['purchase_item_name'] = full_item_name
    context.user_data['purchase_item_price'] = item_price # Assuming price is last part

    payment_message = (
        f"You selected: <b>{full_item_name}</b> for <b>${item_price}</b>.\n"
        f"Please send exactly <code>{item_price}</code> USD equivalent in BTC to:\n" # Ideally calculate BTC amount via API
        f"<code>{config.BTC_ADDRESS}</code>\n\n"
        "Have you made the payment?"
    )
    await query.edit_message_text(payment_message, reply_markup=get_payment_confirmation_keyboard(), parse_mode='HTML')
    return AWAITING_PAYMENT_CONFIRMATION

async def payment_confirmation_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer(); user_id = query.from_user.id
    if query.data == 'payment_paid_yes':
        item_name = context.user_data.get('purchase_item_name', 'Your item')
        await query.edit_message_text(f"To complete purchase of <b>{item_name}</b>, please send a screenshot or transaction ID of your payment.", parse_mode='HTML'); return AWAITING_PROOF
    elif query.data == 'payment_paid_no':
        await query.edit_message_text("Order cancelled. Returning to main menu.", reply_markup=get_main_menu_keyboard(user_id))
        context.user_data.pop('purchase_item_name', None)
        context.user_data.pop('purchase_item_price', None)
        return ConversationHandler.END
    return ConversationHandler.END # Should not happen

async def handle_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.message.from_user; user_id = user.id; username = user.username or user.first_name
    item = context.user_data.get('purchase_item_name', 'N/A'); price = context.user_data.get('purchase_item_price', 'N/A')

    # Forward the proof message and context to admins
    admin_text = f"💰 Payment Proof from User!\nUser: {html.escape(str(username))} (ID: <code>{user_id}</code>)\nItem: {html.escape(item)} (${html.escape(str(price))})\n\nPlease verify the transaction. The proof message is above/below."

    for admin_id in config.ADMIN_IDS:
        try:
            # Forward the message containing the proof (text or photo)
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
    return SELECTING_BUY_OPTION # Go back to selecting plan or token

async def back_to_main_from_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # This calls start_again_in_conversation which handles user_data cleanup
    return await start_again_in_conversation(update, context)

# --- Admin Panel & Related Handlers ---
async def admin_panel_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: # Can also be entry to conv
    query = update.callback_query
    user_id = query.from_user.id
    if not query.answered: await query.answer()

    if not is_admin(user_id):
        await query.edit_message_text("⚠️ Access Denied.", reply_markup=get_main_menu_keyboard(user_id))
        return

    await query.edit_message_text("👑 Admin Panel:", reply_markup=get_admin_panel_keyboard())
    # If this is part of a conversation, return the appropriate state. If standalone, return None or END.
    # For now, assume it can be an entry point or a standalone menu display.

async def back_to_main_from_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await start_again_in_conversation(update, context)

# Ban User Conversation
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
        return AWAITING_USER_ID_TO_BAN # Ask again

async def confirm_ban_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    action = query.data # e.g., ban_yes_12345 or ban_no

    if action.startswith("ban_yes"):
        user_id_to_ban = context.chat_data.get('user_id_to_modify')
        if not user_id_to_ban: # Should exist
             await query.edit_message_text("Error: User ID not found in context. Please try again.", reply_markup=get_admin_panel_keyboard())
             return ConversationHandler.END

        if ban_user(user_id_to_ban):
            await query.edit_message_text(f"User {user_id_to_ban} has been banned.", reply_markup=get_admin_panel_keyboard())
        else:
            await query.edit_message_text(f"Failed to ban user {user_id_to_ban}. They may already be banned or an error occurred.", reply_markup=get_admin_panel_keyboard())
    else: # ban_no
        await query.edit_message_text("Ban action cancelled.", reply_markup=get_admin_panel_keyboard())

    context.chat_data.pop('user_id_to_modify', None)
    return ConversationHandler.END

# Unban User Conversation
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
            await query.edit_message_text("Error: User ID not found. Try again.", reply_markup=get_admin_panel_keyboard())
            return ConversationHandler.END
        if unban_user(user_id_to_unban):
            await query.edit_message_text(f"User {user_id_to_unban} has been unbanned.", reply_markup=get_admin_panel_keyboard())
        else:
            await query.edit_message_text(f"Failed to unban user {user_id_to_unban}. Not banned or error.", reply_markup=get_admin_panel_keyboard())
    else: # unban_no
        await query.edit_message_text("Unban action cancelled.", reply_markup=get_admin_panel_keyboard())

    context.chat_data.pop('user_id_to_modify', None)
    return ConversationHandler.END

# Grant Access Conversation
async def admin_grant_access_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.edit_message_text("Enter User ID to grant access to:")
    return ADMIN_GRANT_USER_ID

async def received_user_id_for_grant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        grant_user_id = int(update.message.text)
        # Verify user exists with get_or_create_user
        user_data = get_or_create_user(grant_user_id)
        if not user_data: # Should not happen if get_or_create_user works
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
    return ConversationHandler.END # Should not happen

async def grant_subscription_plan_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    plan_type = query.data.replace('grant_plan_', '').replace('_', ' ') # e.g. "1 month"
    context.chat_data['grant_plan_type'] = plan_type # Store as "1 month", "lifetime", etc.
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
    action_parts = query.data.split('_') # e.g. grant_sub_yes_UID_plan or grant_tokens_yes_UID_amount

    grant_action = action_parts[0] # grant
    grant_category = action_parts[1] # sub or tokens
    grant_confirmation = action_parts[2] # yes or no

    user_id = context.chat_data.get('grant_user_id')

    if grant_confirmation == 'yes':
        if grant_category == 'sub':
            plan_type = context.chat_data.get('grant_plan_type') # e.g. "1 month"
            # Convert plan_type to match grant_subscription's expectation if needed
            # Assuming grant_subscription takes "1_month", "3_months", "lifetime"
            internal_plan_type = plan_type.replace(' ', '_')
            grant_subscription(user_id, internal_plan_type)
            update_user_free_searches(user_id, DEFAULT_FREE_SEARCHES, datetime.now()) # Reset free searches
            await query.edit_message_text(f"Granted {plan_type.title()} subscription to user {user_id}. Free searches reset.", reply_markup=get_admin_panel_keyboard())
        elif grant_category == 'tokens':
            amount = context.chat_data.get('grant_token_amount')
            grant_tokens(user_id, amount)
            update_user_free_searches(user_id, DEFAULT_FREE_SEARCHES, datetime.now()) # Reset free searches
            await query.edit_message_text(f"Granted {amount} tokens to user {user_id}. Free searches reset.", reply_markup=get_admin_panel_keyboard())
    else: # 'no'
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

async def cancel_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # General cancel for admin convos
    query = update.callback_query
    message_text = "Admin action cancelled. Returning to Admin Panel."
    if query:
        if not query.answered: await query.answer()
        await query.edit_message_text(message_text, reply_markup=get_admin_panel_keyboard())
    elif update.message: # If called via command
        await update.message.reply_text(message_text, reply_markup=get_admin_panel_keyboard())

    # Clear all grant-specific data
    context.chat_data.pop('grant_user_id', None)
    context.chat_data.pop('grant_type', None)
    context.chat_data.pop('grant_plan_type', None)
    context.chat_data.pop('grant_token_amount', None)
    context.chat_data.pop('user_id_to_modify', None) # For ban/unban
    return ConversationHandler.END


# --- Admin Panel & Related Handlers ---
async def admin_view_users_noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles callbacks for no-operation buttons, like page display."""
    query = update.callback_query
    if query:
        await query.answer()

async def admin_view_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays a paginated list of users to the admin."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if not is_admin(user_id):
        await query.edit_message_text("⚠️ Access Denied.", reply_markup=get_main_menu_keyboard(user_id))
        return

    try:
        page = int(query.data.replace('admin_view_users_page_', ''))
    except ValueError:
        logger.error(f"Invalid page number in callback data: {query.data}")
        page = 1 # Default to page 1 on error

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
        uid = user_info.get('user_id')
        username = user_info.get('username', 'N/A')
        first_name = user_info.get('first_name', 'N/A')
        display_name = f"@{username}" if username != 'N/A' else first_name

        free_searches = user_info.get('free_searches_left', 0)
        last_reset_str = user_info.get('last_free_search_reset_time', 'Never')
        if isinstance(last_reset_str, str) and last_reset_str != 'Never':
             try:
                last_reset_dt = datetime.fromisoformat(last_reset_str)
                last_reset_formatted = last_reset_dt.strftime('%Y-%m-%d %H:%M')
             except ValueError:
                last_reset_formatted = last_reset_str
        else:
            last_reset_formatted = 'Never'

        tokens = user_info.get('tokens_left', 0)

        sub_type = user_info.get('subscription_type', 'None')
        sub_expiry_str = user_info.get('subscription_expiry_date')
        sub_status_display = "None"
        if sub_type and sub_type != 'None':
            if sub_type == 'lifetime':
                sub_status_display = "Lifetime"
            elif sub_expiry_str:
                try:
                    sub_expiry_dt = datetime.fromisoformat(sub_expiry_str)
                    if sub_expiry_dt > now:
                        sub_status_display = f"{sub_type.replace('_',' ').title()} (Expires: {sub_expiry_dt.strftime('%Y-%m-%d')})"
                    else:
                        sub_status_display = f"Expired {sub_type.replace('_',' ').title()}"
                except ValueError:
                     sub_status_display = f"{sub_type.replace('_',' ').title()} (Expiry: {sub_expiry_str})"
            else:
                sub_status_display = f"{sub_type.replace('_',' ').title()} (No expiry date)"

        is_banned_val = user_info.get('is_banned', False)
        banned_status = "Yes" if is_banned_val else "No"

        user_lines = [
            f"👤 <b>{html.escape(display_name)}</b> (ID: <code>{uid}</code>)",
            f"  🆓 Free Searches: {free_searches} (Reset: {last_reset_formatted})",
            f"  🪙 Tokens: {tokens}",
            f"  📜 Subscription: {html.escape(sub_status_display)}",
            f"  🚫 Banned: {banned_status}"
        ]
        message_lines.append("\n".join(user_lines))

    message_text = "\n\n".join(message_lines)

    total_pages = (total_users + USER_PAGE_SIZE - 1) // USER_PAGE_SIZE
    pagination_buttons = []
    if page > 1:
        pagination_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f'admin_view_users_page_{page - 1}'))

    if total_pages > 0 :
        pagination_buttons.append(InlineKeyboardButton(f"Page {page}/{total_pages}", callback_data='admin_view_users_noop'))

    if page < total_pages:
        pagination_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f'admin_view_users_page_{page + 1}'))

    keyboard = []
    if pagination_buttons:
        keyboard.append(pagination_buttons)
    # Assume admin_panel_main callback exists or will be added for this button
    keyboard.append([InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data='admin_panel_main')])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(text=message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

# --- Helper function for search checks ---
async def _can_perform_search(user_id: int, context: ContextTypes.DEFAULT_TYPE, query_update: Update = None) -> tuple[bool, str, bool, bool]:
    """
    Checks if a user can perform a search (rate limit, credits).
    Returns: (can_search, message, using_token, using_free_search)
    If query_update (CallbackQuery or Message) is provided and daily searches are reset, it will send a message.
    Updates context.user_data['last_search_init_time'] if search can proceed past rate limit.
    """
    # 1. Rate Limit Check for initiating any search action
    current_time = time.time()
    last_search_action_time = context.user_data.get('last_search_init_time', 0)
    if (current_time - last_search_action_time) < RATE_LIMIT_SECONDS:
        wait_time = int(RATE_LIMIT_SECONDS - (current_time - last_search_action_time))
        return False, f"Please wait {wait_time} more seconds before attempting another search action.", False, False

    context.user_data['last_search_init_time'] = current_time

    # 2. Credit Check (Subscription, Tokens, Free Searches)
    user_db_data = get_or_create_user(user_id)
    now = datetime.now()
    has_active_sub = False
    sub_type = user_db_data.get('subscription_type')
    sub_expiry_str = user_db_data.get('subscription_expiry_date')

    if sub_type and sub_type != 'None':
        if sub_type == 'lifetime':
            has_active_sub = True
        elif sub_expiry_str:
            sub_expiry_dt = datetime.fromisoformat(sub_expiry_str) if isinstance(sub_expiry_str, str) else sub_expiry_str
            if sub_expiry_dt and sub_expiry_dt > now:
                has_active_sub = True

    using_token = False
    using_free_search = False
    search_type_msg = ""
    search_credit_info = ""

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
                 try:
                     await context.bot.send_message(chat_id=query_update.effective_chat.id, text="Your daily free searches have been reset!")
                 except Exception as e:
                     logger.info(f"Failed to send free search reset message to {user_id}: {e}")

        free_searches_left = user_db_data.get('free_searches_left', 0)
        if free_searches_left > 0:
            search_credit_info = f"Free searches left: {free_searches_left}"
            search_type_msg = f"Using a free search. {search_credit_info}"
            using_free_search = True
            return True, search_type_msg, False, using_free_search
        else:
            return False, "😔 No active subscription, tokens, or free searches available. Please /buy credits or wait for daily free searches to reset.", False, False

# --- Search Conversation Handlers ---
async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name)
    if user_db_data and user_db_data.get('is_banned'):
        await query.answer("You are banned.", show_alert=True)
        try:
            await query.edit_message_text("You are banned.")
        except BadRequest:
            logger.debug(f"Could not edit message for banned user {user_id} in search_start.")
        return ConversationHandler.END

    current_time = time.time()
    last_search_button_click_time = context.user_data.get('last_search_button_click_time', 0)
    if (current_time - last_search_button_click_time) < RATE_LIMIT_SECONDS:
        wait_time = int(RATE_LIMIT_SECONDS - (current_time - last_search_button_click_time))
        await query.answer(f"Please wait {wait_time} more seconds.", show_alert=True)
        return ConversationHandler.END
    context.user_data['last_search_button_click_time'] = current_time

    context.user_data.pop('search_category', None)
    context.user_data.pop('using_token', None)
    context.user_data.pop('using_free_search', None)

    await query.edit_message_text(text="👇 Please select a search category:", reply_markup=get_search_categories_keyboard())
    return SELECTING_CATEGORY

async def select_search_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    can_search, message, using_token, using_free_search = await _can_perform_search(user_id, context, query_update=query)

    if not can_search:
        if "Please wait" in message:
             await query.answer(message, show_alert=True)
        else:
            await query.edit_message_text(f"{message}", reply_markup=get_main_menu_keyboard(user_id))

        context.user_data.pop('search_category', None)
        context.user_data.pop('using_token', None)
        context.user_data.pop('using_free_search', None)
        return ConversationHandler.END

    context.user_data['using_token'] = using_token
    context.user_data['using_free_search'] = using_free_search

    category_code = query.data.replace('search_category_', '')
    category_map = {'domains': 'Domain', 'emails': 'Email', 'phone': 'Phone Number', 'name': 'Name'}
    category_name = category_map.get(category_code, category_code.replace('_', ' ').title())
    context.user_data['search_category'] = category_name

    prompt_msg = f"{message}\n✍️ Enter {category_name} to search:"
    await query.edit_message_text(text=prompt_msg)
    return TYPING_QUERY

async def handle_search_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query_text = update.message.text
    category = context.user_data.get('search_category', 'N/A')
    user_id = update.message.from_user.id

    if category == 'N/A':
        logger.warning(f"User {user_id} reached handle_search_query without a category set.")
        await update.message.reply_text("⚠️ An error occurred: search category not selected. Please start over.", reply_markup=get_main_menu_keyboard(user_id))
        context.user_data.pop('using_token', None)
        context.user_data.pop('using_free_search', None)
        return ConversationHandler.END

    if context.user_data.get('using_token'):
        if not decrement_tokens(user_id):
            await update.message.reply_text("⚠️ Error using token. Your token count might be outdated. Please try starting the search again.", reply_markup=get_main_menu_keyboard(user_id))
            context.user_data.pop('search_category', None)
            context.user_data.pop('using_token', None)
            context.user_data.pop('using_free_search', None)
            return ConversationHandler.END
    elif context.user_data.get('using_free_search'):
        if not decrement_user_free_searches(user_id):
            await update.message.reply_text("⚠️ Error using free search. Your free search count might be outdated. Please try starting the search again.", reply_markup=get_main_menu_keyboard(user_id))
            context.user_data.pop('search_category', None)
            context.user_data.pop('using_token', None)
            context.user_data.pop('using_free_search', None)
            return ConversationHandler.END

    job_id = str(uuid.uuid4())[:8]
    if not create_search_job(job_id, user_id, category, query_text):
        await update.message.reply_text("⚠️ Failed to queue your search due to a system error. Please try again later.", reply_markup=get_main_menu_keyboard(user_id))
        context.user_data.pop('search_category', None)
        context.user_data.pop('using_token', None)
        context.user_data.pop('using_free_search', None)
        return ConversationHandler.END

    confirm_msg = (
        f"✅ Your search for {html.escape(category)}: \"{html.escape(query_text)}\" (Job ID: <code>{job_id}</code>) has been queued.\n\n"
        "This may take some time depending on the search complexity and target system load.\n"
        "Results will be sent automatically as soon as they are ready. You can continue using other bot features."
    )
    await update.message.reply_text(text=confirm_msg, parse_mode='HTML', reply_markup=get_main_menu_keyboard(user_id))

    context.user_data.pop('search_category', None)
    context.user_data.pop('using_token', None)
    context.user_data.pop('using_free_search', None)
    return ConversationHandler.END

async def back_to_main_menu_from_search_cats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await start_again_in_conversation(update, context)

# --- Handlers for "Search Again", "New Search in Category", and "Show Main Menu" ---
async def handle_search_again(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query: return
    await query.answer()
    user_id = query.from_user.id

    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name)
    if user_db_data and user_db_data.get('is_banned'):
        await query.answer("You are banned.", show_alert=True)
        try: await query.edit_message_text("You are banned from performing this action.")
        except BadRequest: pass
        return

    if not get_job_details:
        logger.error("get_job_details function is not available/imported in bot.py.")
        await context.bot.send_message(chat_id=user_id, text="An internal error occurred.", reply_markup=get_main_menu_keyboard(user_id))
        return

    job_id_to_repeat = query.data.replace("search_again_", "")
    original_job = get_job_details(job_id_to_repeat)

    if not original_job:
        await context.bot.send_message(chat_id=user_id, text=f"Could not find details for Job ID: {job_id_to_repeat}.", reply_markup=get_main_menu_keyboard(user_id))
        return

    category = original_job.get('category')
    query_text = original_job.get('query_text')

    if not category or not query_text:
        await context.bot.send_message(chat_id=user_id, text=f"Original job {job_id_to_repeat} missing details.", reply_markup=get_main_menu_keyboard(user_id))
        return

    can_search, message, using_token, using_free_search = await _can_perform_search(user_id, context, query_update=query)

    if not can_search:
        if "Please wait" in message: await query.answer(message, show_alert=True)
        else: await context.bot.send_message(chat_id=user_id, text=message, reply_markup=get_main_menu_keyboard(user_id))
        return

    if using_token:
        if not decrement_tokens(user_id):
            await context.bot.send_message(chat_id=user_id, text="⚠️ Error using token.", reply_markup=get_main_menu_keyboard(user_id))
            return
    elif using_free_search:
        if not decrement_user_free_searches(user_id):
            await context.bot.send_message(chat_id=user_id, text="⚠️ Error using free search.", reply_markup=get_main_menu_keyboard(user_id))
            return

    new_job_id = str(uuid.uuid4())[:8]
    if not create_search_job(new_job_id, user_id, category, query_text):
        logger.error(f"Failed to create search job {new_job_id} for user {user_id} after credit decrement.")
        await context.bot.send_message(chat_id=user_id, text="⚠️ Failed to queue repeated search. Contact support.", reply_markup=get_main_menu_keyboard(user_id))
        return

    confirm_msg = (
        f"✅ Your repeated search for {html.escape(category)}: \"{html.escape(query_text)}\" (New Job ID: <code>{new_job_id}</code>) has been queued.\n\n"
        "Results will be sent automatically when ready."
    )
    await context.bot.send_message(chat_id=user_id, text=confirm_msg, parse_mode='HTML', reply_markup=get_main_menu_keyboard(user_id))

async def handle_new_search_in_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query: return ConversationHandler.END
    await query.answer()
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

    context.user_data['using_token'] = using_token
    context.user_data['using_free_search'] = using_free_search

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
    if query and not query.answered: await query.answer()
    return await start_again_in_conversation(update, context)

async def deliver_results_background_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Fetches undelivered jobs from the database and attempts to deliver results or error messages to users.
    """
    try:
        undelivered_jobs = get_all_undelivered_jobs()
        if not undelivered_jobs:
            return

        for job in undelivered_jobs:
            job_id = job.get('job_id')
            user_id = job.get('user_id')
            status = job.get('status')
            result_file_path = job.get('result_file_path')
            error_message_from_job = job.get('error_message')

            if not job_id or not user_id:
                logger.error(f"Job missing job_id or user_id in undelivered_jobs: {job}")
                continue

            try:
                if status == 'completed':
                    if result_file_path and os.path.exists(result_file_path):
                        try:
                            with open(result_file_path, 'rb') as f_doc:
                                await context.bot.send_document(chat_id=user_id, document=f_doc,
                                                                caption=f"✅ Results for your search job <code>{job_id}</code>.",
                                                                parse_mode=ParseMode.HTML)
                            mark_job_delivered(job_id)
                            logger.info(f"Successfully sent results for job {job_id} to user {user_id}.")
                        except FileNotFoundError:
                            logger.error(f"Result file {result_file_path} not found for job {job_id}.")
                            await context.bot.send_message(chat_id=user_id,
                                                           text=f"⚠️ Error delivering results for job <code>{job_id}</code>: Result file is missing.",
                                                           parse_mode=ParseMode.HTML)
                            mark_job_delivered(job_id)
                        except telegram_error.Forbidden:
                            logger.warning(f"User {user_id} blocked the bot. Marking job {job_id} as delivered.")
                            mark_job_delivered(job_id)
                        except (telegram_error.BadRequest, telegram_error.NetworkError, TimedOut) as e_telegram:
                            logger.error(f"Telegram API error sending results for job {job_id} to user {user_id}: {type(e_telegram).__name__}. Retrying.")
                        except Exception as e_send:
                            logger.error(f"Unexpected error sending results for job {job_id} to user {user_id}: {e_send}. Retrying.")
                    else:
                        logger.warning(f"Job {job_id} completed but result file '{result_file_path}' is missing.")
                        await context.bot.send_message(chat_id=user_id,
                                                       text=f"⚠️ Job <code>{job_id}</code> completed, but issue retrieving results. Contact support.",
                                                       parse_mode=ParseMode.HTML)
                        mark_job_delivered(job_id)

                elif status == 'failed':
                    user_facing_error = "An issue occurred with your search." # Default generic message
                    if error_message_from_job:
                        if "Timeout: The target bot did not respond" in error_message_from_job:
                            # This specific timeout message from user_client.py is user-friendly
                            user_facing_error = html.escape(error_message_from_job)
                        else:
                            # For other errors caught by user_client.py and stored in error_message,
                            # log the detailed error but provide a more generic message to the user.
                            logger.warning(f"Job {job_id} failed with specific error from user_client: {error_message_from_job}. Showing generic message to user.")
                            user_facing_error = "The search task encountered an unexpected issue from the source."
                    else: # No error_message_from_job
                        user_facing_error = "No specific error message was provided by the search task."

                    await context.bot.send_message(chat_id=user_id,
                                                   text=f"❌ Your search job <code>{job_id}</code> failed. {user_facing_error}",
                                                   parse_mode=ParseMode.HTML)
                    mark_job_delivered(job_id)
                    logger.info(f"Sent failure notification for job {job_id} to user {user_id}. Original error from job record: {error_message_from_job if error_message_from_job else 'N/A'}")
                else: # Unknown status
                    logger.warning(f"Job {job_id} has unknown status '{status}'. Marking as delivered to avoid loop.") # Corrected comment
                    mark_job_delivered(job_id)
            except telegram_error.Forbidden: # Outer Forbidden
                logger.warning(f"User {user_id} blocked bot (job {job_id}). Marking delivered.")
                mark_job_delivered(job_id)
            except (telegram_error.BadRequest, telegram_error.NetworkError, TimedOut) as e_outer_telegram:
                logger.error(f"Outer Telegram API error for user {user_id}, job {job_id}: {type(e_outer_telegram).__name__}. Retrying.")
            except Exception as e_outer: # Outer other exceptions
                logger.error(f"Outer error processing job {job_id} for user {user_id}: {e_outer}. Retrying.")
    except Exception as e_critical:
        logger.error(f"Critical error in deliver_results_background_job: {e_critical}", exc_info=True)


def main() -> None:
    application = Application.builder().token(config.BOT_TOKEN).build()
    application.add_error_handler(error_handler)
    application.add_handler(CommandHandler("start", start))

    job_queue = application.job_queue
    job_queue.run_repeating(deliver_results_background_job, interval=30, first=10, name='result_delivery_job')
    logger.info("Background result delivery job scheduled.")

    # Standalone handlers for search again and show main menu
    application.add_handler(CallbackQueryHandler(handle_search_again, pattern=r'^search_again_'))
    application.add_handler(CallbackQueryHandler(handle_show_main_menu, pattern=r'^show_main_menu$'))

    # Search Conversation Handler
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
            CommandHandler('start', start_again_in_conversation),
            CallbackQueryHandler(handle_show_main_menu, pattern='^show_main_menu$')
        ],
        per_message=False
    )
    application.add_handler(search_conv_handler)

    # (Add other conversation handlers like buy, admin operations here later)

    # Buy Conversation Handler
    buy_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(buy_start, pattern='^main_buy$')],
        states={
            SELECTING_BUY_OPTION: [CallbackQueryHandler(select_buy_option, pattern='^(buy_monthly_plans|buy_tokens)$')],
            SELECTING_PLAN: [
                CallbackQueryHandler(select_plan_or_token, pattern='^buy_plan_'),
                CallbackQueryHandler(back_to_buy_options, pattern='^back_to_buy_options$') # Back from plan selection
            ],
            SELECTING_TOKEN_AMOUNT: [
                CallbackQueryHandler(select_plan_or_token, pattern='^buy_token_'),
                CallbackQueryHandler(back_to_buy_options, pattern='^back_to_buy_options$') # Back from token selection
            ],
            AWAITING_PAYMENT_CONFIRMATION: [CallbackQueryHandler(payment_confirmation_prompt, pattern='^(payment_paid_yes|payment_paid_no)$')],
            AWAITING_PROOF: [MessageHandler(filters.TEXT | filters.PHOTO & ~filters.COMMAND, handle_payment_proof)]
        },
        fallbacks=[
            CallbackQueryHandler(back_to_main_from_buy, pattern='^back_to_main_from_buy$'),
            CommandHandler('start', start_again_in_conversation)
        ],
        per_message=False
    )
    application.add_handler(buy_conv_handler)

    # Admin Conversation Handlers
    # Ban User
    ban_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_ban_user_start_callback, pattern='^admin_ban_user_start$')],
        states={
            AWAITING_USER_ID_TO_BAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_user_id_for_ban)],
            CONFIRM_BAN: [CallbackQueryHandler(confirm_ban_action, pattern=r'^(ban_yes_|ban_no$)')] # Regex for ban_yes_UID
        },
        fallbacks=[CallbackQueryHandler(cancel_admin_action, pattern='^admin_panel_main$'), CommandHandler('start', start_again_in_conversation)], # Go to admin panel on cancel
        map_to_parent={ConversationHandler.END: ConversationHandler.END} # End completely
    )
    # Unban User
    unban_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_unban_user_start_callback, pattern='^admin_unban_user_start$')],
        states={
            AWAITING_USER_ID_TO_UNBAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_user_id_for_unban)],
            CONFIRM_UNBAN: [CallbackQueryHandler(confirm_unban_action, pattern=r'^(unban_yes_|unban_no$)')]
        },
        fallbacks=[CallbackQueryHandler(cancel_admin_action, pattern='^admin_panel_main$'), CommandHandler('start', start_again_in_conversation)],
        map_to_parent={ConversationHandler.END: ConversationHandler.END}
    )
    # Grant Access
    grant_access_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_grant_access_start_callback, pattern='^admin_grant_access_start$')],
        states={
            ADMIN_GRANT_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_user_id_for_grant)],
            ADMIN_GRANT_TYPE: [CallbackQueryHandler(grant_type_selected, pattern='^grant_type_')],
            ADMIN_GRANT_SUB_PLAN: [
                CallbackQueryHandler(grant_subscription_plan_selected, pattern='^grant_plan_'),
                CallbackQueryHandler(grant_back_to_type_selected, pattern='^grant_back_to_type$') # Back to type selection
            ],
            ADMIN_GRANT_TOKEN_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, grant_token_amount_received)],
            ADMIN_CONFIRM_GRANT: [CallbackQueryHandler(confirm_grant_action, pattern=r'^(grant_sub_yes_|grant_tokens_yes_|grant_sub_no|grant_tokens_no$)')]
        },
        fallbacks=[
            CallbackQueryHandler(cancel_admin_action, pattern='^grant_cancel_to_admin_panel$'), # Explicit cancel to admin panel
            CallbackQueryHandler(cancel_admin_action, pattern='^admin_panel_main$'), # General cancel
            CommandHandler('start', start_again_in_conversation)
        ],
        map_to_parent={ConversationHandler.END: ConversationHandler.END}
    )

    # Register Admin Panel main handlers
    application.add_handler(CallbackQueryHandler(admin_panel_main_callback, pattern='^admin_panel_main$'))
    application.add_handler(CallbackQueryHandler(back_to_main_from_admin_panel, pattern='^back_to_main_from_admin_panel$'))

    # Register Admin View Users handlers (already added in previous step, ensure no duplication)
    # application.add_handler(CallbackQueryHandler(admin_view_users_callback, pattern=r'^admin_view_users_page_'))
    # application.add_handler(CallbackQueryHandler(admin_view_users_noop_callback, pattern=r'^admin_view_users_noop$'))
    # These are already present from the previous successful diff.

    application.add_handler(ban_conv_handler)
    application.add_handler(unban_conv_handler)
    application.add_handler(grant_access_conv_handler)

    application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
