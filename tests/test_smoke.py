"""Smoke tests for configuration, secret-safe logging, and main entry point."""

from __future__ import annotations

import logging
from pathlib import Path

from config.logging_config import setup_logging
from config.settings import load_settings
from main import main


def test_settings_and_password_masking(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "APP_NAME=Test Cam App",
                f"LOG_DIR={tmp_path / 'logs'}",
                "CAMERA_IP=10.0.0.50",
                "CAMERA_USER=operator",
                "CAMERA_PASS=TopSecret#123",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("CAMERA_PASS", raising=False)
    settings = load_settings(env_file=env_file, override=True)
    assert settings.camera_ip == "10.0.0.50"
    assert settings.camera_main_stream == "stream1"
    assert settings.camera_sub_stream == "stream2"

    setup_logging(settings)
    logger = logging.getLogger("smoke-test")
    logger.info("Connecting to rtsp://operator:TopSecret#123@10.0.0.50:554/stream1")
    logger.error("Failed with CAMERA_PASS=TopSecret#123")

    for handler in logging.getLogger().handlers:
        handler.flush()

    log_text = settings.log_path.read_text(encoding="utf-8")
    assert "TopSecret#123" not in log_text
    assert "****" in log_text


def test_main_exits_cleanly_and_prints_banner(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("APP_NAME", "Vision Surveillance Test")

    exit_code = main()
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Vision Surveillance Test" in captured.out
