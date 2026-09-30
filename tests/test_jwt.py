"""JWT issuance + verification tests (v0.3)."""

from app.auth.jwt import create_access_token, decode_token, TokenError
import pytest


class TestJWT:
    def test_round_trip(self):
        token = create_access_token(
            user_id=1, email="t@t.com", user_type="subscriber_admin",
            subscriber_id=7,
        )
        payload = decode_token(token)
        assert payload["sub"] == "1"
        assert payload["subscriber_id"] == 7
        assert payload["type"] == "subscriber_admin"

    def test_internal_user_no_tenancy(self):
        token = create_access_token(
            user_id=2, email="i@t.com", user_type="internal",
        )
        payload = decode_token(token)
        assert payload["subscriber_id"] is None

    def test_expired_token_raises(self):
        token = create_access_token(
            user_id=1, email="t@t.com", user_type="internal",
            expires_minutes=-1,
        )
        with pytest.raises(TokenError, match="expired"):
            decode_token(token)

    def test_tampered_token_raises(self):
        with pytest.raises(TokenError):
            decode_token("not.a.token")
