# PuriPuly Heart — Portable Version

Self-contained build: requires no installed Python and leaves nothing outside
the folder. The folder can be moved to another disk or computer — the launch
works relative to its location, the folder name is not hardcoded anywhere.

## Launch

`start.exe` — the single launch file, no parameters:

- **Launch** — normal start, no extra windows;
- **Launch with debug** — Python runtime console + debug UI controls in the
  program (key `--debug-ui-preview`);
- **Maintenance** — working with data without starting the program:
  - **Clear user data** — deletes all contents of `data_user`;
  - **Clear Python runtime temporary files** — deletes `__pycache__`
    (compiled code; use after changing code so the program does not follow
    the stale cache);
  - **Clear cache** — deletes all contents of the `cache` folder
    (regenerable caches: downloaded packages, client caches, temp files;
    recreated automatically);
- **Exit**.

Item explanations are shown on hover. The launcher UI language follows the
Windows language; 35 languages are built in.

Notes: if the program exits with an error right after a normal start, the
launcher shows a dialog with the exit code (use debug for diagnostics). A
cleanup result with errors means some files are in use — close the program
and retry. Only one launcher window can be open at a time.

Secrets and API keys are stored in plain text inside `data_user/` —
encryption is deliberately not used.

## Structure

- `start.exe`, `start.cs` — launcher and its source (UTF-8)
- `src/` — program code (runs directly from the sources)
- `python/` — portable Python 3.14 + all dependencies (`Lib/site-packages`)
- `build/` — native recognition runtime: ASR worker, transcribe.cpp, ggml backends
- `data_user/` — all user data: settings, models, secrets, logs
- `cache/` — regenerable caches and temp files (safe to delete)
- `prompts/` — prompt templates (runtime data)
- `third_party/` — license notices, fonts, OpenVR
- `native/` — Rust component sources (overlay etc.)
- `scripts/`, `pyproject.toml`, `uv.lock`, `LICENSE` — build scripts, dependency manifests, license
- `tests/` — tests
- `docs/` — the added/deleted feature logs (three languages)

## Portability and isolation

- The interpreter and all dependencies live in `python/`; the system Python
  is not used and not touched.
- `python/python314._pth` defines paths: standard library, own
  `Lib/site-packages`, sources `../src`. Foreign environment variables
  (`PYTHONPATH`, `PYTHONHOME`) are ignored; the user site-packages is not
  mixed in.
- Registry, PATH and file associations are not modified.
- Application data always lives in `data_user/` inside the folder.
- Note: all regenerable caches (pip, uv, Flet client, Hugging Face, temp
  files) live in `cache/` inside the folder; `data_user/` holds user data only.

## Package management

Manage packages with the portable interpreter only:

```
python\python.exe -m pip install <package>
python\python.exe -m pip install -U <package>
python\python.exe -m pip uninstall <package>
```

Everything stays inside the folder: package code and metadata in
`python/Lib/site-packages`, entry-point shims in `python/Scripts`, download
cache in `cache/pip`. The interpreter never touches system Python locations.
`python/INSTALLED_PACKAGES.txt` lists the packaged set.

## Launcher rebuild

```
%SystemRoot%\Microsoft.NET\Framework64\v4.0.30319\csc.exe /nologo /codepage:65001 /target:winexe /platform:anycpu /optimize+ /r:System.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll /r:System.Core.dll /win32icon:src\puripuly_heart\data\icons\icon.ico /out:start.exe start.cs
```

The `/codepage:65001` flag is required: the source is UTF-8 (35 languages).
