import inspect
from types import SimpleNamespace

from one_memory import api, cli, log_file, runtime


def test_production_serve_passes_only_supported_app_options(monkeypatch, tmp_path):
    import uvicorn

    settings = SimpleNamespace(data_dir=tmp_path, host="127.0.0.1", port=12345)
    captured = {}
    marker = object()
    create_app_signature = inspect.signature(api.create_app)

    def factory(*args, **kwargs):
        create_app_signature.bind(*args, **kwargs)
        captured.update(kwargs)
        return marker

    def run(application, **kwargs):
        assert application is marker
        assert kwargs == {
            "host": "127.0.0.1",
            "port": 12345,
            "reload": False,
            "access_log": False,
        }

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(log_file, "start_logging_to_file", lambda _: None)
    monkeypatch.setattr(runtime, "parent_pid", lambda: None)
    monkeypatch.setattr(api, "create_app", factory)
    monkeypatch.setattr(uvicorn, "run", run)
    cli.serve()
    assert captured == {"manage_runtime": True}


def test_maintenance_serve_does_not_own_the_runtime(monkeypatch, tmp_path):
    """Restore requires a maintenance Core, not the managed Runtime path."""
    import uvicorn

    settings = SimpleNamespace(data_dir=tmp_path, host="127.0.0.1", port=12346)
    captured = {}
    marker = object()
    create_app_signature = inspect.signature(api.create_app)

    def factory(*args, **kwargs):
        create_app_signature.bind(*args, **kwargs)
        captured.update(kwargs)
        return marker

    def run(application, **kwargs):
        assert application is marker
        assert kwargs == {
            "host": "127.0.0.1",
            "port": 12346,
            "reload": False,
            "access_log": False,
        }

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(log_file, "start_logging_to_file", lambda _: None)
    monkeypatch.setattr(runtime, "parent_pid", lambda: None)
    monkeypatch.setattr(api, "create_app", factory)
    monkeypatch.setattr(uvicorn, "run", run)

    entered = []

    class MaintenanceLock:
        def __init__(self, value, *, publish_receipt=True):
            assert value is settings
            assert publish_receipt is False

        def __enter__(self):
            entered.append("lock")

        def __exit__(self, *_args):
            entered.append("unlock")

    monkeypatch.setattr(runtime, "CoreRuntime", MaintenanceLock)

    cli.serve(maintenance=True)

    assert captured == {"manage_runtime": False}
    assert entered == ["lock", "unlock"]
