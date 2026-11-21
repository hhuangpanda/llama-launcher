"""Builds dist\llama-launcher\llama-launcher.exe (PyInstaller, one folder)."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

subprocess.run([
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
    "--name", "llama-launcher", "--paths", str(ROOT),
    "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build"),
    str(ROOT / "tools" / "entry.py"),
], check=True)
print(ROOT / "dist" / "llama-launcher" / "llama-launcher.exe")
