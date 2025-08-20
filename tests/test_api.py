import pytest
from unittest.mock import patch, MagicMock
import os
import time
import json
import jwt
from datetime import datetime, timedelta

# Import the app and config
from app import app as flask_app
import config

# --- Test Fixtures ---

@pytest.fixture
def app():
    """Create and configure a new app instance for each test."""
    yield flask_app

@pytest.fixture
def client(app):
    """A test client for the app."""
    return app.test_client()

@pytest.fixture(autouse=True)
def patch_config_and_db(monkeypatch):
    """
    Auto-used fixture to:
    1. Patch the config to use a predictable JWT secret key for tests.
    2. Mock all db_manager functions to isolate the API from the database.
    """
    # 1. Patch config
    test_jwt_key = 'test-jwt-secret-key-for-unit-tests'
    monkeypatch.setattr(config, 'API_JWT_KEY', test_jwt_key)

    # 2. Mock db_manager
    with patch('app.db_manager') as mock_db:
        mock_db.get_or_create_user.return_value = {'user_id': 12345, 'username': 'api_user_12345'}
        mock_db.create_search_job.return_value = True
        yield mock_db, test_jwt_key # Yield both the mock and the key for tests that need it

# --- Helper Function ---

def get_auth_header(key, expired=False, sub='test-client'):
    """Helper to generate a valid or expired JWT token and return the auth header."""
    now = datetime.utcnow() # Use UTC for consistency
    if expired:
        exp_time = now - timedelta(minutes=5)
    else:
        exp_time = now + timedelta(minutes=5)

    payload = {
        'sub': sub,
        'exp': exp_time,
        'iat': now
    }
    token = jwt.encode(payload, key, algorithm="HS256")
    return {'X-API-Key': token}

# --- API Tests ---

def test_search_success_polling(client, patch_config_and_db):
    """Test a successful search scenario where the job completes after polling."""
    mock_db_manager, test_key = patch_config_and_db

    job_id_mock = "mock_job_123"
    result_file_content = json.dumps([{"result": "some data"}])
    result_file_path = "/tmp/mock_result.json"
    with open(result_file_path, 'w') as f:
        f.write(result_file_content)

    with patch('uuid.uuid4', return_value=MagicMock(hex=job_id_mock)):
        mock_db_manager.get_job_details.side_effect = [
            {'status': 'pending'},
            {'status': 'processing'},
            {'status': 'completed', 'result_file_path': result_file_path}
        ]

        with patch('time.sleep', return_value=None):
            response = client.post('/search',
                                   headers=get_auth_header(test_key),
                                   json={'query': 'test2'})

    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data['status'] == 'success'
    assert json_data['data'] == [{"result": "some data"}]
    mock_db_manager.create_search_job.assert_called_once_with(job_id=job_id_mock, user_id=config.DEDICATED_API_USER_ID, category='api_search', query='test2')
    os.remove(result_file_path)

def test_search_job_failed(client, patch_config_and_db):
    """Test a scenario where the search job fails in the backend."""
    mock_db_manager, test_key = patch_config_and_db
    job_id_mock = "mock_job_fail"

    with patch('uuid.uuid4', return_value=MagicMock(hex=job_id_mock)):
        mock_db_manager.get_job_details.return_value = {
            'status': 'failed', 'error_message': 'Backend error.'
        }
        with patch('time.sleep', return_value=None):
            response = client.post('/search',
                                   headers=get_auth_header(test_key),
                                   json={'user_id': '12345', 'query': 'failing query'})

    assert response.status_code == 500
    assert 'Search job failed' in response.get_json()['message']

def test_search_timeout(client, patch_config_and_db):
    """Test a scenario where the search job times out."""
    mock_db_manager, test_key = patch_config_and_db
    with patch('app.POLL_TIMEOUT_SECONDS', 0.1), patch('app.POLL_INTERVAL_SECONDS', 0.05):
        with patch('uuid.uuid4', return_value=MagicMock(hex="mock_job_timeout")):
            mock_db_manager.get_job_details.return_value = {'status': 'processing'}
            with patch('time.sleep', return_value=None):
                response = client.post('/search',
                                       headers=get_auth_header(test_key),
                                       json={'user_id': '12345', 'query': 'timeout query'})

    assert response.status_code == 504
    assert 'Search timed out' in response.get_json()['message']

# --- Auth-specific Tests ---

def test_search_no_auth_header(client):
    """Test request without an X-API-Key header."""
    response = client.post('/search', json={'user_id': '12345', 'query': 'test'})
    assert response.status_code == 401
    assert 'Missing X-API-Key header' in response.get_json()['message']

def test_search_invalid_token_format(client):
    """Test a malformed token."""
    # Malformed token
    response = client.post('/search', headers={'X-API-Key': 'not.a.real.token'}, json={'user_id': '12345', 'query': 'test'})
    assert response.status_code == 401
    assert 'Invalid token' in response.get_json()['message']

def test_search_invalid_token_signature(client, patch_config_and_db):
    """Test a token signed with the wrong key."""
    _, test_key = patch_config_and_db
    invalid_header = get_auth_header(key='a-different-wrong-key')
    response = client.post('/search', headers=invalid_header, json={'user_id': '12345', 'query': 'test'})
    assert response.status_code == 401
    assert 'Signature verification failed' in response.get_json()['message']

def test_search_expired_token(client, patch_config_and_db):
    """Test an expired token."""
    _, test_key = patch_config_and_db
    expired_header = get_auth_header(key=test_key, expired=True)
    response = client.post('/search', headers=expired_header, json={'user_id': '12345', 'query': 'test'})
    assert response.status_code == 401
    assert 'Token has expired' in response.get_json()['message']

# --- Input Validation Tests ---

def test_search_missing_query(client, patch_config_and_db):
    """Test request with missing 'query' in JSON payload."""
    _, test_key = patch_config_and_db
    response = client.post('/search', headers=get_auth_header(test_key), json={'user_id': '12345'})
    assert response.status_code == 400
    assert "Missing 'query' field" in response.get_json()['message']

def test_search_malformed_json(client, patch_config_and_db):
    """Test request with a malformed JSON body."""
    _, test_key = patch_config_and_db
    response = client.post('/search', headers=get_auth_header(test_key), data='not-json', content_type='application/json')
    assert response.status_code == 400
    assert 'Malformed or missing JSON body' in response.get_json()['message']
