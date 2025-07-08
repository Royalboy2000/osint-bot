# OSINT Search API

A RESTful API to proxy search requests to an OSINT search engine. This API requires API key authentication for all endpoints and returns JSON responses. It is designed to not expose or depend on any Telegram-session or bot-account data.

## Features

- Secure: API key authentication (`X-API-Key` header).
- Input Validation: `user_id` and `query` parameters are validated.
- Standardized JSON Responses: `status`, `data`, `message` fields.
- Mocked Backend: Simulates an OSINT search engine for demonstration.
- Documented: Includes an OpenAPI (Swagger) specification.

## Project Structure

```
.
├── app.py                  # Main Flask application
├── openapi.yaml            # OpenAPI (Swagger) specification
├── requirements.txt        # Python dependencies
├── README.md               # This file
└── tests/
    ├── __init__.py
    └── test_api.py         # Pytest unit tests
```

## Setup and Installation

1.  **Clone the repository (if applicable):**
    ```bash
    # git clone <repository-url>
    # cd <repository-directory>
    ```

2.  **Create a virtual environment:**
    It's highly recommended to use a virtual environment to manage dependencies.
    ```bash
    python3 -m venv venv  # Or python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Set the API Key:**
    The API requires an API key for authentication. This key should be set as an environment variable named `SEARCH_API_KEY`.

    **For Linux/macOS:**
    ```bash
    export SEARCH_API_KEY="your_strong_and_unique_api_key_here"
    ```
    To make it permanent, add this line to your shell's configuration file (e.g., `~/.bashrc`, `~/.zshrc`).

    **For Windows (Command Prompt):**
    ```bash
    set SEARCH_API_KEY="your_strong_and_unique_api_key_here"
    ```
    **For Windows (PowerShell):**
    ```bash
    $env:SEARCH_API_KEY="your_strong_and_unique_api_key_here"
    ```
    For development, if `SEARCH_API_KEY` is not set, the application will use a default, insecure key (`your-secret-api-key-for-dev-only`) and print a warning. **Do not use the default key in production.** A strong key can be generated using `python -c "import secrets; print(secrets.token_hex(32))"`.

## Running the Application

Once the setup is complete and `SEARCH_API_KEY` is set:

```bash
python app.py
```

The API will typically start on `http://0.0.0.0:5000/`. You can configure the port and debug mode using environment variables:
- `PORT`: Defaults to `5000`.
- `FLASK_DEBUG`: Set to `True` or `False`. Defaults to `True` (enabling debug mode).

Example:
```bash
export PORT=8080
export FLASK_DEBUG=False # For a more production-like setting (though a proper WSGI server is recommended for production)
python app.py
```

## API Usage

The primary endpoint is `/search`.

- **Method:** `POST`
- **Headers:**
    - `Content-Type: application/json`
    - `X-API-Key: <your_api_key>`
- **Request Body (JSON):**
    ```json
    {
        "user_id": "some_user_identifier",
        "query": "search term here"
    }
    ```

**Example Request (using cURL):**

```bash
curl -X POST http://localhost:5000/search \
-H "Content-Type: application/json" \
-H "X-API-Key: your_strong_and_unique_api_key_here" \
-d '{
    "user_id": "user123",
    "query": "example query"
}'
```

**Example Success Response:**

```json
{
    "status": "success",
    "message": "Search successful",
    "data": [
        {
            "id": "result-123",
            "title": "Mock Result 1 for 'example query'",
            "snippet": "This is the first mock search result related to 'example query'. Processed for user: user123.",
            "source": "MockSource1"
        }
        // ... more results
    ]
}
```

**Example Error Response (e.g., invalid API key):**

```json
{
    "status": "error",
    "message": "Unauthorized: Invalid or missing API Key",
    "data": []
}
```

## API Documentation (OpenAPI/Swagger)

The API is documented using an OpenAPI specification located in `openapi.yaml`.
You can use tools like Swagger Editor or Swagger UI to view and interact with this specification.

-   **Swagger Editor (Online):** Go to [https://editor.swagger.io/](https://editor.swagger.io/) and paste the content of `openapi.yaml`.
-   **Running Swagger UI locally (example using Docker):**
    If `openapi.yaml` is in the current directory:
    ```bash
    docker run -p 8081:8080 -e SWAGGER_JSON=/tmp/openapi.yaml -v $(pwd)/openapi.yaml:/tmp/openapi.yaml swaggerapi/swagger-ui
    ```
    Then open `http://localhost:8081` in your browser.
    Alternatively, many Flask extensions can serve Swagger UI directly from the `openapi.yaml` file (e.g., `flask-swagger-ui`).

## Running Tests

The project uses `pytest` for unit testing.

1.  Ensure you have installed development dependencies (including `pytest` from `requirements.txt`).
2.  Make sure you are in the project root directory with the virtual environment activated.
3.  Run the tests:
    ```bash
    pytest
    ```
    Or, for more verbose output:
    ```bash
    pytest -v
    ```

The tests cover API functionality, authentication, input validation, and error handling.
The API key for tests will use the `SEARCH_API_KEY` environment variable if set, otherwise, it defaults to the development key.
```
