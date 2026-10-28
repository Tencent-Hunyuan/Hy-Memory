import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Enough history to cover the run before the one being complained about,
# capped so that a machine left running for months does not fill up.
MAX_BYTES = 4 * 1024 * 1024
KEEP = 3


def log_path(data_dir: Path) -> Path:
    return data_dir / "logs" / "core.log"


def start_logging_to_file(data_dir: Path) -> Path:
    """Also write the log to a file the user can find.

    Core inherits its parent's output streams, so when the app is launched
    the normal way — from the Dock, by launchd — everything it says goes
    nowhere. That includes the loop watchdog: the one report that names what
    stalled the app was unreadable in exactly the situation it exists for.

    The active diagnostic collection level controls Core and provider client
    records. The HY SDK keeps its own text log, hy_memory.log under the SDK's
    log directory, and stops propagating here once its first client is built;
    `confine_hy_sdk_logging` then forwards only its warnings and errors to
    this file. Uvicorn keeps its request log on its own non-propagating
    loggers.
    """
    destination = log_path(data_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for existing in root.handlers:
        if (
            isinstance(existing, RotatingFileHandler)
            and Path(existing.baseFilename) == destination.resolve()
        ):
            return destination
    handler = RotatingFileHandler(
        destination,
        maxBytes=MAX_BYTES,
        backupCount=KEEP,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    return destination


HY_SDK_LOGGER = "hy_memory"


class _CoreLogForwarder(logging.Handler):
    """Hands HY warnings to Core's own core.log handler, rotation and format."""

    def __init__(self, target: logging.Handler) -> None:
        super().__init__(level=logging.WARNING)
        self.target = target

    def emit(self, record: logging.LogRecord) -> None:
        self.target.handle(record)


def confine_hy_sdk_logging() -> None:
    """Keep HY's text log in hy_memory.log, with only its problems in core.log.

    HY's first client sets up hy_memory.log plus a stdout and a stderr stream
    on the "hy_memory" logger, and stops it propagating to the root. Under
    launchd those streams are service.log and service-error.log, so every SDK
    line was written twice, and none reached core.log. Drop the streams and
    forward WARNING and above to core.log, which the Debug panel reads; the
    SDK keeps conversation text out of those levels.

    Idempotent. Does nothing until HY has configured its logger, because a
    propagating logger already reaches core.log through the root.
    """
    sdk = logging.getLogger(HY_SDK_LOGGER)
    if sdk.propagate:
        return
    for handler in list(sdk.handlers):
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, logging.FileHandler
        ):
            sdk.removeHandler(handler)
    if any(isinstance(handler, _CoreLogForwarder) for handler in sdk.handlers):
        return
    core = next(
        (
            handler
            for handler in logging.getLogger().handlers
            if isinstance(handler, RotatingFileHandler)
            and Path(handler.baseFilename).name == "core.log"
        ),
        None,
    )
    if core is not None:
        sdk.addHandler(_CoreLogForwarder(core))
