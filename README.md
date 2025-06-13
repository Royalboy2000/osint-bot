# Telegram OSINT Bot System

## 1. Project Description

This project implements a Telegram bot system designed to automate OSINT-like search queries. Users interact with a main Telegram Bot (`bot.py`) to submit search requests. These requests are queued and then processed by a separate client script (`user_client.py`) which acts as a real Telegram user account. The `user_client.py` sends commands to a specified target Telegram bot, retrieves file-based results, and saves them. The main bot then automatically delivers these results back to the originating user. The system includes features for user management, payment simulation, admin controls, and rate limiting.

## 2. Key Features

### User-Facing Features:
*   **Interactive Search:** Users can initiate searches through inline buttons for categories like Domains, Emails, Phone Numbers, and Names.
*   **Automated Results:** Search results (files) are automatically delivered to the user once processed.
*   **Payment System (Simulated):**
    *   Option to "Buy" monthly plans (1 Month, 3 Months, Lifetime) or individual search tokens.
    *   Displays a BTC address for payment and prompts users to confirm payment and submit proof (forwarded to admins).
*   **Access Tiers:**
    *   5 Free daily searches (resets every 24 hours or when an admin grants access).
    *   Searches prioritize active subscriptions, then tokens, then free daily searches.
*   **Rate Limiting:** Users are limited to initiating one new search every 5 seconds.
*   **Status Updates:** Users are informed about their search queue status, potential delays, and available credits (on `/start`).
*   **Support & Channel Links:** Easy access to support and an informational channel.

### Admin Features (via Admin Panel):
*   **Admin Identification:** Specific users (defined in `config.py`) have access to the Admin Panel.
*   **Ban/Unban Users:** Admins can ban or unban users by their Telegram User ID. Banned users cannot interact with the bot.
*   **Grant Access:** Admins can manually grant users:
    *   Subscription plans (1 Month, 3 Months, Lifetime).
    *   A specific number of search tokens.
    *   Granting access also resets the user's daily free search count.
*   **View Users:** Admins can view a paginated list of all registered users, showing their:
    *   User ID, Username/First Name.
    *   Remaining free searches and last reset time.
    *   Token balance.
    *   Current subscription type and expiry date.
    *   Banned status.
*   **Payment Proof Forwarding:** Admins receive messages when users submit payment proof.

## 3. Setup Instructions

1.  **Clone/Download Files:**
    *   Obtain all project files (`bot.py`, `user_client.py`, `db_manager.py`, `config.py.example`, `authorize_user_client.py`, `requirements.txt`).

2.  **Create Python Virtual Environment:**
    *   It's highly recommended to use a virtual environment.
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    # On Windows: venv\Scripts\activate
    ```

3.  **Install Dependencies:**
    *   Install the required Python libraries using the `requirements.txt` file. This project uses `python-telegram-bot` with its `JobQueue` feature for background tasks (like automatic result delivery), so the `requirements.txt` file specifies `python-telegram-bot[job-queue]`.
    ```bash
    pip install -r requirements.txt
    ```
    *   If you were to install `python-telegram-bot` manually, ensure you include the `job-queue` extra: `pip install "python-telegram-bot[job-queue]"`.

4.  **Configure the Bot (`config.py`):**
    *   Copy the example configuration file:
        ```bash
        cp config.py.example config.py
        ```
    *   Edit `config.py` and fill in all the required values:
        *   `BOT_TOKEN`: Your Telegram Bot Father token for `bot.py`.
        *   `API_ID` and `API_HASH`: Your Telegram Core API credentials (from my.telegram.org) for `user_client.py`.
        *   `ADMIN_IDS`: A list of numeric Telegram User IDs for those who should have admin access.
        *   `TARGET_BOT_ID`: The numeric User ID of the Telegram bot that `user_client.py` will send search commands to.
        *   `BTC_ADDRESS`: The BTC address to display for payments.
        *   `SUPPORT_URL`, `CHANNEL_URL`: Links for the respective buttons.
        *   (Other settings like `BOT_USERNAME`, `PRIVATE_GROUP_ID` might be present but `PRIVATE_GROUP_ID` is not used if `TARGET_BOT_ID` is active for searches).

5.  **Authorize `user_client.py` (Telethon First-Time Setup):**
    *   The `user_client.py` script uses Telethon to act as your real Telegram account. It needs to be authorized once.
    *   Run the authorization script:
        ```bash
        python3 authorize_user_client.py
        ```
    *   You will be prompted in the terminal to enter your phone number (the one associated with the account `user_client.py` will use) and the login code sent to your Telegram account. If you have 2FA enabled, you'll also be prompted for your password.
    *   This will create a session file (e.g., `user_search_session.session`) in your project directory.

6.  **Initialize Database:**
    *   The database (`bot_database.db`) and its tables will be created automatically when `bot.py` or `user_client.py` (or `db_manager.py` if run directly) is started for the first time, as they import `db_manager.py` which calls `init_db()`.
    *   If you encounter schema errors with `db_manager.py` (e.g., "no such column"), delete any existing `bot_database.db` file and let it be recreated.

## 4. Running the Bot System

You need to run two main scripts, preferably in separate terminals or using a process manager (like `supervisor` or `pm2`) for long-term operation.

1.  **Run the Main Bot (`bot.py`):**
    *   This script handles user interactions, admin commands, payment flows, and automatic result delivery.
    *   Activate your virtual environment if not already active.
    ```bash
    python3 bot.py
    ```

2.  **Run the User Client (`user_client.py`):**
    *   This script polls the database for search jobs, interacts with the target bot to perform searches, and downloads result files.
    *   Activate your virtual environment if not already active.
    *   Ensure it's authorized (see Setup step 5).
    ```bash
    python3 user_client.py
    ```
    *   Monitor its output for successful connection and job processing.

## 5. Project File Structure

*   `bot.py`: Main Telegram bot application (using `python-telegram-bot`). Handles user interface, commands, admin panel, payment logic, and automatic result delivery.
*   `user_client.py`: Telegram client application (using `Telethon`). Acts as a user account to perform searches by interacting with the `TARGET_BOT_ID`.
*   `db_manager.py`: Handles all SQLite database operations (user management, job queue, search credits, etc.).
*   `config.py`: Stores all configuration variables (API keys, tokens, IDs, etc.). **This file should not be committed to public repositories if it contains sensitive information.**
*   `config.py.example`: A template for `config.py`.
*   `authorize_user_client.py`: Script for one-time authorization of the `user_client.py` Telethon session.
*   `requirements.txt`: Lists Python dependencies.
*   `bot_database.db`: SQLite database file (created automatically).
*   `downloads_user_client/`: Directory where `user_client.py` saves downloaded result files (created automatically).
*   `user_search_session.session`: Telethon session file for `user_client.py` (created automatically after authorization).
*   `README.md`: This file.

## 6. Troubleshooting

### `telegram.error.TimedOut` or `httpx.ConnectTimeout` when running `bot.py`

If `bot.py` fails to start and you see errors in the log like `telegram.error.TimedOut`, `httpx.ConnectTimeout`, or similar network-related messages, it means the script is unable to connect to the Telegram Bot API servers. Here are some common causes and checks:

1.  **Internet Connectivity:**
    *   Ensure the machine running `bot.py` has a stable internet connection.
    *   Try pinging a common domain (e.g., `ping google.com`) or using `curl` to test general connectivity.

2.  **Firewall Restrictions:**
    *   Telegram Bot API primarily uses HTTPS on port 443. Ensure your firewall (on the machine itself, or network firewall, or cloud provider security groups) allows outgoing connections to `api.telegram.org` on port 443.

3.  **DNS Resolution:**
    *   Verify that DNS resolution is working correctly on the machine. Try:
        ```bash
        nslookup api.telegram.org
        # Or:
        dig api.telegram.org
        ```
    *   If DNS resolution fails or points to incorrect IPs, you may need to configure your system's DNS servers (e.g., to use `8.8.8.8` or `1.1.1.1`).

4.  **Basic Connectivity Test to Telegram API:**
    *   From the machine where `bot.py` is running, try using `curl` to check basic connectivity to the Telegram API. Replace `YOUR_TELEGRAM_BOT_FATHER_TOKEN_HERE` with your actual bot token:
        ```bash
        curl -v https://api.telegram.org/botYOUR_TELEGRAM_BOT_FATHER_TOKEN_HERE/getMe
        ```
    *   A successful connection should return a JSON response with your bot's details (e.g., `{"ok":true,"result":{"id":...}}`). If `curl` also fails with timeout or connection errors, it confirms a network-level issue.

5.  **Proxy Configuration (If applicable):**
    *   If you are behind a proxy, ensure that your environment variables (`HTTP_PROXY`, `HTTPS_PROXY`) are correctly set up, or that `python-telegram-bot` is configured to use the proxy if it has specific settings for it (check its documentation for advanced proxy setups).

6.  **Temporary Telegram Issues:**
    *   Rarely, Telegram itself might have temporary server-side issues. You can check community forums or Telegram status pages if you suspect this. Usually, such issues are short-lived.

If these steps don't resolve the issue, you may need to investigate your specific network environment or hosting provider's network policies more deeply.
