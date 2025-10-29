# app.py
import streamlit as st
from typing import List, Dict, Tuple, Optional
import json, os, urllib.request, ssl, re, csv, uuid
from urllib.parse import urlparse, parse_qs

st.set_page_config(page_title="FR/LG Companion App", layout="wide")

# =========================
# Constants / Rules
# =========================
TYPES = [
    "Normal","Fire","Water","Electric","Grass","Ice","Fighting","Poison",
    "Ground","Flying","Psychic","Bug","Rock","Ghost","Dragon","Dark","Steel"
]  # intentionally no Fairy

STONE_ITEMS = ["Fire Stone","Water Stone","Thunder Stone","Leaf Stone","Moon Stone"]
STONE_EMOJI = {
    'Fire Stone': '🔥',
    'Water Stone': '💧',
    'Thunder Stone': '⚡',
    'Leaf Stone': '🍃',
    'Moon Stone': '🌙',
}
def stone_with_emoji(name: str) -> str:
    return f"{STONE_EMOJI.get(name, '🪨')} {name}" if name else name


TRADE_EVOLVE_LEVEL = 37  # treat trade evolutions as Lv37 in auto-level evolve

OFFENSE_SCORE = {4.0: 4, 2.0: 2, 1.0: 0, 0.5: -2, 0.25: -4, 0.0: -5}
# DEFENSE_SCORE meaning: 0.0 (immune) best for you (+5), 4.0 worst (-4)
DEFENSE_SCORE = {4.0:-4, 2.0:-2, 1.0: 0, 0.5:  2, 0.25:  4, 0.0:  5}

TYPE_CHART: Dict[str, Dict[str, float]] = {
    "Normal": {"Rock":0.5,"Ghost":0.0,"Steel":0.5},
    "Fire": {"Fire":0.5,"Water":0.5,"Grass":2.0,"Ice":2.0,"Bug":2.0,"Rock":0.5,"Dragon":0.5,"Steel":2.0},
    "Water": {"Fire":2.0,"Water":0.5,"Grass":0.5,"Ground":2.0,"Rock":2.0,"Dragon":0.5},
    "Electric": {"Water":2.0,"Electric":0.5,"Grass":0.5,"Ground":0.0,"Flying":2.0,"Dragon":0.5},
    "Grass": {"Fire":0.5,"Water":2.0,"Grass":0.5,"Poison":0.5,"Ground":2.0,"Flying":0.5,"Bug":0.5,"Rock":2.0,"Dragon":0.5,"Steel":0.5},
    "Ice": {"Water":0.5,"Grass":2.0,"Ice":0.5,"Ground":2.0,"Flying":2.0,"Dragon":2.0,"Steel":0.5,"Fire":0.5},
    "Fighting": {"Normal":2.0,"Ice":2.0,"Poison":0.5,"Flying":0.5,"Psychic":0.5,"Bug":0.5,"Rock":2.0,"Ghost":0.0,"Dark":2.0,"Steel":2.0},
    "Poison": {"Grass":2.0,"Poison":0.5,"Ground":0.5,"Rock":0.5,"Ghost":0.5,"Steel":0.0},
    "Ground": {"Fire":2.0,"Electric":2.0,"Grass":0.5,"Poison":2.0,"Flying":0.0,"Bug":0.5,"Rock":2.0,"Steel":2.0},
    "Flying": {"Electric":0.5,"Grass":2.0,"Fighting":2.0,"Bug":2.0,"Rock":0.5,"Steel":0.5},
    "Psychic": {"Fighting":2.0,"Poison":2.0,"Psychic":0.5,"Dark":0.0,"Steel":0.5},
    "Bug": {"Fire":0.5,"Grass":2.0,"Fighting":0.5,"Poison":0.5,"Flying":0.5,"Psychic":2.0,"Ghost":0.5,"Dark":2.0,"Steel":0.5},
    "Rock": {"Fire":2.0,"Ice":2.0,"Fighting":0.5,"Ground":0.5,"Flying":2.0,"Bug":2.0,"Steel":0.5},
    "Ghost": {"Normal":0.0,"Psychic":2.0,"Ghost":2.0,"Dark":0.5},
    "Dragon": {"Dragon":2.0},
    "Dark": {"Fighting":0.5,"Psychic":2.0,"Ghost":2.0,"Dark":0.5},
    "Steel": {"Ice":2.0,"Rock":2.0}
}

DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/u/0/d/1frqW2CeHop4o0NP6Ja_TAAPPkGIrvxkeQJBfyxFggyk/htmlview?pli=1#gid=422900446"

STATE_PATH = "state.json"
STATE_BAK = "state.backup.json"
STATE_TMP = "state.json.tmp"
STATE_RECOVERED = False
STATE_RESET = False

MOVES_MASTER: Dict[str, Dict] = {}
MOVES_BY_NAME: Dict[str, Dict] = {}
MOVES_BY_ID: Dict[str, Dict] = {}
EVOS: Dict[str, List[Dict]] = {}

# Trade reward species (appear once in Add list)
TRADE_REWARD_SPECIES = {"mrmime","farfetchd","jynx","lickitung"}

def new_guid() -> str:
    return uuid.uuid4().hex

def do_rerun():
    try: st.rerun()
    except Exception:
        try: st.experimental_rerun()
        except Exception: pass

# =========================
# Type normalization
# =========================
def normalize_type(t: Optional[str]) -> Optional[str]:
    if not t: return None
    t = str(t).title()
    if t == "Fairy":
        return "Normal"   # only used for moves/effectiveness math
    return t

def purge_fairy_types_pair(types_list) -> List[Optional[str]]:
    raw = (types_list or [])
    t1 = raw[0] if len(raw) > 0 else None
    t2 = raw[1] if len(raw) > 1 else None
    candidates: List[str] = []
    for t in (t1, t2):
        if not t: continue
        tt = str(t).title()
        if tt == "Fairy": continue
        if tt not in candidates: candidates.append(tt)
    if not candidates: candidates = ["Normal"]
    if len(candidates) == 1: candidates.append(None)
    return [candidates[0], candidates[1]]

# =========================
# Robust decode
# =========================
def decode_bytes(data: bytes) -> str:
    for enc in ("utf-8","utf-8-sig","cp1252","latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8","ignore")

# =========================
# Persistence / defaults
# =========================
def _default_state() -> Dict:
    return {
        "moves_db": {},
        "species_db": {},
        "roster": [],
        "locks": [],
        "caught_counts": {},
        "fulfilled": [],
        "stones": {k: 0 for k in STONE_ITEMS},
        "settings": {
            "unique_sig": True,
            "default_level":  5,
            "hide_spinner": True,
            "visible_pages": {
                "pokedex": True, "team": True, "matchup": True, "evo": True,
                "opponents": False, "moves": False, "datapacks": False, "species": False, "saveload": True
            }
        },
        "opponents": {"meta":{"sheet_url":"","last_loaded":"","last_pick":[0,0]},"encounters":[], "cleared":[]},
        "team_tie_memory": {"signature":"", "selected":[]}
    }

def _atomic_write_json(path: str, data: Dict):
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    with open(STATE_TMP, "w", encoding="utf-8") as f:
        f.write(payload); f.flush(); os.fsync(f.fileno())
    os.replace(STATE_TMP, path)
    with open(STATE_BAK, "w", encoding="utf-8") as fb:
        fb.write(payload); fb.flush(); os.fsync(fb.fileno())

def save_state(state: Dict):
    try:
        _atomic_write_json(STATE_PATH, state)
    except Exception:
        try:
            with open(STATE_BAK, "w", encoding="utf-8") as fb:
                json.dump(state, fb, indent=2, ensure_ascii=False)
        except Exception:
            pass

def load_state() -> Dict:
    global STATE_RECOVERED, STATE_RESET
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "rb") as f:
                return json.loads(decode_bytes(f.read()))
        except Exception:
            pass
    if os.path.exists(STATE_BAK):
        try:
            with open(STATE_BAK, "rb") as f:
                data = json.loads(decode_bytes(f.read()))
            try: _atomic_write_json(STATE_PATH, data)
            except Exception: pass
            STATE_RECOVERED = True
            return data
        except Exception:
            pass
    STATE_RESET = True
    return _default_state()

def migrate_state(state: Dict) -> Dict:
    state.setdefault("moves_db",{})
    state.setdefault("species_db",{})
    state.setdefault("roster",[])
    state.setdefault("locks",[])
    state.setdefault("caught_counts",{})
    state.setdefault("fulfilled",[])
    state.setdefault("stones", {k: 0 for k in STONE_ITEMS})
    # ensure all stones exist
    for k in STONE_ITEMS:
        state["stones"].setdefault(k, 0)

    stg = state.setdefault("settings",{})
    stg.setdefault("unique_sig", True)
    stg.setdefault("default_level",  5)
    stg.setdefault("hide_spinner", True)
    vis = stg.setdefault("visible_pages", {
        "pokedex": True, "team": True, "matchup": True, "evo": True,
        "opponents": False, "moves": False, "datapacks": False, "species": False, "saveload": True
    })
    vis.setdefault("evo", True)
    opp = state.setdefault("opponents",{"meta":{"sheet_url":"","last_loaded":"","last_pick":[0,0]},"encounters":[]})
    opp.setdefault("meta",{"sheet_url":"","last_loaded":"","last_pick":[0,0]})
    opp.setdefault("encounters",[])
    opp.setdefault("cleared",[])
    state.setdefault("team_tie_memory", {"signature":"", "selected":[]})

    changed = False
    for k, sp in list(state.get("species_db", {}).items()):
        if "types" in sp:
            newpair = purge_fairy_types_pair(sp.get("types"))
            if newpair != sp.get("types"):
                sp["types"] = newpair; changed = True

    for mon in state.get("roster", []):
        if "guid" not in mon: mon["guid"] = new_guid(); changed = True
        if "species_key" not in mon and mon.get("species"):
            mon["species_key"] = species_key(mon["species"]); changed = True
        if "nickname" in mon:
            del mon["nickname"]; changed = True
        if "types" in mon:
            newpair = purge_fairy_types_pair(mon.get("types"))
            if newpair != mon.get("types"):
                mon["types"] = newpair; changed = True

    for mk, mv in list(state.get("moves_db", {}).items()):
        tp = normalize_type(mv.get("type"))
        if tp != mv.get("type"):
            mv["type"] = tp; changed = True

    if changed: save_state(state)
    return state

STATE = migrate_state(load_state())
save_state(STATE)

# =========================
# UI polish
# =========================
st.markdown("""
<style>
[data-testid="stStatusWidget"] { visibility: hidden !important; }
.stSpinner, [data-testid="stSpinner"] { display: none !important; }

.badge{display:inline-block;padding:2px 8px;border-radius:9999px;font-size:12px}
.b-level{background:#e6f4ea;color:#0b6b2b;border:1px solid #b8e0c3}
.b-trade{background:#e8f0fe;color:#174ea6;border:1px solid #c6d1ff}
.b-item{background:#fff4e5;color:#8a4b00;border:1px solid #ffd8a8}
.b-manual{background:#ececec;color:#444;border:1px solid #d6d6d6}
.b-ready{background:#e6ffed;color:#055d20;border:1px solid #b2f2bb}
.b-wait{background:#fff5f5;color:#7a1f1f;border:1px solid #ffc9c9}

.card{border:1px solid #e5e7eb;border-radius:12px;padding:12px;box-shadow:0 1px 2px rgba(0,0,0,.04);background:#fff}
.card h4{margin:0}
.row{padding:4px 0;border-bottom:1px dashed #e5e7eb}
.row:last-child{border-bottom:none}
.head{font-weight:600;color:#111827}
.small{font-size:12px;color:#6b7280}
</style>
""", unsafe_allow_html=True)

# =========================
# Utilities
# =========================
def norm_key(name: str) -> str:
    return (name or "").strip().lower()

def move_id(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def ps_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]","",(name or "").lower()).replace("♀","f").replace("♂","m")

def species_key(name: str) -> str:
    s = (name or "").lower().replace("♀","f").replace("♂","m")
    return re.sub(r"[^a-z0-9]","",s)

def clean_invisibles(s: str) -> str:
    if not s: return s
    s = s.replace("\u00A0"," ").replace("\u202F"," ").replace("\u2009"," ")
    s = s.replace("\u2013","-").replace("\u2014","-")
    s = re.sub(r"[\u200b-\u200f\u202a-\u202e]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s

def clean_move_token(s: str) -> str:
    s = clean_invisibles((s or "").strip())
    s = re.sub(r"\(.*?\)", "", s).strip()
    return s

def mon_display_name(mon: Dict) -> str:
    return (mon.get("species") or "").strip()

# ============= Fetchers =============
@st.cache_data(show_spinner=False)
def fetch_text(url: str) -> str:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
        return decode_bytes(r.read())

@st.cache_data(show_spinner=False)
def fetch_json(url: str) -> dict:
    return json.loads(fetch_text(url))

@st.cache_data(show_spinner=False)
def get_pokedex_cached() -> dict:
    return fetch_json("https://play.pokemonshowdown.com/data/pokedex.json")

# =========================
# Moves master
# =========================
def load_moves_master():
    global MOVES_MASTER, MOVES_BY_NAME, MOVES_BY_ID
    if MOVES_MASTER:
        return
    try:
        moves = fetch_json("https://play.pokemonshowdown.com/data/moves.json")
    except Exception:
        moves = {}
    for mid, md in moves.items():
        name = md.get("name", mid)
        mid_showdown = md.get("id", mid)
        mtype = normalize_type(md.get("type",""))
        cat = md.get("category","")
        bp = md.get("basePower",0)
        is_dmg = (cat.lower()!="status") or (isinstance(bp,(int,float)) and bp>0) or ("damage" in md) or ("ohko" in md)
        rec = {**md, "name":name, "type":mtype, "category":cat, "basePower":bp, "is_damaging":bool(is_dmg)}
        MOVES_MASTER[name] = rec
        MOVES_BY_NAME[norm_key(name)] = rec
        MOVES_BY_ID[move_id(name)] = rec
        if mid_showdown:
            MOVES_BY_ID[move_id(mid_showdown)] = rec
load_moves_master()

def lookup_move(s: str) -> Optional[Dict]:
    if not s: return None
    s_clean = clean_move_token(s)
    rec = MOVES_BY_ID.get(move_id(s_clean))
    if rec: return rec
    rec = MOVES_BY_NAME.get(norm_key(s_clean))
    if rec: return rec
    return None

def move_is_damaging(move_name: str) -> bool:
    info = lookup_move(move_name)
    if info is None:
        return True
    return bool(info.get("is_damaging", True))

# =========================
# Learnsets (Gen 3) — merged, damage-only
# =========================
@st.cache_data(show_spinner=False)
def get_gen3_data_cached() -> dict:
    return fetch_json("https://cdn.jsdelivr.net/gh/Deskbot/Pokemon-Learnsets/output/gen3.json")

@st.cache_data(show_spinner=False)
def get_showdown_learnsets_cached() -> dict:
    return fetch_json("https://play.pokemonshowdown.com/data/learnsets.json")

def _merge_into_levelmap(out: Dict[str, List[str]], level: int, name: str):
    key = str(level)
    cur = out.setdefault(key, [])
    if name not in cur:
        cur.append(name)

def rebuild_learnset_for(species_name: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}

    gen3 = get_gen3_data_cached()
    keys = list(gen3.keys())
    sk = species_key(species_name)
    gk = sk if sk in gen3 else next((k for k in keys if species_key(k) == sk), None)

    if gk and isinstance(gen3.get(gk, {}).get("level", {}), dict):
        for lv, mv in gen3[gk]["level"].items():
            seq = mv if isinstance(mv, list) else [mv]
            for m in seq:
                rec = lookup_move(m)
                nm = rec["name"] if rec else clean_move_token(m)
                if nm and move_is_damaging(nm):
                    _merge_into_levelmap(out, int(re.sub(r"\D","",str(lv)) or "0"), nm)

    ls = get_showdown_learnsets_cached()
    showdown_key = None
    nsk = ps_id(species_name)
    if nsk in ls:
        showdown_key = nsk
    else:
        for k in ls.keys():
            if ps_id(k) == nsk:
                showdown_key = k
                break

    if showdown_key:
        learn = ls[showdown_key].get("learnset", {})
        for move_id_key, sources in learn.items():
            if not isinstance(sources, list):
                continue
            levels = []
            three_ok = False
            for s in sources:
                if isinstance(s, str):
                    if s.startswith("3L"):
                        m = re.match(r"^3L(\d+)$", s); 
                        if m: levels.append(int(m.group(1)))
                        three_ok = True
                    if s.startswith("3M") or s.startswith("3T"):
                        three_ok = True
            if not levels and not three_ok:
                continue
            rec = MOVES_BY_ID.get(move_id(move_id_key)) or MOVES_BY_NAME.get(norm_key(move_id_key))
            nm = rec["name"] if rec else clean_move_token(move_id_key)
            if not nm or not move_is_damaging(nm):
                continue
            for lv in levels or [0]:
                _merge_into_levelmap(out, lv, nm)

    return {k: v for k, v in out.items() if v}

def last_four_moves_by_level(learnset: Dict[str, List[str]], level: int) -> List[str]:
    entries = []
    for k, v in learnset.items():
        num = ''.join([c for c in str(k) if c.isdigit()])
        if not num:
            continue
        lv = int(num)
        if lv <= level:
            seq = v if isinstance(v, list) else [v]
            for m in seq:
                nm = (lookup_move(m) or {}).get("name", clean_move_token(m))
                if nm and move_is_damaging(nm):
                    entries.append((lv, nm))
    entries = [(lv, mv, i) for i, (lv, mv) in enumerate(entries)]
    entries.sort(key=lambda p: (p[0], p[2]))
    seen, ordered = set(), []
    for lv, mv, _ in entries:
        if mv in seen:
            continue
        seen.add(mv); ordered.append(mv)
    return ordered[-4:]

def showdown_key_for_species(species_name: str) -> Optional[str]:
    ls = get_showdown_learnsets_cached()
    key = ps_id(species_name)
    if key in ls: return key
    for k in ls.keys():
        if ps_id(k) == key:
            return k
    return None

def family_closure(species_name: str) -> List[str]:
    dex = get_pokedex_cached()
    def canon_name(nm: str) -> Optional[str]:
        rec = dex.get(ps_id(nm))
        if not rec or rec.get("forme"): return None
        num = rec.get("num")
        if not (isinstance(num, int) and 1 <= num <= 151): return None
        return rec.get("name", nm)
    names: List[str] = []
    seen = set()
    queue = [species_name]
    while queue:
        cur = queue.pop(0)
        cn = canon_name(cur)
        if not cn: continue
        pid = ps_id(cn)
        if pid in seen: continue
        seen.add(pid); names.append(cn)
        rec = dex.get(ps_id(cn)) or {}
        pre = rec.get("prevo")
        if pre: queue.append(pre)
        for e in rec.get("evos", []) or []: queue.append(e)
    return names

@st.cache_data(show_spinner=False)
def allowed_moves_for_family(species_name: str) -> List[str]:
    allowed = set()
    fam = family_closure(species_name)
    def norm_move(token: str) -> Optional[str]:
        rec = MOVES_BY_ID.get(move_id(token)) or MOVES_BY_NAME.get(norm_key(token))
        nmv = (rec.get("name") if rec else clean_move_token(token)) if token else None
        return nmv
    try:
        ls = get_showdown_learnsets_cached()
        for nm in fam:
            skey = showdown_key_for_species(nm)
            if not skey: continue
            learn = ls.get(skey, {}).get("learnset", {}) or {}
            for move_id_key, sources in learn.items():
                if not isinstance(sources, list): continue
                include = any(isinstance(s, str) and (s.startswith("3L") or s.startswith("3M") or s.startswith("3T")) for s in sources)
                if not include: continue
                nmv = norm_move(move_id_key)
                if nmv and move_is_damaging(nmv): allowed.add(nmv)
    except Exception:
        pass
    try:
        gen3 = get_gen3_data_cached()
        keys = list(gen3.keys())
        def find_key(name: str) -> str:
            sk = species_key(name)
            if sk in gen3: return sk
            for k in keys:
                if species_key(k) == sk: return k
            return ""
        for nm in fam:
            gk = find_key(nm)
            if not gk: continue
            lvlmap = gen3.get(gk, {}).get("level", {})
            if not isinstance(lvlmap, dict): continue
            for lv, seq in lvlmap.items():
                moves = seq if isinstance(seq, list) else [seq]
                for m in moves:
                    nmv = norm_move(m)
                    if nmv and move_is_damaging(nmv): allowed.add(nmv)
    except Exception:
        pass
    try:
        for nm in fam:
            sk = species_key(nm)
            sp = STATE.get("species_db", {}).get(sk, {})
            for lv, seq in (sp.get("learnset", {}) or {}).items():
                moves = seq if isinstance(seq, list) else [seq]
                for m in moves:
                    nmv = norm_move(m)
                    if nmv and move_is_damaging(nmv): allowed.add(nmv)
    except Exception:
        pass
    return sorted(allowed, key=lambda s: s.lower())

def render_move_quad_ui(key_prefix: str, sp_name: str, level: int, current_moves: List[str]) -> List[str]:
    allowed = set(allowed_moves_for_family(sp_name))
    for m in current_moves or []:
        if m and m != "(none)": allowed.add(m)
    allowed = sorted(allowed, key=lambda s: s.lower())

    sp = STATE["species_db"].get(species_key(sp_name), {})
    proposed = list(current_moves or last_four_moves_by_level(sp.get("learnset", {}), int(level)) or [])
    while len(proposed) < 4: proposed.append("(none)")

    cols = st.columns(4)
    picks: List[str] = []
    for i in range(4):
        cur = proposed[i]
        opts = ["(none)"] + allowed
        if cur not in opts and cur != "(none)":
            cols[i].caption("Current move not legal under curated list; please pick a valid move.")
        sel = cols[i].selectbox(f"Move {i+1}", opts, index=(opts.index(cur) if cur in opts else 0), key=f"{key_prefix}_mv_{i}")
        typed = canonical_typed(sel)
        cols[i].caption(f"Type: {typed[1] if typed else '—'}")
        picks.append(sel)
    return picks

def ensure_learnset(species_name: str) -> Dict[str, List[str]]:
    sk = species_key(species_name)
    sp = STATE.get("species_db", {}).get(sk)
    if not sp:
        try:
            ensure_species_in_db(species_name)
            sp = STATE.get("species_db", {}).get(sk)
        except Exception:
            sp = None
    if not sp: return {}
    if not sp.get("learnset"):
        try:
            merged = rebuild_learnset_for(sp.get("name", species_name))
            if merged:
                sp["learnset"] = merged
                STATE["species_db"][sk] = sp
                save_state(STATE)
        except Exception:
            pass
    return sp.get("learnset", {}) or {}

def get_prefill_moves(sp: Dict, level: int) -> List[str]:
    try:
        if sp.get("learnset"):
            learned4 = last_four_moves_by_level(sp["learnset"], int(level))
            if learned4:
                return learned4[-4:]
    except Exception:
        pass
    return []

# =========================
# Data building (web)
# =========================
def is_kanto_base_for_151(sd: dict, dex: dict) -> bool:
    num = sd.get("num")
    if not isinstance(num, int) or not (1 <= num <= 151): return False
    if sd.get("forme"): return False
    prevo = sd.get("prevo")
    if not prevo: return True
    pre = dex.get(ps_id(prevo))
    pnum = pre.get("num") if pre else None
    return not (isinstance(pnum, int) and 1 <= pnum <= 151)

@st.cache_data(show_spinner=False)
def build_kanto_state_from_web_cached() -> Dict:
    pokedex = get_pokedex_cached()
    gen3 = get_gen3_data_cached()
    gen3_keys = list(gen3.keys())

    def find_gen3_key(name: str) -> Optional[str]:
        sk = species_key(name)
        if sk in gen3: return sk
        for k in gen3_keys:
            if species_key(k) == sk: return k
        return None

    moves_db = {}
    for rec in MOVES_MASTER.values():
        moves_db[norm_key(rec["name"])] = {"name": rec["name"], "type": normalize_type(rec.get("type",""))}

    species_db = {}
    for sid, sd in pokedex.items():
        if not is_kanto_base_for_151(sd, pokedex): continue
        name = sd.get("name", sid)
        types_raw = sd.get("types", [])
        t1, t2 = purge_fairy_types_pair(types_raw)
        base = sd.get("baseStats", {})
        total = int(sum(base.values())) if base else 0

        gk = find_gen3_key(name)
        learnset = {}
        if gk and isinstance(gen3.get(gk, {}).get("level", {}), dict):
            for lv, mv in gen3[gk]["level"].items():
                seq = mv if isinstance(mv, list) else [mv]
                lm = []
                for m in seq:
                    nm = (lookup_move(m) or {}).get("name", clean_move_token(m))
                    if nm and move_is_damaging(nm): lm.append(nm)
                if lm: learnset[str(lv)] = lm

        species_db[species_key(name)] = {"name": name, "types": [t1, t2], "total": total, "learnset": learnset}

    return {
        "moves_db": moves_db,
        "species_db": species_db,
        "roster": [],
        "locks": [],
        "caught_counts": {},
        "fulfilled": [],
        "stones": {k: 0 for k in STONE_ITEMS},
        "settings": {
            "unique_sig": True, "default_level":  5, "hide_spinner": True,
            "visible_pages": {"pokedex": True,"team": True,"matchup": True,"evo": True,"opponents": False,"moves": False,"datapacks": False,"species": False,"saveload": True}
        },
        "opponents": {"meta":{"sheet_url":"","last_loaded":"","last_pick":[0,0]}, "encounters":[], "cleared":[]},
        "team_tie_memory": {"signature":"", "selected":[]}
    }

@st.cache_data(show_spinner=False)
def build_evo_index_kanto_cached() -> Dict[str, List[Dict]]:
    dex = get_pokedex_cached()
    idx: Dict[str, List[Dict]] = {}
    for sid, sd in dex.items():
        num = sd.get("num")
        if not (isinstance(num, int) and 1 <= num <= 151): continue
        if sd.get("forme"): continue
        name = sd.get("name", sid)
        evos = sd.get("evos", []) or []
        opts: List[Dict] = []
        for e in evos:
            tgt = dex.get(ps_id(e))
            if not tgt: continue
            tnum = tgt.get("num")
            if not (isinstance(tnum, int) and 1 <= tnum <= 151): continue
            if tgt.get("forme"): continue
            method = None; level = None; item = None
            prevo = tgt.get("prevo")
            if prevo and ps_id(prevo) == ps_id(name):
                if "evoLevel" in tgt and isinstance(tgt.get("evoLevel"), int):
                    method = "level"; level = int(tgt["evoLevel"])
                else:
                    etype = tgt.get("evoType")
                    if etype == "useItem": method = "item"; item = tgt.get("evoItem")
                    elif etype == "trade": method = "trade"
                    elif etype == "levelMove": method = "levelMove"
                    elif etype: method = etype
            opts.append({"to": tgt.get("name", e), "method": method, "level": level, "item": item})
        idx[species_key(name)] = opts
    return idx

def ensure_species_in_db(name: str) -> bool:
    sk = species_key(name)
    if sk in STATE["species_db"]: return True
    dex = get_pokedex_cached()
    def _find_record(target_name: str):
        rec = dex.get(ps_id(target_name))
        if rec and rec.get("forme"): rec = None
        if rec and not (isinstance(rec.get("num"), int) and 1 <= rec.get("num") <= 151): rec = None
        if rec: return rec
        for _, r in dex.items():
            if ps_id(r.get("name","")) == ps_id(target_name):
                if r.get("forme"): continue
                if isinstance(r.get("num"), int) and 1 <= r.get("num") <= 151: return r
        return None
    sd = _find_record(name)
    if not sd: return False
    nm = sd.get("name", name)
    t1, t2 = purge_fairy_types_pair(sd.get("types", []))
    base = sd.get("baseStats", {})
    total = int(sum(base.values())) if base else 0
    learnset = rebuild_learnset_for(nm) or {}
    STATE["species_db"][species_key(nm)] = {"name": nm, "types": [t1, t2], "total": total, "learnset": learnset}
    save_state(STATE); return True

def base_key_for(name: str) -> str:
    dex = get_pokedex_cached()
    cur = dex.get(ps_id(name))
    if not cur: return species_key(name)
    while True:
        pre = cur.get("prevo")
        if not pre: break
        pre_rec = dex.get(ps_id(pre))
        if not pre_rec: break
        if pre_rec.get("forme"): break
        num = pre_rec.get("num")
        if isinstance(num, int) and 1 <= num <= 151: cur = pre_rec
        else: break
    return species_key(cur.get("name", name))

# Bootstrap
if not STATE["species_db"]:
    try:
        new_state = build_kanto_state_from_web_cached()
        STATE.update({k: new_state[k] for k in ["moves_db","species_db"]})
        save_state(STATE)
    except Exception:
        pass
try:
    EVOS = build_evo_index_kanto_cached()
except Exception:
    EVOS = {}

# =========================
# Opponents parsing (sheet)
# =========================
def parse_sheet_url_to_csv(url: str, preferred_gid: Optional[str]=None) -> Optional[str]:
    if not url: return None
    try:
        parts = urlparse(url)
        if "docs.google.com" not in parts.netloc: return None
        bits = parts.path.strip("/").split("/")
        if "spreadsheets" in bits and "d" in bits:
            i = bits.index("d")
            doc_id = bits[i+1]
            qs = parse_qs(parts.query or "")
            frag_gid = None
            if parts.fragment:
                m = re.search(r"(?:^|[&#])gid=(\d+)", parts.fragment)
                if m: frag_gid = m.group(1)
            gid = preferred_gid or (qs.get("gid",[None])[0]) or frag_gid or "0"
            return f"https://docs.google.com/spreadsheets/d/{doc_id}/export?format=csv&gid={gid}"
    except Exception:
        return None
    return None

def load_venusaur_sheet(csv_text: str) -> List[Dict]:
    rdr = csv.reader(csv_text.splitlines())
    rows = list(rdr)
    encounters_list: List[Dict] = []
    current_enc: Optional[Dict] = None
    name_counts: Dict[str, int] = {}
    rownum = 0
    starting_skipped = False

    for r in rows:
        rownum += 1
        if len(r) < 10: r = r + [""] * (10 - len(r))
        if not any(cell.strip() for cell in r): continue
        trainer_cell = clean_invisibles((r[0] or "").strip())
        poke    = clean_invisibles((r[4] or "").strip())
        lvl_str = clean_invisibles((r[5] or "").strip())
        mv_cols = [clean_move_token(c) for c in r[6:10]]

        if not starting_skipped and trainer_cell.lower().startswith("starting"):
            starting_skipped = True; continue

        if trainer_cell:
            base_name = trainer_cell
            name_counts[base_name] = name_counts.get(base_name, 0) + 1
            suffix = f" #{name_counts[base_name]}" if name_counts[base_name] > 1 else ""
            label_unique = f"{base_name}{suffix}"
            current_enc = {"label": label_unique, "base_label": base_name, "mons": []}
            encounters_list.append(current_enc)

        if not current_enc: continue
        if not poke: continue

        try:
            m = re.findall(r"\d+", lvl_str)
            level = int(m[0]) if m else 1
        except Exception:
            level = 1

        sk = species_key(poke)
        sp = STATE["species_db"].get(sk)
        if not sp:
            if ensure_species_in_db(poke):
                sp = STATE["species_db"].get(sk)
            if not sp: continue

        typed_moves: List[Tuple[str,str]] = []
        for mv in mv_cols:
            if not mv: continue
            info = lookup_move(mv)
            if info and not info.get("is_damaging", True): continue
            if info:
                typed_moves.append((info["name"], normalize_type(info.get("type",""))))
            else:
                mtype = normalize_type(STATE["moves_db"].get(norm_key(mv),{}).get("type",""))
                if mtype: typed_moves.append((clean_move_token(mv), mtype))

        mon = {
            "species": sp["name"],
            "level": int(level),
            "types": purge_fairy_types_pair(sp["types"]),
            "moves": typed_moves,
            "source_row": rownum,
            "total": sp["total"]
        }
        current_enc["mons"].append(mon)

    return [enc for enc in encounters_list if enc["mons"]]

def autoload_opponents_if_empty():
    try:
        if not STATE["opponents"]["encounters"]:
            csv_url = parse_sheet_url_to_csv(DEFAULT_SHEET_URL)
            if not csv_url: return
            csv_text = fetch_text(csv_url)
            encounters = load_venusaur_sheet(csv_text)
            if encounters:
                STATE["opponents"]["encounters"] = encounters
                STATE["opponents"]["meta"]["sheet_url"] = DEFAULT_SHEET_URL
                STATE["opponents"]["meta"]["last_loaded"] = csv_url
                save_state(STATE)
    except Exception:
        pass

autoload_opponents_if_empty()

# =========================
# Evolution helpers
# =========================
def available_evos_for(species_name: str) -> List[Dict]:
    return EVOS.get(species_key(species_name), [])

def auto_evolve_chain_by_level(species_name: str, level: int) -> str:
    current = species_name
    visited = set()
    while True:
        if ps_id(current) in visited: break
        visited.add(ps_id(current))
        opts = available_evos_for(current)
        candidates: List[Tuple[int, Dict]] = []
        for o in opts:
            m = o.get("method")
            if m == "level" and isinstance(o.get("level"), int) and o["level"] <= level:
                candidates.append((o["level"], o))
            elif m == "trade" and level >= TRADE_EVOLVE_LEVEL:
                candidates.append((TRADE_EVOLVE_LEVEL, o))
        if not candidates: break
        candidates.sort(key=lambda x: x[0])
        chosen = candidates[-1][1]
        current = chosen["to"]
    return current

def evolve_mon_record(mon: Dict, to_species_name: str, rebuild_moves: bool=False):
    ensure_species_in_db(to_species_name)
    sk = species_key(to_species_name)
    sp = STATE["species_db"].get(sk)
    if not sp: return False
    if not sp.get("learnset"):
        merged = ensure_learnset(sp.get("name", to_species_name))
        if merged:
            sp["learnset"] = merged
            STATE["species_db"][sk] = sp
            save_state(STATE)

    mon["species"] = sp["name"]
    mon["species_key"] = sk
    mon["types"] = purge_fairy_types_pair(sp["types"])
    mon["total"] = sp["total"]
    if rebuild_moves:
        learned = last_four_moves_by_level(sp.get("learnset", {}), int(mon.get("level",1)))
        typed: List[Tuple[str,str]] = []
        for m in learned:
            info = lookup_move(m)
            if info and not info.get("is_damaging", True): continue
            mtype = normalize_type((info.get("type") if info else None) or STATE["moves_db"].get(norm_key(m),{}).get("type",""))
            if mtype: typed.append(((info["name"] if info else m), mtype))
        mon["moves"] = typed
    return True

# =========================
# Trade-fodder duplication rules
# =========================
def double_catch_base_set() -> set:
    return {species_key(x) for x in {"Abra","Spearow","Poliwag","Psyduck","Slowpoke"}}

def required_catches_for_species(name: str) -> int:
    sk = species_key(name)
    if sk in TRADE_REWARD_SPECIES: return 1
    return 2 if sk in double_catch_base_set() else 1

# =========================
# Move dropdown helpers
# =========================
@st.cache_data(show_spinner=False)
def all_damaging_moves_sorted() -> List[str]:
    names = [rec["name"] for rec in MOVES_MASTER.values() if rec.get("is_damaging", True)]
    return sorted(set(names), key=lambda s: s.lower())

def canonical_typed(move_name: str) -> Optional[Tuple[str,str]]:
    if not move_name or move_name == "(none)": return None
    info = lookup_move(move_name)
    if info: return (info["name"], normalize_type(info.get("type","")) or "")
    tp = normalize_type(STATE["moves_db"].get(norm_key(move_name),{}).get("type",""))
    if tp: return (clean_move_token(move_name), tp)
    return None

def ensure_move_in_db(move_name: str, default_type: Optional[str]=None):
    mk = norm_key(clean_move_token(move_name))
    if mk and mk not in STATE['moves_db']:
        info = lookup_move(move_name)
        mtype = normalize_type((info.get('type') if info else None) or (default_type or ''))
        STATE['moves_db'][mk] = {'name': clean_move_token(move_name), 'type': mtype}

# =========================
# Startup gate with progress bar
# =========================
def ensure_bootstrap_ready():
    if st.session_state.get("bootstrap_done", False): return
    total_steps = 3 + max(0, len(STATE.get("roster", [])) * 3)
    step = 0
    prog = st.progress(0, text="Preparing data...")
    def tick(n=1, note=None):
        nonlocal step
        step += n
        pct = min(100, int(step / max(1, total_steps) * 100))
        prog.progress(pct, text=note or "Preparing data...")
    if not STATE.get("species_db"):
        try:
            new_state = build_kanto_state_from_web_cached()
            STATE["moves_db"] = new_state.get("moves_db", STATE.get("moves_db", {}))
            STATE["species_db"] = new_state.get("species_db", {})
            save_state(STATE)
        except Exception: pass
    tick(1, "Loading species...")
    global EVOS
    if not EVOS:
        try: EVOS = build_evo_index_kanto_cached()
        except Exception: EVOS = EVOS or {}
    tick(1, "Indexing evolutions...")
    try: autoload_opponents_if_empty()
    except Exception: pass
    tick(1, "Loading opponents...")
    seen_species = set()
    for mon in STATE.get("roster", []):
        nm = mon.get("species", "")
        if not nm: continue
        try: ensure_species_in_db(nm)
        except Exception: pass
        tick(1, f"Preparing {nm}...")
        try: ensure_learnset(nm)
        except Exception: pass
        base_nm = STATE["species_db"].get(species_key(nm), {}).get("name", nm)
        if base_nm not in seen_species:
            try: _ = allowed_moves_for_family(base_nm)
            except Exception: pass
            seen_species.add(base_nm)
        tick(1, f"Caching {nm} moves...")
    prog.empty()
    st.session_state["bootstrap_done"] = True
    try: do_rerun()
    except Exception: pass
    st.stop()

# =========================
# Pages
# =========================
def render_pokedex():
    if "rm_request" in st.session_state:
        guid = st.session_state.pop("rm_request")
        fset = set(STATE.get("fulfilled", []))
        cc = STATE.get("caught_counts", {})
        idx = next((i for i,m in enumerate(STATE["roster"]) if m.get("guid")==guid), None)
        if idx is not None:
            mon = STATE["roster"][idx]
            base_sk = base_key_for(mon.get("species",""))
            req = required_catches_for_species(base_sk)
            if not (base_sk in fset and base_sk in double_catch_base_set()):
                cc[base_sk] = max(0, int(cc.get(base_sk,0)) - 1)
                if cc[base_sk] < req and base_sk in fset:
                    fset.remove(base_sk)
            del STATE["roster"][idx]
            STATE["caught_counts"] = cc
            STATE["fulfilled"] = sorted(list(fset))
            save_state(STATE)
        do_rerun()

    st.header("Pokédex (Your Roster)")
    default_level = int(STATE["settings"].get("default_level", 5))

    with st.expander("Sync levels & auto-evolve (ALL roster)", expanded=True):
        with st.form("sync_levels_form", clear_on_submit=False):
            col = st.columns([1,1,1,1])
            target_level = col[0].number_input("Set roster level to", 1, 100, default_level, 1, key="sync_target_level")
            auto_level_evos = col[1].checkbox("Auto level-up evolutions (trade treated as Lv37)", value=True, key="sync_auto")
            rebuild_moves = col[2].checkbox("Rebuild moves after evolution", value=False, key="sync_rebuild")
            apply_btn = col[3].form_submit_button("Apply")
            if apply_btn:
                if not STATE["species_db"]:
                    st.error("Species database empty.")
                else:
                    evo_log = []
                    for mon in STATE["roster"]:
                        mon["level"] = int(target_level)
                        original = mon.get("species","")
                        if auto_level_evos:
                            final = auto_evolve_chain_by_level(original, mon["level"])
                            if final != original and evolve_mon_record(mon, final, rebuild_moves=rebuild_moves):
                                evo_log.append(f"{original} → {final}")
                    STATE["settings"]["default_level"] = int(target_level)
                    save_state(STATE)
                    st.success("Level sync complete." + (f" Evolutions: {'; '.join(evo_log)}" if evo_log else ""))

    with st.expander("Add Pokémon to Pokédex", expanded=False):
        cc = STATE.get("caught_counts", {})
        fset = set(STATE.get("fulfilled", []))

        roster_base_counts: Dict[str,int] = {}
        for mon in STATE.get("roster", []):
            bsk = base_key_for(mon.get("species",""))
            roster_base_counts[bsk] = roster_base_counts.get(bsk, 0) + 1

        def is_base_name_151(name: str) -> bool:
            return base_key_for(name) == species_key(name)

        def available_species_entries() -> List[Tuple[str,str]]:
            entries: List[Tuple[str,str]] = []
            for k in sorted(STATE["species_db"].keys()):
                name = STATE["species_db"][k]["name"]
                if not is_base_name_151(name):
                    continue
                base_sk = species_key(name)
                req = required_catches_for_species(name)
                have_cc = int(cc.get(base_sk, 0))
                have_roster = int(roster_base_counts.get(base_sk, 0))
                have_eff = max(have_cc, have_roster)
                if base_sk in fset or have_eff >= req:
                    continue
                tag = " [trade reward]" if base_sk in TRADE_REWARD_SPECIES else ""
                label = f"{name} ({have_eff}/{req}){tag}"
                entries.append((name, label))
            return entries

        entries = available_species_entries()
        label_to_name = {label: name for name, label in entries}
        species_options = ["(choose)"] + [label for _, label in entries]

        col = st.columns(4)
        choice_label = col[0].selectbox("Species", species_options, key="add_species_choice")
        species_name = label_to_name.get(choice_label, None)
        level = col[1].number_input("Level", 1, 100, int(STATE["settings"].get("default_level", 5)), 1, key="add_level")

        if species_name:
            sk = species_key(species_name)
            sp = STATE["species_db"][sk]
            merged = ensure_learnset(sp.get("name", species_name))
            if merged:
                sp["learnset"] = merged
                STATE["species_db"][sk] = sp
                save_state(STATE)

            st.write("Moves (curated to species family; no duplicates):")
            chosen_names = render_move_quad_ui('add', sp.get('name', species_name), int(level), [])

            if st.button("Add to Pokédex", key="add_btn"):
                entry_moves: List[Tuple[str,str]] = []
                for name in chosen_names:
                    ct = canonical_typed(name)
                    if ct: entry_moves.append(ct)
                entry = {
                    "guid": new_guid(),
                    "species_key": sk,
                    "species": sp["name"],
                    "level": int(level),
                    "types": purge_fairy_types_pair(sp["types"]),
                    "total": sp["total"],
                    "moves": entry_moves,
                }
                STATE["roster"].append(entry)

                have = int(STATE["caught_counts"].get(sk, 0)) + 1
                STATE["caught_counts"][sk] = have
                req = required_catches_for_species(sp["name"])
                fset = set(STATE.get("fulfilled", []))
                if have >= req: fset.add(sk)
                STATE["fulfilled"] = sorted(list(fset))
                save_state(STATE)
                st.success(f"Added {entry['species']}."); do_rerun()
        else:
            st.caption("Pick a species to enter moves.")

    st.markdown("---")
    st.subheader("Current Pokédex")
    if not STATE["roster"]:
        st.info("No Pokémon yet.")
    else:
        roster_sorted = sorted(STATE["roster"], key=lambda m: int(m.get("total",0)), reverse=True)
        for idx, mon in enumerate(roster_sorted):
            name_disp = mon_display_name(mon)
            mstr = ", ".join([f"{mv} ({tp})" for mv,tp in mon.get("moves",[])])
            t1, t2 = mon["types"][0], mon["types"][1]
            st.write(f"{idx+1}. **{name_disp}** — Lv{mon['level']} — {t1}/{t2 or '—'} — Total {mon['total']} — Moves: {mstr}")
            with st.expander(f"Edit / Evolve / Remove {name_disp}", expanded=False):
                with st.form(f"edit_form_{mon['guid']}", clear_on_submit=False):
                    c = st.columns([1,1,4])
                    new_level = c[0].number_input("Level", 1, 100, mon["level"], 1, key=f"edit_lvl_{mon['guid']}")

                    sp = STATE["species_db"].get(mon.get("species_key") or species_key(mon["species"]), {})
                    merged = ensure_learnset(sp.get("name", mon["species"]))
                    if merged:
                        sp["learnset"] = merged
                        STATE["species_db"][species_key(sp.get("name", mon["species"]))] = sp
                        save_state(STATE)

                    names_e: List[str] = [mv for mv,_ in mon.get("moves",[])] or get_prefill_moves(sp, int(new_level))
                    names_e = render_move_quad_ui(f"edit_{mon['guid']}", sp.get('name', mon['species']), int(new_level), names_e)

                    save_btn = c[2].form_submit_button("Save changes", use_container_width=False)

                opts = available_evos_for(mon["species"])
                if opts:
                    labels, targets = [], []
                    for o in opts:
                        if o.get("method") == "level" and isinstance(o.get("level"), int):
                            lab = f"{o['to']} (Lv {o['level']})"
                        elif o.get("method") == "item" and o.get("item"):
                            lab = f"{o['to']} (Use {o['item']})"
                        elif o.get("method") == "trade":
                            lab = f"{o['to']} (Trade — auto at Lv{TRADE_EVOLVE_LEVEL} on sync)"
                        else:
                            lab = f"{o['to']} (Manual)"
                        labels.append(lab); targets.append(o["to"])
                    d1, d2 = st.columns([2,1])
                    choice = d1.selectbox("Evolve to", ["(choose)"] + labels, key=f"evo_choice_{mon['guid']}")
                    rebuild = d2.checkbox("Rebuild moves", value=False, key=f"evo_moves_{mon['guid']}")
                    if st.button("Evolve now", key=f"evo_btn_{mon['guid']}"):
                        if choice == "(choose)":
                            st.error("Pick an evolution target.")
                        else:
                            to = targets[labels.index(choice)]
                            if evolve_mon_record(mon, to, rebuild_moves=rebuild):
                                save_state(STATE); st.success(f"Evolved to {to}."); do_rerun()
                            else:
                                st.error("Evolution failed (species not in database).")
                else:
                    st.caption("No listed evolutions for this species (within 151).")

                rm_col1, rm_col2, _ = st.columns([1,1,6])
                if rm_col2.button("Remove from roster", key=f"rm_{mon['guid']}"):
                    st.session_state["rm_request"] = mon["guid"]
                    do_rerun()

                if save_btn:
                    entry_moves: List[Tuple[str,str]] = []
                    for name in names_e:
                        ct = canonical_typed(name)
                        if ct: entry_moves.append(ct)
                    mon["level"] = int(new_level)
                    mon["moves"] = entry_moves
                    save_state(STATE); st.success("Saved.")
            st.divider()

        ccc1, ccc2 = st.columns(2)
        if ccc1.button("Clear Pokédex (roster only)", key="clear_roster"):
            STATE["roster"] = []; save_state(STATE); st.warning("Pokédex roster cleared. Caught counts preserved.")
        if ccc2.button("Reset caught counts (makes all species available again)", key="reset_counts"):
            STATE["caught_counts"] = {}; STATE["fulfilled"] = []; save_state(STATE); st.warning("Caught counts reset.")

def render_team():

    # Removal handler (moved here so it always runs)
    if "rm_request" in st.session_state:
        guid = st.session_state.pop("rm_request")
        fset = set(STATE.get("fulfilled", []))
        cc = STATE.get("caught_counts", {})
        idx = next((i for i,m in enumerate(STATE["roster"]) if m.get("guid")==guid), None)
        if idx is not None:
            mon = STATE["roster"][idx]
            base_sk = base_key_for(mon.get("species",""))
            req = required_catches_for_species(base_sk)
            if not (base_sk in fset and base_sk in double_catch_base_set()):
                cc[base_sk] = max(0, int(cc.get(base_sk,0)) - 1)
                if cc[base_sk] < req and base_sk in fset:
                    fset.remove(base_sk)
            del STATE["roster"][idx]
            STATE["caught_counts"] = cc
            STATE["fulfilled"] = sorted(list(fset))
            save_state(STATE)
        do_rerun()

    # Allow removal requests triggered from this page
    if "rm_request" in st.session_state:
        guid = st.session_state.pop("rm_request")
        fset = set(STATE.get("fulfilled", []))
        cc = STATE.get("caught_counts", {})
        idx = next((i for i,m in enumerate(STATE["roster"]) if m.get("guid")==guid), None)
        if idx is not None:
            mon = STATE["roster"][idx]
            base_sk = base_key_for(mon.get("species",""))
            req = required_catches_for_species(base_sk)
            if not (base_sk in fset and base_sk in double_catch_base_set()):
                cc[base_sk] = max(0, int(cc.get(base_sk,0)) - 1)
                if cc[base_sk] < req and base_sk in fset:
                    fset.remove(base_sk)
            del STATE["roster"][idx]
            STATE["caught_counts"] = cc
            STATE["fulfilled"] = sorted(list(fset))
            save_state(STATE)
        do_rerun()
    st.header("Pokédex")
    st.caption("Avoid duplicate exact type combinations. Selection uses Total only. Level is ignored.")
    if not STATE["roster"]:
        st.info("Add Pokémon on the Pokédex page first."); return
    # --- Moved from old Pokédex: Sync levels (no auto-evolve) ---
    default_level = int(STATE["settings"].get("default_level", 5))
    
    # Top controls in two columns; everything else remains single-column below.
    col_sync, col_add = st.columns(2)
    with col_sync:
        with st.expander("Sync levels (ALL roster)", expanded=True):
                    with st.form("sync_levels_form", clear_on_submit=False):
                        col = st.columns([1,1])
                        target_level = col[0].number_input("Set roster level to", 1, 100, int(STATE["settings"].get("default_level", 5)), 1, key="sync_target_level")
                        apply_btn = col[1].form_submit_button("Apply")
                        if apply_btn:
                            if not STATE["species_db"]:
                                st.error("Species database empty.")
                            else:
                                for mon in STATE["roster"]:
                                    mon["level"] = int(target_level)
                                STATE["settings"]["default_level"] = int(target_level)
                                save_state(STATE)
                                st.success("Level sync complete.")
                # --- Moved from old Pokédex: Add Pokémon ---
        
        
    with col_add:
        
        with st.expander("Add Pokémon to Pokédex", expanded=False):
                    cc = STATE.get("caught_counts", {})
                    fset = set(STATE.get("fulfilled", []))
        
                    roster_base_counts = {}
                    for mon in STATE.get("roster", []):
                        bsk = base_key_for(mon.get("species",""))
                        roster_base_counts[bsk] = roster_base_counts.get(bsk, 0) + 1
        
                    def is_base_name_151(name: str) -> bool:
                        return base_key_for(name) == species_key(name)
        
                    def available_species_entries():
                        entries = []
                        for k in sorted(STATE["species_db"].keys()):
                            name = STATE["species_db"][k]["name"]
                            if not is_base_name_151(name):
                                continue
                            base_sk = species_key(name)
                            req = required_catches_for_species(name)
                            have_cc = int(cc.get(base_sk, 0))
                            have_roster = int(roster_base_counts.get(base_sk, 0))
                            have_eff = max(have_cc, have_roster)
                            if base_sk in fset or have_eff >= req:
                                continue
                            tag = " [trade reward]" if base_sk in TRADE_REWARD_SPECIES else ""
                            label = f"{name} ({have_eff}/{req}){tag}"
                            entries.append((name, label))
                        return entries
        
                    entries = available_species_entries()
                    label_to_name = {label: name for name, label in entries}
                    species_options = ["(choose)"] + [label for _, label in entries]
        
                    col = st.columns(4)
                    choice_label = col[0].selectbox("Species", species_options, key="add_species_choice")
                    species_name = label_to_name.get(choice_label, None)
                    level = col[1].number_input("Level", 1, 100, int(STATE["settings"].get("default_level", 5)), 1, key="add_level")
                    auto_moves = True  # auto-learnset enforced
        
                    if species_name:
                        sk = species_key(species_name)
                        sp = STATE["species_db"][sk]
                        if auto_moves:
                            merged = rebuild_learnset_for(sp["name"])
                            if merged:
                                sp["learnset"] = merged
                                STATE["species_db"][sk] = sp
                                save_state(STATE)
        
                        suggestions = []
                        if sp.get("learnset"):
                            for lv_str, mv_list in sp["learnset"].items():
                                try:
                                    lv = int((''.join([c for c in lv_str if c.isdigit()]) or "0"))
                                except:
                                    lv = 0
                                if lv <= int(level):
                                    seq = mv_list if isinstance(mv_list, list) else [mv_list]
                                    for m in seq:
                                        nm = (lookup_move(m) or {}).get("name", clean_move_token(m))
                                        if nm and move_is_damaging(nm):
                                            suggestions.append(nm)
                        suggestions = sorted(set(suggestions), key=lambda s: s.lower())
                        all_moves = all_damaging_moves_sorted()
        
                        prefill = get_prefill_moves(sp, int(level)) if auto_moves else []
                        while len(prefill) < 4:
                            prefill.append("(none)")
        
                        st.write("Moves (dropdowns with type auto-filled):")
                        mcols = st.columns(4)
                        chosen_names = []
                        for i in range(4):
                            opts = ["(none)"] + suggestions + ["— all moves —"] + all_moves
                            default_val = prefill[i] if i < len(prefill) else "(none)"
                            if default_val not in opts:
                                opts.insert(1, default_val)
                            sel = mcols[i].selectbox(f"Move {i+1}", opts, index=opts.index(default_val), key=f"add_mv_{i}")
                            typed = canonical_typed(sel)
                            mcols[i].caption(f"Type: {typed[1] if typed else '—'}")
                            chosen_names.append(sel)
        
                        if st.button("Add to Pokédex", key="add_btn"):
                            entry_moves = []
                            for name in chosen_names:
                                ct = canonical_typed(name)
                                if ct:
                                    entry_moves.append(ct)
                                    ensure_move_in_db(ct[0], default_type=ct[1])
        
                            entry = {
                                "guid": new_guid(),
                                "species_key": sk,
                                "species": sp["name"],
                                "level": int(level),
                                "types": purge_fairy_types_pair(sp["types"]),
                                "total": sp["total"],
                                "moves": entry_moves,
                            }
                            STATE["roster"].append(entry)
        
                            have = int(STATE["caught_counts"].get(sk, 0)) + 1
                            STATE["caught_counts"][sk] = have
                            req = required_catches_for_species(sp["name"])
                            fset = set(STATE.get("fulfilled", []))
                            if have >= req:
                                fset.add(sk)
        
                            STATE["fulfilled"] = sorted(list(fset))
                            save_state(STATE)
                            st.success(f"Added {entry['species']}.")
                            do_rerun()
                    else:
                        st.caption("Pick a species to enter moves.")
    st.markdown("---")
    st.markdown("---")

    
    def sig_of(mon) -> Tuple[Optional[str], Optional[str]]:
        t = purge_fairy_types_pair(mon.get("types", []))
        return (t[0], t[1])

    # ---- Lock UI
    st.subheader("Lock / Unlock")
    guid_to_label = {m["guid"]: f"{mon_display_name(m)} • Total {m.get('total',0)} • {sig_of(m)[0]}/{sig_of(m)[1] or '—'}" for m in STATE["roster"]}
    with st.form("locks_form", clear_on_submit=False):
        opts = [m["guid"] for m in STATE["roster"]]
        current_locks = st.multiselect("Locked entries (always on team while slots remain)", options=opts, default=list(STATE.get("locks", [])), format_func=lambda g: guid_to_label.get(g,g))
        if st.form_submit_button("Save locks"):
            STATE["locks"] = list(current_locks); save_state(STATE); st.success("Locks saved."); do_rerun()

    unique_sig = bool(STATE["settings"].get("unique_sig", True))
    lock_ids = set(STATE.get("locks", []))

    def sort_key(m):
        return (-int(m.get("total",0)), m.get("species",""))

    roster_sorted = sorted(STATE["roster"], key=sort_key)

    # 1) Start with locks
    picks_locked: List[Dict] = [m for m in roster_sorted if m["guid"] in lock_ids]
    used_sigs = set(sig_of(m) for m in picks_locked)
    target_slots = min(6, len(STATE["roster"]))
    needed = max(0, target_slots - len(picks_locked))

    # 2) Build eligible pool respecting unique signatures
    eligible: List[Dict] = []
    seen_sig = set()
    for m in roster_sorted:
        if m in picks_locked: continue
        s = sig_of(m)
        if unique_sig:
            if s in used_sigs or s in seen_sig: continue
            seen_sig.add(s)
        eligible.append(m)

    # ---- Tiebreaker math
    at: List[Dict] = []
    above: List[Dict] = []
    slots_for_tie = 0
    signature = ""
    if needed > 0 and len(eligible) >= needed:
        cut_total = int(eligible[needed-1].get("total",0))
        above = [m for m in eligible if int(m.get("total",0)) > cut_total]
        at = [m for m in eligible if int(m.get("total",0)) == cut_total]
        slots_for_tie = max(0, needed - len(above))
        if slots_for_tie >= 1 and len(at) > slots_for_tie:
            signature = "T|" + str(slots_for_tie) + "|" + "|".join(sorted(m["guid"] for m in at))

    # ---- Tiebreaker UI (always visible when a tie exists)
    selected_ids: List[str] = []
    if signature:
        mem = STATE.get("team_tie_memory", {"signature":"", "selected":[]})
        saved_valid = (mem.get("signature") == signature and len([x for x in mem.get("selected",[]) if x in {m['guid'] for m in at}]) == slots_for_tie)
        default_selection = mem.get("selected", []) if saved_valid else [m["guid"] for m in at][:slots_for_tie]

        st.warning(f"Tie detected for the final {slots_for_tie} slot(s). Choose who makes the team.")
        labels = {m["guid"]: f"{mon_display_name(m)} • Total {m.get('total',0)} • {sig_of(m)[0]}/{sig_of(m)[1] or '—'}" for m in at}
        if slots_for_tie == 1:
            current = default_selection[0] if default_selection else (at[0]["guid"] if at else None)
            choice = st.radio("Pick one", options=[m["guid"] for m in at], index=[m["guid"] for m in at].index(current), format_func=lambda g: labels.get(g,g), key="tie_radio")
            selected_ids = [choice]
        else:
            selected_ids = st.multiselect(
                f"Select exactly {slots_for_tie}",
                options=[m["guid"] for m in at],
                default=default_selection,
                format_func=lambda g: labels.get(g,g),
                key="tie_multi"
            )
        c1, c2, c3 = st.columns([1,1,3])
        apply = c1.button("Apply tiebreaker")
        reset = c2.button("Reset")
        if reset:
            STATE["team_tie_memory"] = {"signature":"", "selected":[]}
            save_state(STATE); do_rerun()
        if apply:
            if len(selected_ids) != slots_for_tie:
                st.error(f"Select exactly {slots_for_tie}.")
            else:
                STATE["team_tie_memory"] = {"signature": signature, "selected": list(selected_ids)}
                save_state(STATE); do_rerun()

    # ---- Build final team using tiebreaker selection (or default deterministic pick)
    chosen_from_tie: List[Dict] = []
    if signature:
        idmap = {m["guid"]: m for m in at}
        mem = STATE.get("team_tie_memory", {"signature":"", "selected":[]})
        if mem.get("signature") == signature and len(mem.get("selected",[])) == slots_for_tie:
            chosen_from_tie = [idmap[g] for g in mem["selected"] if g in idmap]
        else:
            chosen_from_tie = at[:slots_for_tie]

    team_core = picks_locked[:]
    if needed > 0:
        team_core += above
        if len(team_core) < target_slots:
            team_core += chosen_from_tie
        # If still short (no tie or partial), fill with remaining eligible
        if len(team_core) < target_slots:
            remaining = [m for m in eligible if m not in team_core]
            team_core += remaining
    team = team_core[:target_slots]

    # Render team
    
    st.subheader("Your Team")
    for i, mon in enumerate(team[:6]):
        name_disp = mon_display_name(mon)
        t = purge_fairy_types_pair(mon["types"])
        t1, t2 = t[0], t[1]
        st.write(f"{i+1}. **{name_disp}** — {t1}/{t2 or '—'} — Total {mon['total']}")
        # Quick edit moves directly on Pokédex page (team entries)
        with st.expander(f"Quick edit moves • {name_disp}", expanded=False):
            sp = STATE["species_db"].get(mon.get("species_key") or species_key(mon["species"]), {})
            merged = ensure_learnset(sp.get("name", mon["species"]))
            if merged:
                sp["learnset"] = merged
                STATE["species_db"][species_key(sp.get("name", mon["species"]))] = sp
                save_state(STATE)
    
            current_moves = [mv for mv,_ in mon.get("moves",[])] or get_prefill_moves(sp, int(mon.get("level",1)))
            picks_local = render_move_quad_ui(f"team_{mon['guid']}", sp.get('name', mon['species']), int(mon.get('level',1)), current_moves)
    
            if st.button("Save team moves", key=f"team_save_{mon['guid']}"):
                entry_moves: List[Tuple[str,str]] = []
                for name in picks_local:
                    ct = canonical_typed(name)
                    if ct:
                        entry_moves.append(ct)
                        ensure_move_in_db(ct[0], default_type=ct[1])
                mon["moves"] = entry_moves
                save_state(STATE)
                st.success("Team moves saved.")
    
            st.markdown("---")
            if st.button("Remove from Pokédex", key=f"rm_team_inside_{mon['guid']}"):
                st.session_state["rm_request"] = mon["guid"]
                do_rerun()
    
    st.session_state["active_team"] = team[:6]
    
    st.markdown("---")
    st.subheader("Pokédex (not on team)")
    bench = [m for m in STATE["roster"] if m["guid"] not in {x['guid'] for x in team[:6]}]
    if bench:
        bench_sorted = sorted(bench, key=sort_key)
        for idx, mon in enumerate(bench_sorted, start=1):
            name_disp = mon_display_name(mon)
            t1, t2 = purge_fairy_types_pair(mon["types"])
            st.write(f"{idx}. **{name_disp}** — {t1}/{t2 or '—'} — Total {mon['total']}")
            with st.expander(f"Edit moves • {name_disp}", expanded=False):
                sp = STATE["species_db"].get(mon.get("species_key") or species_key(mon["species"]), {})
                merged = ensure_learnset(sp.get("name", mon["species"]))
                if merged:
                    sp["learnset"] = merged
                    STATE["species_db"][species_key(sp.get("name", mon["species"]))] = sp
                    save_state(STATE)
                current_moves = [mv for mv,_ in mon.get("moves",[])] or get_prefill_moves(sp, int(mon.get("level",1)))
                picks_local = render_move_quad_ui(f"roster_{mon['guid']}", sp.get('name', mon['species']), int(mon.get('level',1)), current_moves)
                if st.button(f"Save moves • {name_disp}", key=f"roster_save_{mon['guid']}"):
                    entry_moves: List[Tuple[str,str]] = []
                    for name in picks_local:
                        ct = canonical_typed(name)
                        if ct: entry_moves.append(ct)
                    mon["moves"] = entry_moves
                    save_state(STATE); st.success("Saved.")
                st.markdown("---")
                if st.button("Remove from Pokédex", key=f"rm_bench_inside_{mon['guid']}"):
                    st.session_state["rm_request"] = mon["guid"]
                    do_rerun()
    else:
        st.caption("No unused Pokémon — your team already includes all available.")
    
    
def get_mult(move_type: str, defender_types: Tuple[Optional[str], Optional[str]]) -> float:
    if not move_type: return 1.0
    move_type = normalize_type(move_type) or "Normal"
    m = 1.0
    for dt in defender_types:
        if dt:
            d = normalize_type(dt) or "Normal"
            m *= TYPE_CHART.get(move_type,{}).get(d,1.0)
    if m <= 0.0: return 0.0
    for v in (4.0,2.0,1.0,0.5,0.25):
        if abs(m-v) < 1e-9: return v
    if m >= 3.0: return 4.0
    if m >= 1.5: return 2.0
    if m <= 0.375: return 0.25
    if m <= 0.75: return 0.5
    return 1.0

def score_offense(mult: float) -> int: return OFFENSE_SCORE.get(mult,0)
def score_defense(mult: float) -> int: return DEFENSE_SCORE.get(mult,0)

def render_matchup():
    st.header("Battle")
    team = st.session_state.get("active_team", STATE["roster"][:6])
    if not team:
        st.info("Build a team on the Team page."); return

    if not STATE["opponents"]["encounters"]:
        st.warning("No opponents loaded yet. Trying to load your default sheet…")
        autoload_opponents_if_empty()
    if not STATE["opponents"]["encounters"]:
        st.error("Could not load opponents from the sheet automatically.")
        return

    with st.form("sheet_pick_form", clear_on_submit=False):
        enc_options = [f"{i+1}. {enc['label']}" for i, enc in enumerate(STATE["opponents"]["encounters"])]
        default_enc_idx, default_mon_idx = STATE["opponents"]["meta"].get("last_pick",[0,0])
        pick = st.selectbox("Encounter (trainer)", enc_options, index=min(default_enc_idx, max(0,len(enc_options)-1)))
        selected_enc_idx = enc_options.index(pick)
        enc = STATE["opponents"]["encounters"][selected_enc_idx]
        mon_labels = [f"{i+1}. {m['species']} Lv{m['level']} (Total {m.get('total',0)})" for i, m in enumerate(enc["mons"])]
        pick_mon = st.selectbox("Their Pokémon", mon_labels, index=min(default_mon_idx, max(0,len(mon_labels)-1)))
        apply_pick = st.form_submit_button("Load encounter")

    if 'cached_sheet_pick' not in st.session_state:
        st.session_state['cached_sheet_pick'] = None
    if apply_pick:
        st.session_state['cached_sheet_pick'] = (selected_enc_idx, mon_labels.index(pick_mon))
        STATE["opponents"]["meta"]["last_pick"] = [selected_enc_idx, mon_labels.index(pick_mon)]
        save_state(STATE)
    if st.session_state['cached_sheet_pick'] is None:
        st.session_state['cached_sheet_pick'] = (default_enc_idx, default_mon_idx)

    selected_enc_idx, selected_mon_idx = st.session_state['cached_sheet_pick']
    selected_enc_idx = min(selected_enc_idx, max(0,len(STATE["opponents"]["encounters"])-1))
    enc = STATE["opponents"]["encounters"][selected_enc_idx]
    selected_mon_idx = min(selected_mon_idx, max(0,len(enc["mons"])-1))
    opmon = enc["mons"][selected_mon_idx]
    t1, t2 = purge_fairy_types_pair(opmon["types"])
    opp_types = (t1, t2)
    opp_pairs = [(mv, normalize_type(tp) or "") for mv,tp in opmon["moves"]]
    mon_labels = [f"{i+1}. {m['species']} Lv{m['level']} (Total {m.get('total',0)})" for i, m in enumerate(enc["mons"]) ]
    opp_label = enc["label"] + " — " + mon_labels[selected_mon_idx]
    opp_total = opmon.get("total", 0)
    moves_str = ", ".join([f"{n}({t})" for n,t in opp_pairs]) if opp_pairs else "—"
    st.caption(f"Opponent: **{opp_label}** | Types: {t1 or '—'} / {t2 or '—'} | Total: {opp_total} | Moves: {moves_str}")

    b1, b2 = st.columns(2)
    if b1.button("✅ Beat Pokémon (remove just this one)"):
        try:
            if len(enc["mons"]) == 1:
                label_before = enc["label"]
                count = len(enc["mons"])
                STATE["opponents"]["cleared"].append({
                    "what": "trainer",
                    "trainer": label_before,
                    "count": count,
                    "data": enc
                })
                STATE["opponents"]["encounters"].pop(selected_enc_idx)
            else:
                label_before = enc["label"]
                beaten = enc["mons"].pop(selected_mon_idx)
                STATE["opponents"]["cleared"].append({"what":"pokemon","trainer":label_before,"species":beaten.get("species"),"level":beaten.get("level"),"row":beaten.get("source_row"),"index":selected_mon_idx,"data":beaten})
            save_state(STATE)
            st.session_state['cached_sheet_pick'] = None
            do_rerun()
        except Exception as e:
            st.error(f"Failed to remove: {e}")

    if b2.button("🧹 Beat Trainer (remove entire encounter)"):
        try:
            label_before = STATE["opponents"]["encounters"][selected_enc_idx]["label"]
            STATE["opponents"]["cleared"].append({"what":"trainer","trainer":label_before,"count":len(enc["mons"]),"index":selected_enc_idx,"data":enc})
            STATE["opponents"]["encounters"].pop(selected_enc_idx)
            save_state(STATE)
            st.session_state['cached_sheet_pick'] = None
            do_rerun()
        except Exception as e:
            st.error("Failed to remove trainer: {}".format(e))

    
    with st.expander("Cleared log (latest 15)", expanded=False):
        # Helpers for undo visibility and action
        def _enc_by_label(lbl):
            for e in STATE["opponents"]["encounters"]:
                if e.get("label") == lbl:
                    return e
            return None
        def _same_mon(a, b):
            return (a.get("species") == b.get("species")
                    and int(a.get("level", 0)) == int(b.get("level", 0))
                    and a.get("source_row") == b.get("source_row"))
        def _can_undo_item(item):
            try:
                data = item.get("data")
                if not data:
                    return False
                if item.get("what") == "trainer":
                    lbl = data.get("label") or item.get("trainer")
                    return _enc_by_label(lbl) is None
                if item.get("what") == "pokemon":
                    lbl = item.get("trainer")
                    enc = _enc_by_label(lbl)
                    if enc is None:
                        return False
                    for m in enc.get("mons", []):
                        if _same_mon(m, data):
                            return False
                    return True
                return False
            except Exception:
                return False
        def _undo_item(item):
            try:
                if item.get("what") == "trainer" and item.get("data"):
                    idx = int(item.get("index", len(STATE["opponents"]["encounters"])))
                    idx = max(0, min(idx, len(STATE["opponents"]["encounters"])))
                    STATE["opponents"]["encounters"].insert(idx, item["data"])
                    save_state(STATE)
                    return True
                if item.get("what") == "pokemon" and item.get("data"):
                    enc = _enc_by_label(item.get("trainer"))
                    if enc is None:
                        return False
                    mons = enc.setdefault("mons", [])
                    idx = int(item.get("index", len(mons)))
                    idx = max(0, min(idx, len(mons)))
                    mons.insert(idx, item["data"])
                    save_state(STATE)
                    return True
                return False
            except Exception:
                return False

        log = STATE["opponents"].get("cleared", [])
        if not log:
            st.caption("— empty —")
        else:
            for idx, item in enumerate(list(reversed(log[-15:]))):
                if item.get("what") == "pokemon":
                    st.write(f"• Beat Pokémon: {item.get('species')} (Lv{item.get('level')}) — Trainer: {item.get('trainer')}")
                    if _can_undo_item(item) and st.button("Undo", key=f"undo_p_{idx}"):
                        if _undo_item(item):
                            st.success("Restored Pokémon to encounter."); do_rerun()
                        else:
                            st.error("Could not undo this entry.")
                else:
                    st.write(f"• Beat Trainer: {item.get('trainer')} — removed {item.get('count',0)} Pokémon")
                    if _can_undo_item(item) and st.button("Undo", key=f"undo_t_{idx}"):
                        if _undo_item(item):
                            st.success("Restored trainer encounter."); do_rerun()
                        else:
                            st.error("Could not undo this entry.")
# ---- Scoring
    def compute_best_offense(my_moves, opp_types):
        detail = []; best_score = -9999; best_move = None; best_mult = 1.0
        for mv, t in my_moves:
            mult = get_mult(t, opp_types); sc = score_offense(mult)
            detail.append({"move": mv, "type": t, "mult": mult, "score": sc})
            if sc > best_score: best_score, best_move, best_mult = sc, mv, mult
        if best_move is None: best_score, best_move, best_mult = 0, None, 1.0
        return (best_score, best_move, best_mult), detail

    def compute_their_best_vs_me(opp_moves, my_types):
        detail = []
        if not opp_moves: return (0, None, 1.0), detail
        best_score = 9999; best_move = None; best_mult = 1.0
        for mv, t in opp_moves:
            mult = get_mult(t, my_types); sc = score_defense(mult)
            detail.append({"move": mv, "type": t, "mult": mult, "score": sc})
            if sc < best_score: best_score, best_move, best_mult = sc, mv, mult
        return (best_score, best_move, best_mult), detail

    results = []
    for mon in team:
        tpair = purge_fairy_types_pair(mon["types"])
        my_types = (tpair[0], tpair[1])
        my_total = mon.get("total", 0)
        sp = STATE["species_db"].get(mon.get("species_key") or species_key(mon["species"]), {})
        if not sp.get("learnset"):
            sp["learnset"] = ensure_learnset(sp.get("name", mon["species"]))
            STATE["species_db"][species_key(sp.get("name", mon["species"]))] = sp; save_state(STATE)
        my_moves = [(mv, normalize_type(tp) or "") for mv,tp in mon.get("moves", [])]
        if not my_moves and sp.get("learnset"):
            learned = last_four_moves_by_level(sp.get("learnset"), int(mon["level"]))
            typed = []
            for m in learned:
                ct = canonical_typed(m)
                if ct: typed.append(ct)
            my_moves = typed
        (off_sc, off_move, off_mult), off_rows = compute_best_offense(my_moves, opp_types)
        (def_sc, def_move, def_mult), def_rows = compute_their_best_vs_me(opp_pairs, my_types)
        total = off_sc + def_sc
        results.append({
            "mon": mon, "my_total": my_total, "opp_total": opmon.get("total",0),
            "off": (off_sc, off_move, off_mult), "def": (def_sc, def_move, def_mult),
            "off_rows": off_rows, "def_rows": def_rows, "total_score": total
        })
    results.sort(key=lambda r: r["total_score"], reverse=True)
    st.markdown("---")
    st.subheader("Results (sorted by best total vs opponent)")
    for r in results:
        mon = r["mon"]
        off_sc, off_move, off_mult = r["off"]
        def_sc, def_move, def_mult = r["def"]
        total = r["total_score"]
        totals_text = f"(Your Total: {r['my_total']}" + (f" vs Opp Total: {r['opp_total']}" if r["opp_total"] is not None else "") + ")"
        st.markdown(
            f"**{mon_display_name(mon)}** — {totals_text} — "
            f"Offense: **{off_sc}** (best: {off_move} ×{off_mult}) | "
            f"Defense: **{def_sc}** (their best vs you: {def_move} ×{def_mult}) → **Total {total}**"
        )
        off_rows = r["off_rows"]; def_rows = r["def_rows"]
        if off_rows:
            max_off = max(row["score"] for row in off_rows) if off_rows else 0
            off_table = [{
                "Move": (row["move"] or "—") + (" ★" if row["score"] == max_off and row["move"] else ""),
                "Type": row["type"] or "?",
                "Effectiveness": f"x{row['mult']}",
                "Offense Score": row["score"],
            } for row in sorted(off_rows, key=lambda x: (-x["score"], x["move"] or ""))]
            st.caption("Your moves vs them:"); st.table(off_table)
        else:
            st.caption("Your moves vs them: —")
        if def_rows:
            min_def = min(row["score"] for row in def_rows) if def_rows else 0
            def_table = [{
                "Opp Move": (row["move"] or "—") + (" ★" if row["score"] == min_def and row["move"] else ""),
                "Type": row["type"] or "?",
                "Effectiveness vs you": f"x{row['mult']}",
                "Defense Score": row["score"],
            } for row in sorted(def_rows, key=lambda x: (x["score"], x["move"] or ""))]
            st.caption("Their moves vs you:"); st.table(def_table)
        else:
            st.caption("Their moves vs you: — (no opponent moves)")

def get_species_total(name: str) -> int:
    sk = species_key(name)
    rec = STATE["species_db"].get(sk)
    return int(rec["total"]) if rec and isinstance(rec.get("total"),int) else 0

def render_evo_watch():
    st.header("Evolution Watch")

    if not STATE["roster"]:
        st.info("No Pokémon yet."); return

    # ---- Stone inventory controls
    st.subheader("Stone inventory")
    ecols = st.columns(5)
    for i, stone in enumerate(STONE_ITEMS):
        c = ecols[i % 5]
        cur = int(STATE["stones"].get(stone, 0))
        c.markdown(f"**{stone_with_emoji(stone)}**: {cur}")
        cc1, cc2 = c.columns(2)
        if cc1.button("− Remove", key=f"st_dec_{stone.replace(' ','_')}"):
            if STATE["stones"].get(stone,0) > 0:
                STATE["stones"][stone] -= 1; save_state(STATE); do_rerun()
        if cc2.button("Add +", key=f"st_inc_{stone.replace(' ','_')}"):
            STATE["stones"][stone] = int(STATE["stones"].get(stone,0)) + 1; save_state(STATE); do_rerun()

    c1, c2 = st.columns(2)
    show_ready_only = c1.checkbox("Show only 'Ready' evolutions", value=False, key="evo_ready_only")
    rebuild_moves_default = False

    def evo_row(mon, opt):
        lvl = int(mon.get("level", 1))
        method = opt.get("method")
        to_name = opt.get("to", "?")
        req_txt = "—"; status_txt = "Manual"; ready = True; badge_class = "b-manual"; req_level_val = 0; item = None

        if method == "level" and isinstance(opt.get("level"), int):
            req = opt["level"]
            req_txt = f"Lv {req}"
            ready = lvl >= req
            status_txt = "Ready" if ready else f"Needs Lv {req}"
            badge_class = "b-level"; req_level_val = req
        elif method == "item":
            item = opt.get("item") or "Use item"
            req_txt = stone_with_emoji(item)
            have = int(STATE["stones"].get(item, 0))
            ready = have > 0
            status_txt = f"{'Ready' if ready else 'Need'} {item} (you have {have})"
            badge_class = "b-item"; req_level_val = 0
        elif method == "trade":
            req_txt = "Trade"
            ready = lvl >= TRADE_EVOLVE_LEVEL
            status_txt = f"Ready (Lv{TRADE_EVOLVE_LEVEL})" if ready else f"Trade or reach Lv{TRADE_EVOLVE_LEVEL}"
            badge_class = "b-trade"; req_level_val = TRADE_EVOLVE_LEVEL
        else:
            ready = True; status_txt = "Manual"; badge_class = "b-manual"; req_level_val = 0

        to_total = get_species_total(to_name)
        from_total = int(mon.get("total", 0))

        return {
            "to": to_name, "method": method or "manual", "req_txt": req_txt, "ready": ready,
            "status": status_txt, "badge": badge_class, "req_level": req_level_val,
            "item": item, "from_total": from_total, "to_total": to_total
        }

    def method_bucket(r):
        if r["ready"]: return 0
        if r["method"] == "item": return 1
        if r["method"] in ("level", "trade"): return 2
        return 3

    mon_cards = []
    for mon in STATE["roster"]:
        species = mon.get("species","?")
        lvl = int(mon.get("level",1))
        opts = available_evos_for(species) or []
        rows = [evo_row(mon, o) for o in opts]
        rows.sort(key=lambda r: (method_bucket(r), 0 if r["method"] == "item" else (r["req_level"] if r["method"] in ("level","trade") else 999), r["to"]))
        mon_cards.append((mon, rows, lvl))

    def mon_bucket_and_delta(rows, lvl):
        has_ready = any(r["ready"] and r["method"] in ("level","trade","item") for r in rows)
        if has_ready: return (0, 0)
        has_item = any(r["method"] == "item" for r in rows)
        if has_item: return (1, 0)
        deltas = []
        for r in rows:
            if r["method"] == "level": deltas.append(max(0, r["req_level"] - lvl))
            elif r["method"] == "trade": deltas.append(max(0, TRADE_EVOLVE_LEVEL - lvl))
        return (2, min(deltas) if deltas else 999)

    mon_cards.sort(key=lambda tup: (mon_bucket_and_delta(tup[1], tup[2])[0],
                                    mon_bucket_and_delta(tup[1], tup[2])[1],
                                    tup[0]["species"].lower()))

    # Full-width one per row
    ncols = 1
    for i in range(0, len(mon_cards), ncols):
        cols = st.columns(ncols)
        for j in range(ncols):
            if i + j >= len(mon_cards): break
            mon, rows, lvl = mon_cards[i + j]
            species = mon.get("species","?")
            if show_ready_only:
                rows = [r for r in rows if r["ready"]]
            with cols[j].container(border=True):
                st.markdown(f"**{species} • Lv{lvl}**")
                if not rows:
                    st.caption("No evolutions listed or none match filter."); continue

                h1, h2, h3, h4, h5, h6 = st.columns([3,2,2,3,2,2])
                h1.markdown("**Target**")
                h2.markdown("**Method**")
                h3.markdown("**Requirement**")
                h4.markdown("**Status**")
                h5.markdown("**Totals**")
                h6.markdown("**Action**")

                for idx, r in enumerate(rows):
                    c1, c2, c3, c4, c5, c6 = st.columns([3,2,2,3,2,2])
                    c1.write(r["to"])
                    method_pretty = {"level":"Level","item":"Use Item","trade":"Trade","manual":"Manual"}[r["method"]]
                    c2.markdown(f"<span class='badge {r['badge']}'>{method_pretty}</span>", unsafe_allow_html=True)
                    c3.write(r["req_txt"])
                    c4.markdown(f"<span class='badge {'b-ready' if r['ready'] else 'b-wait'}'>{r['status']}</span>", unsafe_allow_html=True)
                    c5.write(f"{r['from_total']} → {r['to_total']}")
                    if r["ready"]:
                        if c6.button(f"Evolve → {r['to']}", key=f"evo_watch_btn_{mon['guid']}_{idx}"):
                            # If item evolution, consume stone
                            if r["method"] == "item" and r.get("item") in STONE_ITEMS:
                                if STATE["stones"].get(r["item"], 0) <= 0:
                                    st.error(f"No {r['item']} left."); do_rerun()
                                else:
                                    STATE["stones"][r["item"]] -= 1; save_state(STATE)
                            if evolve_mon_record(mon, r["to"], rebuild_moves=rebuild_moves_default):
                                save_state(STATE); st.success(f"Evolved into {r['to']}."); do_rerun()
                            else:
                                st.error("Evolution failed (species not in database).")
                    else:
                        c6.caption("—")



def render_saveload():
    st.header("Save / Load")
    st.caption("All data lives in 'state.json' next to app.py. Atomic save with backup.")
    if st.button("Force save now"):
        save_state(STATE); st.success("Saved to state.json")
    st.download_button("Download current state.json", data=json.dumps(STATE,indent=2), file_name="state.json")
    up = st.file_uploader("Load state.json", type=["json"])
    if up is not None:
        try:
            data = json.load(up)
            if not isinstance(data, dict): raise ValueError("bad json")
            data = migrate_state(data)
            STATE.clear(); STATE.update(data); save_state(STATE)
            st.success("State loaded.")
        except Exception as e:
            st.error(f"Failed to load: {e}")

def render_moves_db():
    st.header("Moves DB (override / add types)")
    st.caption("Used only when a move’s type isn’t known from Showdown. Non-damaging moves are ignored elsewhere.")
    with st.form("add_move_form"):
        c1, c2 = st.columns(2)
        mv = c1.text_input("Move name")
        tp = c2.selectbox("Type", [""]+TYPES, index=0)
        if st.form_submit_button("Add / Update"):
            if mv and tp:
                mk = norm_key(clean_move_token(mv))
                STATE["moves_db"][mk] = {"name": clean_move_token(mv), "type": normalize_type(tp)}
                save_state(STATE); st.success(f"Saved: {clean_move_token(mv)} → {tp}")
            else:
                st.error("Provide both name and type.")
    if STATE["moves_db"]:
        st.markdown("---"); st.subheader("Custom entries")
        for k in sorted(STATE["moves_db"].keys()):
            rec = STATE["moves_db"][k]
            st.write(f"• {rec['name']} — {rec.get('type','?')}")
        if st.button("Clear custom entries"):
            STATE["moves_db"] = {}; save_state(STATE); st.warning("Cleared custom moves.")
    else:
        st.info("No custom entries yet.")

def render_species_db():
    st.header("Species DB (Advanced Viewer)")
    st.caption("Base 151 dataset only (base forms). No Fairy typing anywhere.")
    q = st.text_input("Filter (name contains)", value="")
    keys = sorted(STATE["species_db"].keys())
    shown = 0
    for k in keys:
        sp = STATE["species_db"][k]
        if q and q.lower() not in sp["name"].lower(): continue
        shown += 1
        t1, t2 = sp["types"]
        st.write(f"{shown}. **{sp['name']}** — {t1}/{t2 or '—'} — Total {sp['total']}")
    if shown == 0: st.caption("No matches.")

def render_opponents():
    st.header("Opponents (auto-loaded)")
    if STATE["opponents"]["encounters"]:
        st.write(f"Loaded trainers: {len(STATE['opponents']['encounters'])}")
        for i, enc in enumerate(STATE["opponents"]["encounters"][:50]):
            st.write(f"{i+1}. **{enc['label']}** — {len(enc['mons'])} Pokémon")
        if st.button("Reload from default sheet"):
            STATE["opponents"]["encounters"] = []; save_state(STATE)
            autoload_opponents_if_empty()
            if STATE["opponents"]["encounters"]: st.success("Reloaded from default sheet.")
            else: st.error("Reload failed.")
    else:
        if st.button("Load from default sheet"):
            autoload_opponents_if_empty()
            if STATE["opponents"]["encounters"]: st.success("Loaded.")
            else: st.error("Failed to load.")

# =========================
# Sidebar routing
# =========================
ensure_bootstrap_ready()

PAGE_REGISTRY = [("pokedex", "Pokédex", render_team),
    ("matchup", "Battle", render_matchup),
    ("evo", "Evolution Watch", render_evo_watch),
    ("opponents", "Opponents", render_opponents),
    ("moves", "Moves DB", render_moves_db),
    ("species", "Species DB (Advanced)", render_species_db),
    ("saveload", "Save/Load", render_saveload),
    
]

vis = STATE["settings"].get("visible_pages", {})
options = []; labels = []
for pid, label, _fn in PAGE_REGISTRY:
    if pid in ("pokedex","settings") or vis.get(pid, False):
        options.append(pid); labels.append(label)

st.sidebar.title("FR/LG Companion App")
if not options: options = ["pokedex"]; labels = ["Pokédex"]

try:
    default_index = options.index("pokedex")
except ValueError:
    default_index = 0
if "last_page" in st.session_state:
    try:
        default_index = options.index(st.session_state["last_page"])
    except ValueError:
        pass

page_choice = st.sidebar.radio("Go to", options=options, format_func=lambda pid: labels[options.index(pid)], index=default_index)
st.session_state["last_page"] = page_choice

for pid, label, fn in PAGE_REGISTRY:
    if pid == page_choice:
        fn(); break
