# Main application file
from flask import Flask, request, jsonify
import os
import secrets
from functools import wraps

app = Flask(__name__)

# --- API Key Configuration ---
# Try to get the API key from an environment variable for better security.
# If SEARCH_API_KEY is not set, a default key is used FOR DEVELOPMENT ONLY.
# In a production environment, SEARCH_API_KEY *must* be set to a strong, unique value.
DEFAULT_API_KEY = "your-secret-api-key-for-dev-only" # Clearly mark as dev only
API_KEY = os.environ.get("SEARCH_API_KEY")

if API_KEY is None:
    API_KEY = DEFAULT_API_KEY
    print(f"WARNING: SEARCH_API_KEY environment variable not set. Using default development key: {API_KEY}", flush=True)
    print("WARNING: This is insecure for production. Please set a strong SEARCH_API_KEY environment variable.", flush=True)
elif API_KEY == DEFAULT_API_KEY: # Check if the env var is explicitly set to the default dev key
    print(f"WARNING: SEARCH_API_KEY is set to the default development key: {API_KEY}", flush=True)
    print("WARNING: This is insecure for production. Please set a strong, unique SEARCH_API_KEY environment variable.", flush=True)


# --- Helper for API Key Authentication ---
def require_api_key(func):
    @wraps(func) # Preserves metadata of the decorated function
    def decorated_function(*args, **kwargs):
        api_key_header = request.headers.get('X-API-Key')
        # Use secrets.compare_digest for timing attack resistance
        if not api_key_header or not secrets.compare_digest(api_key_header, API_KEY):
            return jsonify({"status": "error", "data": [], "message": "Unauthorized: Invalid or missing API Key"}), 401
        return func(*args, **kwargs)
    return decorated_function

@app.route('/search', methods=['POST'])
@require_api_key # Decorate the endpoint to require API key
def search():
    # Get data from JSON body
    data = request.get_json(silent=True) # Use silent=True to prevent it from raising Werkzeug's BadRequest directly
    if data is None: # Check if JSON was successfully parsed
        # This message will be used if content type is wrong, or JSON is malformed/empty
        return jsonify({"status": "error", "data": [], "message": "Bad Request: Malformed or missing JSON body"}), 400

    user_id = data.get('user_id')
    query = data.get('query')

    # --- Input Validation ---
    # user_id checks
    if user_id is None:
        return jsonify({"status": "error", "data": [], "message": "Bad Request: Missing 'user_id' field"}), 400
    if not isinstance(user_id, str):
        return jsonify({"status": "error", "data": [], "message": "Bad Request: 'user_id' must be a string"}), 400
    if len(user_id.strip()) == 0:
        return jsonify({"status": "error", "data": [], "message": "Bad Request: 'user_id' cannot be empty or just whitespace"}), 400

    # query checks
    if query is None:
        return jsonify({"status": "error", "data": [], "message": "Bad Request: Missing 'query' field"}), 400
    if not isinstance(query, str):
        return jsonify({"status": "error", "data": [], "message": "Bad Request: 'query' must be a string"}), 400
    if len(query.strip()) == 0:
        return jsonify({"status": "error", "data": [], "message": "Bad Request: 'query' cannot be empty or just whitespace"}), 400

    # Basic sanitization (inputs are now validated)
    user_id = user_id.strip()
    query = query.strip()

    # Mock OSINT Search Engine Integration
    search_results = mock_osint_search(user_id, query)

    return jsonify({"status": "success", "data": search_results, "message": "Search successful"}), 200

# --- Mock OSINT Search Engine ---
def mock_osint_search(user_id: str, query: str) -> list:
    """
    Mocks a call to an OSINT search engine.
    It does not use any Telegram-session or bot-account data.
    Returns a list of mock search results.
    """
    print(f"Mock OSINT Search: Received user_id='{user_id}', query='{query}'", flush=True)

    # Simulate different results based on the query for variety
    if "error" in query.lower():
        # Simulate an error from the search engine itself (though the API call is successful)
        # For a true API error status, the main endpoint would return status: "error"
        return [{"type": "system_message", "content": "Simulated backend error for query: " + query}]
    elif "empty" in query.lower():
        return []
    elif "user_info" in query.lower():
        return [
            {"type": "profile", "source": "Mock Social Platform A", "username": query.split(':')[1] if ':' in query else user_id, "url": f"http://mockplatform.com/{user_id}"},
            {"type": "post", "source": "Mock Forum B", "content": f"Discussion about '{query}' by user {user_id}", "timestamp": "2024-07-28T10:30:00Z"}
        ]
    else:
        return [
            {"id": "result-123", "title": f"Mock Result 1 for '{query}'", "snippet": f"This is the first mock search result related to '{query}'. Processed for user: {user_id}.", "source": "MockSource1"},
            {"id": "result-456", "title": f"Mock Result 2 for '{query}'", "snippet": f"Another item found for '{query}'. This demonstrates multiple results.", "source": "MockSource2"},
            {"id": "result-789", "title": f"'{query}' mentioned in a document", "details": "The document contains sensitive information regarding the query.", "source": "MockSourceInternalDoc"},
        ]

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
