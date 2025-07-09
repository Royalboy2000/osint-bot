# OSINT Search API

This RESTful API acts as a frontend to an asynchronous backend OSINT search system, which typically involves `user_client.py` interacting with Telegram bots. The API submits search jobs, polls for their completion, and then returns the results.

It requires API key authentication for all endpoints and returns JSON responses. The API itself does not directly handle Telegram sessions but relies on `user_client.py` (or a similar backend process) for those interactions.

## Core Workflow

1.  An API client sends a search request (`user_id`, `query`) to this Flask API (`app.py`).
2.  `app.py` validates the request and API key.
3.  It then records a new search job in a shared database (via `db_manager.py`).
4.  A separate, continuously running process, `user_client.py`, monitors this database for new jobs.
5.  When `user_client.py` finds a job, it performs the actual OSINT search (e.g., by interacting with a Telegram bot like `TARGET_BOT_ID`) and downloads the result file.
6.  `user_client.py` updates the job's status and result file path in the database.
7.  Meanwhile, `app.py` polls the database for the job's completion.
8.  Once the job is marked 'completed' by `user_client.py`, `app.py` retrieves the result file path, reads the file, formats its content, and returns it in the API response.
9.  If the job fails or times out, `app.py` returns an appropriate error.

## Features

-   Secure: API key authentication (`X-API-Key` header).
-   Input Validation: `user_id` (must be an integer string) and `query` parameters are validated.
-   Standardized JSON Responses: `status`, `data`, `message` fields.
-   Asynchronous Backend: Integrates with a database-driven job queue processed by `user_client.py`.
-   Flexible Result Handling: Parses result files as JSON, JSONL, or plain text.
-   Documented: Includes an OpenAPI (Swagger) specification (`openapi.yaml`).

## Project Structure

```
.
├── app.py                  # Main Flask API application (this service)
├── user_client.py          # Backend Telegram client worker (RUN SEPARATELY)
├── bot.py                  # Main Telegram bot (if used, RUN SEPARATELY)
├── db_manager.py           # Handles SQLite database interactions
├── config.py               # Configuration for all components
├── openapi.yaml            # OpenAPI (Swagger) specification for app.py
├── requirements.txt        # Python dependencies
├── README.md               # This file
└── tests/
    └── test_api.py         # Pytest unit tests for app.py
```

## Setup and Installation

1.  **Clone the repository.**
2.  **Create and activate a virtual environment:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```
3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
4.  **Configure `config.py`:**
    *   Copy `config.py.example` to `config.py`.
    *   Fill in your Telegram `API_ID`, `API_HASH` (for `user_client.py`).
    *   Set the `TARGET_BOT_ID` in `config.py` (the bot `user_client.py` will send commands to).
    *   (If using `bot.py`): Set `BOT_TOKEN`, `ADMIN_IDS`, etc.
5.  **Initialize the Database:**
    The database (`bot_database.db`) is created/updated when `db_manager.py` is first imported (e.g., by `app.py` or `user_client.py`). You can also run `python db_manager.py` once to ensure it's set up.

6.  **Set API Key for `app.py`:**
    The Flask API (`app.py`) requires an API key for its own authentication. Set this as an environment variable:
    ```bash
    export SEARCH_API_KEY="your_strong_and_unique_api_key_for_app.py"
    ```
    (Use `set` or `$env:` for Windows). A strong key can be generated using `python -c "import secrets; print(secrets.token_hex(32))"`.
    If not set, a default insecure key will be used with a warning.

## Running the System

**This system requires at least TWO separate processes to be running:**

1.  **The Backend User Client (`user_client.py`):**
    This process handles the actual Telegram interactions and OSINT searches.
    *   Ensure `config.py` is correctly set up for `API_ID`, `API_HASH`, and `TARGET_BOT_ID`.
    *   You might need to run `authorize_user_client.py` once if the Telethon session is new or unauthorized.
    *   Start it in a terminal:
        ```bash
        python user_client.py
        ```
    *   This client will continuously poll the database for new jobs submitted by the API.

2.  **The Flask API Server (`app.py`):**
    This provides the RESTful interface.
    *   Ensure `SEARCH_API_KEY` environment variable is set.
    *   Start it in another terminal:
        ```bash
        python app.py
        ```
    *   The API will typically start on `http://0.0.0.0:5000/`.
    *   Polling behavior (timeout, interval) for `app.py` waiting for job results can be configured via `API_JOB_POLL_TIMEOUT` and `API_JOB_POLL_INTERVAL` environment variables (defaults to 120s and 5s).

## API Usage (`app.py`)

The primary endpoint is `/search`.

-   **Method:** `POST`
-   **Headers:**
    -   `Content-Type: application/json`
    -   `X-API-Key: <your_app.py_api_key>`
-   **Request Body (JSON):**
    ```json
    {
        "user_id": "123456789", // Must be a string representation of an integer
        "query": "search term here"
    }
    ```
    The `user_id` here will be used to create/lookup a user in the shared database, and jobs will be associated with this user ID. This user will be subject to any limits (e.g., free searches) configured in the bot system.

**Detailed Example Request (using `curl`):**

To send a search request, you'll need:
1.  The API server (`app.py`) running.
2.  The backend processor (`user_client.py`) running.
3.  Your specific API key for `app.py` (which you set via the `SEARCH_API_KEY` environment variable).

```bash
# Replace 'your_actual_api_key_here' with the API key you configured for app.py
# Replace '123456789' with a valid integer string for user_id
# Replace 'information about example.com' with your desired search query

curl -X POST http://localhost:5000/search \
     -H "Content-Type: application/json" \
     -H "X-API-Key: your_actual_api_key_here" \
     -d '{
         "user_id": "123456789",
         "query": "information about example.com"
     }'
```

**Understanding the Workflow with the `curl` Example:**
1.  The `curl` command sends the JSON payload to `app.py`.
2.  `app.py` validates the API key (`your_actual_api_key_here`) and the request data.
3.  It generates a unique job ID and records a new job in the database (e.g., for `user_id: 123456789` with query `information about example.com`).
4.  The separate `user_client.py` process, which is polling the database, picks up this new job.
5.  `user_client.py` sends the command (e.g., `/b information about example.com`) to the configured `TARGET_BOT_ID` on Telegram.
6.  `user_client.py` waits for the `TARGET_BOT_ID` to reply with a result file and downloads it.
7.  `user_client.py` updates the job in the database with 'completed' status and the path to the downloaded file.
8.  Meanwhile, `app.py` has been polling the database for this job ID. Once it sees 'completed', it reads the result file.
9.  `app.py` then formats the content of the file (as JSON, JSONL, or plain text) and sends it back as the HTTP response to your `curl` command.

**Example Success Response (if result file was JSON):**
```json
{
    "status": "success",
    "message": "Search completed successfully.",
    "data": [
        {"id": "doc1", "content": "Details from result file."}
    ]
}
```

**Example Success Response (from a plain text result file):**
```json
{
    "status": "success",
    "message": "Search completed successfully.",
    "data": [
        {
            "type": "text_file",
            "name": "results_for_query.txt",
            "content": "This is the plain text content of the result file."
        }
    ]
}
```

**Example Error Response (e.g., job timeout):**
```json
{
    "status": "error",
    "message": "Search timed out waiting for results from the backend processor.",
    "data": []
}
```
(Refer to `openapi.yaml` for more detailed response codes and schemas).

### Error Handling Notes
In cases of backend processing errors (e.g., the `user_client.py` fails to complete a job, or there's an issue processing the result file), the API (`app.py`) will return a generic error message (typically with a 500 or 504 HTTP status). Detailed error information is logged on the server side by `app.py` and/or `user_client.py` and will not be exposed directly to the API client. This is for security and to provide a cleaner experience for API consumers. Administrators should consult the server logs for specific error details.

## API Documentation (OpenAPI/Swagger)

The API is documented in `openapi.yaml`. View it with tools like [Swagger Editor](https://editor.swagger.io/) or a local Swagger UI instance.
Example using Docker for local Swagger UI (run from project root):
```bash
docker run -p 8081:8080 -e SWAGGER_JSON=/tmp/openapi.yaml -v $(pwd)/openapi.yaml:/tmp/openapi.yaml swaggerapi/swagger-ui
```
Access at `http://localhost:8081`.

## Running Tests (for `app.py`)

The tests for `app.py` use `pytest` and mock the database interactions.
```bash
pytest -v tests/test_api.py
```
These tests do **not** require `user_client.py` to be running.
```
