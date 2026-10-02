import logging

from buddy.logging_setup import RedactingFilter, redact


def test_redact_known_and_patterns():
    assert "abcd1234" not in redact("key abcd1234 used", ["abcd1234"])
    assert "sk-abcdefgh12345" not in redact("x sk-abcdefgh12345 y")
    assert "Bearer ***" in redact("Authorization: Bearer abcdef123456")
    assert "api_key=***" in redact("api_key=hunter22")


def test_filter_masks_args():
    rec = logging.LogRecord("n", logging.INFO, "f", 1, "token=%s", ("zzzzzzzz",), None)
    RedactingFilter(["zzzzzzzz"]).filter(rec)
    assert "zzzzzzzz" not in rec.getMessage()
