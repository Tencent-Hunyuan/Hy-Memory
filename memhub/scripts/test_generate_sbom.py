from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "generate_sbom.py"
spec = importlib.util.spec_from_file_location("generate_sbom", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules["generate_sbom"] = module
spec.loader.exec_module(module)


def test_hy_memory_notice_comes_from_the_installed_dist_info(tmp_path: Path) -> None:
    dist_info = tmp_path / "hy_memory-2.0.5.dev1.dist-info"
    licenses = dist_info / "licenses"
    licenses.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        "\n".join(
            (
                "Metadata-Version: 2.4",
                "Name: hy-memory",
                "Version: 2.0.5.dev1",
                "License-Expression: Apache-2.0",
                "",
            )
        ),
        encoding="utf-8",
    )
    license_text = (
        "Apache License\nVersion 2.0, January 2004\n\n"
        "Copyright (C) 2026 Tencent. All rights reserved.\n\n"
        "Licensed under the Apache License, Version 2.0"
    )
    (licenses / "LICENSE").write_text(license_text, encoding="utf-8")

    notice = module.hy_memory_notice(dist_info)

    assert notice["name"] == "hy-memory"
    assert notice["version"] == "2.0.5.dev1"
    assert notice["license"] == "Apache-2.0"
    assert notice["copyright"] == "Copyright (C) 2026 Tencent. All rights reserved."
    assert "Licensed under the Apache License" in notice["text"]


def test_hy_memory_notice_refuses_missing_dist_info(tmp_path: Path) -> None:
    try:
        module.hy_memory_notice(tmp_path / "missing.dist-info")
    except module.SbomError as error:
        assert "hy-memory" in str(error)
    else:
        raise AssertionError("missing HY dist-info must fail closed")


def test_script_has_a_modern_python_guard_before_type_annotations() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    guard = source.index('if sys.version_info < (3, 10)')
    annotation = source.index("Path | None")
    assert guard < annotation


def test_source_date_epoch_drives_reproducible_release_timestamp(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'os.environ.get("SOURCE_DATE_EPOCH"' in source
    assert "datetime.fromtimestamp(epoch, timezone.utc)" in source
