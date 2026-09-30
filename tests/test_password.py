"""Tests for password hashing and policy. No DB needed."""

import pytest

from app.auth.password import (
    PasswordPolicyError,
    hash_password,
    needs_rehash,
    validate_password,
    verify_password,
)


class TestHashing:
    def test_hash_is_not_plaintext(self):
        h = hash_password("correct horse battery staple")
        assert h != "correct horse battery staple"
        assert h.startswith("$2")    # bcrypt format

    def test_verify_correct_password(self):
        h = hash_password("hunter2hunter2")
        assert verify_password("hunter2hunter2", h) is True

    def test_verify_wrong_password(self):
        h = hash_password("hunter2hunter2")
        assert verify_password("Hunter2hunter2", h) is False

    def test_verify_bad_hash_returns_false_not_raises(self):
        # Garbage hash should NOT raise -- the route uses the return value
        assert verify_password("anything", "not-a-real-hash") is False
        assert verify_password("anything", "") is False

    def test_each_hash_is_unique(self):
        # bcrypt's salt means same input produces different hashes
        a = hash_password("samepassword1")
        b = hash_password("samepassword1")
        assert a != b
        assert verify_password("samepassword1", a)
        assert verify_password("samepassword1", b)


class TestPolicy:
    def test_minimum_length_enforced(self):
        with pytest.raises(PasswordPolicyError, match="at least"):
            validate_password("short1")

    def test_needs_letter(self):
        with pytest.raises(PasswordPolicyError, match="letter"):
            validate_password("1234567890")

    def test_needs_digit(self):
        with pytest.raises(PasswordPolicyError, match="number"):
            validate_password("onlyletters")

    def test_no_leading_or_trailing_whitespace(self):
        with pytest.raises(PasswordPolicyError, match="whitespace"):
            validate_password(" hunter2hunter2 ")

    def test_acceptable_password_passes(self):
        validate_password("hunter2hunter2")    # should not raise


class TestRehash:
    def test_current_hash_does_not_need_rehash(self):
        h = hash_password("hunter2hunter2")
        assert needs_rehash(h) is False
