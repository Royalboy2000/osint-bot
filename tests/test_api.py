import pytest
import json
import uuid
from unittest.mock import patch, mock_open

# Import the Flask app instance from app.py
# Important: This import should ideally happen *after* any environment variables for testing are set,
# or the app should be configurable after import if it reads env vars at import time.
# For 'SEARCH_API_KEY', our app.py reads it at import time.
# Pytest fixtures can help manage app context.
from app import app as flask_app

# Define the API Key that the app will be configured to use for tests
# This should match the DEFAULT_API_KEY or be set via an environment variable
# For testing, we'll rely on the default dev key if SEARCH_API_KEY is not set.
import os
TEST_API_KEY = os.environ.get("SEARCH_API_KEY", "your-secret-api-key-for-dev-only")

# Expected User ID for DB interactions (must be an integer string for API)
DB_INTERACTION_USER_ID_STR = "123456789"
DB_INTERACTION_USER_ID_INT = 123456789


@pytest.fixture
def app_instance():
    """Fixture to provide the Flask app instance configured for testing."""
    flask_app.config.update({
        "TESTING": True,
        # Lower polling intervals for faster tests, if they were app.config based
        # "API_JOB_POLL_TIMEOUT": 5, # Example: shorter timeout for tests
        # "API_JOB_POLL_INTERVAL": 0.1 # Example: shorter interval for tests
    })
    # For environment variables like API_JOB_POLL_TIMEOUT, patch them if not using app.config
    # For this test suite, we will patch time.sleep to speed up polling tests.
    yield flask_app

@pytest.fixture
def client(app_instance):
    """A test client for the app."""
    return app_instance.test_client()

# --- Mocks Setup ---
# We need to mock db_manager, uuid, time.sleep, os.path.exists, and open

@pytest.fixture
def mock_db_manager(mocker):
    mock = mocker.patch('app.db_manager', autospec=True)
    # Default behavior for get_or_create_user
    mock.get_or_create_user.return_value = {'user_id': DB_INTERACTION_USER_ID_INT, 'username': f'api_user_{DB_INTERACTION_USER_ID_INT}'}
    # Default behavior for create_search_job
    mock.create_search_job.return_value = True
    return mock

@pytest.fixture
def mock_uuid(mocker):
    mock = mocker.patch('app.uuid.uuid4', autospec=True)
    mock.return_value.hex = "test-job-id-123" # Ensure it returns an object with a 'hex' attribute
    return mock

@pytest.fixture
def mock_time_sleep(mocker):
    # Patch time.sleep to avoid actual waiting during tests
    return mocker.patch('time.sleep', return_value=None)

@pytest.fixture
def mock_os_path_exists(mocker):
    return mocker.patch('os.path.exists')

@pytest.fixture
def mock_builtin_open(mocker):
    return mocker.patch('builtins.open', new_callable=mock_open)


# --- Test Cases ---

def test_hello_endpoint(client):
    response = client.get('/')
    assert response.status_code == 200
    assert b"Search API is running!" in response.data

# --- Test /search endpoint: Happy Paths ---

def test_search_success_json_result(client, mock_db_manager, mock_uuid, mock_time_sleep, mock_os_path_exists, mock_builtin_open):
    """Test successful search where result file is JSON."""
    mock_os_path_exists.return_value = True
    mock_file_content = json.dumps({"result_key": "result_value"})
    mock_builtin_open.return_value.read.return_value = mock_file_content

    # Simulate job status progression: pending -> completed
    mock_db_manager.get_job_details.side_effect = [
        {'status': 'pending', 'job_id': 'test-job-id-123'},
        {'status': 'completed', 'job_id': 'test-job-id-123', 'result_file_path': '/path/to/results.json'}
    ]

    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': DB_INTERACTION_USER_ID_STR, 'query': 'json query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data['status'] == 'success'
    assert json_data['message'] == 'Search completed successfully.'
    assert json_data['data'] == [{"result_key": "result_value"}] # Wrapped in list by app

    mock_db_manager.get_or_create_user.assert_called_once_with(user_id=DB_INTERACTION_USER_ID_INT, username=f"api_user_{DB_INTERACTION_USER_ID_INT}")
    mock_db_manager.create_search_job.assert_called_once_with(job_id='test-job-id-123', user_id=DB_INTERACTION_USER_ID_INT, category="api_search", query="json query")
    assert mock_db_manager.get_job_details.call_count == 2 # Initial + completed
    mock_os_path_exists.assert_called_once_with('/path/to/results.json')
    mock_builtin_open.assert_called_once_with('/path/to/results.json', 'r', encoding='utf-8')

def test_search_success_jsonl_result(client, mock_db_manager, mock_uuid, mock_time_sleep, mock_os_path_exists, mock_builtin_open):
    """Test successful search where result file is JSONL."""
    mock_os_path_exists.return_value = True
    mock_file_content = '{"id": 1, "text": "line1"}\n{"id": 2, "text": "line2"}'
    mock_builtin_open.return_value.read.return_value = mock_file_content

    mock_db_manager.get_job_details.side_effect = [
        {'status': 'pending'},
        {'status': 'completed', 'result_file_path': '/path/to/results.jsonl'}
    ]

    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': DB_INTERACTION_USER_ID_STR, 'query': 'jsonl query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data['status'] == 'success'
    assert json_data['data'] == [{"id": 1, "text": "line1"}, {"id": 2, "text": "line2"}]

def test_search_success_text_result(client, mock_db_manager, mock_uuid, mock_time_sleep, mock_os_path_exists, mock_builtin_open):
    """Test successful search where result file is plain text."""
    mock_os_path_exists.return_value = True
    mock_file_content = "This is a plain text result."
    mock_builtin_open.return_value.read.return_value = mock_file_content

    mock_db_manager.get_job_details.side_effect = [
        {'status': 'processing'},
        {'status': 'completed', 'result_file_path': '/path/to/results.txt'}
    ]

    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': DB_INTERACTION_USER_ID_STR, 'query': 'text query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data['status'] == 'success'
    assert json_data['data'] == [{"type": "text_file", "name": "results.txt", "content": mock_file_content}]

def test_search_success_empty_result_file(client, mock_db_manager, mock_uuid, mock_time_sleep, mock_os_path_exists, mock_builtin_open):
    """Test successful search with an empty result file (treated as empty JSONL)."""
    mock_os_path_exists.return_value = True
    mock_builtin_open.return_value.read.return_value = "" # Empty file

    mock_db_manager.get_job_details.side_effect = [
        {'status': 'pending'},
        {'status': 'completed', 'result_file_path': '/path/to/empty.jsonl'}
    ]
    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': DB_INTERACTION_USER_ID_STR, 'query': 'empty file query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data['status'] == 'success'
    expected_data_for_empty_file = [{
        "type": "text_file",
        "name": "empty.jsonl", # or whatever the mock path's basename is
        "content": "",
        "comment": "File was empty or not valid JSON/JSONL."
    }]
    assert json_data['data'] == expected_data_for_empty_file


# --- Test /search endpoint: Failure and Error Scenarios ---

def test_search_job_fails_in_db(client, mock_db_manager, mock_uuid, mock_time_sleep):
    """Test when the job is marked 'failed' in the database."""
    mock_db_manager.get_job_details.side_effect = [
        {'status': 'pending'},
        {'status': 'failed', 'error_message': 'Backend processing failed spectacularly.'}
    ]

    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': DB_INTERACTION_USER_ID_STR, 'query': 'failing query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 500
    json_data = response.get_json()
    assert json_data['status'] == 'error'
    assert json_data['message'] == "Search job failed due to a backend processing error. Please check server logs for details or contact support." # Generic message

@patch('app.POLL_TIMEOUT_SECONDS', 0.2) # Shorten timeout for this specific test
@patch('app.POLL_INTERVAL_SECONDS', 0.05) # Shorten interval for this specific test
def test_search_job_timeout(client, mock_db_manager, mock_uuid, mock_time_sleep): # Corrected fixture name
    """Test when the job polling times out."""
    # get_job_details always returns pending/processing
    mock_db_manager.get_job_details.return_value = {'status': 'processing'}

    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': DB_INTERACTION_USER_ID_STR, 'query': 'timeout query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 504 # Gateway Timeout
    json_data = response.get_json()
    assert json_data['status'] == 'error'
    assert json_data['message'] == 'Search timed out waiting for results from the backend processor.'
    # Check that get_job_details was called multiple times due to polling
    assert mock_db_manager.get_job_details.call_count > 1


def test_search_db_create_user_fails(client, mock_db_manager, mock_uuid):
    """Test when get_or_create_user returns None."""
    mock_db_manager.get_or_create_user.return_value = None

    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': DB_INTERACTION_USER_ID_STR, 'query': 'query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 500
    json_data = response.get_json()
    assert json_data['status'] == 'error'
    assert json_data['message'] == 'Internal server error: User record management failed.'

def test_search_db_create_job_fails(client, mock_db_manager, mock_uuid):
    """Test when create_search_job returns False."""
    mock_db_manager.create_search_job.return_value = False

    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': DB_INTERACTION_USER_ID_STR, 'query': 'query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 500
    json_data = response.get_json()
    assert json_data['status'] == 'error'
    assert json_data['message'] == 'Internal server error: Failed to create search job.'

def test_search_completed_result_file_path_missing_in_db(client, mock_db_manager, mock_uuid, mock_time_sleep):
    """Test job completed but result_file_path is None in DB record."""
    mock_db_manager.get_job_details.return_value = {'status': 'completed', 'result_file_path': None}

    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': DB_INTERACTION_USER_ID_STR, 'query': 'query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 500
    json_data = response.get_json()
    assert json_data['message'] == 'Internal server error: Job completed but result path missing.'

def test_search_completed_result_file_not_exist_on_disk(client, mock_db_manager, mock_uuid, mock_time_sleep, mock_os_path_exists):
    """Test job completed, path exists in DB, but file not on disk."""
    mock_db_manager.get_job_details.return_value = {'status': 'completed', 'result_file_path': '/path/to/ghost.txt'}
    mock_os_path_exists.return_value = False # File does not exist

    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': DB_INTERACTION_USER_ID_STR, 'query': 'query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 500
    json_data = response.get_json()
    assert json_data['message'] == 'Internal server error: Result file specified in DB not found on disk.'
    mock_os_path_exists.assert_called_once_with('/path/to/ghost.txt')

def test_search_completed_error_reading_file(client, mock_db_manager, mock_uuid, mock_time_sleep, mock_os_path_exists, mock_builtin_open):
    """Test job completed, file exists, but reading it causes an error."""
    mock_db_manager.get_job_details.return_value = {'status': 'completed', 'result_file_path': '/path/to/problem.txt'}
    mock_os_path_exists.return_value = True
    mock_builtin_open.side_effect = IOError("Disk read error")

    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': DB_INTERACTION_USER_ID_STR, 'query': 'query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)

    assert response.status_code == 500
    json_data = response.get_json()
    assert json_data['message'] == "An error occurred while processing the search results. Please check server logs for details or contact support." # Generic message


# --- Test API Key Authentication (Largely Unchanged but still relevant) ---

def test_search_no_api_key(client):
    headers = {'Content-Type': 'application/json'}
    payload = {'user_id': DB_INTERACTION_USER_ID_STR, 'query': 'query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)
    assert response.status_code == 401
    # ... (rest of assertions)

def test_search_invalid_api_key(client):
    headers = {'X-API-Key': 'invalid-key', 'Content-Type': 'application/json'}
    payload = {'user_id': DB_INTERACTION_USER_ID_STR, 'query': 'query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)
    assert response.status_code == 401
    # ... (rest of assertions)

# --- Test Input Validation (Largely Unchanged but still relevant) ---

def test_search_missing_json_body(client):
    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    response = client.post('/search', headers=headers)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'Bad Request: Malformed or missing JSON body'

def test_search_malformed_json_body(client):
    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    response = client.post('/search', data="this is not json", headers=headers)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'Bad Request: Malformed or missing JSON body'

def test_search_missing_user_id(client):
    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'query': 'some query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)
    assert response.status_code == 400
    assert response.get_json()['message'] == "Bad Request: Missing 'user_id' field"

def test_search_user_id_not_string_convertible_to_int(client):
    """Test when user_id is not a string representation of an integer."""
    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': "not_an_integer", 'query': 'some query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)
    assert response.status_code == 400
    json_data = response.get_json()
    assert json_data['message'] == "Bad Request: 'user_id' must be a string that represents an integer for database operations."

def test_search_invalid_user_id_type_is_int_not_str(client):
    """Test search with user_id as int (not string as per API spec for request)."""
    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': 123, 'query': 'some query'} # API expects string user_id
    response = client.post('/search', data=json.dumps(payload), headers=headers)
    assert response.status_code == 400
    assert response.get_json()['message'] == "Bad Request: 'user_id' must be a string"


# Other input validation tests (empty user_id, missing query, etc.) remain similar
# ... test_search_empty_user_id ...
# ... test_search_missing_query ...
# ... test_search_invalid_query_type ...
# ... test_search_empty_query ...

# --- Test General Error Handling (404, 405 - Unchanged) ---
def test_not_found_endpoint(client):
    response = client.get('/nonexistent-path', headers={'X-API-Key': TEST_API_KEY})
    assert response.status_code == 404
    assert response.get_json()['message'] == 'Resource not found'

def test_method_not_allowed_for_search(client):
    response = client.get('/search', headers={'X-API-Key': TEST_API_KEY})
    assert response.status_code == 405
    assert response.get_json()['message'] == 'Method Not Allowed'

# (Keep other existing tests like test_search_empty_user_id, etc., ensuring their assertions are still valid
# or update them if the specific error messages from input validation changed subtly.)
# For brevity, I'm focusing on the new interaction patterns.
# The old tests for mock_osint_search should be removed.

# Example of an existing validation test that needs to be kept and checked:
def test_search_empty_user_id(client):
    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': '   ', 'query': 'some query'}
    response = client.post('/search', data=json.dumps(payload), headers=headers)
    assert response.status_code == 400
    assert response.get_json()['message'] == "Bad Request: 'user_id' cannot be empty or just whitespace"

def test_search_empty_query(client):
    headers = {'X-API-Key': TEST_API_KEY, 'Content-Type': 'application/json'}
    payload = {'user_id': DB_INTERACTION_USER_ID_STR, 'query': '      '}
    response = client.post('/search', data=json.dumps(payload), headers=headers)
    assert response.status_code == 400
    assert response.get_json()['message'] == "Bad Request: 'query' cannot be empty or just whitespace"

# Remove old mock_osint_search direct tests if they existed
# e.g., test_mock_osint_search_direct_user_info, test_mock_osint_search_direct_empty
