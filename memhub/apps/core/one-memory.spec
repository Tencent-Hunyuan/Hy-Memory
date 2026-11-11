# -*- mode: python ; coding: utf-8 -*-
import os
import sys

from PyInstaller.utils.hooks import collect_all

layout = os.environ.get('ONE_MEMORY_CORE_LAYOUT', 'folder')
if layout not in {'single-file', 'folder'}:
    raise ValueError('Unsupported ONE_MEMORY_CORE_LAYOUT')
folder_layout = layout == 'folder'

datas = [('src/one_memory/embedding_assets', 'one_memory/embedding_assets')]
binaries = []
# Loaded lazily by the API; static dependency analysis cannot discover it.
hiddenimports = []
tmp_ret = collect_all('hy_memory')
# Only the SDK's non-Python data. Its modules are frozen into the archive
# (the hidden imports below); `.py` copies beside them are never imported and
# only invite editing a file the frozen Core does not load.
datas += [entry for entry in tmp_ret[0] if not entry[0].endswith('.py')]
binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('chromadb')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('kuzu')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
# The drivers of the other storage backends the SDK supports, opened lazily
# by the SDK only when `memhub settings backends` selects them
# (docs/contracts/storage-backends-v1.md). Static analysis cannot see them.
# `test_storage_backend_packaging.py` keeps this list and pyproject in step.
for driver in ('neo4j', 'qdrant_client', 'portalocker'):
    tmp_ret = collect_all(driver)
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('tokenizers')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

if sys.platform.startswith('linux'):
    # keyring picks its backend at runtime from entry points, so PyInstaller
    # cannot see the Secret Service backend or its D-Bus dependencies. They are
    # installed on Linux (see uv.lock) but without these imports the frozen
    # runtime has no usable secure-storage backend and refuses every API key.
    hiddenimports += [
        'keyring.backends.SecretService',
        'secretstorage',
        'jeepney',
    ]


a = Analysis(
    ['src/one_memory/cli.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # MemHub refuses the `faiss` storage backend, so its ~18 MB driver is never
    # shipped. The SDK imports it inside a function, which PyInstaller still
    # follows when a build machine happens to have it, so it is excluded by name.
    excludes=['faiss'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    *([] if folder_layout else [a.binaries, a.datas]),
    [],
    name='one-memory',
    exclude_binaries=folder_layout,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

if folder_layout:
    COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name='one-memory-runtime',
    )
