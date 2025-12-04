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
REPLY_TIMEOUT = 120 # seconds for waiting for a search result (Increased)
REPLY_POLL_INTERVAL = 5 # seconds for checking for replies to a command (Adjusted)
PRIVATE_GROUP_ID = 1943303299 # User ID of the private group

async def main_client_loop():
    logger.info("User client starting...")
    api_id = int(config.API_ID)
    api_hash = str(config.API_HASH)

    # Ensure TARGET_BOT_ID is an integer
    try:
        target_bot_id_val = int(config.TARGET_BOT_ID)
    except ValueError:
        logger.error(f"TARGET_BOT_ID '{config.TARGET_BOT_ID}' is not a valid integer. Exiting.")
        return

    client = TelegramClient('user_search_session', api_id, api_hash)

    try:
        logger.info("Connecting to Telegram...")
        await client.connect()
        if not await client.is_user_authorized():
            logger.error("User account is not authorized. Please run authorize_user_client.py first.")
            return
        logger.info("User client authorized and connected.")

        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"Download directory ensured at: {DOWNLOADS_DIR.resolve()}")

        while True:
            logger.debug("Checking for pending jobs...")
            job = db_manager.get_pending_job()

            if job:
                job_id = job['job_id']
                query_text = job['query_text']
                # search_type is removed as it's assumed to be 'bot_only'

                logger.info(f"Processing job ID: {job_id} - Query: '{query_text}' (Bot Only)")

                bot_result_file_path = None
                final_result_path_str = None
                errors = []

                job_download_dir = DOWNLOADS_DIR / job_id
                job_download_dir.mkdir(parents=True, exist_ok=True)

                # --- Helper function for searching and downloading (now bot-only) ---
                async def execute_bot_search(target_id: int, command_prefix: str, file_suffix: str):
                    nonlocal errors # To append errors from this scope
                    search_command = f"{command_prefix} {query_text}"
                    logger.info(f"Job {job_id}: Preparing to send command to bot {target_id}: '{search_command}'")

                    resolved_target_entity = None
                    try:
                        resolved_target_entity = await client.get_input_entity(target_id)
                        logger.info(f"Job {job_id}: Successfully resolved entity for bot {target_id}.")
                    except Exception as e_entity:
                        err_msg = f"Could not get input entity for bot {target_id}. Error: {e_entity}"
                        logger.error(f"Job {job_id}: {err_msg}", exc_info=True)
                        errors.append(err_msg)
                        return None

                    try:
                        sent_command_msg = await client.send_message(resolved_target_entity, search_command)
                        logger.info(f"Job {job_id}: Command '{search_command}' sent to bot {target_id} (Message ID: {sent_command_msg.id})")

                        found_doc_path = None
                        doc_start_time = time.time()

                        # First, check if the command message itself contains the document
                        if sent_command_msg.document:
                            logger.info(f"Job {job_id}: Document found directly in sent command's response from bot {target_id} (MsgID: {sent_command_msg.id})")
                            doc_filename = f"results_{job_id}_{file_suffix}.dat"
                            for attr in sent_command_msg.document.attributes:
                                if isinstance(attr, DocumentAttributeFilename):
                                    doc_filename = f"results_{job_id}_{file_suffix}_{attr.file_name}"
                                    break
                            download_path = job_download_dir / Path(doc_filename).name
                            logger.info(f"Job {job_id}: Downloading from bot {target_id} to {download_path}...")
                            await client.download_media(sent_command_msg.document, file=download_path)
                            logger.info(f"Job {job_id}: Downloaded from bot {target_id} to {download_path.resolve()}")
                            return download_path

                        # If not in the sent message, start polling for subsequent messages from the bot
                        while time.time() - doc_start_time < REPLY_TIMEOUT:
                            elapsed = int(time.time() - doc_start_time)
                            logger.debug(f"Job {job_id}: Waiting for reply from bot {target_id}... {elapsed}s / {REPLY_TIMEOUT}s")

                            async for message in client.iter_messages(resolved_target_entity, limit=10, from_user=resolved_target_entity):
                                if message.date < sent_command_msg.date :
                                    continue

                                if message.document:
                                    logger.info(f"Job {job_id}: Document found in message (MsgID: {message.id}) from bot {target_id}")
                                    doc_filename = f"results_{job_id}_{file_suffix}.dat"
                                    for attr in message.document.attributes:
                                        if isinstance(attr, DocumentAttributeFilename):
                                            doc_filename = f"results_{job_id}_{file_suffix}_{attr.file_name}"
                                            break

                                    download_path = job_download_dir / Path(doc_filename).name
                                    logger.info(f"Job {job_id}: Downloading document from bot {target_id} (MsgID: {message.id}) to {download_path}...")
                                    await client.download_media(message.document, file=download_path)
                                    logger.info(f"Job {job_id}: Document downloaded from bot {target_id} to {download_path.resolve()}")
                                    found_doc_path = download_path
                                    break

                            if found_doc_path:
                                break

                            await asyncio.sleep(REPLY_POLL_INTERVAL)

                        if not found_doc_path:
                            err_msg = f"Timeout or no document: Bot {target_id} did not provide a relevant document within {REPLY_TIMEOUT}s for command '{search_command}'."
                            logger.warning(f"Job {job_id}: {err_msg}")
                            errors.append(err_msg)
                        return found_doc_path

                    except Exception as e_search:
                        err_msg = f"Error during search interaction with bot {target_id} for command '{search_command}': {e_search}"
                        logger.error(f"Job {job_id}: {err_msg}", exc_info=True)
                        errors.append(err_msg)
                        return None

                # --- Perform Bot Search (now the only operation) ---
                logger.info(f"Job {job_id}: Initiating bot search (TARGET_BOT_ID: {target_bot_id_val}).")
                bot_result_file_path = await execute_bot_search(target_bot_id_val, "/b", "bot")

                if bot_result_file_path:
                    logger.info(f"Job {job_id}: Bot search yielded results.")
                    final_result_path_str = str(bot_result_file_path.resolve())
                else:
                    logger.warning(f"Job {job_id}: Bot search did not yield a result file. Errors: {'; '.join(errors)}")

                # --- Update Job Status ---
                if final_result_path_str:
                    db_manager.update_job_status(job_id, 'completed', result_file_path=final_result_path_str, error_message="; ".join(errors) if errors else None)
                else:
                    # If bot_result_file_path is None, it means the search failed or timed out.
                    # Errors list should contain details. If empty, provide a generic message.
                    db_manager.update_job_status(job_id, 'failed', error_message="; ".join(errors) if errors else "Bot search failed or timed out without specific error.")
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
