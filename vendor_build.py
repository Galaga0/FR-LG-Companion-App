import json
import os
import ssl
import sys
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

URLS = {
    "pokedex.json": "https://play.pokemonshowdown.com/data/pokedex.json",
    "moves.json": "https://play.pokemonshowdown.com/data/moves.json",
    "learnsets.json": "https://play.pokemonshowdown.com/data/learnsets.json",
    "gen3.json": "https://cdn.jsdelivr.net/gh/Deskbot/Pokemon-Learnsets/output/gen3.json",
}

def decode_bytes(data: bytes) -> str:
    for enc in ("utf-8","utf-8-sig","cp1252","latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8","ignore")

def download(url, dest: Path, timeout=15):
    print(f"→ Downloading {url} -> {dest}")
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
        text = decode_bytes(r.read())
    dest.write_text(text, encoding="utf-8")
    print(f"✓ Wrote {dest.name} ({len(text)} bytes)")

def species_key(name: str) -> str:
    s = (name or "").lower().replace("♀","f").replace("♂","m")
    import re
    return re.sub(r"[^a-z0-9]","",s)

def purge_fairy(types_list):
    out = []
    for t in (types_list or []):
        tt = str(t or "").title()
        if tt and tt != "Fairy" and tt not in out:
            out.append(tt)
    if not out:
        out = ["Normal"]
    if len(out) == 1:
        out.append(None)
    return [out[0], out[1]]

def build_base_state(pokedex_path: Path, moves_path: Path) -> dict:
    print("→ Building compact base_state.json (Kanto 1–151, types + totals + minimal moves_db)")
    pokedex = json.loads(pokedex_path.read_text(encoding="utf-8"))
    moves = json.loads(moves_path.read_text(encoding="utf-8"))

    # moves_db = {norm_name: {name, type}}
    def normalize_type(t):
        t = (t or "").title()
        return "Normal" if t == "Fairy" else t

    moves_db = {}
    for mid, md in moves.items():
        name = md.get("name", mid)
        tp = normalize_type(md.get("type",""))
        if not name or not tp:
            continue
        norm = (name or "").strip().lower()
        moves_db[norm] = {"name": name, "type": tp}

    species_db = {}
    for sid, sd in pokedex.items():
        num = sd.get("num")
        if not (isinstance(num, int) and 1 <= num <= 151):
            continue
        if sd.get("forme"):
            continue
        name = sd.get("name", sid)
        t1, t2 = purge_fairy(sd.get("types", []))
        base = sd.get("baseStats", {})
        total = int(sum(base.values())) if isinstance(base, dict) else 0
        species_db[species_key(name)] = {
            "name": name,
            "types": [t1, t2],
            "total": total,
            # keep learnset empty to keep file small; app can rebuild on demand
            "learnset": {}
        }

    base_state = {
        "moves_db": moves_db,
        "species_db": species_db,
        "roster": [],
        "locks": [],
        "caught_counts": {},
        "fulfilled": [],
        "settings": {
            "unique_sig": True,
            "default_level": 5,
            "hide_spinner": True,
            "visible_pages": {
                "pokedex": True, "team": True, "matchup": True, "evo": True,
                "opponents": False, "moves": False, "datapacks": False, "species": False, "saveload": True
            }
        },
        "opponents": {"meta":{"sheet_url":"","last_loaded":""},"encounters":[], "cleared":[]}
    }
    return base_state

def main():
    # 1) Download datasets
    for fname, url in URLS.items():
        download(url, DATA_DIR / fname, timeout=20)

    # 2) Build compact base_state.json
    base_state = build_base_state(DATA_DIR / "pokedex.json", DATA_DIR / "moves.json")
    out = DATA_DIR / "base_state.json"
    out.write_text(json.dumps(base_state, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✓ Wrote {out.name} ({out.stat().st_size} bytes)")

    print("\nAll done. Commit contents of the 'data/' folder to your repo.")
    print("On Streamlit, the app will read local JSONs instantly and start from an empty save per user session.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("FAILED:", e)
        sys.exit(1)