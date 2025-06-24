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
                search_type = job.get('search_type', 'bot_only') # Get search_type, default to 'bot_only'

                logger.info(f"Processing job ID: {job_id} - Query: '{query_text}' - Type: '{search_type}'")

                bot_result_file_path = None
                group_result_file_path = None
                final_result_path_str = None
                errors = []

                job_download_dir = DOWNLOADS_DIR / job_id
                job_download_dir.mkdir(parents=True, exist_ok=True)

                # --- Helper function for searching and downloading ---
                async def execute_search(target_id: int, command_prefix: str, file_suffix: str, is_group_search: bool = False):
                    nonlocal errors
                    search_command = f"{command_prefix} {query_text}"
                    logger.info(f"Job {job_id}: Preparing to send command to {target_id}: '{search_command}'")

                    resolved_target_entity = None
                    try:
                        # Attempt to resolve the entity first. This is crucial for TARGET_BOT_ID.
                        # For PRIVATE_GROUP_ID, it should already be an entity or a known ID.
                        resolved_target_entity = await client.get_input_entity(target_id)
                        logger.info(f"Job {job_id}: Successfully resolved entity for {target_id}.")
                    except Exception as e_entity:
                        err_msg = f"Could not get input entity for target_id {target_id}. Error: {e_entity}"
                        logger.error(f"Job {job_id}: {err_msg}", exc_info=True)
                        errors.append(err_msg)
                        return None

                    try:
                        sent_command_msg = await client.send_message(resolved_target_entity, search_command)
                        logger.info(f"Job {job_id}: Command '{search_command}' sent to {target_id} (Message ID: {sent_command_msg.id})")

                        found_doc_path = None
                        doc_start_time = time.time()

                        # First, check if the command message itself contains the document (common for bots)
                        if not is_group_search and sent_command_msg.document:
                            logger.info(f"Job {job_id}: Document found directly in sent command's response from {target_id} (MsgID: {sent_command_msg.id})")
                            doc_filename = f"results_{job_id}_{file_suffix}.dat"
                            for attr in sent_command_msg.document.attributes:
                                if isinstance(attr, DocumentAttributeFilename):
                                    doc_filename = f"results_{job_id}_{file_suffix}_{attr.file_name}"
                                    break
                            download_path = job_download_dir / Path(doc_filename).name
                            logger.info(f"Job {job_id}: Downloading from {target_id} to {download_path}...")
                            await client.download_media(sent_command_msg.document, file=download_path)
                            logger.info(f"Job {job_id}: Downloaded from {target_id} to {download_path.resolve()}")
                            return download_path

                        # If not in the sent message, start polling for subsequent messages
                        while time.time() - doc_start_time < REPLY_TIMEOUT:
                            elapsed = int(time.time() - doc_start_time)
                            logger.debug(f"Job {job_id}: Waiting for reply from {target_id}... {elapsed}s / {REPLY_TIMEOUT}s")

                            # For group search, we need to iterate messages in the group.
                            # For bot search, we are looking for messages from the bot.
                            async for message in client.iter_messages(resolved_target_entity, limit=10, from_user=resolved_target_entity if not is_group_search else None):
                                if message.date < sent_command_msg.date : # Ignore messages older than our command
                                    # For group replies, the reply itself might be slightly delayed, so its date could be >= sent_command_msg.date
                                    # This check is more for not picking up very old unrelated messages.
                                    # Corrected attribute: message.reply_to_msg_id
                                    if not (is_group_search and message.is_reply and message.reply_to_msg_id == sent_command_msg.id):
                                        continue

                                if is_group_search:
                                    # Corrected attribute: message.reply_to_msg_id
                                    if not (message.is_reply and message.reply_to_msg_id == sent_command_msg.id):
                                        # logger.debug(f"Job {job_id}: Group message {message.id} is not a reply to our command {sent_command_msg.id}. Skipping.")
                                        continue
                                    logger.info(f"Job {job_id}: Found reply (MsgID: {message.id}) to our command (MsgID: {sent_command_msg.id}) in group {target_id}.")

                                if message.document:
                                    logger.info(f"Job {job_id}: Document found in message (MsgID: {message.id}) from {target_id}")
                                    doc_filename = f"results_{job_id}_{file_suffix}.dat"
                                    for attr in message.document.attributes:
                                        if isinstance(attr, DocumentAttributeFilename):
                                            doc_filename = f"results_{job_id}_{file_suffix}_{attr.file_name}"
                                            break

                                    download_path = job_download_dir / Path(doc_filename).name
                                    logger.info(f"Job {job_id}: Downloading document from {target_id} (MsgID: {message.id}) to {download_path}...")
                                    await client.download_media(message.document, file=download_path)
                                    logger.info(f"Job {job_id}: Document downloaded from {target_id} to {download_path.resolve()}")
                                    found_doc_path = download_path
                                    break # Exit inner message loop once document found

                            if found_doc_path:
                                break # Exit outer timeout loop

                            await asyncio.sleep(REPLY_POLL_INTERVAL)

                        if not found_doc_path:
                            err_msg = f"Timeout or no document: Target {target_id} did not provide a relevant document within {REPLY_TIMEOUT}s for command '{search_command}'."
                            logger.warning(f"Job {job_id}: {err_msg}")
                            errors.append(err_msg)
                        return found_doc_path

                    except Exception as e_search:
                        err_msg = f"Error during search interaction with {target_id} for command '{search_command}': {e_search}"
                        logger.error(f"Job {job_id}: {err_msg}", exc_info=True)
                        errors.append(err_msg)
                        return None

                # --- Perform Bot Search ---
                if search_type in ['bot_only', 'both']:
                    logger.info(f"Job {job_id}: Initiating bot search (TARGET_BOT_ID: {target_bot_id_val}).")
                    bot_result_file_path = await execute_search(target_bot_id_val, "/b", "bot", is_group_search=False)

                # --- Perform Group Search ---
                if search_type in ['group_only', 'both']:
                    logger.info(f"Job {job_id}: Initiating group search (PRIVATE_GROUP_ID: {PRIVATE_GROUP_ID}).")
                    group_result_file_path = await execute_search(PRIVATE_GROUP_ID, "/s", "group", is_group_search=True)

                # --- Combine Results ---
                if bot_result_file_path and group_result_file_path:
                    logger.info(f"Job {job_id}: Both bot and group searches yielded results. Combining...")
                    combined_filename = f"results_{job_id}_combined.txt"
                    final_result_path = job_download_dir / combined_filename
                    try:
                        with open(final_result_path, 'wb') as outfile: # Open in binary write mode
                            with open(bot_result_file_path, 'rb') as infile: # Open in binary read mode
                                outfile.write(infile.read())
                            outfile.write(b"\n\n--- Results from Private Group ---\n\n") # Separator
                            with open(group_result_file_path, 'rb') as infile: # Open in binary read mode
                                outfile.write(infile.read())
                        final_result_path_str = str(final_result_path.resolve())
                        logger.info(f"Job {job_id}: Combined results into {final_result_path_str}")
                        # Optionally remove individual files after combining
                        # os.remove(bot_result_file_path)
                        # os.remove(group_result_file_path)
                    except Exception as e_combine:
                        err_msg = f"Error combining results for job {job_id}: {e_combine}"
                        logger.error(err_msg, exc_info=True)
                        errors.append(err_msg)
                        # Fallback: decide if one of the files should be sent or mark as error
                        if bot_result_file_path: # Prioritize bot result if combination fails
                             final_result_path_str = str(bot_result_file_path.resolve())
                             errors.append("Combination failed, sending only bot result.")
                        elif group_result_file_path:
                             final_result_path_str = str(group_result_file_path.resolve())
                             errors.append("Combination failed, sending only group result.")
                elif bot_result_file_path:
                    logger.info(f"Job {job_id}: Only bot search yielded results.")
                    final_result_path_str = str(bot_result_file_path.resolve())
                elif group_result_file_path:
                    logger.info(f"Job {job_id}: Only group search yielded results.")
                    final_result_path_str = str(group_result_file_path.resolve())
                else:
                    logger.warning(f"Job {job_id}: Neither search yielded a result file.")

                # --- Update Job Status ---
                if final_result_path_str:
                    db_manager.update_job_status(job_id, 'completed', result_file_path=final_result_path_str, error_message="; ".join(errors) if errors else None)
                else:
                    db_manager.update_job_status(job_id, 'failed', error_message="; ".join(errors) or "No results found from any source.")
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
