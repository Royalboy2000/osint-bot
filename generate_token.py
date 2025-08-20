import jwt
import datetime
import config

# Generate a token that is valid for 1 hour
payload = {
    'sub': 'test-company-for-start-command',
    'exp': datetime.datetime.now() + datetime.timedelta(hours=1),
    'iat': datetime.datetime.now()
}

token = jwt.encode(payload, config.API_JWT_KEY, algorithm="HS256")
print(token)
