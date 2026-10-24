# Building Windows releases

Build on 64-bit Windows with Python 3.12, Tkinter, and Inno Setup 6.7.3.
The first release uses PyInstaller 6.21.0 and Pillow 12.2.0. The result is a
standalone bundled executable plus its `_internal` directory, distributed as
an installer and a portable ZIP. Python is included; Python source remains MIT licensed.

Use a clean virtual environment so unrelated packages from the build PC are
not pulled into the dependency graph:

```powershell
py -3.12 -m venv .build-venv
$env:PIP_CACHE_DIR = "$PWD\.cache\pip"
.\.build-venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.build-venv\Scripts\python.exe -m unittest discover -s tests -v
.\.build-venv\Scripts\python.exe packaging\build.py --iscc 'PATH\TO\ISCC.exe'
```

Install the compiler from [the official Inno Setup downloads](https://jrsoftware.org/isdl.php).
The build script puts temporary files, build work and PyInstaller caches in
the checkout. Keep the checkout on your preferred drive to keep that work there.

Outputs are in `dist/release/`:

- `EndoscopeViewerWIN-1.0.0-windows-x64-setup.exe`
- `EndoscopeViewerWIN-1.0.0-windows-x64-portable.zip`
- `SHA256SUMS.txt`

The application bundle is built from the entry point and code dependencies.
It does not copy the repository directory, screenshots, camera recordings,
preferences, APKs or decompiled vendor code. Third-party runtime notices are
included under `_internal/licenses`. FFmpeg and ffplay are external dependencies.

Before release, run the source tests, the frozen Tcl/Tk/Pillow smoke check,
demo GUI, a live camera check, and an installation/uninstallation round trip
in a separate test folder. The installer preserves user-created `data` on uninstall.

```powershell
& '.\dist\app\EndoscopeViewerWIN\EndoscopeViewerWIN.exe' --self-test "$PWD\build\frozen-check.json"
& '.\dist\app\EndoscopeViewerWIN\EndoscopeViewerWIN.exe' --demo
```

`--self-test` writes a small runtime report without connecting to Wi-Fi or sending camera commands.
Update `app_paths.VERSION`, `packaging/version-info.txt`, documentation asset names
and release notes together when changing versions. Upload only the reviewed
release artifacts and checksums; do not upload test data or the build directory.
