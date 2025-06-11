# job_manager.py
import sqlite3
import uuid

DB_FILE = "jobs.db"

def initialize_db():
    """Creates the database and tables if they don't exist."""
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        # Stores new requests that the user client needs to process
        c.execute('''
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                user_chat_id INTEGER NOT NULL,
                query TEXT NOT NULL,
                status TEXT DEFAULT 'pending', -- pending, processed, failed
                group_message_id INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()

def add_job(user_chat_id, query):
    """Adds a new search job from a user."""
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        job_id = str(uuid.uuid4())
        c.execute("INSERT INTO jobs (job_id, user_chat_id, query) VALUES (?, ?, ?)",
                  (job_id, user_chat_id, query))
        conn.commit()
