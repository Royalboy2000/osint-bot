import asyncio
from telethon import TelegramClient # type: ignore
import logging

# Configure basic logging for the authorization script
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Attempt to import API_ID and API_HASH from config.py
try:
    import config
    API_ID = config.API_ID
    API_HASH = config.API_HASH
    if API_ID is None or API_HASH is None: # Check if they were None in config.py
        logger.warning("API_ID or API_HASH are None in config.py.")
        # Raising an error here to be caught by the same except block for simplicity
        raise ImportError("API_ID/API_HASH are None in config.")
except (ImportError, AttributeError) as e:
    logger.error(f"CRITICAL ERROR: Could not import API_ID or API_HASH from config.py: {e}")
    logger.error("Please ensure config.py exists in the same directory as this script,")
    logger.error("and that it contains valid, non-empty API_ID and API_HASH variables.")
    logger.error("Script cannot proceed without these values.")
    # Set to None to prevent further execution if critical info is missing
    API_ID = None
    API_HASH = None

# This session name must match the one used in your user_client.py
SESSION_NAME = 'user_search_session'

async def main():
    if API_ID is None or API_HASH is None:
        logger.critical("API_ID and/or API_HASH are missing or could not be loaded. Exiting.")
        return

    logger.info("--- Telegram User Account Authorization ---")
    logger.info(f"Attempting to authorize/verify session: {SESSION_NAME}")
    logger.info(f"Using API ID: {API_ID}") # Log the API ID being used
    logger.info("IMPORTANT: This script will connect to Telegram using your API credentials.")
    logger.info("You may be asked for your phone number and a login code sent to your Telegram account.")
    logger.info("If you have Two-Factor Authentication (2FA) enabled, you'll also need your 2FA password.")
    logger.info("-" * 40)

    # Ensure API_ID is an int for Telethon
    processed_api_id = int(API_ID) if isinstance(API_ID, (str, int)) and str(API_ID).isdigit() else None
    if not processed_api_id:
        logger.critical(f"API_ID '{API_ID}' is not a valid integer. Exiting.")
        return

    client = TelegramClient(SESSION_NAME, processed_api_id, API_HASH)

    logger.info("Connecting to Telegram...")
    try:
        await client.connect()
    except Exception as e:
        logger.error(f"Error: Failed to connect to Telegram: {e}")
        logger.error("Troubleshooting tips:")
        logger.error("  - Check your internet connection.")
        logger.error("  - Verify that your API_ID and API_HASH in config.py are correct and active.")
        logger.error("  - Ensure Telethon is installed correctly in your environment.")
        return

    logger.info("Connection successful.")

    try:
        if await client.is_user_authorized():
            logger.info("You are ALREADY AUTHORIZED!")
            me = await client.get_me()
            if me:
                logger.info(f"Currently logged in as: {me.first_name} (Username: @{me.username or ''})")
            else:
                logger.warning("Could not retrieve account details, though authorized. This is unusual.")
        else:
            logger.info("\nAUTHORIZATION REQUIRED.")
            logger.info("Telethon will now ask for your phone number.")
            # client.start() will handle the full login flow
            # (phone, code, 2FA password if applicable)
            await client.start()

            logger.info("\nLogin successful!")
            me = await client.get_me()
            if me:
                 logger.info(f"Now logged in as: {me.first_name} (Username: @{me.username or ''})")
            logger.info(f"Session file '{SESSION_NAME}.session' should now be created or updated.")
            logger.info("You should be able to run user_client.py without authorization errors.")

    except RuntimeError as e:
        logger.error(f"\nRuntime Error during login/authorization: {e}")
        logger.error("This can sometimes happen if another Telethon script using the same session name is running,")
        logger.error("or if there are issues with the session file.")
        logger.error("Please ensure no other instances of user_client.py or this script are active.")
        logger.error(f"You might need to delete the '{SESSION_NAME}.session' file and try again.")
    except Exception as e:
        logger.error(f"\nAn error occurred during the login/authorization process: {e}", exc_info=True)
        logger.error("Troubleshooting tips:")
        logger.error("  - Ensure your API_ID and API_HASH in config.py are correct.")
        logger.error("  - Double-check the phone number and code you enter.")
        logger.error("  - If you use 2FA, make sure you enter the correct password.")
    finally:
        if client.is_connected():
            logger.info("\nDisconnecting client...")
            await client.disconnect()
            logger.info("Client disconnected.")
        logger.info("Authorization process finished.")
        logger.info(f"If user_client.py still reports 'not authorized', try deleting the '{SESSION_NAME}.session' file and run this script again.")

if __name__ == '__main__':
    # This check helps ensure config can be loaded if script is run directly
    if API_ID is not None and API_HASH is not None:
        asyncio.run(main())
    else:
        logger.critical("Cannot run main authorization logic because API_ID or API_HASH failed to load.")
        logger.critical("Please fix the config.py import or hardcode values (for testing only).")
