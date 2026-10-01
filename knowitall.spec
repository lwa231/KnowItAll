# PyInstaller build for the KnowItAll desktop app.
#
# Build it ON the machine you will run it on - PyInstaller does not cross-compile,
# so a Windows .exe must be built on Windows:
#
#     pip install pyinstaller
#     pyinstaller knowitall.spec
#
# The result is dist/KnowItAll/KnowItAll.exe (a folder build: faster to start and
# easier to inspect than a single file). Google Chrome is optional (JavaScript-heavy sites are
# skipped without it); on Windows the Edge WebView2 Runtime is needed for the window.
from PyInstaller.utils.hooks import collect_all, collect_data_files

datas = [("ui", "ui"), ("knowitall/data", "knowitall/data"), ("knowitall/assets", "knowitall/assets")]   # the interface, the curated location data, the app icons
binaries = []
hiddenimports = [
    "knowitall", "knowitall.paths", "knowitall.logsetup", "knowitall.shell", "knowitall.geo", "knowitall.search", "knowitall.server", "knowitall.runner", "knowitall.browser",
    "knowitall.store", "knowitall.scraper", "knowitall.fetch", "knowitall.discovery",
    "knowitall.generic", "knowitall.normalize", "knowitall.ats_detect",
    "knowitall.compat", "knowitall.context", "knowitall.export", "knowitall.maintenance", "knowitall.service", "knowitall.settings",
    "knowitall.outcomes", "knowitall.json_jobs", "knowitall.adapters",
    "knowitall.ats", "knowitall.ats.greenhouse", "knowitall.ats.lever", "knowitall.ats.ashby",
    "knowitall.ats.smartrecruiters", "knowitall.ats.workday", "knowitall.ats.oracle",
]

# botasaurus ships data files and a Go HTTP client binary; grab everything it needs.
for package in ("botasaurus", "botasaurus_requests", "botasaurus_driver", "botasaurus_humancursor", "platformdirs", "webview"):
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
    icon="knowitall/assets/icons/knowitall.ico",
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False,
    upx=False,
    name="KnowItAll",
)

# macOS: a proper app bundle, so the Dock and menu bar say KnowItAll with the KiA icon
import sys
if sys.platform == "darwin":
    app = BUNDLE(
        coll, name="KnowItAll.app", icon="knowitall/assets/icons/knowitall.icns",
        bundle_identifier="com.knowitall.desktop",
        info_plist={"CFBundleName": "KnowItAll", "NSHighResolutionCapable": True},
    )
