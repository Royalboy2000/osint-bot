# db_manager.py
import sqlite3
import uuid
from datetime import datetime, timedelta

DB_FILE = "bot_database.db"
INITIAL_FREE_SEARCHES = 5

def initialize_db():
    """Initializes all necessary tables with the new, simpler schema."""
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        # Inline INITIAL_FREE_SEARCHES instead of using a placeholder
        c.execute(f'''
            CREATE TABLE IF NOT EXISTS users (
                user_id       INTEGER PRIMARY KEY,
                username      TEXT,
                searches_left INTEGER DEFAULT {INITIAL_FREE_SEARCHES},
                plan_type     TEXT    DEFAULT 'free',
                plan_expiry   DATETIME,
                is_banned     BOOLEAN DEFAULT 0
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS jobs (
                job_id           TEXT    PRIMARY KEY,
                user_id          INTEGER NOT NULL,
                query            TEXT    NOT NULL,
                status           TEXT    DEFAULT 'pending',
                group_message_id INTEGER,
                timestamp        DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()


def get_or_create_user(user_id, username):
    """Adds a new user if they don't exist, otherwise returns their data."""
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = c.fetchone()
        if user is None:
            # New users get the initial free searches one time.
            c.execute("""
                INSERT INTO users (user_id, username, searches_left)
                VALUES (?, ?, ?)
            """, (user_id, username, INITIAL_FREE_SEARCHES))
            conn.commit()
            return c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return user

# The daily reset function is no longer needed and has been DELETED.
# def check_and_reset_free_searches(user_id):
#     ...

def get_user(user_id):
    """Fetches a user's data."""
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return c.fetchone()

def can_user_search(user_id):
    """Checks if a user has searches left or an active plan."""
    user = get_user(user_id)
    if not user: return False
    # 0:id, 1:username, 2:searches_left, 3:plan_type, 4:plan_expiry, 5:is_banned
    if user[5]: return False # Banned

    # Check for active paid plan first
    if user[3] != 'free':
        if user[3] == 'lifetime': return True
        # Ensure plan_expiry is not None before checking
        if user[4] and datetime.strptime(user[4], '%Y-%m-%d %H:%M:%S.%f') > datetime.now():
            return True

    # If no active plan, check one-time searches/tokens
    if user[2] > 0: return True

    return False

def use_search_credit(user_id):
    """Deducts one search credit if the user does not have an active paid plan."""
    user = get_user(user_id)
    if not user: return

    has_active_plan = False
    if user[3] != 'free':
        if user[3] == 'lifetime':
            has_active_plan = True
        elif user[4] and datetime.strptime(user[4], '%Y-%m-%d %H:%M:%S.%f') > datetime.now():
            has_active_plan = True

    # Only deduct from their token/free search balance if they don't have an active unlimited plan
    if not has_active_plan:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("UPDATE users SET searches_left = searches_left - 1 WHERE user_id = ? AND searches_left > 0", (user_id,))

# Admin and Job Management functions remain the same as the previous version.
def get_users_paginated(page=0, limit=5):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM users ORDER BY user_id LIMIT ? OFFSET ?", (limit, page * limit))
        users = c.fetchall()
        c.execute("SELECT COUNT(user_id) FROM users")
        total_users = c.fetchone()[0]
        return users, total_users

def update_user_plan(user_id, plan_type, duration_days=None):
    expiry_date = None
    if duration_days:
        expiry_date = datetime.now() + timedelta(days=duration_days)
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("UPDATE users SET plan_type = ?, plan_expiry = ? WHERE user_id = ?", (plan_type, expiry_date, user_id))

def add_tokens(user_id, amount):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("UPDATE users SET searches_left = searches_left + ? WHERE user_id = ?", (amount, user_id))

def set_ban_status(user_id, is_banned):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (int(is_banned), user_id))

def add_job(user_id, query):
    job_id = str(uuid.uuid4())
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT INTO jobs (job_id, user_id, query) VALUES (?, ?, ?)", (job_id, user_id, query))
    return job_id

def get_job_by_group_message_id(msg_id):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT job_id, user_id FROM jobs WHERE group_message_id = ?", (msg_id,))
        return c.fetchone()

def mark_job_as_processed(job_id, group_message_id):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("UPDATE jobs SET status = 'processed', group_message_id = ? WHERE job_id = ?", (group_message_id, job_id))

def delete_job(job_id):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
