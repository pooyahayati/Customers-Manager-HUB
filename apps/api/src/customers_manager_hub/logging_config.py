import json
import logging
import sys
from datetime import UTC, datetime

from customers_manager_hub.production_hardening import current_request_id

_OPERATIONAL_FIELDS = (
    "request_id",
    "tenant_id",
    "actor_user_id",
    "event_id",
    "source_id",
    "conversation_id",
    "agent_id",
    "tool_id",
    "execution_id",
    "error_code",
    "retryable",
    "consumer",
    "stream_id",
    "attempt",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "rate_limit_rule",
    "signal",
)


class JsonFormatter(logging.Formatter):
    """Small JSON formatter suitable for container stdout logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        explicit_request_id = getattr(record, "request_id", None)
        request_id = (
            explicit_request_id if explicit_request_id is not None else current_request_id()
        )
        if request_id is not None:
            payload["request_id"] = str(request_id)[:128]
        for field_name in _OPERATIONAL_FIELDS:
            if field_name == "request_id" or not hasattr(record, field_name):
                continue
            value = getattr(record, field_name)
            if value is None or isinstance(value, (str, int, float, bool)):
                payload[field_name] = value
            else:
                payload[field_name] = str(value)[:256]
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str) -> None:
    """Configure one structured stdout handler for application logs."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)
    root_logger.addHandler(handler)
