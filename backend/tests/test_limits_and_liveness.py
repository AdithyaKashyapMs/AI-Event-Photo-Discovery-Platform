import time

import pytest
import redis
from fastapi import HTTPException

from conftest import make_client, register

from app import liveness, ratelimit


# --- Rate limits ---


def test_login_is_rate_limited_per_email(client):
    register(client, 'user@example.com')
    with make_client() as attacker:
        statuses = [
            attacker.post('/auth/login', json={'email': 'user@example.com', 'password': 'guess-guess'}).status_code
            for _ in range(ratelimit.LOGIN_PER_EMAIL.limit + 1)
        ]
        assert statuses[:-1] == [401] * ratelimit.LOGIN_PER_EMAIL.limit
        assert statuses[-1] == 429
        blocked = attacker.post('/auth/login', json={'email': 'user@example.com', 'password': 'guess-guess'})
    assert int(blocked.headers['Retry-After']) > 0


def test_email_limit_ignores_case_and_spaces(client):
    register(client, 'user@example.com')
    with make_client() as attacker:
        for i in range(ratelimit.LOGIN_PER_EMAIL.limit):
            email = ' USER@example.com ' if i % 2 else 'user@EXAMPLE.com'
            attacker.post('/auth/login', json={'email': email, 'password': 'guess-guess'})
        response = attacker.post('/auth/login', json={'email': 'user@example.com', 'password': 'guess-guess'})
    assert response.status_code == 429


def test_liveness_challenges_are_rate_limited(world):
    url = f'/events/{world.event_id}/liveness/challenge'
    statuses = [world.attendee.post(url).status_code for _ in range(ratelimit.LIVENESS_CHALLENGE_PER_USER.limit + 1)]
    assert statuses[-1] == 429
    assert set(statuses[:-1]) == {200}
    # Limits are per user: another guest is unaffected.
    assert world.other_attendee.post(url).status_code == 200


def test_selfie_search_is_rate_limited_before_any_face_work(world, monkeypatch):
    from app.routers import events

    def no_face_model():
        raise AssertionError('face model must not load for rate-limited requests')

    monkeypatch.setattr(events, 'get_selfie_face_app', no_face_model)
    url = f'/events/{world.event_id}/match-selfie'
    files = [('files', ('a.jpg', b'x', 'image/jpeg'))]
    statuses = [
        world.attendee.post(url, files=files, data={'challenge_id': 'bogus'}).status_code
        for _ in range(ratelimit.SELFIE_MATCH_PER_USER.limit + 1)
    ]
    assert statuses[:-1] == [422] * ratelimit.SELFIE_MATCH_PER_USER.limit  # bogus challenge
    assert statuses[-1] == 429


def test_rate_limit_fails_open_when_redis_is_down():
    class BrokenRedis:
        def pipeline(self):
            raise redis.ConnectionError('down')

    ratelimit.set_redis_client(BrokenRedis())
    rule = ratelimit.Rule('test', 1, 60)
    for _ in range(5):
        ratelimit.hit(rule, 'key')  # never raises


def test_rate_limit_window_resets(redis_fake):
    rule = ratelimit.Rule('test', 2, 60)
    ratelimit.hit(rule, 'k')
    ratelimit.hit(rule, 'k')
    with pytest.raises(HTTPException) as error:
        ratelimit.hit(rule, 'k')
    assert error.value.status_code == 429
    redis_fake.delete('ratelimit:test:k')  # what expiry does after 60 s
    ratelimit.hit(rule, 'k')


# --- Liveness challenges ---


def test_challenge_is_single_use(monkeypatch):
    challenge = liveness.create_challenge(user_id=1, event_id=2)
    monkeypatch.setattr(liveness.time, 'time', lambda: time.time_ns() / 1e9 + 10)
    assert liveness.consume_challenge(challenge['challenge_id'], 1, 2) == challenge['steps']
    with pytest.raises(liveness.LivenessError):
        liveness.consume_challenge(challenge['challenge_id'], 1, 2)


def test_challenge_is_bound_to_user_and_event(monkeypatch):
    monkeypatch.setattr(liveness.time, 'time', lambda: time.time_ns() / 1e9 + 10)
    for user_id, event_id in ((99, 2), (1, 99)):
        challenge = liveness.create_challenge(user_id=1, event_id=2)
        with pytest.raises(liveness.LivenessError):
            liveness.consume_challenge(challenge['challenge_id'], user_id, event_id)


def test_challenge_cannot_be_finished_instantly():
    challenge = liveness.create_challenge(user_id=1, event_id=2)
    with pytest.raises(liveness.LivenessError, match='too quickly'):
        liveness.consume_challenge(challenge['challenge_id'], 1, 2)


def test_challenge_steps_alternate_center_and_turn():
    for _ in range(20):
        steps = liveness.create_challenge(1, 2)['steps']
        assert steps[0] == steps[2] == liveness.STEP_CENTER
        assert steps[1] in liveness.TURN_STEPS and steps[3] in liveness.TURN_STEPS


def test_selfie_search_requires_a_liveness_challenge(world):
    response = world.attendee.post(
        f'/events/{world.event_id}/match-selfie', files=[('files', ('a.jpg', b'x', 'image/jpeg'))]
    )
    assert response.status_code == 400
    assert 'live camera check' in response.json()['detail']


def test_selfie_search_is_capped_when_busy(world, monkeypatch):
    from app.routers import events

    class FullSemaphore:
        def acquire(self, timeout=None):
            return False

        def release(self):
            raise AssertionError('not acquired')

    monkeypatch.setattr(events, '_selfie_slots', FullSemaphore())
    monkeypatch.setattr(events, 'liveness_required', lambda: False)
    response = world.attendee.post(
        f'/events/{world.event_id}/match-selfie', files=[('files', ('a.jpg', b'x', 'image/jpeg'))]
    )
    assert response.status_code == 503
    assert response.headers['Retry-After']
