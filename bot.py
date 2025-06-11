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
import uuid
from datetime import datetime, timedelta
import os # Added for file existence check

# Assuming config.py is in the same directory and has the variables
import config
# Import specific db_manager functions and constants
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
    get_completed_jobs_for_user, # Added
    mark_job_delivered         # Added
)

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- States for Conversations ---
SELECTING_CATEGORY, TYPING_QUERY = range(2)
SELECTING_BUY_OPTION, SELECTING_PLAN, SELECTING_TOKEN_AMOUNT, AWAITING_PAYMENT_CONFIRMATION, AWAITING_PROOF = range(10, 15)
AWAITING_USER_ID_TO_BAN, CONFIRM_BAN, AWAITING_USER_ID_TO_UNBAN, CONFIRM_UNBAN = range(20, 24)
ADMIN_GRANT_USER_ID, ADMIN_GRANT_TYPE, ADMIN_GRANT_SUB_PLAN, ADMIN_GRANT_TOKEN_AMOUNT, ADMIN_CONFIRM_GRANT = range(30, 35)

USER_PAGE_SIZE = 5

# --- Admin Check Function ---
def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS

# --- Keyboards ---
# (All keyboard functions remain the same as previous version)
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

def get_confirmation_keyboard(action_prefix: str):
    keyboard = [
        [
            InlineKeyboardButton(f"Yes, {action_prefix.capitalize()}", callback_data=f'{action_prefix}_yes'),
            InlineKeyboardButton(f"No, Cancel", callback_data=f'{action_prefix}_no')
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
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = update.effective_user.id
    user_db_data = get_or_create_user(user_id, user.username, user.first_name)

    if user_db_data and user_db_data.get('is_banned'):
        await update.message.reply_text("You are banned from using this bot.")
        return

    now = datetime.now()
    last_reset_time_str = user_db_data.get('last_free_search_reset_time')
    if isinstance(last_reset_time_str, str):
        last_reset_time = datetime.fromisoformat(last_reset_time_str)
    elif isinstance(last_reset_time_str, datetime):
        last_reset_time = last_reset_time_str
    else:
        last_reset_time = now - timedelta(days=1)

    if last_reset_time < (now - timedelta(hours=config.FREE_SEARCH_RESET_HOURS)):
        update_user_free_searches(user_id, DEFAULT_FREE_SEARCHES, now)
        user_db_data = get_or_create_user(user_id)
        logger.info(f"Free searches reset for user {user_id}.")
        if update.callback_query:
             await context.bot.send_message(chat_id=user_id, text="Your daily free searches have been reset!")

    searches_left_display = user_db_data.get('free_searches_left', 0)
    tokens_left_display = user_db_data.get('tokens_left', 0)
    sub_type = user_db_data.get('subscription_type', 'None')
    sub_expiry_str = user_db_data.get('subscription_expiry_date')
    sub_status = "None"

    if sub_type and sub_type != 'None':
        if sub_type == 'lifetime':
            sub_status = "Lifetime"
        elif sub_expiry_str:
            sub_expiry_dt = datetime.fromisoformat(sub_expiry_str) if isinstance(sub_expiry_str, str) else sub_expiry_str
            if sub_expiry_dt > now:
                sub_status = f"{sub_type.replace('_', ' ').title()} (Expires: {sub_expiry_dt.strftime('%Y-%m-%d')})"
            else:
                sub_status = f"Expired {sub_type.replace('_', ' ').title()}"

    welcome_text = (
        f"👋 Hello {user.first_name}!\n\n"
        "I am your advanced search assistant.\n\n"
        f"Subscription: <b>{sub_status}</b>\n"
        f"Tokens: <b>{tokens_left_display}</b>\n"
        f"Free Searches Today: <b>{searches_left_display}</b>\n\n"
        "Use the buttons below to get started."
    )

    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(welcome_text, reply_markup=get_main_menu_keyboard(user_id), parse_mode='HTML')
        except Exception as e:
            logger.error(f"Error editing message in start callback: {e}")
            await context.bot.send_message(chat_id=user_id, text=welcome_text, reply_markup=get_main_menu_keyboard(user_id), parse_mode='HTML')
    else:
        await update.message.reply_text(welcome_text, reply_markup=get_main_menu_keyboard(user_id), parse_mode='HTML')

async def start_again_in_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    user_db_data = get_or_create_user(user_id, update.effective_user.username, update.effective_user.first_name)
    if user_db_data and user_db_data.get('is_banned'):
        text_to_send = "You are banned from using this bot."
        if update.callback_query:
            await update.callback_query.answer("You are banned.", show_alert=True)
            try:
                await update.callback_query.message.edit_text(text_to_send)
            except Exception as e:
                await context.bot.send_message(chat_id=user_id, text=text_to_send)
        else:
            await update.message.reply_text(text_to_send)
        return ConversationHandler.END

    await start(update, context)
    context.user_data.clear()
    context.chat_data.clear()
    return ConversationHandler.END

# --- Search Conversation Handlers ---
# (search_start, select_search_category, handle_search_query, back_to_main_menu_from_search_cats remain the same)
async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = query.from_user.id
    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name)
    if user_db_data and user_db_data.get('is_banned'):
        await query.answer("You are banned.", show_alert=True)
        await query.edit_message_text("You are banned from using this bot.")
        return ConversationHandler.END

    await query.answer()
    await query.edit_message_text(text="👇 Please select a search category:", reply_markup=get_search_categories_keyboard())
    return SELECTING_CATEGORY

async def select_search_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name)
    now = datetime.now()

    sub_type = user_db_data.get('subscription_type')
    sub_expiry_str = user_db_data.get('subscription_expiry_date')
    has_active_sub = False
    if sub_type and sub_type != 'None':
        if sub_type == 'lifetime':
            has_active_sub = True
        elif sub_expiry_str:
            sub_expiry_dt = datetime.fromisoformat(sub_expiry_str) if isinstance(sub_expiry_str, str) else sub_expiry_str
            if sub_expiry_dt > now:
                has_active_sub = True

    search_type_message = ""
    can_search = False
    search_credit_info = ""

    if has_active_sub:
        search_type_message = "Using active subscription."
        can_search = True
    elif user_db_data.get('tokens_left', 0) > 0:
        if decrement_tokens(user_id): # Decrement happens here
            user_db_data = get_or_create_user(user_id) # Refresh data
            search_type_message = f"Using a token. Tokens remaining: {user_db_data.get('tokens_left', 0)}."
            can_search = True
        else:
            search_type_message = "Tried to use a token, but failed."
            can_search = False
    else:
        last_reset_time_str = user_db_data.get('last_free_search_reset_time')
        last_reset_time = datetime.fromisoformat(last_reset_time_str) if isinstance(last_reset_time_str, str) else last_reset_time_str
        if not last_reset_time or last_reset_time < (now - timedelta(hours=config.FREE_SEARCH_RESET_HOURS)):
            update_user_free_searches(user_id, DEFAULT_FREE_SEARCHES, now)
            user_db_data = get_or_create_user(user_id)
            logger.info(f"Free searches reset for user {user_id} during category selection.")
            await query.message.reply_text("Your daily free searches have been reset!", quote=False)

        free_searches_left = user_db_data.get('free_searches_left', 0)
        if free_searches_left > 0:
            search_type_message = f"Using a free search."
            search_credit_info = f"Free searches left: {free_searches_left}"
            context.user_data['using_free_search'] = True
            can_search = True
        else:
            search_type_message = "No active subscription, tokens, or free searches available."
            can_search = False

    if can_search:
        category_code = query.data.replace('search_category_', '')
        category_map = {'domains': 'Domain', 'emails': 'Email', 'phone': 'Phone Number', 'name': 'Name'}
        category_name = category_map.get(category_code, 'Unknown Category')
        context.user_data['search_category'] = category_name

        prompt_message = f"{search_type_message}\n"
        if search_credit_info:
            prompt_message += f"{search_credit_info}\n"
        prompt_message += f"✍️ Please enter the {category_name} you want to search for:"

        await query.edit_message_text(text=prompt_message)
        return TYPING_QUERY
    else:
        await query.edit_message_text(
            text=f"😔 {search_type_message}\n\nPlease /buy a subscription or tokens to continue searching.",
            reply_markup=get_main_menu_keyboard(user_id)
        )
        context.user_data.clear()
        return ConversationHandler.END

async def handle_search_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query_text = update.message.text
    category = context.user_data.get('search_category', 'Unknown Category')
    user_id = update.message.from_user.id

    if context.user_data.get('using_free_search'):
        if not decrement_user_free_searches(user_id):
            await update.message.reply_text(
                "⚠️ An issue occurred with your free search credits. Please try starting the search again.",
                reply_markup=get_main_menu_keyboard(user_id)
            )
            context.user_data.clear()
            return ConversationHandler.END
        context.user_data.pop('using_free_search', None)

    job_id = str(uuid.uuid4())[:8]

    if not create_search_job(job_id, user_id, category, query_text):
        await update.message.reply_text(
            "⚠️ Failed to queue your search due to a server error. Please try again later.",
            reply_markup=get_main_menu_keyboard(user_id)
        )
        context.user_data.clear()
        return ConversationHandler.END

    await update.message.reply_text(
        f"✅ Your search for {category}: \"{query_text}\" has been initiated with Job ID: {job_id}.\n"
        f"Results will be processed and sent to you. Use /myresults to check.\n\n" # Added info about /myresults
        "Returning to main menu..."
    )
    await context.bot.send_message(chat_id=user_id, text="👋 Main Menu:", reply_markup=get_main_menu_keyboard(user_id))
    context.user_data.clear()
    return ConversationHandler.END

async def back_to_main_menu_from_search_cats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await start(update, context)
    context.user_data.clear()
    return ConversationHandler.END

# --- Buy Conversation Handlers ---
# (buy_start, select_buy_option, select_plan_or_token, payment_confirmation_prompt, handle_payment_proof, back_to_buy_options, back_to_main_from_buy remain the same)
async def buy_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = query.from_user.id
    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name)
    if user_db_data and user_db_data.get('is_banned'):
        await query.answer("You are banned.", show_alert=True)
        await query.edit_message_text("You are banned from using this bot.")
        return ConversationHandler.END

    await query.answer()
    await query.edit_message_text(
        text="🛍️ Welcome to the Shop!\n\nHow would you like to enhance your search capabilities?",
        reply_markup=get_buy_options_keyboard()
    )
    return SELECTING_BUY_OPTION

async def select_buy_option(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == 'buy_monthly_plans':
        await query.edit_message_text(text="🗓️ Select a Monthly Plan:", reply_markup=get_monthly_plans_keyboard())
        return SELECTING_PLAN
    elif query.data == 'buy_tokens':
        await query.edit_message_text(text="🪙 Select Token Amount:", reply_markup=get_token_options_keyboard())
        return SELECTING_TOKEN_AMOUNT
    logger.warning(f"Unexpected query.data in select_buy_option: {query.data}")
    await start(update, context)
    return ConversationHandler.END


async def select_plan_or_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    parts = query.data.split('_')
    item_type_identifier = parts[1]
    item_details = parts[2]
    item_price = parts[3]

    full_item_name = ""
    if item_type_identifier == "plan":
        if item_details == "1month": full_item_name = "1 Month Plan"
        elif item_details == "3months": full_item_name = "3 Months Plan"
        elif item_details == "lifetime": full_item_name = "Lifetime Plan"
    elif item_type_identifier == "token":
        full_item_name = f"{item_details} Token(s)"

    context.user_data['purchase_item_name'] = full_item_name
    context.user_data['purchase_item_price'] = item_price

    payment_message = (
        f"You selected: <b>{full_item_name}</b> for <b>${item_price}</b>.\n\n"
        f"Please send <code>${item_price}</code> in BTC to the address:\n<code>{config.BTC_ADDRESS}</code>\n\n"
        "Then, click '<b>Yes, I have paid</b>' below."
    )
    await query.edit_message_text(
        text=payment_message,
        reply_markup=get_payment_confirmation_keyboard(),
        parse_mode='HTML'
    )
    return AWAITING_PAYMENT_CONFIRMATION

async def payment_confirmation_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == 'payment_paid_yes':
        item_name = context.user_data.get('purchase_item_name', 'Your selected item')
        await query.edit_message_text(
            text=f"Great! To complete your purchase of <b>{item_name}</b>, please send a single message now with your transaction ID or a screenshot of your payment.\n\n"
                 "(Your message will be forwarded to an admin for verification.)",
            parse_mode='HTML'
        )
        return AWAITING_PROOF
    elif query.data == 'payment_paid_no':
        await query.edit_message_text(
            text="No problem. Your order is pending. Please complete the payment when you're ready and start the process again.\n\nReturning to main menu.",
        )
        await start(update, context)
        context.user_data.clear()
        return ConversationHandler.END

async def handle_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.message.from_user
    user_id = user.id
    username = user.username or user.first_name

    item_name = context.user_data.get('purchase_item_name', 'Unknown Item')
    item_price = context.user_data.get('purchase_item_price', 'N/A')

    admin_notification_text = (
        f"💰 New Payment Proof Received!\n\n"
        f"User: @{username} (ID: {user_id})\n"
        f"Attempted Purchase: {item_name} for ${item_price}\n\n"
        "The message above/below is their submitted proof. Please verify and grant access if payment is confirmed."
    )
    logger.info(f"User @{username} (ID: {user_id}) submitted proof for {item_name}.")

    for admin_id_val in config.ADMIN_IDS:
        try:
            await context.bot.forward_message(
                chat_id=admin_id_val,
                from_chat_id=update.message.chat_id,
                message_id=update.message.message_id
            )
            await context.bot.send_message(
                chat_id=admin_id_val,
                text=admin_notification_text
            )
        except Exception as e:
            logger.error(f"Failed to forward payment proof to admin {admin_id_val}: {e}")

    await update.message.reply_text(
        "Thank you! Your payment proof has been submitted and sent to the admins for verification. "
        "You will be notified once it's confirmed and access is granted.\n\nReturning to main menu."
    )
    await context.bot.send_message(chat_id=user_id, text="👋 Main Menu:", reply_markup=get_main_menu_keyboard(user_id))
    context.user_data.clear()
    return ConversationHandler.END


async def back_to_buy_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        text="🛍️ Welcome to the Shop!\n\nHow would you like to enhance your search capabilities?",
        reply_markup=get_buy_options_keyboard()
    )
    return SELECTING_BUY_OPTION

async def back_to_main_from_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await start(update, context)
    context.user_data.clear()
    return ConversationHandler.END

# --- Admin Panel & Related Handlers ---
# (admin_panel_main_callback, back_to_main_from_admin_panel, admin_view_users_noop_callback, admin_view_users_callback remain the same)
# (Ban/Unban/Grant Access conversations remain the same)
async def admin_panel_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id

    user_db_data = get_or_create_user(user_id, query.from_user.username, query.from_user.first_name)
    if user_db_data and user_db_data.get('is_banned'):
        await query.answer("You are banned.", show_alert=True)
        await query.edit_message_text(text="You are banned from using this bot.")
        return

    await query.answer()
    if not is_admin(user_id):
        await query.edit_message_text(text="⚠️ Access Denied. This area is for admins only.", reply_markup=get_main_menu_keyboard(user_id))
        return
    await query.edit_message_text(text="👑 Admin Panel:", reply_markup=get_admin_panel_keyboard())

async def back_to_main_from_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await start(update, context)
    return ConversationHandler.END

async def admin_view_users_noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

async def admin_view_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.edit_message_text("Access Denied.", reply_markup=get_main_menu_keyboard(query.from_user.id))
        return

    page = 1
    if query.data.startswith('admin_view_users_page_'):
        try:
            page = int(query.data.split('_')[-1])
        except ValueError:
            page = 1

    users_on_page, total_users = get_users_for_view(page=page, page_size=USER_PAGE_SIZE)

    message_text = f"👥 <b>Users List - Page {page}</b>\n\n"
    if not users_on_page and total_users == 0:
        message_text = "No users found in the database."
    elif not users_on_page and total_users > 0:
        message_text = f"No users on this page (Page {page}). Try going back or to page 1."
    else:
        for user in users_on_page:
            username_display = f"@{user['username']}" if user['username'] else (user['first_name'] or "N/A")
            expiry_display = "N/A"
            if user['subscription_type'] == 'lifetime':
                expiry_display = "Lifetime"
            elif user['subscription_expiry_date']:
                date_str = user['subscription_expiry_date']
                expiry_date_obj = datetime.fromisoformat(date_str.split('.')[0]) if isinstance(date_str, str) else date_str
                expiry_display = expiry_date_obj.strftime('%Y-%m-%d') if expiry_date_obj else "Error"

            reset_time_display = "N/A"
            if user['last_free_search_reset_time']:
                time_str = user['last_free_search_reset_time']
                reset_time_obj = datetime.fromisoformat(time_str.split('.')[0]) if isinstance(time_str, str) else time_str
                reset_time_display = reset_time_obj.strftime('%Y-%m-%d') if reset_time_obj else "Error"

            message_text += (
                f"👤 ID: <code>{user['user_id']}</code> ({username_display})\n"
                f"  🆓 Free: {user['free_searches_left']} (Reset: {reset_time_display})\n"
                f"  🪙 Tokens: {user['tokens_left']}\n"
                f"  💳 Sub: {user['subscription_type'] or 'None'} (Expires: {expiry_display})\n"
                f"  🚫 Banned: {'Yes' if user['is_banned'] else 'No'}\n\n"
            )

    keyboard_buttons = []
    pagination_row = []
    total_pages = (total_users + USER_PAGE_SIZE - 1) // USER_PAGE_SIZE if total_users > 0 else 1

    if page > 1:
        pagination_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f'admin_view_users_page_{page-1}'))
    if total_users > 0 :
        pagination_row.append(InlineKeyboardButton(f"Page {page}/{total_pages}", callback_data='admin_view_users_noop'))
    if page < total_pages:
        pagination_row.append(InlineKeyboardButton("Next ➡️", callback_data=f'admin_view_users_page_{page+1}'))

    if pagination_row: keyboard_buttons.append(pagination_row)
    keyboard_buttons.append([InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data='admin_panel_main')])

    await query.edit_message_text(text=message_text, reply_markup=InlineKeyboardMarkup(keyboard_buttons), parse_mode='HTML')

async def admin_ban_user_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🚫 Enter the User ID to ban:")
    return AWAITING_USER_ID_TO_BAN

async def received_user_id_for_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id_to_ban = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Invalid User ID. Please send a numeric ID. Try again or /cancel_admin_action.")
        return AWAITING_USER_ID_TO_BAN

    target_user = get_or_create_user(user_id_to_ban)
    context.chat_data['user_id_to_modify'] = user_id_to_ban

    if target_user:
        username_display = f"(@{target_user['username']})" if target_user['username'] else "(No username)"
        status = "Already Banned" if target_user['is_banned'] else "Not Banned"
        await update.message.reply_text(
            f"User to ban: {user_id_to_ban} {username_display} - Status: {status}.\nAre you sure you want to ban this user?",
            reply_markup=get_confirmation_keyboard("ban")
        )
        return CONFIRM_BAN
    else:
        await update.message.reply_text(f"User ID {user_id_to_ban} not found. Cannot ban.", reply_markup=get_admin_panel_keyboard())
        context.chat_data.clear()
        return ConversationHandler.END

async def confirm_ban_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id_to_ban = context.chat_data.get('user_id_to_modify')

    if query.data == 'ban_yes':
        if ban_user(user_id_to_ban):
            await query.edit_message_text(f"✅ User {user_id_to_ban} has been banned.", reply_markup=get_admin_panel_keyboard())
        else:
            await query.edit_message_text(f"⚠️ Failed to ban user {user_id_to_ban} (already banned or error).", reply_markup=get_admin_panel_keyboard())
    elif query.data == 'ban_no':
        await query.edit_message_text("🚫 Ban cancelled.", reply_markup=get_admin_panel_keyboard())

    context.chat_data.clear()
    return ConversationHandler.END

async def admin_unban_user_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✅ Enter the User ID to unban:")
    return AWAITING_USER_ID_TO_UNBAN

async def received_user_id_for_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id_to_unban = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Invalid User ID. Please send a numeric ID. Try again or /cancel_admin_action.")
        return AWAITING_USER_ID_TO_UNBAN

    target_user = get_or_create_user(user_id_to_unban)
    context.chat_data['user_id_to_modify'] = user_id_to_unban

    if target_user:
        username_display = f"(@{target_user['username']})" if target_user['username'] else "(No username)"
        status = "Banned" if target_user['is_banned'] else "Not Banned"
        await update.message.reply_text(
            f"User to unban: {user_id_to_unban} {username_display} - Status: {status}.\nAre you sure you want to unban this user?",
            reply_markup=get_confirmation_keyboard("unban")
        )
        return CONFIRM_UNBAN
    else:
        await update.message.reply_text(f"User ID {user_id_to_unban} not found. Cannot unban.", reply_markup=get_admin_panel_keyboard())
        context.chat_data.clear()
        return ConversationHandler.END

async def confirm_unban_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id_to_unban = context.chat_data.get('user_id_to_modify')

    if query.data == 'unban_yes':
        if unban_user(user_id_to_unban):
            await query.edit_message_text(f"✅ User {user_id_to_unban} has been unbanned.", reply_markup=get_admin_panel_keyboard())
        else:
            await query.edit_message_text(f"⚠️ Failed to unban user {user_id_to_unban} (already unbanned or error).", reply_markup=get_admin_panel_keyboard())
    elif query.data == 'unban_no':
        await query.edit_message_text("🚫 Unban cancelled.", reply_markup=get_admin_panel_keyboard())

    context.chat_data.clear()
    return ConversationHandler.END

async def admin_grant_access_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🎁 Enter the User ID to grant access to:")
    return ADMIN_GRANT_USER_ID

async def received_user_id_for_grant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id_to_grant = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Invalid User ID. Please send a numeric ID. Try again or /cancel_admin_action.")
        return ADMIN_GRANT_USER_ID

    target_user = get_or_create_user(user_id_to_grant)
    if not target_user:
        await update.message.reply_text(f"Could not get/create user {user_id_to_grant}. Action cancelled.", reply_markup=get_admin_panel_keyboard())
        return ConversationHandler.END

    context.chat_data['grant_user_id'] = user_id_to_grant
    username_display = f"(@{target_user.get('username')})" if target_user and target_user.get('username') else "(No username)"
    await update.message.reply_text(
        f"Granting access to User ID: {user_id_to_grant} {username_display}.\nSelect type of access:",
        reply_markup=get_grant_type_keyboard()
    )
    return ADMIN_GRANT_TYPE

async def grant_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    grant_type = query.data
    context.chat_data['grant_type'] = grant_type

    if grant_type == 'grant_type_subscription':
        await query.edit_message_text("📜 Select subscription plan:", reply_markup=get_grant_subscription_plan_keyboard())
        return ADMIN_GRANT_SUB_PLAN
    elif grant_type == 'grant_type_tokens':
        await query.edit_message_text("🪙 Enter amount of tokens to grant:")
        return ADMIN_GRANT_TOKEN_AMOUNT
    return ConversationHandler.END

async def grant_subscription_plan_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    plan_code = query.data.replace('grant_plan_', '')
    context.chat_data['grant_plan_type'] = plan_code

    user_id_to_grant = context.chat_data['grant_user_id']
    await query.edit_message_text(
        f"Confirm granting <b>{plan_code.replace('_', ' ').title()}</b> subscription to User ID <b>{user_id_to_grant}</b>?",
        reply_markup=get_confirmation_keyboard("grant_sub"), parse_mode='HTML'
    )
    return ADMIN_CONFIRM_GRANT

async def grant_token_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        token_amount = int(update.message.text)
        if token_amount <= 0:
            await update.message.reply_text("Token amount must be positive. Please try again.")
            return ADMIN_GRANT_TOKEN_AMOUNT
    except ValueError:
        await update.message.reply_text("Invalid amount. Please enter a number. Try again or /cancel_admin_action.")
        return ADMIN_GRANT_TOKEN_AMOUNT

    context.chat_data['grant_token_amount'] = token_amount
    user_id_to_grant = context.chat_data['grant_user_id']
    await update.message.reply_text(
        f"Confirm granting <b>{token_amount}</b> token(s) to User ID <b>{user_id_to_grant}</b>?",
        reply_markup=get_confirmation_keyboard("grant_tokens"), parse_mode='HTML'
    )
    return ADMIN_CONFIRM_GRANT

async def confirm_grant_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id_to_grant = context.chat_data.get('grant_user_id')
    grant_type = context.chat_data.get('grant_type')

    message = "Grant action completed."

    if query.data.startswith('grant_sub_yes') and grant_type == 'grant_type_subscription':
        plan_type = context.chat_data.get('grant_plan_type')
        if grant_subscription(user_id_to_grant, plan_type):
            message = f"✅ Subscription ({plan_type.replace('_',' ').title()}) granted to User ID {user_id_to_grant}."
        else:
            message = f"⚠️ Failed to grant subscription to User ID {user_id_to_grant}."
    elif query.data.startswith('grant_tokens_yes') and grant_type == 'grant_type_tokens':
        token_amount = context.chat_data.get('grant_token_amount')
        if grant_tokens(user_id_to_grant, token_amount):
            message = f"✅ {token_amount} token(s) granted to User ID {user_id_to_grant}."
        else:
            message = f"⚠️ Failed to grant tokens to User ID {user_id_to_grant}."
    elif query.data.endswith('_no'):
        message = "🚫 Grant action cancelled."

    await query.edit_message_text(message, reply_markup=get_admin_panel_keyboard())
    context.chat_data.clear()
    return ConversationHandler.END

async def grant_back_to_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id_to_grant = context.chat_data['grant_user_id']
    target_user = get_or_create_user(user_id_to_grant)
    username_display = f"(@{target_user.get('username')})" if target_user and target_user.get('username') else "(No username)"

    await query.edit_message_text(
        f"Granting access to User ID: {user_id_to_grant} {username_display}.\nSelect type of access:",
        reply_markup=get_grant_type_keyboard()
    )
    return ADMIN_GRANT_TYPE

async def cancel_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        if update.message: await update.message.reply_text("This command is for admins only.")
        elif update.callback_query: await update.callback_query.answer("This command is for admins only.", show_alert=True)
        return ConversationHandler.END

    text_to_send = "Admin action cancelled. Returning to Admin Panel."
    reply_markup_to_send = get_admin_panel_keyboard()

    if update.callback_query: # If cancel initiated from a button
        await update.callback_query.edit_message_text(text_to_send, reply_markup=reply_markup_to_send)
    elif update.message: # If cancel initiated by /cancel_admin_action command
        await update.message.reply_text(text_to_send, reply_markup=reply_markup_to_send)

    context.chat_data.clear()
    context.user_data.clear()
    return ConversationHandler.END

# --- My Results Command Handler ---
async def check_my_results_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_db_data = get_or_create_user(user_id, update.effective_user.username, update.effective_user.first_name)

    if user_db_data and user_db_data.get('is_banned'):
        await update.message.reply_text("You are banned from using this bot.")
        return

    completed_jobs = get_completed_jobs_for_user(user_id)

    if not completed_jobs:
        await update.message.reply_text("No new search results found for you at the moment. Use /start to initiate a new search.")
        return

    await update.message.reply_text(f"Found {len(completed_jobs)} result(s) for you:")
    for job in completed_jobs:
        job_id = job['job_id']
        category = job['search_category']
        query_text = job['query_text'] # Renamed from 'query' to match db schema
        status = job['status']

        if status == 'completed':
            result_file_path = job['result_file_path']
            if result_file_path and os.path.exists(result_file_path):
                try:
                    with open(result_file_path, 'rb') as f_doc:
                        await context.bot.send_document(
                            chat_id=user_id,
                            document=f_doc,
                            filename=os.path.basename(result_file_path), # Ensure a filename is provided
                            caption=f"Results for your {category} search: '{query_text}' (Job ID: {job_id})"
                        )
                    mark_job_delivered(job_id)
                    logger.info(f"Delivered result file for job {job_id} to user {user_id}")
                except Exception as e:
                    logger.error(f"Error sending result file for job {job_id} to user {user_id}: {e}")
                    await context.bot.send_message(chat_id=user_id, text=f"Could not send the result file for Job ID {job_id} due to an error. Please contact admin.")
            else:
                logger.error(f"Result file path missing or file not found for job {job_id}: {result_file_path}")
                await context.bot.send_message(chat_id=user_id, text=f"Error: Result file for Job ID {job_id} ({category}: '{query_text}') is missing. Please contact admin.")
                # Optionally mark as 'delivery_failed'
        elif status == 'failed':
            error_msg = job['error_message'] or 'Unknown error'
            await context.bot.send_message(chat_id=user_id, text=f"Your search for {category}: '{query_text}' (Job ID: {job_id}) failed. Error: {error_msg}")
            mark_job_delivered(job_id) # Mark as delivered to prevent re-notification

    await update.message.reply_text("All current results processed. Use /start to return to the main menu.")


# --- Main Application Setup ---
def main() -> None:
    application = Application.builder().token(config.BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("myresults", check_my_results_command)) # Added

    # Search Conversation
    search_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(search_start, pattern='^main_search$')],
        states={
            SELECTING_CATEGORY: [CallbackQueryHandler(select_search_category, pattern='^search_category_')],
            TYPING_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_query)],
        },
        fallbacks=[
            CallbackQueryHandler(back_to_main_menu_from_search_cats, pattern='^back_to_main_search_cats$'),
            CommandHandler('start', start_again_in_conversation)
        ],
        per_message=False
    )
    application.add_handler(search_conv_handler)

    # Buy Conversation
    buy_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(buy_start, pattern='^main_buy$')],
        states={
            SELECTING_BUY_OPTION: [CallbackQueryHandler(select_buy_option, pattern='^(buy_monthly_plans|buy_tokens)$')],
            SELECTING_PLAN: [
                CallbackQueryHandler(select_plan_or_token, pattern='^buy_plan_'),
                CallbackQueryHandler(back_to_buy_options, pattern='^back_to_buy_options$')
            ],
            SELECTING_TOKEN_AMOUNT: [
                CallbackQueryHandler(select_plan_or_token, pattern='^buy_token_'),
                CallbackQueryHandler(back_to_buy_options, pattern='^back_to_buy_options$')
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

    # Admin Panel main button & Back & View Users
    application.add_handler(CallbackQueryHandler(admin_panel_main_callback, pattern='^admin_panel_main$'))
    application.add_handler(CallbackQueryHandler(back_to_main_from_admin_panel, pattern='^back_to_main_from_admin_panel$'))
    application.add_handler(CallbackQueryHandler(admin_view_users_callback, pattern=r'^admin_view_users_page_'))
    application.add_handler(CallbackQueryHandler(admin_view_users_noop_callback, pattern=r'^admin_view_users_noop$'))


    # Ban User Conversation
    ban_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_ban_user_start_callback, pattern='^admin_ban_user_start$')],
        states={
            AWAITING_USER_ID_TO_BAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_user_id_for_ban)],
            CONFIRM_BAN: [CallbackQueryHandler(confirm_ban_action, pattern='^(ban_yes|ban_no)$')]
        },
        fallbacks=[
            CommandHandler('start', start_again_in_conversation),
            CommandHandler('cancel_admin_action', cancel_admin_action),
            CallbackQueryHandler(admin_panel_main_callback, pattern='^admin_panel_main$')
        ],
        map_to_parent={ConversationHandler.END: -1},
        per_message=False
    )
    application.add_handler(ban_conv_handler)

    # Unban User Conversation
    unban_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_unban_user_start_callback, pattern='^admin_unban_user_start$')],
        states={
            AWAITING_USER_ID_TO_UNBAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_user_id_for_unban)],
            CONFIRM_UNBAN: [CallbackQueryHandler(confirm_unban_action, pattern='^(unban_yes|unban_no)$')]
        },
        fallbacks=[
            CommandHandler('start', start_again_in_conversation),
            CommandHandler('cancel_admin_action', cancel_admin_action),
            CallbackQueryHandler(admin_panel_main_callback, pattern='^admin_panel_main$')
        ],
        map_to_parent={ConversationHandler.END: -1},
        per_message=False
    )
    application.add_handler(unban_conv_handler)

    # Grant Access Conversation
    grant_access_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_grant_access_start_callback, pattern='^admin_grant_access_start$')],
        states={
            ADMIN_GRANT_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_user_id_for_grant)],
            ADMIN_GRANT_TYPE: [CallbackQueryHandler(grant_type_selected, pattern='^grant_type_')],
            ADMIN_GRANT_SUB_PLAN: [
                CallbackQueryHandler(grant_subscription_plan_selected, pattern='^grant_plan_'),
                CallbackQueryHandler(grant_back_to_type_selected, pattern='^grant_back_to_type$')
            ],
            ADMIN_GRANT_TOKEN_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, grant_token_amount_received)],
            ADMIN_CONFIRM_GRANT: [CallbackQueryHandler(confirm_grant_action, pattern='^(grant_sub_yes|grant_sub_no|grant_tokens_yes|grant_tokens_no)$')]
        },
        fallbacks=[
            CommandHandler('start', start_again_in_conversation),
            CommandHandler('cancel_admin_action', cancel_admin_action),
            CallbackQueryHandler(admin_panel_main_callback, pattern='^grant_cancel_to_admin_panel$'),
            CallbackQueryHandler(back_to_main_from_admin_panel, pattern='^back_to_main_from_admin_panel$')
        ],
        map_to_parent={ConversationHandler.END: -1},
        per_message=False
    )
    application.add_handler(grant_access_conv_handler)

    logger.info("Bot starting...")
    application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
