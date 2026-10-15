#!/usr/bin/env python3
"""JS syntax check of dashboard.html's inline script (node if installed, else
macOS JavaScriptCore via osascript). Exit 0 = parses."""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

html = (Path(__file__).resolve().parent.parent / "dashboard.html").read_text(encoding="utf-8")
js = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
    f.write(js)
if shutil.which("node"):
    r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True)
elif shutil.which("osascript"):
    wrapper = f'var s = $.NSString.stringWithContentsOfFileEncodingError("{f.name}", $.NSUTF8StringEncoding, null).js; new Function(s); "ok"'
    r = subprocess.run(["osascript", "-l", "JavaScript", "-e", wrapper], capture_output=True, text=True)
else:
    print("no JS engine (node/osascript) available - skipped")
    sys.exit(0)
print(r.stdout.strip() or r.stderr.strip())
sys.exit(r.returncode)
