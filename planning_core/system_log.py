from __future__ import annotations

import contextvars
import json
import logging
import math
import os
import re
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import IO, Any, Iterable, Mapping
from uuid import uuid4

import pandas as pd

from planning_core.paths import OUTPUT_DIR


_LOGGER = logging.getLogger(__name__)

_SENSITIVE_KEY_TOKENS = {
    "api_key",
    "authorization",
    "password",
    "secret",
    "token",
}
_MAX_STRING_LEN = 500
_MAX_COLLECTION_ITEMS = 50
_ACTIVE_SPAN: contextvars.ContextVar["_ActiveSpanContext | None"] = contextvars.ContextVar(
    "planning_core_system_log_active_span",
    default=None,
)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip())
    return cleaned.strip("_") or "unknown"


def _new_identifier(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _is_sensitive_key(key: str | None) -> bool:
    if not key:
        return False
    lowered = key.lower()
    return any(token in lowered for token in _SENSITIVE_KEY_TOKENS)


def _truncate_string(value: str) -> str:
    if len(value) <= _MAX_STRING_LEN:
        return value
    return f"{value[:_MAX_STRING_LEN]}...(truncated)"


def _normalize_scalar(value: Any) -> Any:
    if hasattr(value, "item") and callable(getattr(value, "item")):
        try:
            return value.item()
        except Exception:
            return value
    return value


def sanitize_for_log(value: Any, key: str | None = None, depth: int = 0) -> Any:
    if _is_sensitive_key(key):
        return "<redacted>"

    value = _normalize_scalar(value)

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _truncate_string(value)
    if isinstance(value, Path):
        return _truncate_string(str(value))
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for index, (child_key, child_value) in enumerate(value.items()):
            if index >= _MAX_COLLECTION_ITEMS:
                sanitized["__truncated__"] = True
                break
            key_str = str(child_key)
            sanitized[key_str] = sanitize_for_log(child_value, key=key_str, depth=depth + 1)
        return sanitized
    if isinstance(value, (list, tuple, set)):
        items = []
        for index, item in enumerate(value):
            if index >= _MAX_COLLECTION_ITEMS:
                items.append("<truncated>")
                break
            items.append(sanitize_for_log(item, depth=depth + 1))
        return items
    if isinstance(value, BaseException):
        return {
            "type": value.__class__.__name__,
            "message": _truncate_string(str(value)),
        }
    return _truncate_string(repr(value))


def _filter_none_values(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _status_level(status: str | None, default: str = "INFO") -> str:
    if status in {"error", "failed"}:
        return "ERROR"
    if status in {"fallback", "no_forecast", "series_too_short", "inactive"}:
        return "WARN"
    return default


def _fmt_metric(value: Any) -> str | None:
    value = sanitize_for_log(value)
    if value is None:
        return None
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


@dataclass(frozen=True)
class _ActiveSpanContext:
    execution_id: str
    operation_id: str


class EventSink:
    def write(self, record: dict[str, Any]) -> None:
        raise NotImplementedError


class JsonlSink(EventSink):
    def __init__(self, base_dir: str | Path | None = None):
        self.base_dir = Path(base_dir) if base_dir else (OUTPUT_DIR / "system_logs")

    def write(self, record: dict[str, Any]) -> None:
        ts_utc = str(record.get("ts_utc", "1970-01-01T00:00:00.000Z"))
        day = ts_utc[:10]
        source = _slugify(str(record.get("source", "system")))
        file_path = self.base_dir / day / f"events_{source}_{os.getpid()}.jsonl"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "a", encoding="utf-8") as file_handle:
            file_handle.write(json.dumps(record, ensure_ascii=True))
            file_handle.write("\n")


class ConsoleSink(EventSink):
    _LEVEL_COLOR = {
        "INFO": "\033[36m",
        "WARN": "\033[33m",
        "ERROR": "\033[31m",
    }
    _RESET = "\033[0m"

    def __init__(self, stream: IO[str] | None = None, use_color: bool | None = None):
        self.stream = stream or sys.stdout
        self.use_color = self.stream.isatty() if use_color is None else use_color

    def write(self, record: dict[str, Any]) -> None:
        line = format_console_record(record, use_color=self.use_color)
        self.stream.write(line + "\n")
        self.stream.flush()


def format_console_record(record: Mapping[str, Any], use_color: bool = False) -> str:
    ts_utc = str(record.get("ts_utc", ""))
    level = str(record.get("level", "INFO")).upper()
    event_name = str(record.get("event_name", "event")).ljust(30)
    entity = ""
    entity_id = record.get("entity_id")
    if entity_id:
        entity_type = str(record.get("entity_type") or "entity")
        entity = f"{entity_type}={entity_id}"
    execution_id = str(record.get("execution_id", ""))
    execution_label = f"exec={execution_id[:16]}" if execution_id else ""
    status = str(record.get("status", "")) if record.get("status") else ""
    status_label = f"status={status}" if status else ""

    summary_parts: list[str] = []
    result = record.get("result") or {}
    metrics = record.get("metrics") or {}
    error = record.get("error") or {}

    if isinstance(result, Mapping):
        for key in ("model", "health_status", "alert_level", "action", "run_id", "sb_class", "granularity"):
            if key in result and result[key] is not None:
                summary_parts.append(f"{key}={result[key]}")
    if isinstance(metrics, Mapping):
        for key in (
            "mase",
            "bias",
            "urgency_score",
            "positioning_ratio",
            "h",
            "n_obs",
            "n_candidates",
            "outlier_count",
            "n_skus",
            "n_ok",
            "n_fallback",
            "n_no_forecast",
            "n_error",
            "n_items",
            "n_actionable",
            "n_suppliers",
            "final_qty",
            "total_units_to_order",
        ):
            if key in metrics and metrics[key] is not None:
                metric_value = _fmt_metric(metrics[key])
                if metric_value is not None:
                    summary_parts.append(f"{key}={metric_value}")
    if isinstance(error, Mapping) and error.get("message"):
        summary_parts.append(f"error={error['message']}")

    duration_ms = record.get("duration_ms")
    if duration_ms is not None:
        summary_parts.append(f"dur={duration_ms}ms")

    left = " ".join(part for part in [ts_utc, level.ljust(5), event_name] if part)
    right = " ".join(part for part in [entity, execution_label, status_label, *summary_parts] if part)
    line = f"{left} {right}".rstrip()

    if use_color and level in ConsoleSink._LEVEL_COLOR:
        return f"{ConsoleSink._LEVEL_COLOR[level]}{line}{ConsoleSink._RESET}"
    return line


@dataclass
class EventSpan:
    logger: "EventLogger"
    event_base: str
    module: str
    entity_type: str | None = None
    entity_id: str | None = None
    params: dict[str, Any] | None = None
    level: str = "INFO"
    source: str | None = None
    _start_ts: float = field(init=False, default=0.0)
    _status: str = field(init=False, default="ok")
    _metrics: dict[str, Any] = field(init=False, default_factory=dict)
    _result: dict[str, Any] = field(init=False, default_factory=dict)
    _error: dict[str, Any] | None = field(init=False, default=None)
    _execution_id: str = field(init=False, default="")
    _operation_id: str = field(init=False, default="")
    _parent_operation_id: str | None = field(init=False, default=None)
    _token: contextvars.Token | None = field(init=False, default=None)

    def __enter__(self) -> "EventSpan":
        active_context = _ACTIVE_SPAN.get()
        self._execution_id = active_context.execution_id if active_context else _new_identifier("exec")
        self._parent_operation_id = active_context.operation_id if active_context else None
        self._operation_id = _new_identifier("op")
        self._start_ts = perf_counter()
        self._token = _ACTIVE_SPAN.set(
            _ActiveSpanContext(execution_id=self._execution_id, operation_id=self._operation_id)
        )
        self.logger.emit(
            event_name=f"{self.event_base}.started",
            module=self.module,
            level=self.level,
            status="running",
            entity_type=self.entity_type,
            entity_id=self.entity_id,
            params=self.params,
            execution_id=self._execution_id,
            operation_id=self._operation_id,
            parent_operation_id=self._parent_operation_id,
            source=self.source,
        )
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        duration_ms = int((perf_counter() - self._start_ts) * 1000)
        if exc is not None:
            self._status = "error"
            self._error = sanitize_for_log(exc)
            level = "ERROR"
            event_name = f"{self.event_base}.failed"
        else:
            level = _status_level(self._status, default=self.level)
            event_name = f"{self.event_base}.completed"

        self.logger.emit(
            event_name=event_name,
            module=self.module,
            level=level,
            status=self._status,
            entity_type=self.entity_type,
            entity_id=self.entity_id,
            params=self.params,
            metrics=self._metrics,
            result=self._result,
            error=self._error,
            duration_ms=duration_ms,
            execution_id=self._execution_id,
            operation_id=self._operation_id,
            parent_operation_id=self._parent_operation_id,
            source=self.source,
        )
        if self._token is not None:
            _ACTIVE_SPAN.reset(self._token)
        return False

    @property
    def execution_id(self) -> str:
        return self._execution_id

    def set_status(self, status: str) -> None:
        self._status = status

    def set_metrics(self, **metrics: Any) -> None:
        self._metrics.update(metrics)

    def set_result(self, **result: Any) -> None:
        self._result.update(result)

    def set_error(self, error: BaseException | str | Mapping[str, Any]) -> None:
        self._error = sanitize_for_log(error)


class NullEventSpan:
    execution_id = ""

    def __enter__(self) -> "NullEventSpan":
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        return False

    def set_status(self, status: str) -> None:
        return None

    def set_metrics(self, **metrics: Any) -> None:
        return None

    def set_result(self, **result: Any) -> None:
        return None

    def set_error(self, error: BaseException | str | Mapping[str, Any]) -> None:
        return None


class EventLogger:
    def __init__(
        self,
        sinks: Iterable[EventSink] | None = None,
        *,
        source: str = "service",
        base_dir: str | Path | None = None,
    ):
        self._sinks = list(sinks or [])
        self.source = source
        self.base_dir = Path(base_dir) if base_dir else (OUTPUT_DIR / "system_logs")
        self._dropped_events = 0
        self._last_failure_notice: str | None = None

    @classmethod
    def default(
        cls,
        *,
        source: str = "service",
        enable_console: bool | None = None,
        base_dir: str | Path | None = None,
        stream: IO[str] | None = None,
        use_color: bool | None = None,
    ) -> "EventLogger":
        resolved_base_dir = Path(base_dir) if base_dir else (OUTPUT_DIR / "system_logs")
        console_enabled = _env_flag("SOTA_SYSTEM_LOG_CONSOLE", default=False) if enable_console is None else enable_console
        color_enabled = _env_flag("SOTA_SYSTEM_LOG_COLOR", default=True) if use_color is None else use_color
        sinks: list[EventSink] = [JsonlSink(resolved_base_dir)]
        if console_enabled:
            sinks.append(ConsoleSink(stream=stream, use_color=color_enabled))
        return cls(sinks, source=source, base_dir=resolved_base_dir)

    @property
    def dropped_events_count(self) -> int:
        return self._dropped_events

    def span(
        self,
        event_base: str,
        *,
        module: str,
        entity_type: str | None = None,
        entity_id: str | None = None,
        params: Mapping[str, Any] | None = None,
        level: str = "INFO",
        source: str | None = None,
    ) -> EventSpan:
        return EventSpan(
            logger=self,
            event_base=event_base,
            module=module,
            entity_type=entity_type,
            entity_id=entity_id,
            params=dict(params or {}),
            level=level,
            source=source,
        )

    def emit(
        self,
        *,
        event_name: str,
        module: str,
        level: str = "INFO",
        status: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        params: Mapping[str, Any] | None = None,
        metrics: Mapping[str, Any] | None = None,
        result: Mapping[str, Any] | None = None,
        error: BaseException | str | Mapping[str, Any] | None = None,
        duration_ms: int | None = None,
        execution_id: str | None = None,
        operation_id: str | None = None,
        parent_operation_id: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        active_context = _ACTIVE_SPAN.get()
        resolved_execution_id = execution_id or (active_context.execution_id if active_context else _new_identifier("exec"))
        resolved_parent_operation_id = (
            parent_operation_id
            if parent_operation_id is not None
            else (active_context.operation_id if active_context else None)
        )
        record = _filter_none_values(
            {
                "schema_version": 1,
                "ts_utc": _utc_now_iso(),
                "level": level.upper(),
                "event_name": event_name,
                "status": status,
                "module": module,
                "source": source or self.source,
                "execution_id": resolved_execution_id,
                "operation_id": operation_id or _new_identifier("op"),
                "parent_operation_id": resolved_parent_operation_id,
                "event_id": _new_identifier("evt"),
                "entity_type": entity_type,
                "entity_id": entity_id,
                "duration_ms": duration_ms,
                "params": sanitize_for_log(dict(params or {})),
                "metrics": sanitize_for_log(dict(metrics or {})),
                "result": sanitize_for_log(dict(result or {})),
                "error": sanitize_for_log(error) if error is not None else None,
            }
        )
        self._write_record(record)
        return record

    def tail(self, n: int = 100) -> pd.DataFrame:
        if n <= 0:
            return pd.DataFrame()
        return self.query(limit=n)

    def query(
        self,
        *,
        event_name: str | None = None,
        execution_id: str | None = None,
        entity_id: str | None = None,
        day: str | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        files = self._iter_log_files(day=day)
        if limit is not None and limit > 0:
            buffer: deque[dict[str, Any]] = deque(maxlen=limit)
            for row in self._iter_rows(files):
                if event_name and row.get("event_name") != event_name:
                    continue
                if execution_id and row.get("execution_id") != execution_id:
                    continue
                if entity_id and row.get("entity_id") != entity_id:
                    continue
                buffer.append(row)
            rows = list(buffer)
        else:
            for row in self._iter_rows(files):
                if event_name and row.get("event_name") != event_name:
                    continue
                if execution_id and row.get("execution_id") != execution_id:
                    continue
                if entity_id and row.get("entity_id") != entity_id:
                    continue
                rows.append(row)

        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows).sort_values("ts_utc").reset_index(drop=True)

    def _iter_log_files(self, *, day: str | None = None) -> list[Path]:
        if not self.base_dir.exists():
            return []
        if day:
            day_dir = self.base_dir / day
            return sorted(day_dir.glob("*.jsonl")) if day_dir.exists() else []
        return sorted(self.base_dir.glob("*/*.jsonl"))

    def _iter_rows(self, files: Iterable[Path]) -> Iterable[dict[str, Any]]:
        for file_path in files:
            try:
                with open(file_path, "r", encoding="utf-8") as file_handle:
                    for line in file_handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue
            except FileNotFoundError:
                continue

    def _write_record(self, record: dict[str, Any]) -> None:
        for sink in self._sinks:
            try:
                sink.write(record)
            except Exception as exc:
                self._dropped_events += 1
                failure_notice = f"{sink.__class__.__name__}:{exc.__class__.__name__}"
                if failure_notice != self._last_failure_notice:
                    _LOGGER.warning(
                        "System log sink failure (%s). Event dropped; main flow continues.",
                        failure_notice,
                    )
                    self._last_failure_notice = failure_notice


class NullEventLogger(EventLogger):
    def __init__(self):
        super().__init__(sinks=[], source="null")

    def span(
        self,
        event_base: str,
        *,
        module: str,
        entity_type: str | None = None,
        entity_id: str | None = None,
        params: Mapping[str, Any] | None = None,
        level: str = "INFO",
        source: str | None = None,
    ) -> NullEventSpan:
        return NullEventSpan()

    def emit(
        self,
        *,
        event_name: str,
        module: str,
        level: str = "INFO",
        status: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        params: Mapping[str, Any] | None = None,
        metrics: Mapping[str, Any] | None = None,
        result: Mapping[str, Any] | None = None,
        error: BaseException | str | Mapping[str, Any] | None = None,
        duration_ms: int | None = None,
        execution_id: str | None = None,
        operation_id: str | None = None,
        parent_operation_id: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        return {}


__all__ = [
    "ConsoleSink",
    "EventLogger",
    "EventSink",
    "JsonlSink",
    "NullEventLogger",
    "format_console_record",
    "sanitize_for_log",
]
