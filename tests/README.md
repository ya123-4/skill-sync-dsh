# Tests

Run from the workspace root (`C:\Users\ybc12\Documents\deepseek-harness\default-workspace`):

```powershell
$env:PYTHONPATH = "$PWD\format-factory\.pytest-libs"
python -m pytest dsh-plugin-build/skill-sync-dsh/tests --basetemp=tmpwork/skill-sync-pytest -p no:cacheprovider -v
```

Three environment facts drive this command:

1. **pytest source.** The DSH runtime's Python has no pytest. A working pytest 9.1.1 tree
   already lives in `format-factory\.pytest-libs` (verified 2026-10-12) and is the one this
   project uses. A local copy under `dsh-plugin-build\skill-sync-dsh\.pytest-libs` is also
   supported and is git-ignored, but it is not vendored because the copy is ~216 MB.
   `pip install --target ...` is the third option, though PyPI installs stall on this host.
2. **`--basetemp` is mandatory.** The file sandbox denies writes outside the workspace, so
   pytest's default system temp directory is not usable. Pointing `--basetemp` at
   `tmpwork/skill-sync-pytest` keeps every `tmp_path` inside the workspace.
3. **`-p no:cacheprovider`.** The sandbox also rejects pytest's cache-directory creation
   (`WinError 5` on `pytest-cache-files-*` under the workspace root), so the cache plugin is
   disabled. Nothing in this suite depends on it.

Product code under `skills/skill-sync/scripts/` must import nothing outside the Python
standard library; pytest is a test-only dependency.
