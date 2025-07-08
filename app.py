# Main application file
from flask import Flask, request, jsonify
import os
import secrets
from functools import wraps
import uuid # For generating job IDs
import time # For polling timeout
import json # For reading result files that might be JSON

# Import the database manager
import db_manager # Assuming db_manager.py is in the same directory or accessible

app = Flask(__name__)

# Configure basic logging for the app using Flask's built-in app.logger
# The default level is WARNING. If FLASK_DEBUG is true, it's DEBUG.
# You can add handlers or change levels if needed.
# Example: app.logger.setLevel(logging.INFO) if needed.

# --- Module-level constants for Job Polling ---
POLL_TIMEOUT_SECONDS = int(os.environ.get("API_JOB_POLL_TIMEOUT", 120))
POLL_INTERVAL_SECONDS = int(os.environ.get("API_JOB_POLL_INTERVAL", 5))

# --- API Key Configuration ---
DEFAULT_API_KEY = "your-secret-api-key-for-dev-only"
API_KEY = os.environ.get("SEARCH_API_KEY")

if API_KEY is None:
    API_KEY = DEFAULT_API_KEY
    app.logger.warning(f"SEARCH_API_KEY environment variable not set. Using default development key (THIS IS INSECURE): {API_KEY}")
elif API_KEY == DEFAULT_API_KEY:
    app.logger.warning(f"SEARCH_API_KEY is set to the default development key (THIS IS INSECURE): {API_KEY}")


# --- Helper for API Key Authentication ---
def require_api_key(func):
    @wraps(func)
    def decorated_function(*args, **kwargs):
        api_key_header = request.headers.get('X-API-Key')
        if not api_key_header or not secrets.compare_digest(api_key_header, API_KEY):
            app.logger.warning(f"Unauthorized API access attempt. Missing or invalid API Key from IP: {request.remote_addr}")
            return jsonify({"status": "error", "data": [], "message": "Unauthorized: Invalid or missing API Key"}), 401
        return func(*args, **kwargs)
    return decorated_function

@app.route('/search', methods=['POST'])
@require_api_key
def search():
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"status": "error", "data": [], "message": "Bad Request: Malformed or missing JSON body"}), 400

    user_id_str = data.get('user_id') # This is the user_id from the API consumer
    query = data.get('query')

    # --- Input Validation ---
    if user_id_str is None:
        return jsonify({"status": "error", "data": [], "message": "Bad Request: Missing 'user_id' field"}), 400
    if not isinstance(user_id_str, str):
        return jsonify({"status": "error", "data": [], "message": "Bad Request: 'user_id' must be a string"}), 400
    if len(user_id_str.strip()) == 0:
        return jsonify({"status": "error", "data": [], "message": "Bad Request: 'user_id' cannot be empty or just whitespace"}), 400

    # The user_id for the database (jobs.user_id FK to users.user_id) must be an integer.
    # The API consumer provides a string `user_id_str`. We'll attempt to convert it to an int.
    try:
        db_user_id_for_job = int(user_id_str)
    except ValueError:
        return jsonify({"status": "error", "data": [], "message": "Bad Request: 'user_id' must be a string that represents an integer for database operations."}), 400

    if query is None:
        return jsonify({"status": "error", "data": [], "message": "Bad Request: Missing 'query' field"}), 400
    if not isinstance(query, str):
        return jsonify({"status": "error", "data": [], "message": "Bad Request: 'query' must be a string"}), 400
    if len(query.strip()) == 0:
        return jsonify({"status": "error", "data": [], "message": "Bad Request: 'query' cannot be empty or just whitespace"}), 400

    sanitized_query = query.strip()
    # The original user_id_str is kept for logging/reference; db_user_id_for_job is for DB interaction.

    # --- Job Submission and Polling ---
    job_id = uuid.uuid4().hex

    # Ensure user exists in DB for foreign key constraint in jobs table.
    # The username can be generic, e.g., "api_user_<db_user_id_for_job>".
    # This user_id (db_user_id_for_job) will be subject to bot's free search limits, etc.
    db_user_record = db_manager.get_or_create_user(user_id=db_user_id_for_job, username=f"api_user_{db_user_id_for_job}")
    if not db_user_record:
        app.logger.error(f"Failed to get or create user in database for db_user_id: {db_user_id_for_job}")
        return jsonify({"status": "error", "data": [], "message": "Internal server error: User record management failed."}), 500

    # Create the search job with a default category "api_search"
    # Note: The `user_id` field in `create_search_job` refers to `jobs.user_id` which is the Telegram user ID.
    if not db_manager.create_search_job(job_id=job_id, user_id=db_user_id_for_job, category="api_search", query=sanitized_query):
        app.logger.error(f"Failed to create search job for job_id: {job_id}, db_user_id: {db_user_id_for_job}")
        return jsonify({"status": "error", "data": [], "message": "Internal server error: Failed to create search job."}), 500

    app.logger.info(f"Job {job_id} created for API user_id_str '{user_id_str}' (db_user_id: {db_user_id_for_job}), query: '{sanitized_query}'")

    # Poll for job completion using module-level constants
    start_time = time.time()

    while time.time() - start_time < POLL_TIMEOUT_SECONDS:
        job_details = db_manager.get_job_details(job_id)

        if not job_details:
            app.logger.warning(f"Job details not found for job_id: {job_id} during polling. Retrying shortly.")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        status = job_details.get('status')
        app.logger.debug(f"Job {job_id} status: {status}")

        if status == 'completed':
            result_file_path = job_details.get('result_file_path')
            if not result_file_path:
                app.logger.error(f"Job {job_id} completed but no result file path found in DB record.")
                return jsonify({"status": "error", "data": [], "message": "Internal server error: Job completed but result path missing."}), 500

            try:
                if not os.path.exists(result_file_path):
                    app.logger.error(f"Result file not found at path: {result_file_path} for job {job_id} (DB record was: {result_file_path})")
                    return jsonify({"status": "error", "data": [], "message": f"Internal server error: Result file specified in DB not found on disk."}), 500

                file_content_raw = ""
                with open(result_file_path, 'r', encoding='utf-8') as f:
                    file_content_raw = f.read()

                data_payload = []
                try:
                    parsed_json = json.loads(file_content_raw)
                    if isinstance(parsed_json, list):
                        data_payload = parsed_json
                    else:
                        data_payload = [parsed_json]
                except json.JSONDecodeError:
                    try: # Attempt to parse as JSONL (JSON Lines)
                        lines = file_content_raw.strip().split('\n')
                        data_payload = [json.loads(line) for line in lines if line.strip()]
                        if not data_payload: # Handle case of empty file or only whitespace lines
                             data_payload = [{"type": "text_file", "name": os.path.basename(result_file_path), "content": file_content_raw, "comment": "File was empty or not valid JSON/JSONL."}]
                    except json.JSONDecodeError:
                        app.logger.info(f"Result file {result_file_path} for job {job_id} is not JSON/JSONL. Treating as plain text.")
                        data_payload = [{"type": "text_file", "name": os.path.basename(result_file_path), "content": file_content_raw}]

                app.logger.info(f"Job {job_id} completed. Returning content from {result_file_path}.")
                return jsonify({"status": "success", "data": data_payload, "message": "Search completed successfully."}), 200

            except Exception as e:
                app.logger.error(f"Error reading or processing result file {result_file_path} for job {job_id}: {e}", exc_info=True)
                return jsonify({"status": "error", "data": [], "message": f"Internal server error: Error processing result file."}), 500

        elif status == 'failed':
            error_message = job_details.get('error_message', "Search job failed without a specific error message.")
            app.logger.warning(f"Job {job_id} failed. Error: {error_message}")
            return jsonify({"status": "error", "data": [], "message": error_message}), 500 # Using 500 for job failure, could be 422 if it's a user-side query issue

        # If status is 'pending' or 'processing', continue polling
        time.sleep(POLL_INTERVAL_SECONDS)

    # If loop finishes, it means timeout
    app.logger.warning(f"Job {job_id} (API user '{user_id_str}') timed out after {POLL_TIMEOUT_SECONDS} seconds waiting for completion by user_client.")
    return jsonify({"status": "error", "data": [], "message": "Search timed out waiting for results from the backend processor."}), 504


@app.route('/')
def hello():
    return "Search API is running! Use the /search endpoint to perform searches."

if __name__ == '__main__':
    # It's good practice to get port and debug mode from environment variables for production
    # import os # os is already imported at the top
    port = int(os.environ.get("PORT", "5000")) # Ensure string default for get before int()
    debug_mode = os.environ.get("FLASK_DEBUG", "True").lower() == "true"
    # Note: For production, consider using a more robust WSGI server like Gunicorn or uWSGI
    app.run(debug=debug_mode, host='0.0.0.0', port=port)

# --- Global Error Handlers ---
@app.errorhandler(400)
def bad_request_error(error):
    # For errors triggered by request.get_json(), error.description might be Werkzeug's generic message.
    # We want our specific message for validation errors from our code.
    # This global handler will catch other 400s or if our specific checks are missed.
    # For JSON decoding issues, Flask often handles this before our explicit checks.
    # The message might be "Bad Request: Missing JSON body" from our code, or Werkzeug's.
    # Let's keep it simple: if an error object has a custom message, use it.
    # Our specific route checks for user_id/query return specific messages.
    # This handler is more of a fallback for other 400s.
    message = "Bad Request"
    if hasattr(error, 'description') and isinstance(error.description, str) and not error.description.startswith("<"): # Avoid HTML descriptions
        message = error.description
    # If one of our specific validation errors triggered this (though they return directly)
    if hasattr(error, 'custom_message'):
        message = error.custom_message
    return jsonify({"status": "error", "data": [], "message": message}), 400

@app.errorhandler(404)
def not_found_error(error):
    return jsonify({"status": "error", "data": [], "message": "Resource not found"}), 404

@app.errorhandler(405)
def method_not_allowed_error(error):
    return jsonify({"status": "error", "data": [], "message": "Method Not Allowed"}), 405

@app.errorhandler(500)
def internal_server_error(error):
    # Log the error for debugging (e.g., using app.logger.error or a proper logging setup)
    print(f"Internal Server Error: {error}", flush=True) # Basic print for now
    return jsonify({"status": "error", "data": [], "message": "Internal Server Error"}), 500
