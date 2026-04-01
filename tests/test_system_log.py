from __future__ import annotations

import io

from planning_core.forecasting.evaluation import EvalConfig, run_catalog_evaluation
from planning_core.repository import CanonicalRepository
from planning_core.services import PlanningService
from planning_core.system_log import EventLogger, EventSink, format_console_record
from tests.test_services import _write_minimal_dataset


class _FailingSink(EventSink):
    def write(self, record: dict) -> None:
        raise OSError("disk full")


def test_event_logger_fail_open_counts_dropped_events():
    logger = EventLogger(sinks=[_FailingSink()], source="test")

    logger.emit(
        event_name="test.event",
        module="test",
        status="ok",
        result={"value": 1},
    )

    assert logger.dropped_events_count == 1


def test_event_logger_tail_reads_recent_events(tmp_path):
    logger = EventLogger.default(source="test", base_dir=tmp_path / "logs")

    logger.emit(
        event_name="test.event",
        module="test",
        status="ok",
        entity_type="sku",
        entity_id="SKU-001",
        metrics={"mase": 0.72},
        result={"model": "AutoETS"},
    )

    df = logger.tail(5)

    assert len(df) == 1
    assert df.iloc[0]["event_name"] == "test.event"
    assert df.iloc[0]["entity_id"] == "SKU-001"


def test_console_formatter_includes_professional_summary():
    record = {
        "ts_utc": "2026-03-31T19:23:01.452Z",
        "level": "INFO",
        "event_name": "forecast.sku.completed",
        "execution_id": "exec_1234567890ab",
        "entity_type": "sku",
        "entity_id": "SKU-042",
        "status": "ok",
        "duration_ms": 3412,
        "metrics": {"mase": 0.72},
        "result": {"model": "Ensemble"},
    }

    rendered = format_console_record(record, use_color=False)

    assert "forecast.sku.completed" in rendered
    assert "sku=SKU-042" in rendered
    assert "status=ok" in rendered
    assert "model=Ensemble" in rendered
    assert "mase=0.720" in rendered
    assert "dur=3412ms" in rendered


def test_default_logger_can_render_to_console_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SOTA_SYSTEM_LOG_CONSOLE", "1")
    buffer = io.StringIO()
    logger = EventLogger.default(source="test_console", base_dir=tmp_path / "logs", stream=buffer, use_color=False)

    logger.emit(
        event_name="test.console",
        module="test",
        status="ok",
        result={"model": "AutoETS"},
        metrics={"mase": 0.55},
    )

    output = buffer.getvalue()
    assert "test.console" in output
    assert "model=AutoETS" in output
    assert "mase=0.550" in output


def test_planning_service_can_enable_console_logging_via_parameter(tmp_path):
    _write_minimal_dataset(tmp_path)
    buffer = io.StringIO()
    service = PlanningService(
        CanonicalRepository(tmp_path),
        log_source="test_param_console",
        enable_console_log=True,
        console_use_color=False,
        console_stream=buffer,
        system_log_dir=tmp_path / "logs",
    )

    service.classify_single_sku("SKU-001", granularity="M")

    output = buffer.getvalue()
    assert "classification.sku.completed" in output
    assert "sku=SKU-001" in output


def test_service_forecast_emits_correlated_events(tmp_path):
    _write_minimal_dataset(tmp_path)
    logger = EventLogger.default(source="test_service", base_dir=tmp_path / "logs")
    service = PlanningService(CanonicalRepository(tmp_path), event_logger=logger)

    result = service.sku_forecast("SKU-001", h=1, n_windows=1)
    df = logger.tail(20)

    assert result["status"] in {"ok", "fallback", "no_forecast"}
    event_names = set(df["event_name"].tolist())
    assert "forecast.sku.started" in event_names
    assert "forecast.sku.completed" in event_names
    assert "classification.sku.completed" in event_names
    assert "forecast.sku.profile.completed" in event_names
    assert "forecast.sku.horizon.completed" in event_names
    assert "forecast.sku.series.completed" in event_names
    assert "forecast.sku.selection.completed" in event_names

    forecast_completed = df[df["event_name"] == "forecast.sku.completed"].iloc[-1]
    classification_completed = df[df["event_name"] == "classification.sku.completed"].iloc[-1]
    selection_completed = df[df["event_name"] == "forecast.sku.selection.completed"].iloc[-1]

    assert forecast_completed["execution_id"] == classification_completed["execution_id"]
    assert forecast_completed["execution_id"] == selection_completed["execution_id"]
    assert forecast_completed["status"] == result["status"]


def test_batch_evaluation_emits_batch_events(tmp_path):
    _write_minimal_dataset(tmp_path)
    logger = EventLogger.default(source="test_batch", base_dir=tmp_path / "logs")
    service = PlanningService(CanonicalRepository(tmp_path), event_logger=logger)

    result = run_catalog_evaluation(
        service,
        EvalConfig(granularity="M", h=1, n_windows=1, run_name="test"),
        verbose=False,
        n_jobs=1,
        event_logger=logger,
    )
    df = logger.tail(50)

    assert not result.sku_results.empty
    event_names = set(df["event_name"].tolist())
    assert "forecast.batch.started" in event_names
    assert "forecast.batch.completed" in event_names
    assert "forecast.batch.sku.completed" in event_names

    batch_completed = df[df["event_name"] == "forecast.batch.completed"].iloc[-1]
    sku_completed = df[df["event_name"] == "forecast.batch.sku.completed"].iloc[-1]

    assert batch_completed["execution_id"] == sku_completed["execution_id"]


def test_batch_evaluation_can_enable_console_logging_via_parameter(tmp_path):
    _write_minimal_dataset(tmp_path)
    buffer = io.StringIO()
    service = PlanningService(CanonicalRepository(tmp_path), system_log_dir=tmp_path / "logs")

    run_catalog_evaluation(
        service,
        EvalConfig(granularity="M", h=1, n_windows=1, run_name="console"),
        verbose=False,
        n_jobs=1,
        enable_console_log=True,
        console_use_color=False,
        console_stream=buffer,
    )

    output = buffer.getvalue()
    assert "forecast.batch.started" in output
    assert "forecast.batch.completed" in output
