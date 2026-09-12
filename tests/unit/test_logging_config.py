"""Unit tests for rental_search_agent.logging_config."""

import logging
import time

import pytest

from rental_search_agent import logging_config as lc
from rental_search_agent.logging_config import (
    clear_run_id,
    configure_logging,
    format_run_id_suffix,
    get_run_id,
    log_stage,
    new_run_id,
    reset_logging_config,
    set_run_id,
)


@pytest.fixture(autouse=True)
def _reset_logging():
    """Isolate logging config and run_id between tests."""
    reset_logging_config()
    clear_run_id()
    yield
    reset_logging_config()
    clear_run_id()


class TestConfigureLogging:
    def test_respects_log_level_from_env(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        monkeypatch.delenv("LOG_FILE", raising=False)
        configure_logging()
        pkg = logging.getLogger("rental_search_agent")
        assert pkg.level == logging.WARNING
        assert any(isinstance(h, logging.StreamHandler) for h in pkg.handlers)

    def test_respects_log_level_argument(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "ERROR")
        monkeypatch.delenv("LOG_FILE", raising=False)
        configure_logging(level="DEBUG")
        pkg = logging.getLogger("rental_search_agent")
        assert pkg.level == logging.DEBUG

    def test_default_level_is_info(self, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        monkeypatch.delenv("LOG_FILE", raising=False)
        configure_logging()
        pkg = logging.getLogger("rental_search_agent")
        assert pkg.level == logging.INFO

    def test_file_handler_only_when_log_file_set(self, monkeypatch, tmp_path):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        monkeypatch.delenv("LOG_FILE", raising=False)
        configure_logging(project_root=tmp_path)
        pkg = logging.getLogger("rental_search_agent")
        assert not any(isinstance(h, logging.FileHandler) for h in pkg.handlers)

        reset_logging_config()
        log_path = tmp_path / "test.log"
        configure_logging(log_file=log_path, project_root=tmp_path)
        pkg = logging.getLogger("rental_search_agent")
        file_handlers = [h for h in pkg.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 1
        pkg.info("hello file")
        assert log_path.exists()
        assert "hello file" in log_path.read_text(encoding="utf-8")

    def test_idempotent(self, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        monkeypatch.delenv("LOG_FILE", raising=False)
        configure_logging()
        pkg = logging.getLogger("rental_search_agent")
        handler_count = len(pkg.handlers)
        configure_logging()
        configure_logging(level="DEBUG")  # ignored without force
        assert len(pkg.handlers) == handler_count
        assert pkg.level == logging.INFO

    def test_force_reconfigures(self, monkeypatch):
        monkeypatch.delenv("LOG_FILE", raising=False)
        configure_logging(level="INFO")
        pkg = logging.getLogger("rental_search_agent")
        first_handlers = list(pkg.handlers)
        configure_logging(level="DEBUG", force=True)
        assert pkg.level == logging.DEBUG
        assert len(pkg.handlers) == len(first_handlers)
        for h in first_handlers:
            assert h not in pkg.handlers

    def test_reset_restores_propagate_and_level(self, monkeypatch):
        monkeypatch.delenv("LOG_FILE", raising=False)
        configure_logging(level="WARNING")
        pkg = logging.getLogger("rental_search_agent")
        assert pkg.propagate is False
        assert pkg.level == logging.WARNING
        reset_logging_config()
        assert pkg.propagate is True
        assert pkg.level == logging.NOTSET
        assert not any(getattr(h, "_rental_search_agent_handler", False) for h in pkg.handlers)


class TestLogStage:
    def test_emits_start_and_end(self, caplog):
        logger = logging.getLogger("test_log_stage_isolated")
        with caplog.at_level(logging.INFO, logger="test_log_stage_isolated"):
            with log_stage(logger, "search", city="Vancouver"):
                time.sleep(0.001)
        messages = [r.getMessage() for r in caplog.records]
        assert any("stage start name=search" in m and "city=Vancouver" in m for m in messages)
        assert any("stage end name=search" in m and "elapsed_ms=" in m for m in messages)

    def test_emits_error_on_exception(self, caplog):
        logger = logging.getLogger("test_log_stage_error")
        with caplog.at_level(logging.INFO, logger="test_log_stage_error"):
            with pytest.raises(RuntimeError, match="boom"):
                with log_stage(logger, "failing"):
                    raise RuntimeError("boom")
        messages = [r.getMessage() for r in caplog.records]
        assert any("stage start name=failing" in m for m in messages)
        error_records = [
            r for r in caplog.records if "stage error name=failing" in r.getMessage()
        ]
        assert error_records
        assert all(r.levelno == logging.WARNING for r in error_records)
        assert all(r.exc_info is not None for r in error_records)
        assert any("elapsed_ms=" in r.getMessage() for r in error_records)
        assert not any("stage end name=failing" in m for m in messages)

    def test_includes_run_id_from_context(self, caplog):
        logger = logging.getLogger("test_log_stage_run_id")
        set_run_id("abc123def456")
        with caplog.at_level(logging.INFO, logger="test_log_stage_run_id"):
            with log_stage(logger, "scored"):
                pass
        messages = [r.getMessage() for r in caplog.records]
        assert any("run_id=abc123def456" in m for m in messages)

    def test_omits_secret_looking_ctx_keys(self, caplog):
        logger = logging.getLogger("test_log_stage_secrets")
        with caplog.at_level(logging.INFO, logger="test_log_stage_secrets"):
            with log_stage(
                logger,
                "call",
                api_key="sk-secret",
                access_token="tok",
                city="Toronto",
                monkey="ok",
            ):
                pass
        messages = [r.getMessage() for r in caplog.records]
        assert any("city=Toronto" in m for m in messages)
        assert any("monkey=ok" in m for m in messages)
        assert not any("sk-secret" in m for m in messages)
        assert not any("api_key=" in m for m in messages)
        assert not any("access_token=" in m for m in messages)
        assert not any("tok" in m and "access_token" in m for m in messages)


class TestRunId:
    def test_new_run_id_is_short_hex(self):
        rid = new_run_id()
        assert len(rid) == 12
        assert all(c in "0123456789abcdef" for c in rid)

    def test_set_get_clear(self):
        assert get_run_id() is None
        set_run_id("run1")
        assert get_run_id() == "run1"
        clear_run_id()
        assert get_run_id() is None

    def test_format_run_id_suffix(self):
        assert format_run_id_suffix() == ""
        set_run_id("abc123")
        assert format_run_id_suffix() == " run_id=abc123"
        clear_run_id()
        assert format_run_id_suffix() == ""
