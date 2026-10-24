"""HY SDK records are not rewritten by One Memory's logging setup."""

import logging

from one_memory.hy_runtime_config import configure_hy_runtime_environment


def test_the_extractors_parse_failures_pass_through_unchanged(tmp_path):
    logger = logging.getLogger("hy_memory.coding.extractor")
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    capture = Capture()
    original_filters = list(logger.filters)
    original_level = logger.level
    original_handlers = list(logger.handlers)
    logger.handlers = [capture]
    logger.setLevel(logging.WARNING)
    configure_hy_runtime_environment(tmp_path)
    try:
        secret = "x" * 5000
        logger.warning(
            '[coding-extract] failed to parse output: \'{"experiences": "%s"}\'',
            secret,
        )
    finally:
        logger.handlers = original_handlers
        logger.filters = original_filters
        logger.setLevel(original_level)

    assert records, "nothing was logged at all"
    rendered = records[0].getMessage()
    assert rendered == (
        '[coding-extract] failed to parse output: \'{"experiences": "' + secret + "\"}'"
    )


def test_other_warnings_pass_through_unchanged(tmp_path):
    logger = logging.getLogger("hy_memory.coding.extractor")
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    capture = Capture()
    original_filters = list(logger.filters)
    original_level = logger.level
    original_handlers = list(logger.handlers)
    logger.handlers = [capture]
    logger.setLevel(logging.WARNING)
    configure_hy_runtime_environment(tmp_path)
    try:
        logger.warning("[coding-extract] judge failed, fallback to chat: %s", "reason")
    finally:
        logger.handlers = original_handlers
        logger.filters = original_filters
        logger.setLevel(original_level)

    assert records
    assert records[0].getMessage() == "[coding-extract] judge failed, fallback to chat: reason"
