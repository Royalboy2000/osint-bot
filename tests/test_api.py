import pytest
import json
from app import app as flask_app # Import the Flask app instance

# Define the API Key that the app will be configured to use for tests
# This should match the DEFAULT_API_KEY or be set via an environment variable
# For testing, we'll rely on the default dev key if SEARCH_API_KEY is not set.
# If SEARCH_API_KEY is set in the test environment, these tests will use it.
import os
TEST_API_KEY = os.environ.get("SEARCH_API_KEY", "your-secret-api-key-for-dev-only")


@pytest.fixture
def app():
    """Create and configure a new app instance for each test."""
    # flask_app.config.update({"TESTING": True}) # Standard practice, already implicitly handled by pytest-flask
    # If you had specific test configurations, you'd apply them here.
    # For example, setting a specific API_KEY for testing if it wasn't handled by env vars:
    # flask_app.config['API_KEY'] = TEST_API_KEY
    # However, our app reads API_KEY at import time, so direct patching or env var is better.
    yield flask_app

@pytest.fixture
def client(app):
    """A test client for the app."""
    return app.test_client()

def test_hello_endpoint(client):
    """Test the root endpoint."""
    response = client.get('/')
    assert response.status_code == 200
    assert b"Search API is running!" in response.data

# --- Test /search endpoint ---

def test_search_success(client):
    """Test successful search."""
    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': 'testuser1', 'query': 'normal query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data['status'] == 'success'
    assert json_data['message'] == 'Search successful'
    assert isinstance(json_data['data'], list)
    # Check if data matches mock for "normal query"
    assert len(json_data['data']) == 3
    assert json_data['data'][0]['title'] == "Mock Result 1 for 'normal query'"

def test_search_empty_results(client):
    """Test search that returns empty results from mock."""
    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': 'testuser2', 'query': 'empty results query'} # mock returns [] for "empty"
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data['status'] == 'success'
    assert json_data['message'] == 'Search successful'
    assert json_data['data'] == []

def test_search_simulated_backend_error(client):
    """Test search where mock simulates a backend error."""
    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': 'testuser3', 'query': 'trigger error query'} # mock returns specific data for "error"
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data['status'] == 'success' # API call itself is successful
    assert isinstance(json_data['data'], list)
    assert len(json_data['data']) == 1
    assert json_data['data'][0]['content'] == "Simulated backend error for query: trigger error query"

# --- Test API Key Authentication ---

def test_search_no_api_key(client):
    """Test search without API key."""
    headers = {'Content-Type': 'application/json'}
    payload = {'user_id': 'testuser', 'query': 'query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 401
    json_data = response.get_json()
    assert json_data['status'] == 'error'
    assert json_data['message'] == 'Unauthorized: Invalid or missing API Key'
    assert json_data['data'] == []

def test_search_invalid_api_key(client):
    """Test search with an invalid API key."""
    headers = {'X-API-Key': 'invalid-key', 'Content-Type': 'application/json'}
    payload = {'user_id': 'testuser', 'query': 'query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 401
    json_data = response.get_json()
    assert json_data['status'] == 'error'
    assert json_data['message'] == 'Unauthorized: Invalid or missing API Key'
    assert json_data['data'] == []

# --- Test Input Validation ---

def test_search_missing_json_body(client):
    """Test search with no JSON body."""
    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    response = client.post('/search', headers=headers) # No data

    assert response.status_code == 400
    json_data = response.get_json()
    assert json_data['status'] == 'error'
    assert json_data['message'] == 'Bad Request: Malformed or missing JSON body' # Updated message
    assert json_data['data'] == []

def test_search_malformed_json_body(client):
    """Test search with malformed JSON body."""
    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    response = client.post('/search', data="this is not json", headers=headers)

    assert response.status_code == 400
    json_data = response.get_json()
    assert json_data['status'] == 'error'
    assert json_data['message'] == 'Bad Request: Malformed or missing JSON body' # Updated message
    assert json_data['data'] == []


def test_search_missing_user_id(client):
    """Test search with missing user_id."""
    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'query': 'some query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 400
    json_data = response.get_json()
    assert json_data['status'] == 'error'
    assert json_data['message'] == "Bad Request: Missing 'user_id' field" # Updated message
    assert json_data['data'] == []

def test_search_invalid_user_id_type(client):
    """Test search with invalid user_id type (not a string)."""
    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': 123, 'query': 'some query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 400
    json_data = response.get_json()
    assert json_data['status'] == 'error'
    assert json_data['message'] == "Bad Request: 'user_id' must be a string" # Updated message
    assert json_data['data'] == []

def test_search_empty_user_id(client):
    """Test search with empty string user_id."""
    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': '   ', 'query': 'some query'} # Whitespace only
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 400
    json_data = response.get_json()
    assert json_data['status'] == 'error'
    assert json_data['message'] == "Bad Request: 'user_id' cannot be empty or just whitespace" # Updated message
    assert json_data['data'] == []

def test_search_missing_query(client):
    """Test search with missing query."""
    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': 'testuser'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 400
    json_data = response.get_json()
    assert json_data['status'] == 'error'
    assert json_data['message'] == "Bad Request: Missing 'query' field" # Updated message
    assert json_data['data'] == []

def test_search_invalid_query_type(client):
    """Test search with invalid query type (not a string)."""
    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': 'testuser', 'query': ['list', 'is', 'not', 'string']}
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 400
    json_data = response.get_json()
    assert json_data['status'] == 'error'
    assert json_data['message'] == "Bad Request: 'query' must be a string" # Updated message
    assert json_data['data'] == []

def test_search_empty_query(client):
    """Test search with empty string query."""
    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': 'testuser', 'query': '      '} # Whitespace only
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 400
    json_data = response.get_json()
    assert json_data['status'] == 'error'
    assert json_data['message'] == "Bad Request: 'query' cannot be empty or just whitespace" # Updated message
    assert json_data['data'] == []

# --- Test General Error Handling (404, 405) ---

def test_not_found_endpoint(client):
    """Test accessing a non-existent endpoint."""
    headers = {'X-API-Key': TEST_API_KEY} # Needs API key for consistency if it were a protected base path
    response = client.get('/nonexistent-path', headers=headers)

    assert response.status_code == 404
    json_data = response.get_json()
    assert json_data['status'] == 'error'
    assert json_data['message'] == 'Resource not found' # Default message from our 404 handler
    assert json_data['data'] == []

def test_method_not_allowed_for_search(client):
    """Test using GET method on /search endpoint (which expects POST)."""
    headers = {'X-API-Key': TEST_API_KEY}
    response = client.get('/search', headers=headers)

    assert response.status_code == 405
    json_data = response.get_json()
    assert json_data['status'] == 'error'
    assert json_data['message'] == 'Method Not Allowed' # Default message from our 405 handler
    assert json_data['data'] == []

def test_method_not_allowed_for_root(client):
    """Test using POST method on / endpoint (which expects GET)."""
    headers = {'X-API-Key': TEST_API_KEY} # Not strictly needed for root, but good for consistency
    response = client.post('/', headers=headers)

    assert response.status_code == 405
    json_data = response.get_json()
    assert json_data['status'] == 'error'
    assert json_data['message'] == 'Method Not Allowed'
    assert json_data['data'] == []

# Example of how to test the mock function directly if needed, though usually done via endpoint tests
from app import mock_osint_search

def test_mock_osint_search_direct_user_info():
    results = mock_osint_search("directuser", "user_info:some_target")
    assert len(results) == 2
    assert results[0]['type'] == 'profile'
    assert results[0]['username'] == 'some_target'

def test_mock_osint_search_direct_empty():
    results = mock_osint_search("directuser", "empty")
    assert results == []
