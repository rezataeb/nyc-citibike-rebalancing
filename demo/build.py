"""Embed the scene screenshots into demo/template.html as base64 and write demo/index.html.

Run from the repo root after placing the JPEGs in a folder:
    python3 demo/build.py path/to/jpgs
Expects 1-historical, 2-detail, 3-live, 5-weather, 6-reliability (.jpg).
"""
import base64, sys
from pathlib import Path

shots = Path(sys.argv[1])
names = {"IMG_1": "1-historical", "IMG_2": "2-detail", "IMG_3": "3-live", "IMG_5": "5-weather", "IMG_6": "6-reliability"}
html = Path(__file__).with_name("template.html").read_text()
for key, stem in names.items():
    b64 = base64.b64encode((shots / f"{stem}.jpg").read_bytes()).decode()
    html = html.replace("{{" + key + "}}", "data:image/jpeg;base64," + b64)
assert "{{" not in html, "unreplaced placeholder"
out = Path(__file__).with_name("index.html")
out.write_text(html)
print(f"wrote {out} ({out.stat().st_size/1e6:.2f} MB)")
