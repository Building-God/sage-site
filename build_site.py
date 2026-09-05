"""Package only explicitly public site assets; never publish project internals."""
from pathlib import Path
import shutil
ROOT=Path(__file__).resolve().parent
PUBLIC=("index.html", "board.html", "privacy.html", "terms.html", "style.css", "board.js")
def build():
    for name in ("privacy.html", "terms.html"):
        text=(ROOT/name).read_text(encoding="utf-8")
        if "PENDING CONFIRMATION" in text or "Draft awaiting" in text:
            raise SystemExit("Legal pages still await a verified contact; publication is blocked.")
    out=ROOT/"public"
    out.mkdir(exist_ok=True)
    for name in PUBLIC:
        shutil.copyfile(ROOT/name, out/name)
    print("Prepared", len(PUBLIC), "public assets")
if __name__=="__main__":
    build()
