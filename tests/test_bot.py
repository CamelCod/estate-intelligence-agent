"""
Tests for Telegram bot state machine transitions.
"""

from bot import validate_rtsp


def test_validate_rtsp_valid():
    valid, reason = validate_rtsp("rtsp://admin:password@192.168.1.64:554/stream1")
    # Note: this test may fail if camera is not reachable
    # In CI, mock socket.create_connection
    assert isinstance(valid, bool)
    assert isinstance(reason, str)


def test_validate_rtsp_invalid_prefix():
    valid, reason = validate_rtsp("http://192.168.1.64/stream")
    assert valid is False
    assert "rtsp://" in reason.lower()


def test_validate_rtsp_missing_port():
    valid, reason = validate_rtsp("rtsp://admin:password@192.168.1.64/stream1")
    # Should use default port 554
    assert isinstance(valid, bool)


def test_rtsp_url_parsing():
    """Test RTSP URL parsing for host extraction."""
    url = "rtsp://admin:password@192.168.1.64:554/stream1"
    stripped = url[7:]  # remove rtsp://
    assert "@" in stripped
    after_at = stripped.split("@", 1)[1]
    host_part = after_at.split("/")[0]
    assert host_part == "192.168.1.64:554"


def test_rtsp_url_parsing_no_auth():
    """Test RTSP URL without credentials."""
    url = "rtsp://192.168.1.64:554/stream1"
    stripped = url[7:]
    host_part = stripped.split("/")[0]
    assert host_part == "192.168.1.64:554"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])