# user_client.py
import asyncio
import sqlite3
import os
from telethon import TelegramClient, events
import config
import db_manager

DB_FILE = "bot_database.db"
BOT_USERNAME = config.BOT_USERNAME  # e.g. 'Receive1337_bot'

# Initialize your user-session client
client = TelegramClient('user_session', config.API_ID, config.API_HASH)

@client.on(events.NewMessage(chats=config.PRIVATE_GROUP_ID))
async def group_reply_handler(event):
    """When your bot replies in the private group, download the result and send to the bot."""
    if not event.message.is_reply:
        return

    replied_to_msg_id = event.message.reply_to_msg_id
    job_info = db_manager.get_job_by_group_message_id(replied_to_msg_id)
    if not job_info:
        return

    job_id, _ = job_info
    print(f"[Client] Reply in group for job {job_id}; downloading and sending to @{BOT_USERNAME}")

    try:
        # 1) Download media (or message text) locally
        download_path = None
        if event.message.media:
            download_path = await event.message.download_media()
        else:
            # If it's just text or a document without media, save text to a file
            download_path = f"/tmp/{job_id}.txt"
            with open(download_path, "w", encoding="utf-8") as f:
                f.write(event.message.text or "")

        # 2) Send the file to your bot
        await client.send_file(BOT_USERNAME, download_path)

        # 3) Send a caption so the bot knows which job this belongs to
        caption = f"result_for_job: {job_id}"
        await client.send_message(BOT_USERNAME, caption)

    except Exception as e:
        print(f"[Client] ERROR sending job {job_id} to bot: {e}")

    finally:
        # Clean up the local file
        if download_path and os.path.exists(download_path):
            os.remove(download_path)

async def process_pending_jobs():
    """Polls the local SQLite DB for new 'pending' jobs and sends them to the group."""
    while True:
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT job_id, query FROM jobs WHERE status = 'pending' LIMIT 1")
            row = c.fetchone()

        if row:
            job_id, query = row
            print(f"[Client] Sending query '{query}' for job {job_id} to group")
            try:
                sent = await client.send_message(config.PRIVATE_GROUP_ID, f"/s {query}")
                db_manager.mark_job_as_processed(job_id, sent.id)
            except Exception as e:
                print(f"[Client] ERROR processing job {job_id}: {e}")

        await asyncio.sleep(5)  # adjust your polling interval as needed

async def main():
    await client.start()
    me = await client.get_me()
    print(f"[Client] Logged in as @{me.username} (ID: {me.id})")
    print(f"[Client] Will send results to @{BOT_USERNAME}")

    # Launch the background job-processor
    asyncio.create_task(process_pending_jobs())

    # Notify yourself that the client is up
    await client.send_message('me', "✅ User client is online and watching for jobs.")
    print("[Client] Ready and running.")

    # Keep running until disconnected
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())

