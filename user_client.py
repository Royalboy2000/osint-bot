import asyncio
import logging
import os
from pathlib import Path
import sqlite3 # For type hints, actual db ops via db_manager
from telethon import TelegramClient # type: ignore
from telethon.tl.types import DocumentAttributeFilename # For filename extraction
import time

# Assuming db_manager.py and config.py are in the same directory or accessible in PYTHONPATH
import db_manager
import config

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DOWNLOADS_DIR = Path("./downloads_user_client")
POLL_INTERVAL = 10  # seconds for checking new jobs
REPLY_TIMEOUT = 120 # seconds for waiting for a search result from the group
REPLY_POLL_INTERVAL = 5 # seconds for checking for replies to a command

async def main_client_loop():
    logger.info("User client starting...")
    api_id = int(config.API_ID)
    api_hash = str(config.API_HASH)
    private_group_id = int(config.PRIVATE_GROUP_ID) # Ensure it's an integer

    client = TelegramClient('user_search_session', api_id, api_hash)

    try:
        logger.info("Connecting to Telegram...")
        await client.connect()
        if not await client.is_user_authorized():
            logger.error("User account is not authorized. Please run an interactive session first to log in.")
            logger.info("To authorize, you might need to run a separate script like:")
            logger.info(f"from telethon import TelegramClient; client = TelegramClient('user_search_session', {api_id}, '{api_hash}'); await client.start()")
            return
        logger.info("User client authorized and connected.")

        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"Download directory ensured at: {DOWNLOADS_DIR.resolve()}")

        while True:
            logger.debug("Checking for pending jobs...")
            job = db_manager.get_pending_job()

            if job:
                job_id = job['job_id']
                # category = job['search_category'] # Category might not be needed for the /s command
                query_text = job['query_text']

                logger.info(f"Processing job ID: {job_id} - Query: '{query_text}'")

                try:
                    search_command = f"/s {query_text}"
                    logger.info(f"Sending command to group {private_group_id}: '{search_command}'")
                    sent_command_msg = await client.send_message(private_group_id, search_command)
                    sent_command_id = sent_command_msg.id

                    found_reply = False
                    start_time = time.time()

                    while time.time() - start_time < REPLY_TIMEOUT:
                        elapsed_time = int(time.time() - start_time)
                        logger.debug(f"Job {job_id}: Waiting for reply... {elapsed_time}s / {REPLY_TIMEOUT}s")

                        # Fetch recent messages in the group, specifically looking for replies
                        async for message in client.iter_messages(private_group_id, limit=10, reply_to=sent_command_id):
                            if message.document:
                                logger.info(f"Job {job_id}: Reply with document found (Message ID: {message.id})")

                                filename = f"results_{job_id}.dat" # Default filename
                                for attribute in message.document.attributes:
                                    if isinstance(attribute, DocumentAttributeFilename):
                                        filename = attribute.file_name
                                        break

                                job_download_dir = DOWNLOADS_DIR / job_id
                                job_download_dir.mkdir(parents=True, exist_ok=True)
                                result_file_path = job_download_dir / filename

                                logger.info(f"Job {job_id}: Downloading document to {result_file_path}...")
                                await client.download_media(message.document, file=result_file_path)
                                logger.info(f"Job {job_id}: Document downloaded to {result_file_path.resolve()}")

                                db_manager.update_job_status(job_id, 'completed', result_file_path=str(result_file_path.resolve()))
                                found_reply = True
                                break

                        if found_reply:
                            break

                        await asyncio.sleep(REPLY_POLL_INTERVAL)

                    if not found_reply:
                        logger.warning(f"Job {job_id}: Timeout. No valid reply received within {REPLY_TIMEOUT} seconds.")
                        db_manager.update_job_status(job_id, 'failed', error_message="Timeout waiting for reply from group.")

                except Exception as e:
                    logger.error(f"Job {job_id}: Error during processing: {e}", exc_info=True)
                    db_manager.update_job_status(job_id, 'failed', error_message=str(e))
            else:
                logger.debug(f"No pending jobs. Waiting for {POLL_INTERVAL} seconds.")

            await asyncio.sleep(POLL_INTERVAL)

    except ConnectionError:
        logger.error("Failed to connect to Telegram. Check API credentials (API_ID, API_HASH in config.py) and network.")
    except Exception as e:
        logger.error(f"An unexpected error occurred in the user client: {e}", exc_info=True)
    finally:
        if client.is_connected():
            logger.info("Disconnecting user client...")
            await client.disconnect()
        logger.info("User client stopped.")

if __name__ == '__main__':
    db_manager.init_db()
    logger.info("db_manager initialized by user_client.py for standalone run.")

    asyncio.run(main_client_loop())
