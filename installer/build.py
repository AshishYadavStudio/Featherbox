"""Build the one-click Windows installer: installer/dist/Featherbox-Setup-<version>.exe

Run with the project's Python (the one in backend/.venv):
    backend\\.venv\\Scripts\\python installer\\build.py [--skip-ui]

What it does (no extra tools needed; everything ships with Windows or this project):
  1. draws the app icon and builds the web UI (npm run build)
  2. stages a private Python runtime (copied from the Python this venv was made from, trimmed),
     the venv's packages, the Microsoft C++ runtime DLLs, the backend and the built UI
  3. compiles the launcher and the uninstaller with the C# compiler built into Windows
  4. zips the stage and compiles Setup.exe with that zip embedded
"""
from __future__ import annotations

import compileall
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BUILD = HERE / "build"
STAGE = BUILD / "stage"
DIST = HERE / "dist"
CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"

VERSION = re.search(r'APP_VERSION = "([^"]+)"', (ROOT / "backend/app/config.py").read_text("utf-8")).group(1)

# Parts of the standard library a server app never needs.
LIB_SKIP = {"test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "pydoc_data", "site-packages",
            "venv", "__pycache__", "turtle.py"}
DLL_SKIP = {"_tkinter.pyd", "tcl86t.dll", "tk86t.dll"}
PKG_SKIP = {"pip", "__pycache__", "_virtualenv.py", "_virtualenv.pth", "distutils-precedence.pth", "_distutils_hack"}
# Microsoft C++ runtime, deployed app-locally (allowed by the Visual C++ redistributable licence) so
# Python packages and the downloaded engines run even on PCs without the redistributable installed.
VC_DLLS = ["msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll",
           "vcomp140.dll", "concrt140.dll"]


def step(msg: str) -> None:
    print(f"\n== {msg}", flush=True)


def make_icon(path: Path) -> None:
    """App icon: orange-to-pink rounded square with a white feather (matches the UI logo)."""
    import math
    from PIL import Image, ImageDraw

    S = 1024
    grad = Image.new("RGBA", (S, S))
    px = grad.load()
    a, b = (255, 122, 26), (255, 61, 127)
    for y in range(S):
        for x in range(S):
            t = (x + y) / (2 * (S - 1))
            px[x, y] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)) + (255,)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((40, 40, S - 40, S - 40), radius=230, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    # Feather drawn on its own layer along a gently curved diagonal axis (base bottom-left, tip top-right).
    feather = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    fd = ImageDraw.Draw(feather)
    p0, p1 = (285.0, 800.0), (775.0, 225.0)
    ax, ay = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(ax, ay)
    ux, uy = ax / length, ay / length          # along the shaft
    nx, ny = -uy, ux                           # across the shaft

    def at(t: float, off: float) -> tuple[float, float]:
        bend = 38 * math.sin(math.pi * t)      # slight curve, like a real feather
        return (p0[0] + ax * t + nx * (off + bend), p0[1] + ay * t + ny * (off + bend))

    ts = [0.2 + 0.8 * k / 80 for k in range(81)]
    left = [at(t, 150 * math.sin(math.pi * (t - 0.2) / 0.82) ** 0.75) for t in ts]
    right = [at(t, -118 * math.sin(math.pi * (t - 0.2) / 0.82) ** 0.75) for t in ts]
    fd.polygon(left + right[::-1], fill=(255, 255, 255, 255))
    # two notches in the vane, then the shaft cut through it so the background shows as a thin line
    for t, side in ((0.47, 1), (0.66, -1)):
        edge = at(t, side * 150 * math.sin(math.pi * (t - 0.2) / 0.82) ** 0.75 * (1 if side > 0 else 118 / 150))
        inner = at(t - 0.05, side * 20)
        fd.polygon([edge, at(t + 0.035, side * 160), inner], fill=(0, 0, 0, 0))
    shaft = [at(t, 0) for t in [k / 60 for k in range(57)]]
    fd.line(shaft[12:], fill=(0, 0, 0, 0), width=18)          # shaft inside the vane: a gradient-coloured line
    fd.line(shaft[:14], fill=(255, 255, 255, 255), width=26)  # quill below the vane: solid white
    fd.ellipse((shaft[0][0] - 13, shaft[0][1] - 13, shaft[0][0] + 13, shaft[0][1] + 13), fill=(255, 255, 255, 255))
    img.alpha_composite(feather)
    sizes = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.resize((256, 256), Image.LANCZOS).save(path, sizes=sizes)


def copytree(src: Path, dst: Path, skip: set[str]) -> None:
    shutil.copytree(src, dst, ignore=lambda d, names: [n for n in names if n in skip or n.endswith(".pyc")])


def stage_python(dst: Path) -> None:
    base = Path(sys.base_prefix)
    venv_pkgs = ROOT / "backend/.venv/Lib/site-packages"
    dst.mkdir(parents=True)
    for f in ["python.exe", "pythonw.exe", "python3.dll", f"python{sys.version_info.major}{sys.version_info.minor}.dll",
              "LICENSE.txt"]:
        shutil.copy2(base / f, dst / f)
    copytree(base / "DLLs", dst / "DLLs", DLL_SKIP)
    copytree(base / "Lib", dst / "Lib", LIB_SKIP)
    copytree(venv_pkgs, dst / "Lib" / "site-packages", PKG_SKIP | {n for n in os.listdir(venv_pkgs) if n.startswith("pip-")})
    sys32 = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32"
    for dll in VC_DLLS:
        src = base / dll if (base / dll).exists() else sys32 / dll
        if src.exists():
            shutil.copy2(src, dst / dll)
        else:
            print(f"   (note: {dll} not found, skipped)")
    # A ._pth file makes this an isolated runtime: it ignores PYTHONPATH, user site-packages and any
    # other Python installed on the PC, and puts the backend on the import path.
    tag = f"python{sys.version_info.major}{sys.version_info.minor}"
    (dst / f"{tag}._pth").write_text("Lib\nDLLs\nLib\\site-packages\n..\\app\\backend\nimport site\n", "utf-8")


def csc(out: Path, sources: list[Path], icon: Path, defines: list[str] = (), resources: list[Path] = ()) -> None:
    cmd = [str(CSC), "/nologo", "/target:winexe", "/optimize+", "/platform:anycpu", f"/out:{out}",
           f"/win32icon:{icon}", f"/win32manifest:{HERE / 'app.manifest'}",
           "/r:System.dll", "/r:System.Drawing.dll", "/r:System.Windows.Forms.dll",
           "/r:System.IO.Compression.dll", "/r:System.IO.Compression.FileSystem.dll"]
    cmd += [f"/define:{d}" for d in defines]
    cmd += [f"/resource:{r},{r.name}" for r in resources]
    cmd += [str(s) for s in sources]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"C# compile failed for {out.name}:\n{r.stdout}{r.stderr}")


def main() -> None:
    if not CSC.exists():
        sys.exit(f"C# compiler not found at {CSC} (it ships with Windows' .NET Framework 4).")
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True)
    DIST.mkdir(exist_ok=True)

    step("Icon")
    icon = BUILD / "icon.ico"
    make_icon(icon)
    public = ROOT / "frontend/public"
    public.mkdir(exist_ok=True)
    shutil.copy2(icon, public / "favicon.ico")

    if "--skip-ui" not in sys.argv:
        step("Web UI (npm run build)")
        subprocess.run("npm run build", cwd=ROOT / "frontend", shell=True, check=True)
    if not (ROOT / "frontend/dist/index.html").exists():
        sys.exit("frontend/dist is missing: run without --skip-ui.")

    step("Python runtime + packages")
    stage_python(STAGE / "python")

    step("App files")
    copytree(ROOT / "backend/app", STAGE / "app/backend/app", {"__pycache__"})
    shutil.copy2(ROOT / "backend/run.py", STAGE / "app/backend/run.py")
    shutil.copytree(ROOT / "frontend/dist", STAGE / "app/frontend/dist")
    step("Pre-compiling Python files (faster first start)")
    compileall.compile_dir(STAGE / "python/Lib", quiet=2, workers=0)
    compileall.compile_dir(STAGE / "app/backend", quiet=2, workers=0)

    # Windows' classic 260-character path limit: Setup refuses install folders that would exceed it.
    longest = max(len(str(p.relative_to(STAGE))) for p in STAGE.rglob("*"))
    print(f"   longest path inside the app: {longest} characters")
    (BUILD / "BuildInfo.cs").write_text(
        f'static class BuildInfo {{ public const string Version = "{VERSION}"; '
        f'public const int LongestPath = {longest}; }}\n', "utf-8")

    step("Launcher + uninstaller (C#)")
    csc(STAGE / "Featherbox.exe", [HERE / "Launcher.cs"], icon)
    csc(STAGE / "uninstall.exe", [HERE / "Setup.cs", BUILD / "BuildInfo.cs"], icon, defines=["UNINSTALLER"])

    step("Payload zip")
    payload = BUILD / "payload.zip"
    files = sorted(p for p in STAGE.rglob("*") if p.is_file())
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=7) as z:
        for p in files:
            z.write(p, p.relative_to(STAGE).as_posix())
    raw = sum(p.stat().st_size for p in files)
    print(f"   {len(files)} files, {raw / 2**20:.0f} MB -> {payload.stat().st_size / 2**20:.0f} MB zipped")

    step("Setup.exe")
    out = DIST / f"Featherbox-Setup-{VERSION}.exe"
    csc(out, [HERE / "Setup.cs", BUILD / "BuildInfo.cs"], icon, resources=[payload])
    print(f"\nBuilt {out}  ({out.stat().st_size / 2**20:.0f} MB)")


if __name__ == "__main__":
    main()
