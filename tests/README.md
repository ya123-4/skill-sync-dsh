# Tests

Run from the workspace root (`C:\Users\ybc12\Documents\deepseek-harness\default-workspace`):

```powershell
$env:PYTHONPATH = "$PWD\format-factory\.pytest-libs"
python -m pytest dsh-plugin-build/skill-sync-dsh/tests -p no:cacheprovider -p no:tmpdir -q
```

Three environment facts drive this command:

1. **pytest source.** The DSH runtime's Python has no pytest. A working pytest 9.1.1 tree
   already lives in `format-factory\.pytest-libs` (verified 2026-10-12) and is the one this
   project uses. A local copy under `dsh-plugin-build\skill-sync-dsh\.pytest-libs` is also
   supported and is git-ignored, but it is not vendored because the copy is ~216 MB.
   `pip install --target ...` is the third option, though PyPI installs stall on this host.
2. **`-p no:tmpdir`.** pytest's tmp directory handling cannot run under the sandbox: even with
   `--basetemp` inside the workspace, its cleanup hits `WinError 5` and turns passing tests
   into errors. Tests use the `scratch` fixture from `conftest.py` instead — a
   self-created, self-removed directory under `tmpwork/skill-sync-scratch`.
3. **`-p no:cacheprovider`.** The sandbox also rejects pytest's cache-directory creation
   (`WinError 5` on `pytest-cache-files-*` under the workspace root). Nothing depends on it.

Product code under `skills/skill-sync/scripts/` must import nothing outside the Python
standard library; pytest is a test-only dependency.
