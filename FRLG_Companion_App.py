
import streamlit as st
from typing import List, Dict, Tuple, Optional
import json, os, urllib.request, ssl, re, csv, uuid
from urllib.parse import urlparse, parse_qs

st.set_page_config(page_title="FR/LG Companion App", layout="wide")


import tempfile
if "session_uid" not in st.session_state:
    st.session_state["session_uid"] = uuid.uuid4().hex
_SESSION_DIR = os.path.join(tempfile.gettempdir(), f"frlg_{st.session_state['session_uid']}")
os.makedirs(_SESSION_DIR, exist_ok=True)
STATE_PATH = os.path.join(_SESSION_DIR, "state.json")
STATE_BAK = os.path.join(_SESSION_DIR, "state.backup.json")
STATE_TMP = os.path.join(_SESSION_DIR, "state.tmp.json")
STATE_RECOVERED = False
STATE_RESET = False
# =============================================================================
# Constants
# =============================================================================
TYPES = [
    "Normal","Fire","Water","Electric","Grass","Ice","Fighting","Poison",
    "Ground","Flying","Psychic","Bug","Rock","Ghost","Dragon","Dark","Steel"
]

STONE_EMOJI = {
    "Fire Stone": "🔥", "Water Stone": "💧", "Thunder Stone": "⚡",
    "Leaf Stone": "🍃", "Moon Stone": "🌙", "Sun Stone": "☀️"
}
def stone_with_emoji(name: str) -> str:
    return f"{STONE_EMOJI.get(name, '🪨')} {name}" if name else name

TRADE_EVOLVE_LEVEL = 37

OFFENSE_SCORE = {4.0: 4, 2.0: 2, 1.0: 0, 0.5: -2, 0.25: -4, 0.0: -5}
DEFENSE_SCORE  = {4.0:-4, 2.0:-2, 1.0: 0, 0.5:  2, 0.25:  4, 0.0:  5}

DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/u/0/d/1frqW2CeHop4o0NP6Ja_TAAPPkGIrvxkeQJBfyxFggyk/htmlview?pli=1#gid=422900446"

# STATE_PATH = "state.json"
# STATE_BAK  = "state.backup.json"

# =============================================================================
# Globals (in-memory)
# =============================================================================
MOVES_MASTER: Dict[str, Dict] = {}
MOVES_BY_NAME: Dict[str, Dict] = {}
MOVES_BY_ID: Dict[str, Dict] = {}
EVOS: Dict[str, List[Dict]] = {}

TRADE_REWARD_SPECIES = {"mrmime","farfetchd","jynx","lickitung"}

# =============================================================================
# Small utils
# =============================================================================
def new_guid() -> str:
    return uuid.uuid4().hex

def do_rerun():
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
            pass

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

def normalize_type(t: Optional[str]) -> Optional[str]:
    if not t: return None
    t = str(t).title()
    if t == "Fairy":  # collapse to Normal for Gen3 math
        return "Normal"
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
        if tt not in candidates:
            candidates.append(tt)
    if not candidates:
        candidates = ["Normal"]
    if len(candidates) == 1:
        candidates.append(None)
    return [candidates[0], candidates[1]]

def decode_bytes(data: bytes) -> str:
    for enc in ("utf-8","utf-8-sig","cp1252","latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8","ignore")

# =============================================================================
# Persistence
# =============================================================================
def _default_state() -> Dict:
    return {
        "moves_db": {},
        "species_db": {},
        "roster": [],
        "locks": [],
        "caught_counts": {},
        "fulfilled": [],
        "stone_bag": {},
        "settings": {
            "unique_sig": True,
            "default_level": 5,
            "hide_spinner": True,
            "visible_pages": {
                "pokedex": True, "battle": True, "evo": True,
                "opponents": False, "moves": False, "species": False, "saveload": True
            }
        },
        "opponents": {"meta":{"sheet_url":"","last_loaded":""},"encounters":[], "cleared":[]},
        "last_battle_pick": [0,0]
    }

def _atomic_write_json(path: str, data: Dict):
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
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
            return data
        except Exception:
            pass
    return _default_state()

def migrate_state(state: Dict) -> Dict:
    state.setdefault("stone_bag", {})
    stg = state.setdefault("settings", {})
    stg.setdefault("default_level", 5)
    stg.setdefault("unique_sig", True)
    stg.setdefault("hide_spinner", True)
    vis = stg.setdefault("visible_pages", {})
    for k, v in _default_state()["settings"]["visible_pages"].items():
        vis.setdefault(k, v)
    opp = state.setdefault("opponents",{"meta":{"sheet_url":"","last_loaded":""},"encounters":[],"cleared":[]})
    opp.setdefault("meta",{"sheet_url":"","last_loaded":""})
    opp.setdefault("encounters",[]); opp.setdefault("cleared",[])
    state.setdefault("last_battle_pick", [0,0])
    return state

STATE = migrate_state(load_state())

# =============================================================================
# Cached web fetchers
# =============================================================================
@st.cache_data(show_spinner=False, ttl=86400)
def fetch_text(url: str) -> str:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
        return decode_bytes(r.read())

@st.cache_data(show_spinner=False, ttl=86400)
def fetch_json(url: str) -> dict:
    return json.loads(fetch_text(url))

@st.cache_data(show_spinner=False, ttl=86400)
def get_pokedex_cached() -> dict:
    return fetch_json("https://play.pokemonshowdown.com/data/pokedex.json")

@st.cache_data(show_spinner=False, ttl=86400)
def get_showdown_learnsets_cached() -> dict:
    return fetch_json("https://play.pokemonshowdown.com/data/learnsets.json")

@st.cache_data(show_spinner=False, ttl=86400)
def get_gen3_data_cached() -> dict:
    return fetch_json("https://cdn.jsdelivr.net/gh/Deskbot/Pokemon-Learnsets/output/gen3.json")

# =============================================================================
# Moves master and learnset helpers
# =============================================================================
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
    return MOVES_BY_ID.get(move_id(s_clean)) or MOVES_BY_NAME.get(norm_key(s_clean))

def move_is_damaging(move_name: str) -> bool:
    info = lookup_move(move_name)
    if info is None:
        return True
    return bool(info.get("is_damaging", True))

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
    # Showdown: only level-up sources for gen3
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
            if not isinstance(sources, list): continue
            levels = []
            for s in sources:
                m = re.match(r"^3L(\d+)$", str(s))
                if m: levels.append(int(m.group(1)))
            if not levels: continue
            rec = MOVES_BY_ID.get(move_id(move_id_key))
            nm = rec["name"] if rec else clean_move_token(move_id_key)
            if not nm or not move_is_damaging(nm): continue
            for lv in levels:
                _merge_into_levelmap(out, lv, nm)
    return {k: v for k, v in out.items() if v}

def last_four_moves_by_level(learnset: Dict[str, List[str]], level: int) -> List[str]:
    entries = []
    for k, v in learnset.items():
        num = ''.join([c for c in str(k) if c.isdigit()])
        if not num: continue
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
        if mv in seen: continue
        seen.add(mv); ordered.append(mv)
    return ordered[-4:]

def ensure_move_in_db(move_name: str, default_type: Optional[str]=None):
    if not move_name: return
    mk = norm_key(clean_move_token(move_name))
    if mk and mk not in STATE["moves_db"]:
        info = lookup_move(move_name)
        mtype = normalize_type((info.get("type") if info else None) or (default_type or ""))
        STATE["moves_db"][mk] = {"name":clean_move_token(move_name),"type":mtype}

# =============================================================================
# Species building
# =============================================================================
def is_kanto_base_for_151(sd: dict, dex: dict) -> bool:
    num = sd.get("num")
    if not isinstance(num, int) or not (1 <= num <= 151):
        return False
    if sd.get("forme"):
        return False
    prevo = sd.get("prevo")
    if not prevo:
        return True
    pre = dex.get(ps_id(prevo))
    pnum = pre.get("num") if pre else None
    return not (isinstance(pnum, int) and 1 <= pnum <= 151)

@st.cache_data(show_spinner=False, ttl=86400)
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
                    if nm and move_is_damaging(nm):
                        lm.append(nm)
                if lm:
                    learnset[str(lv)] = lm
        species_db[species_key(name)] = {"name": name, "types": [t1, t2], "total": total, "learnset": learnset}
    return {
        "moves_db": moves_db,
        "species_db": species_db,
        "roster": [],
        "locks": [],
        "caught_counts": {},
        "fulfilled": [],
        "stone_bag": {},
        "settings": {
            "unique_sig": True, "default_level": 5, "hide_spinner": True,
            "visible_pages": STATE["settings"].get("visible_pages",{
                "pokedex": True,"battle": True,"evo": True,
                "opponents": False,"moves": False,"species": False,"saveload": True
            })
        },
        "opponents": {"meta":{"sheet_url":"","last_loaded":""}, "encounters":[], "cleared":[]},
        "last_battle_pick": [0,0]
    }

def ensure_species_in_db(name: str) -> bool:
    sk = species_key(name)
    if sk in STATE["species_db"]:
        return True
    dex = get_pokedex_cached()
    def _find_record(target_name: str):
        rec = dex.get(ps_id(target_name))
        if rec and rec.get("forme"):
            rec = None
        if rec and not (isinstance(rec.get("num"), int) and 1 <= rec.get("num") <= 151):
            rec = None
        if rec:
            return rec
        for _, r in dex.items():
            if ps_id(r.get("name","")) == ps_id(target_name):
                if r.get("forme"): continue
                if isinstance(r.get("num"), int) and 1 <= r.get("num") <= 151:
                    return r
        return None
    sd = _find_record(name)
    if not sd:
        return False
    nm = sd.get("name", name)
    t1, t2 = purge_fairy_types_pair(sd.get("types", []))
    base = sd.get("baseStats", {})
    total = int(sum(base.values())) if base else 0
    learnset = rebuild_learnset_for(nm) or {}
    STATE["species_db"][species_key(nm)] = {"name": nm, "types": [t1, t2], "total": total, "learnset": learnset}
    save_state(STATE)
    return True

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
        if isinstance(num, int) and 1 <= num <= 151:
            cur = pre_rec
        else:
            break
    return species_key(cur.get("name", name))

# =============================================================================
# Opponents parsing (sheet)
# =============================================================================
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
        if len(r) < 10:
            r = r + [""] * (10 - len(r))
        if not any(cell.strip() for cell in r):
            continue
        trainer_cell = clean_invisibles((r[0] or "").strip())
        poke    = clean_invisibles((r[4] or "").strip())
        lvl_str = clean_invisibles((r[5] or "").strip())
        mv_cols = [clean_move_token(c) for c in r[6:10]]
        if not starting_skipped and trainer_cell.lower().startswith("starting"):
            starting_skipped = True
            continue
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
            if not sp:
                continue
        typed_moves: List[Tuple[str,str]] = []
        for mv in mv_cols:
            if not mv: continue
            info = lookup_move(mv)
            if info and not info.get("is_damaging", True):
                continue
            if info:
                ensure_move_in_db(info["name"], default_type=normalize_type(info.get("type","")))
                typed_moves.append((info["name"], normalize_type(info.get("type",""))))
            else:
                mtype = normalize_type(STATE["moves_db"].get(norm_key(mv),{}).get("type",""))
                if mtype:
                    ensure_move_in_db(mv, default_type=mtype)
                    typed_moves.append((mv, mtype))
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

# =============================================================================
# Evolutions
# =============================================================================
def available_evos_for(species_name: str) -> List[Dict]:
    dex = get_pokedex_cached()
    opts: List[Dict] = []
    me = dex.get(ps_id(species_name))
    if not me: return []
    for e in me.get("evos", []) or []:
        tgt = dex.get(ps_id(e))
        if not tgt: continue
        if tgt.get("forme"): continue
        if not (isinstance(tgt.get("num"), int) and 1 <= tgt.get("num") <= 151): continue
        method = None; level = None; item = None
        prevo = tgt.get("prevo")
        if prevo and ps_id(prevo) == ps_id(me.get("name", species_name)):
            if isinstance(tgt.get("evoLevel"), int):
                method = "level"; level = int(tgt["evoLevel"])
            else:
                etype = tgt.get("evoType")
                if etype == "useItem": method = "item"; item = tgt.get("evoItem")
                elif etype == "trade": method = "trade"
                elif etype == "levelMove": method = "levelMove"
                elif etype: method = etype
        opts.append({"to": tgt.get("name", e), "method": method, "level": level, "item": item})
    return opts

def evolve_mon_record(mon: Dict, to_species_name: str, rebuild_moves: bool=False):
    ensure_species_in_db(to_species_name)
    sk = species_key(to_species_name)
    sp = STATE["species_db"].get(sk)
    if not sp:
        return False
    if not sp.get("learnset"):
        merged = rebuild_learnset_for(sp["name"])
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
            if info and not info.get("is_damaging", True):
                continue
            mtype = normalize_type((info.get("type") if info else None) or STATE["moves_db"].get(norm_key(m),{}).get("type",""))
            if mtype: typed.append(((info["name"] if info else m), mtype))
        mon["moves"] = typed
    return True

# =============================================================================
# Matchup helpers
# =============================================================================
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
    "Dragon": {"Dragon":2.0,"Steel":0.5},
    "Dark": {"Fighting":0.5,"Psychic":2.0,"Ghost":2.0,"Dark":0.5,"Steel":0.5},
    "Steel": {"Fire":0.5,"Water":0.5,"Electric":0.5,"Ice":2.0,"Rock":2.0,"Steel":0.5}
}

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

# =============================================================================
# Forced loading gate
# =============================================================================
def ensure_bootstrap_ready():
    progress = st.empty()
    bar = progress.progress(0, text="Loading base data...")
    step = 0
    try:
        get_pokedex_cached(); step += 1; bar.progress(int(step/6*100), text="Loaded Pokédex")
        get_showdown_learnsets_cached(); step += 1; bar.progress(int(step/6*100), text="Loaded learnsets")
        get_gen3_data_cached(); step += 1; bar.progress(int(step/6*100), text="Loaded Gen3 levels")
        load_moves_master(); step += 1; bar.progress(int(step/6*100), text="Loaded moves")
        if not STATE["species_db"]:
            base = build_kanto_state_from_web_cached()
            STATE["moves_db"] = base["moves_db"]
            STATE["species_db"] = base["species_db"]
            save_state(STATE)
        step += 1; bar.progress(int(step/6*100), text="Species ready")
        autoload_opponents_if_empty(); step += 1; bar.progress(int(step/6*100), text="Opponents ready")
    finally:
        bar.progress(100, text="Ready")
        progress.empty()

ensure_bootstrap_ready()

# =============================================================================
# UI helpers
# =============================================================================
def all_damaging_moves_sorted() -> List[str]:
    names = [rec["name"] for rec in MOVES_MASTER.values() if rec.get("is_damaging", True)]
    return sorted(set(names), key=lambda s: s.lower())

def canonical_typed(move_name: str) -> Optional[Tuple[str,str]]:
    if not move_name or move_name == "(none)":
        return None
    info = lookup_move(move_name)
    if info:
        return (info["name"], normalize_type(info.get("type","")) or "")
    tp = normalize_type(STATE["moves_db"].get(norm_key(move_name),{}).get("type",""))
    if tp:
        return (clean_move_token(move_name), tp)
    return None

def get_prefill_moves(sp: Dict, level: int) -> List[str]:
    try:
        if sp.get("learnset"):
            learned4 = last_four_moves_by_level(sp["learnset"], int(level))
            if learned4:
                return learned4[-4:]
    except Exception:
        pass
    return []

# =============================================================================
# Pokedex page: sync, add, roster with edit/evolve/remove, team + tiebreak
# =============================================================================
def required_catches_for_species(name: str) -> int:
    sk = species_key(name)
    if sk in TRADE_REWARD_SPECIES: return 1
    return 2 if sk in {species_key(x) for x in {"Abra","Spearow","Poliwag","Psyduck","Slowpoke"}} else 1

def render_pokedex():
    st.header("Pokédex")
    # 2-column top controls
    col_sync, col_add = st.columns(2)

    with col_sync:
        with st.expander("Sync levels (ALL roster)", expanded=True):
            with st.form("sync_levels_form", clear_on_submit=False):
                col = st.columns([1,1])
                target_level = col[0].number_input("Set roster level to", 1, 100, int(STATE["settings"].get("default_level", 5)), 1, key="sync_target_level")
                apply_btn = col[1].form_submit_button("Apply")
                if apply_btn:
                    for mon in STATE["roster"]:
                        mon["level"] = int(target_level)
                    STATE["settings"]["default_level"] = int(target_level)
                    save_state(STATE)
                    st.success("Level sync complete.")

    with col_add:
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
                    if not is_base_name_151(name): continue
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
                if not sp.get("learnset"):
                    merged = rebuild_learnset_for(sp["name"])
                    if merged:
                        sp["learnset"] = merged
                        STATE["species_db"][sk] = sp
                        save_state(STATE)

                suggestions: List[str] = []
                if sp.get("learnset"):
                    for lv_str, mv_list in sp["learnset"].items():
                        try:
                            lv = int((''.join([c for c in lv_str if c.isdigit()]) or "0"))
                        except Exception:
                            lv = 0
                        if lv <= int(level):
                            seq = mv_list if isinstance(mv_list, list) else [mv_list]
                            for m in seq:
                                nm = (lookup_move(m) or {}).get("name", clean_move_token(m))
                                if nm and move_is_damaging(nm):
                                    suggestions.append(nm)
                suggestions = sorted(set(suggestions), key=lambda s: s.lower())
                all_moves = all_damaging_moves_sorted()

                prefill = get_prefill_moves(sp, int(level))
                while len(prefill) < 4:
                    prefill.append("(none)")

                st.write("Moves (dropdowns with type auto-filled):")
                mcols = st.columns(4)
                chosen_names: List[str] = []
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
                    entry_moves: List[Tuple[str,str]] = []
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
    # Team builder with locks and tie-breaker (only when someone would be left out)
    st.subheader("Your Team")
    if not STATE["roster"]:
        st.info("No Pokémon yet.")
    else:
        st.caption("Lock / Unlock")
        guid_to_label = {m["guid"]: f"{m['species']} Lv{m['level']}" for m in STATE["roster"]}
        lock_ids = set(STATE.get("locks", []))
        with st.form("locks_form", clear_on_submit=False):
            opts = [m["guid"] for m in STATE["roster"]]
            current_locks = st.multiselect("Locked entries", options=opts, default=list(lock_ids), format_func=lambda g: guid_to_label.get(g,g))
            if st.form_submit_button("Save locks"):
                STATE["locks"] = list(current_locks); save_state(STATE); st.success("Locks saved.")

        def sig_of(mon) -> Tuple[Optional[str], Optional[str]]:
            t = purge_fairy_types_pair(mon.get("types", []))
            return (t[0], t[1])

        def sort_key(m):
            return (-int(m.get("total",0)), m.get("species",""))

        roster_sorted = sorted(STATE["roster"], key=sort_key)
        unique_sig = True

        picks: List[Dict] = []
        used_sigs = set()
        locks = [m for m in roster_sorted if m["guid"] in STATE.get("locks", [])]
        picks.extend(locks)
        for m in locks: used_sigs.add(sig_of(m))

        best_per_sig: Dict[Tuple[Optional[str],Optional[str]], Dict] = {}
        for mon in roster_sorted:
            if mon in picks: continue
            sig = sig_of(mon)
            if sig not in best_per_sig:
                best_per_sig[sig] = mon

        candidates = [m for s, m in best_per_sig.items() if s not in used_sigs]
        candidates.sort(key=sort_key)

        team = picks[:]
        for m in candidates:
            if len(team) >= min(6, len(STATE["roster"])): break
            s = sig_of(m)
            if unique_sig and s in used_sigs: continue
            team.append(m); used_sigs.add(s)

        remaining_slots = max(0, 6 - len(team))
        remaining_pool = [m for m in candidates if m not in team]
        if remaining_slots > 0 and len(remaining_pool) > remaining_slots:
            st.warning("Tie detected for final team slots. Choose who gets in.")
            tiebreak_guid = st.selectbox(
                "Pick the last slot",
                options=[m["guid"] for m in remaining_pool],
                format_func=lambda g: guid_to_label.get(g,g),
                key="tiebreak_pick"
            )
            if st.button("Apply tiebreaker"):
                chosen = next(m for m in remaining_pool if m["guid"] == tiebreak_guid)
                team = team + [chosen]
                locks2 = set(STATE.get("locks", []))
                locks2.add(chosen["guid"])
                STATE["locks"] = list(locks2); save_state(STATE); st.success("Saved.")
                do_rerun()

        for i, mon in enumerate(team[:6]):
            st.write(f"{i+1}. **{mon['species']}** — Lv{mon['level']} — {mon['types'][0]}/{mon['types'][1] or '—'} — Total {mon['total']}")
            with st.expander(f"Quick edit moves • {mon['species']}", expanded=False):
                sp = STATE["species_db"].get(mon.get("species_key") or species_key(mon["species"]), {})
                if not sp.get("learnset"):
                    merged = rebuild_learnset_for(sp.get("name", mon["species"]))
                    if merged:
                        sp["learnset"] = merged
                        STATE["species_db"][species_key(sp.get("name", mon["species"]))] = sp
                        save_state(STATE)
                suggestions: List[str] = []
                if sp.get("learnset"):
                    for lv_str, mv_list in sp["learnset"].items():
                        try:
                            lv = int((''.join([c for c in lv_str if c.isdigit()]) or "0"))
                        except Exception:
                            lv = 0
                        if lv <= int(mon["level"]):
                            seq = mv_list if isinstance(mv_list, list) else [mv_list]
                            for m in seq:
                                nm = (lookup_move(m) or {}).get("name", clean_move_token(m))
                                if nm and move_is_damaging(nm):
                                    suggestions.append(nm)
                suggestions = sorted(set(suggestions), key=lambda s: s.lower())
                all_moves = all_damaging_moves_sorted()
                proposed = [mv for mv,_ in mon.get("moves",[])] or get_prefill_moves(sp, int(mon["level"]))
                while len(proposed) < 4:
                    proposed.append("(none)")
                cols = st.columns(4)
                picks_local = []
                for j in range(4):
                    cur = proposed[j]
                    opts = ["(none)"] + suggestions + ["— all moves —"] + all_moves
                    if cur not in opts:
                        opts.insert(1, cur)
                    sel = cols[j].selectbox(f"Move {j+1}", opts, index=opts.index(cur), key=f"team_mv_{mon['guid']}_{j}")
                    typed = canonical_typed(sel)
                    cols[j].caption(f"Type: {typed[1] if typed else '—'}")
                    picks_local.append(sel)
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

        st.session_state["active_team"] = team[:6]

    st.markdown("---")
    st.subheader("Full roster")
    bench_sorted = sorted(STATE["roster"], key=lambda m: int(m.get("total",0)), reverse=True)
    for idx, mon in enumerate(bench_sorted, start=1):
        name_disp = mon["species"]
        t1, t2 = purge_fairy_types_pair(mon["types"])
        st.write(f"{idx}. **{name_disp}** — Lv{mon['level']} — {t1}/{t2 or '—'} — Total {mon['total']}")
        with st.expander(f"Edit / Evolve / Remove {name_disp}", expanded=False):
            c = st.columns([1,1,1,1,1])
            new_level = c[0].number_input("Level", 1, 100, mon["level"], 1, key=f"edit_lvl_{mon['guid']}")

            sp = STATE["species_db"].get(mon.get("species_key") or species_key(mon["species"]), {})
            if not sp.get("learnset"):
                merged = rebuild_learnset_for(sp.get("name", mon["species"]))
                if merged:
                    sp["learnset"] = merged
                    STATE["species_db"][species_key(sp.get("name", mon["species"]))] = sp
                    save_state(STATE)

            suggestions: List[str] = []
            if sp.get("learnset"):
                for lv_str, mv_list in sp["learnset"].items():
                    try:
                        lv = int((''.join([c for c in lv_str if c.isdigit()]) or "0"))
                    except Exception:
                        lv = 0
                    if lv <= int(new_level):
                        seq = mv_list if isinstance(mv_list, list) else [mv_list]
                        for m in seq:
                            nm = (lookup_move(m) or {}).get("name", clean_move_token(m))
                            if nm and move_is_damaging(nm):
                                suggestions.append(nm)
            suggestions = sorted(set(suggestions), key=lambda s: s.lower())
            all_moves = all_damaging_moves_sorted()

            names_e: List[str] = []
            proposed = [mv for mv,_ in mon.get("moves",[])] or get_prefill_moves(sp, int(new_level))
            while len(proposed) < 4:
                proposed.append("(none)")
            for i in range(4):
                cur = proposed[i]
                opts = ["(none)"] + suggestions + ["— all moves —"] + all_moves
                if cur not in opts:
                    opts.insert(1, cur)
                sel = c[i if i<4 else 3].selectbox(f"Move {i+1}", opts, index=opts.index(cur), key=f"edit_mv_{mon['guid']}_{i}")
                typed = canonical_typed(sel)
                c[i if i<4 else 3].caption(f"Type: {typed[1] if typed else '—'}")
                names_e.append(sel)

            if st.button("Save changes", key=f"save_{mon['guid']}"):
                entry_moves: List[Tuple[str,str]] = []
                for name in names_e:
                    ct = canonical_typed(name)
                    if ct:
                        entry_moves.append(ct)
                        ensure_move_in_db(ct[0], default_type=ct[1])
                mon["level"] = int(new_level)
                mon["moves"] = entry_moves
                save_state(STATE)
                st.success("Saved.")

            # Evolutions inline; button only if ready (level/trade/stone in bag)
            opts = available_evos_for(mon["species"])
            if opts:
                labels, targets = [], []
                for o in opts:
                    if o.get("method") == "level" and isinstance(o.get("level"), int):
                        lab = f"{o['to']} (Lv {o['level']})"
                    elif o.get("method") == "item" and o.get("item"):
                        lab = f"{o['to']} (Use {o['item']})"
                    elif o.get("method") == "trade":
                        lab = f"{o['to']} (Trade)"
                    else:
                        lab = f"{o['to']} (Manual)"
                    labels.append(lab); targets.append(o["to"])
                d1, d2 = st.columns([2,1])
                choice = d1.selectbox("Evolve to", ["(choose)"] + labels, key=f"evo_choice_{mon['guid']}")
                # Determine readiness
                can_evolve = False
                if choice != "(choose)":
                    o = opts[labels.index(choice)]
                    m = o.get("method")
                    if m == "level" and isinstance(o.get("level"), int):
                        can_evolve = mon["level"] >= int(o["level"])
                    elif m == "trade":
                        can_evolve = mon["level"] >= TRADE_EVOLVE_LEVEL
                    elif m == "item" and o.get("item"):
                        have = int(STATE["stone_bag"].get(o["item"], 0))
                        can_evolve = have > 0
                if can_evolve and st.button("Evolve now", key=f"evo_btn_{mon['guid']}"):
                    o = opts[labels.index(choice)]
                    to = targets[labels.index(choice)]
                    # consume stone if needed
                    if o.get("method") == "item" and o.get("item"):
                        item = o["item"]; have = int(STATE["stone_bag"].get(item,0))
                        if have <= 0:
                            st.error("No stone available.")
                        else:
                            STATE["stone_bag"][item] = have - 1
                            if evolve_mon_record(mon, to, rebuild_moves=False):
                                save_state(STATE); st.success(f"Evolved to {to}."); do_rerun()
                            else:
                                st.error("Evolution failed.")
                    else:
                        if evolve_mon_record(mon, to, rebuild_moves=False):
                            save_state(STATE); st.success(f"Evolved to {to}."); do_rerun()
                        else:
                            st.error("Evolution failed.")

            # Remove at bottom inside expander
            rm_col1, rm_col2, _ = st.columns([1,1,6])
            if rm_col2.button("Remove from roster", key=f"rm_{mon['guid']}"):
                base_sk = base_key_for(mon.get("species",""))
                req = required_catches_for_species(base_sk)
                fset = set(STATE.get("fulfilled", []))
                cc = STATE.get("caught_counts", {})
                # Remove this entry
                idx_to_del = next((i for i,m in enumerate(STATE["roster"]) if m.get("guid")==mon["guid"]), None)
                if idx_to_del is not None:
                    del STATE["roster"][idx_to_del]
                # Adjust caught count
                if not (base_sk in fset and req == 2):
                    cc[base_sk] = max(0, int(cc.get(base_sk,0)) - 1)
                    if cc[base_sk] < req and base_sk in fset:
                        fset.remove(base_sk)
                STATE["caught_counts"] = cc
                STATE["fulfilled"] = sorted(list(fset))
                save_state(STATE)
                st.warning("Removed from roster.")
                do_rerun()

# =============================================================================
# Battle page (Matchup) with cleared log + undo
# =============================================================================
def render_battle():
    # Ensure opponents are loaded lazily
    autoload_opponents_if_empty()
    st.header("Battle")
    team = st.session_state.get("active_team", STATE["roster"][:6])
    if not team:
        st.info("Build a team on the Pokédex page."); return

    if not STATE["opponents"]["encounters"]:
        st.warning("No opponents loaded yet. Trying to load your default sheet…")
        autoload_opponents_if_empty()
    if not STATE["opponents"]["encounters"]:
        st.error("Could not load opponents automatically.")
        return

    with st.form("sheet_pick_form", clear_on_submit=False):
        enc_options = [f"{i+1}. {enc['label']}" for i, enc in enumerate(STATE["opponents"]["encounters"])]
        default_idx = min(STATE.get("last_battle_pick",[0,0])[0], len(enc_options)-1)
        pick = st.selectbox("Encounter (trainer)", enc_options, index=default_idx)
        selected_enc_idx = enc_options.index(pick)
        enc = STATE["opponents"]["encounters"][selected_enc_idx]
        mon_labels = [f"{i+1}. {m['species']} Lv{m['level']} (Total {m.get('total',0)})" for i, m in enumerate(enc["mons"])]
        default_mon_idx = min(STATE.get("last_battle_pick",[0,0])[1], len(mon_labels)-1)
        pick_mon = st.selectbox("Their Pokémon", mon_labels, index=default_mon_idx)
        apply_pick = st.form_submit_button("Load encounter")

    if apply_pick:
        STATE["last_battle_pick"] = [selected_enc_idx, mon_labels.index(pick_mon)]; save_state(STATE)

    selected_enc_idx, selected_mon_idx = STATE.get("last_battle_pick",[0,0])
    enc = STATE["opponents"]["encounters"][selected_enc_idx]
    opmon = enc["mons"][selected_mon_idx]
    t1, t2 = purge_fairy_types_pair(opmon["types"])
    opp_types = (t1, t2)
    opp_pairs = [(mv, normalize_type(tp) or "") for mv,tp in opmon["moves"]]
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
                STATE["opponents"]["cleared"].append({"what": "trainer","trainer": label_before,"count": count, "data": enc})
                STATE["opponents"]["encounters"].pop(selected_enc_idx)
            else:
                label_before = enc["label"]
                beaten = enc["mons"].pop(selected_mon_idx)
                STATE["opponents"]["cleared"].append({"what":"pokemon","trainer": label_before,"species": beaten.get("species"),"level": beaten.get("level"),"row": beaten.get("source_row"),"data": beaten})
            save_state(STATE)
            STATE["last_battle_pick"] = [0,0]; save_state(STATE)
            do_rerun()
        except Exception as e:
            st.error(f"Failed to remove: {e}")

    if b2.button("🧹 Beat Trainer (remove entire encounter)"):
        try:
            label_before = enc["label"]
            STATE["opponents"]["cleared"].append({"what": "trainer","trainer": label_before,"count": len(enc["mons"]), "data": enc})
            STATE["opponents"]["encounters"].pop(selected_enc_idx)
            save_state(STATE)
            STATE["last_battle_pick"] = [0,0]; save_state(STATE)
            do_rerun()
        except Exception as e:
            st.error(f"Failed to remove trainer: {e}")

    with st.expander("Cleared log (latest 15)", expanded=False):
        log = STATE["opponents"].get("cleared", [])
        if not log:
            st.caption("— empty —")
        else:
            # Only show undo if restoration is possible (i.e., trainer not already present in current encounters)
            # Build a quick map of labels
            current_labels = {enc["label"] for enc in STATE["opponents"]["encounters"]}
            for item in list(reversed(log[-15:])):
                if item.get("what") == "pokemon":
                    label = f"• Beat Pokémon: {item.get('species')} (Lv{item.get('level')}) — Trainer: {item.get('trainer')}"
                    can_undo = item.get("trainer") in current_labels
                else:
                    label = f"• Beat Trainer: {item.get('trainer')} — removed {item.get('count',0)} Pokémon"
                    can_undo = item.get("trainer") not in current_labels
                cols = st.columns([6,1])
                cols[0].write(label)
                if can_undo:
                    if cols[1].button("Undo", key=f"undo_{hash(str(item))}"):
                        if item.get("what") == "pokemon":
                            # put mon back into matching encounter
                            for enc2 in STATE["opponents"]["encounters"]:
                                if enc2["label"] == item["trainer"]:
                                    enc2.setdefault("mons", []).append(item["data"])
                                    break
                        else:
                            # restore entire trainer only if not present
                            if item["trainer"] not in {e["label"] for e in STATE["opponents"]["encounters"]}:
                                STATE["opponents"]["encounters"].append(item["data"])
                        save_state(STATE)
                        st.success("Undo applied.")
                        do_rerun()

    # Scoring table
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
            sp["learnset"] = rebuild_learnset_for(sp.get("name", mon["species"]))
            STATE["species_db"][species_key(sp.get("name", mon["species"]))] = sp; save_state(STATE)
        my_moves = [(mv, normalize_type(tp) or "") for mv,tp in mon.get("moves", [])]
        if not my_moves and sp.get("learnset"):
            learned = last_four_moves_by_level(sp["learnset"], int(mon["level"]))
            typed = []
            for m in learned:
                ct = canonical_typed(m)
                if ct: typed.append(ct)
            my_moves = typed
        (off_sc, off_move, off_mult), off_rows = compute_best_offense(my_moves, opp_types)
        (def_sc, def_move, def_mult), def_rows = compute_their_best_vs_me(opp_pairs, my_types)
        total = off_sc + def_sc
        results.append({
            "mon": mon, "my_total": my_total, "opp_total": opp_total,
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
            f"**{mon['species']}** — {totals_text} — "
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

# =============================================================================
# Evolution Watch page
# =============================================================================
def render_evo_watch():
    st.header("Evolution Watch")

    if not STATE["roster"]:
        st.info("No Pokémon yet.")
        return

    # Stone inventory controls
    st.subheader("Stones in bag")
    cols = st.columns(6)
    stones = ["Fire Stone","Water Stone","Thunder Stone","Leaf Stone","Moon Stone","Sun Stone"]
    for i, item in enumerate(stones):
        count = int(STATE["stone_bag"].get(item, 0))
        with cols[i]:
            st.caption(stone_with_emoji(item))
            c1, c2, c3 = st.columns([1,1,2])
            if c1.button("+", key=f"stone_add_{i}"):
                STATE["stone_bag"][item] = count + 1; save_state(STATE); do_rerun()
            if c2.button("-", key=f"stone_sub_{i}"):
                STATE["stone_bag"][item] = max(0, count - 1); save_state(STATE); do_rerun()
            st.write(f"Have: {STATE['stone_bag'].get(item,0)}")

    # One row per Pokémon, tidy container
    st.markdown("---")
    for mon in STATE["roster"]:
        lvl = int(mon.get("level",1))
        species = mon.get("species","?")
        opts = available_evos_for(species) or []

        with st.container(border=True):
            st.markdown(f"**{species} • Lv{lvl}** • Total {mon.get('total',0)}")
            if not opts:
                st.caption("No evolutions")
                continue

            # header
            h1, h2, h3, h4, h5 = st.columns([3,2,2,3,2])
            h1.markdown("**Target**"); h2.markdown("**Method**"); h3.markdown("**Requirement**"); h4.markdown("**Status**"); h5.markdown("**Action**")

            for idx, o in enumerate(opts):
                to_name = o.get("to","?")
                method = o.get("method")
                req_txt = "—"; ready = False; status_txt = "Manual"
                if method == "level" and isinstance(o.get("level"), int):
                    req = int(o["level"]); req_txt = f"Lv {req}"; ready = lvl >= req; status_txt = "Ready" if ready else f"Needs Lv {req}"
                elif method == "trade":
                    req = TRADE_EVOLVE_LEVEL; req_txt = "Trade"; ready = lvl >= req; status_txt = f"Ready (Lv{req})" if ready else f"Trade or reach Lv{req}"
                elif method == "item" and o.get("item"):
                    item = o["item"]; have = int(STATE["stone_bag"].get(item,0))
                    req_txt = stone_with_emoji(item)
                    ready = have > 0
                    status_txt = f"{'Ready' if ready else 'Need'} {item} (you have {have})"
                else:
                    req_txt = "—"; ready = True; status_txt = "Manual"

                c1, c2, c3, c4, c5 = st.columns([3,2,2,3,2])
                c1.write(to_name)
                c2.write({"level":"Level","item":"Use Item","trade":"Trade","manual":"Manual"}.get(method or "manual","Manual"))
                c3.write(req_txt)
                c4.write(status_txt)
                if ready:
                    if c5.button(f"Evolve → {to_name}", key=f"evo_watch_btn_{mon['guid']}_{idx}"):
                        # consume stone if needed
                        if method == "item" and o.get("item"):
                            item = o["item"]; have = int(STATE["stone_bag"].get(item,0))
                            if have <= 0:
                                st.error("No stone available.")
                            else:
                                STATE["stone_bag"][item] = have - 1
                                if evolve_mon_record(mon, to_name, rebuild_moves=False):
                                    save_state(STATE); st.success(f"Evolved into {to_name}."); do_rerun()
                        else:
                            if evolve_mon_record(mon, to_name, rebuild_moves=False):
                                save_state(STATE); st.success(f"Evolved into {to_name}."); do_rerun()

# =============================================================================
# Save/Load
# =============================================================================
def render_saveload():
    st.header("Save / Load")
    st.caption("All data lives in 'state.json' next to the app file. Atomic save with backup.")
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

# =============================================================================
# Sidebar routing
# =============================================================================
PAGE_REGISTRY = [
    ("pokedex", "Pokédex", render_pokedex),
    ("battle", "Battle", render_battle),
    ("evo", "Evolution Watch", render_evo_watch),
    ("saveload", "Save/Load", render_saveload),
]

options = [pid for pid, _, _ in PAGE_REGISTRY]
labels  = [label for _, label, _ in PAGE_REGISTRY]

st.sidebar.title("FR/LG Companion App")
default_index = 0  # landing page is Pokédex
if "last_page" in st.session_state:
    try:
        default_index = options.index(st.session_state["last_page"])
    except ValueError:
        default_index = 0

page_choice = st.sidebar.radio("Go to", options=options, format_func=lambda pid: labels[options.index(pid)], index=default_index)
st.session_state["last_page"] = page_choice

for pid, label, fn in PAGE_REGISTRY:
    if pid == page_choice:
        fn()
        break
