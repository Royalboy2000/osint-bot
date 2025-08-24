import sqlite3
import os

DATABASE_NAME = "bot_database.db"

def clear_jobs_table():
    """Connects to the database and deletes all records from the jobs table."""
    if not os.path.exists(DATABASE_NAME):
        print(f"Error: Database file '{DATABASE_NAME}' not found.")
        return

    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()

        print("Connecting to the database...")

        # Get the count of jobs before deleting
        cursor.execute("SELECT COUNT(*) FROM jobs")
        count_before = cursor.fetchone()[0]
        print(f"Found {count_before} jobs to delete.")

        # Delete all records from the jobs table
        cursor.execute("DELETE FROM jobs")
        conn.commit()

        # Get the count of jobs after deleting
        cursor.execute("SELECT COUNT(*) FROM jobs")
        count_after = cursor.fetchone()[0]

        print(f"Successfully deleted {count_before} jobs. The 'jobs' table now has {count_after} records.")

    except sqlite3.Error as e:
        print(f"An error occurred: {e}")
    finally:
        if conn:
            conn.close()
            print("Database connection closed.")

if __name__ == '__main__':
    print("This script will delete all records from the 'jobs' table.")
    # Add a confirmation step to prevent accidental deletion
    # In a real-world script, you might want this. For this context, we'll proceed directly.
    # confirm = input("Are you sure you want to continue? (yes/no): ")
    # if confirm.lower() == 'yes':
    clear_jobs_table()
    # else:
    #     print("Operation cancelled.")
