import sqlite3
from datetime import datetime, timedelta
import logging # Optional: for logging DB operations

# Setup logging (optional)
logger = logging.getLogger(__name__)
# Configure logger if you want to see output, e.g., basicConfig
# logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


DATABASE_NAME = "bot_database.db"
DEFAULT_FREE_SEARCHES = 5
# FREE_SEARCH_RESET_HOURS = 24 # This might come from main bot config

def get_db_connection():
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row # Access columns by name
    return conn

def init_db():
    """Initializes the database and creates tables if they don't exist."""
    conn = None # Ensure conn is defined in the wider scope for the finally block
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Added created_at and updated_at with CURRENT_TIMESTAMP default for new rows
        # For updated_at, will need a trigger or manual update in functions.
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT DEFAULT NULL,
                first_name TEXT DEFAULT NULL,
                free_searches_left INTEGER DEFAULT {DEFAULT_FREE_SEARCHES},
                last_free_search_reset_time DATETIME,
                is_banned BOOLEAN DEFAULT FALSE,
                subscription_type TEXT DEFAULT NULL,
                subscription_expiry_date DATETIME DEFAULT NULL,
                tokens_left INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Example of a trigger to automatically update 'updated_at'
        # This might be specific to SQLite versions or require careful handling
        cursor.execute('''
            CREATE TRIGGER IF NOT EXISTS update_users_updated_at
            AFTER UPDATE ON users
            FOR EACH ROW
            BEGIN
                UPDATE users SET updated_at = CURRENT_TIMESTAMP WHERE user_id = OLD.user_id;
            END;
        ''')
        # Jobs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                search_category TEXT NOT NULL,
                query_text TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                result_file_path TEXT DEFAULT NULL,
                error_message TEXT DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS update_jobs_updated_at
            AFTER UPDATE ON jobs
            FOR EACH ROW
            BEGIN
                UPDATE jobs SET updated_at = CURRENT_TIMESTAMP WHERE job_id = OLD.job_id;
            END;
        """)
        conn.commit()
        logger.info("Database initialized successfully with users & jobs tables and triggers.")
    except sqlite3.Error as e:
        logger.error(f"Database initialization error: {e}")
    finally:
        if conn:
            conn.close()

def get_or_create_user(user_id: int, username: str = None, first_name: str = None) -> dict:
    """Retrieves a user by user_id or creates them if they don't exist."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()

        now = datetime.now()
        if user is None:
            # For last_free_search_reset_time, using 'now' is appropriate for a new user.
            # created_at and updated_at will use CURRENT_TIMESTAMP default.
            cursor.execute('''
                INSERT INTO users (user_id, username, first_name, free_searches_left, last_free_search_reset_time)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, username, first_name, DEFAULT_FREE_SEARCHES, now))
            conn.commit()
            logger.info(f"New user {user_id} created.")
            # Fetch the newly created user to return consistent dict
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            user = cursor.fetchone()
        # Update username and first_name if they have changed since last seen
        elif (username and user['username'] != username) or \
             (first_name and user['first_name'] != first_name):
            cursor.execute('''
                UPDATE users SET username = ?, first_name = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            ''', (username, first_name, user_id)) # Corrected: Use CURRENT_TIMESTAMP for updated_at
            conn.commit()
            logger.info(f"User {user_id} info updated (username/first_name).")
            # Re-fetch to get the most current data including the new updated_at
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            user = cursor.fetchone()

        return dict(user) if user else None
    except sqlite3.Error as e:
        logger.error(f"Error in get_or_create_user for {user_id}: {e}")
        return None
    finally:
        if conn:
            conn.close()

def update_user_free_searches(user_id: int, searches_left: int, reset_time: datetime) -> bool:
    """Updates a user's free searches count and reset time."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # updated_at will be handled by the trigger if it was created successfully
        cursor.execute('''
            UPDATE users
            SET free_searches_left = ?, last_free_search_reset_time = ?
            WHERE user_id = ?
        ''', (searches_left, reset_time, user_id))
        conn.commit()
        logger.info(f"Updated free searches for user {user_id} to {searches_left}.")
        return cursor.rowcount > 0
    except sqlite3.Error as e:
        logger.error(f"Error updating free searches for user {user_id}: {e}")
        return False
    finally:
        if conn:
            conn.close()

def decrement_user_free_searches(user_id: int) -> bool:
    """Decrements a user's free search count by 1 if available."""
    # Fetch user data first to check searches_left
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT free_searches_left FROM users WHERE user_id = ?", (user_id,))
        user_data = cursor.fetchone()

        if not user_data:
            logger.warning(f"User {user_id} not found for decrementing searches.")
            return False # Or create user here if that's the desired logic

        if user_data['free_searches_left'] > 0:
            # updated_at will be handled by the trigger
            cursor.execute('''
                UPDATE users
                SET free_searches_left = free_searches_left - 1
                WHERE user_id = ? AND free_searches_left > 0
            ''', (user_id,))
            conn.commit()
            if cursor.rowcount > 0:
                logger.info(f"Decremented free search for user {user_id}.")
                return True
            else:
                logger.warning(f"Failed to decrement search for {user_id} (race condition or already 0).")
                return False
        else:
            logger.info(f"User {user_id} has no free searches left to decrement.")
            return False
    except sqlite3.Error as e:
        logger.error(f"Error decrementing free searches for {user_id}: {e}")
        return False
    finally:
        if conn:
            conn.close()

# Call init_db when the module is first loaded so tables are created.
if __name__ == '__main__':
    # For testing or direct script execution
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    logger.info("Running db_manager.py directly for testing.")
    init_db()
    # Example usage:
    test_user_id = 12345
    test_username = "testuser"
    test_first_name = "Test"

    logger.info(f"Getting/creating user {test_user_id}")
    test_user = get_or_create_user(test_user_id, test_username, test_first_name)
    if test_user:
        logger.info(f"Test user data: {test_user}")

        initial_searches = test_user['free_searches_left']
        logger.info(f"Attempting to decrement search for {test_user_id}. Searches left: {initial_searches}")
        if decrement_user_free_searches(test_user_id):
            logger.info(f"Successfully decremented a search for {test_user_id}")
        else:
            logger.info(f"Failed to decrement search for {test_user_id}")

        updated_user = get_or_create_user(test_user_id) # Re-fetch user
        if updated_user:
            logger.info(f"Updated user searches left: {updated_user['free_searches_left']}")

        # Test updating username/firstname
        get_or_create_user(test_user_id, "new_username", "New First")
        updated_user_info = get_or_create_user(test_user_id)
        if updated_user_info:
            logger.info(f"User info after update: {updated_user_info['username']}, {updated_user_info['first_name']}")

    else:
        logger.error(f"Could not get or create test user {test_user_id}")
else:
    # Ensure DB is initialized when imported by the bot
    # This will run every time the module is imported in different parts of a larger application
    # if not structured carefully (e.g. using a global flag or a dedicated init in main bot script)
    # For now, this is standard practice for simpler modules.
    init_db()
    logger.info("db_manager.py imported, database initialized.")


def ban_user(user_id: int) -> bool:
    """Bans a user by setting is_banned to TRUE."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # updated_at will be handled by the trigger
        cursor.execute("UPDATE users SET is_banned = TRUE WHERE user_id = ?", (user_id,))
        conn.commit()
        if cursor.rowcount > 0:
            logger.info(f"User {user_id} has been banned.")
            return True
        logger.warning(f"User {user_id} not found or already banned (no rows updated).")
        return False
    except sqlite3.Error as e:
        logger.error(f"Error banning user {user_id}: {e}")
        return False
    finally:
        if conn: conn.close()

def create_search_job(job_id: str, user_id: int, category: str, query: str) -> bool:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # created_at and updated_at will use CURRENT_TIMESTAMP by default on insert
        cursor.execute("""
            INSERT INTO jobs (job_id, user_id, search_category, query_text, status)
            VALUES (?, ?, ?, ?, 'pending')
        """, (job_id, user_id, category, query))
        conn.commit()
        logger.info(f"Created search job {job_id} for user {user_id}.")
        return True
    except sqlite3.Error as e:
        logger.error(f"Error creating search job {job_id}: {e}")
        return False
    finally:
        if conn: conn.close()

def get_pending_job() -> dict | None:
    conn = get_db_connection()
    job_data_dict = None
    try:
        cursor = conn.cursor()
        # Use a transaction to ensure atomicity of select and update
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute("SELECT * FROM jobs WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1")
        job_data_row = cursor.fetchone()

        if job_data_row:
            job_data_dict = dict(job_data_row) # Convert to dict before potential commit closes cursor implicitly
            job_id = job_data_dict['job_id']
            # updated_at will be handled by the trigger
            cursor.execute("UPDATE jobs SET status = 'processing' WHERE job_id = ?", (job_id,))
            conn.commit()
            logger.info(f"Picked up job {job_id} for processing.")
        else:
            conn.commit() # Commit even if no job found to end the transaction
        return job_data_dict
    except sqlite3.Error as e:
        logger.error(f"Error getting pending job: {e}")
        if conn: conn.rollback()
        return None
    finally:
        if conn: conn.close()

def update_job_status(job_id: str, status: str, result_file_path: str = None, error_message: str = None) -> bool:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # updated_at will be handled by the trigger
        cursor.execute("""
            UPDATE jobs SET status = ?, result_file_path = ?, error_message = ?
            WHERE job_id = ?
        """, (status, result_file_path, error_message, job_id))
        conn.commit()
        if cursor.rowcount > 0:
            logger.info(f"Updated job {job_id} status to {status}.")
            return True
        logger.warning(f"Job {job_id} not found for status update or status already set.")
        return False
    except sqlite3.Error as e:
        logger.error(f"Error updating job status for {job_id}: {e}")
        return False
    finally:
        if conn: conn.close()

# Define plan durations (in days)
PLAN_DURATIONS = {
    '1_month': 30,
    '3_months': 90,
}
LIFETIME_EXPIRY_DATE = datetime(2099, 12, 31) # A far future date for lifetime

def grant_subscription(user_id: int, plan_type: str) -> bool:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        expiry_date = None
        if plan_type == 'lifetime':
            expiry_date = LIFETIME_EXPIRY_DATE
        elif plan_type in PLAN_DURATIONS:
            expiry_date = datetime.now() + timedelta(days=PLAN_DURATIONS[plan_type])
        else:
            logger.error(f"Invalid plan_type: {plan_type} for grant_subscription")
            return False

        # updated_at will be handled by the trigger
        cursor.execute("""
            UPDATE users
            SET subscription_type = ?, subscription_expiry_date = ?,
                free_searches_left = ?, last_free_search_reset_time = ?
            WHERE user_id = ?
        """, (plan_type, expiry_date, DEFAULT_FREE_SEARCHES, datetime.now(), user_id))
        conn.commit()
        if cursor.rowcount > 0:
            logger.info(f"Granted {plan_type} subscription to user {user_id}.")
            return True
        logger.warning(f"User {user_id} not found for subscription grant.")
        return False
    except sqlite3.Error as e:
        logger.error(f"Error granting subscription to user {user_id}: {e}")
        return False
    finally:
        if conn: conn.close()

def grant_tokens(user_id: int, token_amount: int) -> bool:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # updated_at will be handled by the trigger
        cursor.execute("""
            UPDATE users
            SET tokens_left = tokens_left + ?,
                free_searches_left = ?, last_free_search_reset_time = ?
            WHERE user_id = ?
        """, (token_amount, DEFAULT_FREE_SEARCHES, datetime.now(), user_id))
        conn.commit()
        if cursor.rowcount > 0:
            logger.info(f"Granted {token_amount} tokens to user {user_id}.")
            return True
        logger.warning(f"User {user_id} not found for token grant.")
        return False
    except sqlite3.Error as e:
        logger.error(f"Error granting tokens to user {user_id}: {e}")
        return False
    finally:
        if conn: conn.close()

def decrement_tokens(user_id: int) -> bool:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT tokens_left FROM users WHERE user_id = ?", (user_id,))
        user_tokens_row = cursor.fetchone()
        if user_tokens_row and user_tokens_row['tokens_left'] > 0:
            # updated_at will be handled by the trigger
            cursor.execute("UPDATE users SET tokens_left = tokens_left - 1 WHERE user_id = ?", (user_id,))
            conn.commit()
            if cursor.rowcount > 0:
                logger.info(f"Decremented token for user {user_id}. Remaining: {user_tokens_row['tokens_left'] - 1}")
                return True
            return False # Should not happen if select and update are atomic or rowcount is checked
        logger.info(f"User {user_id} has no tokens to decrement or user not found.")
        return False
    except sqlite3.Error as e:
        logger.error(f"Error decrementing tokens for user {user_id}: {e}")
        return False
    finally:
        if conn: conn.close()

def get_users_for_view(page: int = 1, page_size: int = 5) -> tuple[list[dict], int]:
    # Fetches all users with pagination. Returns a list of users and total user count.
    conn = get_db_connection()
    offset = (page - 1) * page_size
    try:
        cursor = conn.cursor()
        # Get total count of users
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]

        # Get paginated users
        cursor.execute("""
            SELECT user_id, username, first_name, free_searches_left,
                   subscription_type, subscription_expiry_date, tokens_left, is_banned, last_free_search_reset_time
            FROM users
            ORDER BY user_id DESC
            LIMIT ? OFFSET ?
        """, (page_size, offset))
        users_on_page = [dict(row) for row in cursor.fetchall()]

        return users_on_page, total_users
    except sqlite3.Error as e:
        logger.error(f"Error fetching users for view (page {page}): {e}")
        return [], 0
    finally:
        if conn: conn.close()

def get_completed_jobs_for_user(user_id: int) -> list[dict]:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # Fetch completed or failed jobs to notify the user
        cursor.execute("SELECT * FROM jobs WHERE user_id = ? AND (status = 'completed' OR status = 'failed') ORDER BY updated_at ASC", (user_id,))
        jobs = [dict(row) for row in cursor.fetchall()]
        return jobs
    except sqlite3.Error as e:
        logger.error(f"Error getting completed/failed jobs for user {user_id}: {e}")
        return []
    finally:
        if conn: conn.close()

def mark_job_delivered(job_id: str) -> bool:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # updated_at will be handled by the trigger
        cursor.execute("UPDATE jobs SET status = 'delivered' WHERE job_id = ?", (job_id,))
        conn.commit()
        if cursor.rowcount > 0:
            logger.info(f"Marked job {job_id} as delivered.")
            return True
        logger.warning(f"Job {job_id} not found or status not updated for delivery marking.")
        return False
    except sqlite3.Error as e:
        logger.error(f"Error marking job {job_id} as delivered: {e}")
        return False
    finally:
        if conn: conn.close()

def unban_user(user_id: int) -> bool:
    """Unbans a user by setting is_banned to FALSE."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # updated_at will be handled by the trigger
        cursor.execute("UPDATE users SET is_banned = FALSE WHERE user_id = ?", (user_id,))
        conn.commit()
        if cursor.rowcount > 0:
            logger.info(f"User {user_id} has been unbanned.")
            return True
        logger.warning(f"User {user_id} not found or already unbanned (no rows updated).")
        return False
    except sqlite3.Error as e:
        logger.error(f"Error unbanning user {user_id}: {e}")
        return False
    finally:
        if conn: conn.close()
