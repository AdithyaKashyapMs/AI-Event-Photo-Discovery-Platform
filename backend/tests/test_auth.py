import os
import subprocess
import sys
from pathlib import Path

from conftest import PASSWORD, make_client, register

from app.auth import SESSION_COOKIE, SESSION_HINT_COOKIE, create_access_token

BACKEND_DIR = Path(__file__).resolve().parent.parent


def test_register_sets_httponly_session_cookie_and_returns_no_token(client):
    response = register(client, 'new@example.com')
    body = response.json()
    assert 'access_token' not in body
    assert body['role'] == 'attendee'

    set_cookie = [h for h in response.headers.get_list('set-cookie')]
    session_cookie = next(h for h in set_cookie if h.startswith(f'{SESSION_COOKIE}='))
    hint_cookie = next(h for h in set_cookie if h.startswith(f'{SESSION_HINT_COOKIE}='))
    assert 'HttpOnly' in session_cookie
    assert 'samesite=lax' in session_cookie.lower()
    assert 'HttpOnly' not in hint_cookie

    assert client.get('/auth/me').json()['email'] == 'new@example.com'


def test_login_wrong_password_and_unknown_email_look_the_same(client):
    register(client, 'user@example.com')
    with make_client() as other:
        wrong = other.post('/auth/login', json={'email': 'user@example.com', 'password': 'nope-nope'})
        unknown = other.post('/auth/login', json={'email': 'ghost@example.com', 'password': 'nope-nope'})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_logout_clears_session(client):
    register(client, 'user@example.com')
    assert client.post('/auth/logout').status_code == 200
    assert client.get('/auth/me').status_code == 401


def test_requests_without_a_session_are_rejected(client):
    assert client.get('/auth/me').status_code == 401
    assert client.get('/events/').status_code == 401


def test_bearer_token_still_works_for_scripts(client):
    register(client, 'user@example.com')
    user_id = client.get('/auth/me').json()['id']
    token = create_access_token({'sub': str(user_id), 'role': 'attendee'})
    with make_client() as script:
        response = script.get('/auth/me', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 200


def test_tampered_token_is_rejected(client):
    with make_client() as script:
        response = script.get('/auth/me', headers={'Authorization': 'Bearer not.a.real-token'})
    assert response.status_code == 401


def test_cookie_requests_from_another_origin_are_blocked(client):
    register(client, 'host@example.com', 'host')
    evil = client.post('/events/', params={'name': 'x'}, headers={'Origin': 'https://evil.example'})
    assert evil.status_code == 403
    ok = client.post('/events/', params={'name': 'x'})
    assert ok.status_code == 200
    # Reads are not state-changing and stay allowed.
    assert client.get('/events/', headers={'Origin': 'https://evil.example'}).status_code == 200


def test_login_from_another_origin_is_blocked(client):
    register(client, 'user@example.com')
    response = client.post(
        '/auth/login',
        json={'email': 'user@example.com', 'password': PASSWORD},
        headers={'Origin': 'https://evil.example'},
    )
    assert response.status_code == 403


def test_production_refuses_to_start_without_a_real_secret():
    env = {**os.environ, 'APP_ENV': 'production'}
    env.pop('JWT_SECRET_KEY', None)
    result = subprocess.run(
        [sys.executable, '-c', 'import app.auth'],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert 'JWT_SECRET_KEY' in result.stderr

    env['JWT_SECRET_KEY'] = 'short'
    result = subprocess.run(
        [sys.executable, '-c', 'import app.auth'], cwd=BACKEND_DIR, env=env, capture_output=True, text=True
    )
    assert result.returncode != 0


def test_production_uses_secure_cookies():
    env = {**os.environ, 'APP_ENV': 'production', 'JWT_SECRET_KEY': 'x' * 48}
    env.pop('COOKIE_SECURE', None)
    result = subprocess.run(
        [sys.executable, '-c', 'import app.auth as a; print(a.COOKIE_SECURE)'],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == 'True', result.stderr
