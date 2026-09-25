# PyInstaller build for the KnowItAll desktop app.
#
# Build it ON the machine you will run it on - PyInstaller does not cross-compile,
# so a Windows .exe must be built on Windows:
#
#     pip install pyinstaller
#     pyinstaller knowitall.spec
#
# The result is dist/KnowItAll/KnowItAll.exe (a folder build: faster to start and
# easier to inspect than a single file). Google Chrome must be installed to run it.
from PyInstaller.utils.hooks import collect_all, collect_data_files

datas = [("ui", "ui")]          # the interface itself
binaries = []
hiddenimports = [
    "knowitall", "knowitall.server", "knowitall.runner", "knowitall.browser",
    "knowitall.store", "knowitall.scraper", "knowitall.fetch", "knowitall.discovery",
    "knowitall.generic", "knowitall.normalize", "knowitall.ats_detect",
    "knowitall.ats", "knowitall.ats.greenhouse", "knowitall.ats.lever", "knowitall.ats.ashby",
    "knowitall.ats.smartrecruiters", "knowitall.ats.workday",
]

# botasaurus ships data files and a Go HTTP client binary; grab everything it needs.
for package in ("botasaurus", "botasaurus_requests", "botasaurus_driver", "botasaurus_humancursor"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception:
        pass                      # optional packages simply are not bundled

datas += collect_data_files("certifi")

a = Analysis(
    ["ui.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PyQt5"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="KnowItAll",
    debug=False,
    strip=False,
    upx=False,
    console=True,          # keep the console: it shows the scraper log and any startup error
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False,
    upx=False,
    name="KnowItAll",
)
