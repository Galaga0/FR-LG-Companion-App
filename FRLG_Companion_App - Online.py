
# Global exclude for FRLG moves (intentionally empty — per project rules)
FRLG_EXCLUDE_MOVES: set[str] = set()

# --- FRLG species-line legal moves resolver (wrapper) ---
def legal_moves_for_species_chain(species_name: str):
    """Return legal move names for a species considering its Gen1 Kanto line (FR/LG).
    Tries cached FR/LG-accurate set, then falls back to damaging moves for the line.
    """
    try:
        # Preferred: cached FRLG-accurate legal move list if available
        moves = _frlg_cached_legal_for_species(species_name)
    except Exception:
        moves = None
    if not moves:
        try:
            moves = _legal_damaging_moves_for_chain(species_name)
        except Exception:
            moves = None
    return moves or []


def _frlg_cached_legal_for_species(species_name: str):
    """Cache FRLG-accurate species-line damaging moves for quick reuse."""
    key = species_key(species_name)
    cache = STATE.setdefault("_frlg_legal_cache", {})
    if key in cache:
        return cache[key]
    moves = _legal_damaging_moves_for_chain(species_name)
    cache[key] = moves
    return moves

def _legal_damaging_moves_for_chain(species_name: str):
    try:
        dex = get_pokedex_cached() or {}
        lsets = get_showdown_learnsets_cached() or {}
    except Exception:
        return []

    maxdex = dex_max()

    def _dex_rec(name: str):
        sid = ps_id(name)
        rec = dex.get(sid)
        if rec and rec.get("forme"):
            rec = None
        if rec and isinstance(rec.get("num"), int) and 1 <= rec["num"] <= maxdex:
            return rec
        for r in dex.values():
            if r and isinstance(r.get("num"), int) and 1 <= r["num"] <= maxdex and not r.get("forme"):
                if ps_id(r.get("name","")) == sid:
                    return r
        return None
    # ...rest of your function unchanged...

    sd = _dex_rec(species_name)
    if not sd:
        return []

    # Walk to base (prevo chain) restricted to Kanto
    base = sd
    seen_names = set()
    while base.get("prevo"):
        if base.get("name") in seen_names:
            break
        seen_names.add(base.get("name"))
        prev = _dex_rec(base.get("prevo"))
        if not prev:
            break
        base = prev

    # BFS from base through evos (Kanto only)
    fam = []
    q = [base]
    seen_ids = set()
    while q:
        cur = q.pop(0)
        key = ps_id(cur.get("name",""))
        if key in seen_ids:
            continue
        seen_ids.add(key)
        fam.append(cur)
        for evn in (cur.get("evos") or []):
            evr = _dex_rec(evn)
            if evr and ps_id(evr.get("name","")) not in seen_ids:
                q.append(evr)

    # Map Showdown learnsets id
    def _ls_key(name: str):
        sid = ps_id(name)
        if sid in lsets: 
            return sid
        # fallback: match by ps_id of keys
        for k in lsets.keys():
            if ps_id(k) == sid:
                return k
        return None

    out = []
    seen_moves = set()
    for rec in fam:
        k = _ls_key(rec.get("name",""))
        if not k: 
            continue
        ls = (lsets.get(k, {}) or {}).get("learnset", {}) or {}
        for mv_id, methods in ls.items():
            meths = methods if isinstance(methods, list) else [methods]
            if not any(isinstance(t, str) and (t.startswith("3L") or t == "3M" or t == "3T") for t in meths):
                continue
            mv = lookup_move(mv_id) or {}
            nm = mv.get("name", clean_move_token(mv_id))
            if not nm or not move_is_damaging(nm):
                continue
            if nm in seen_moves:
                continue
            seen_moves.add(nm)
            out.append(nm)

    out.sort()
    return out

def is_base_name_151(name: str) -> bool:
    """Return True if the species is a base form (no evolves_from in species_db)."""
    sk = species_key(name)
    sp = STATE.get("species_db", {}).get(sk, {}) or {}
    return not bool(sp.get("evolves_from"))

import streamlit as st
def st_html(html: str):
    # Prevent Markdown from treating indented lines as a code block
    html = "\n".join(line.lstrip() for line in (html or "").splitlines())
    st.markdown(html, unsafe_allow_html=True)
import textwrap
import streamlit.components.v1 as components
from typing import List, Dict, Tuple, Optional
import json, os, urllib.request, ssl, re, csv, uuid, hashlib
from urllib.parse import urlparse, parse_qs, urlencode, quote

# --- Session persistence mode ---
# Default: ephemeral (no disk writes, fresh state per browser session)
import os
EPHEMERAL = bool(int(os.getenv("FRLG_EPHEMERAL", "1")))  # set to "0" only if you WANT disk saves
STATE_PATH = "state.json"
STATE_BAK  = "state.backup.json"

# ===== PATCH HELPERS (keep) =====
def _nx(x): return (x or "").strip()
def _lc(x): return _nx(x).lower()
def _tt(x): return _nx(x).title()

def _lookup_move(name):
    """
    Prefer the per-session moves_db, but *always* fall back to the master
    Play Showdown move record so types like Dark/Fire/etc. are available
    even if the move wasn't pre-seeded into STATE["moves_db"].
    """
    try:
        rec = STATE.get("moves_db", {}).get(_lc(name))
    except Exception:
        rec = None
    return rec or lookup_move(name)

def normalize_moves_list(val):
    out = []
    seq = val or []
    for it in seq:
        mv = None; tp = ""
        if isinstance(it, (list, tuple)):
            if len(it)>0: mv = it[0]
            if len(it)>1: tp = _tt(it[1])
        elif isinstance(it, dict):
            mv = it.get("name") or it.get("move") or it.get("mv")
            tp = _tt(it.get("type",""))
        elif isinstance(it, str):
            mv = it
        if mv and mv != "(none)":
            info = _lookup_move(mv) or {}
            nm = _nx(info.get("name") or mv).title()
            if not tp:
                tp = _tt(info.get("type",""))
            out.append((nm, tp or ""))
    seen = set(); res = []
    for nm,tp in out:
        k = (nm.lower(), tp)
        if k not in seen:
            seen.add(k); res.append((nm,tp))
        if len(res)==4: break
    return res

def _coerce_learnset(ls):
    out = []
    if not ls: return out
    if isinstance(ls, list):
        for x in ls:
            if isinstance(x, str):
                nm = _nx(x).title()
                if nm: out.append(nm)
            elif isinstance(x, dict):
                nm = _nx(x.get("name") or x.get("move") or "").title()
                if nm: out.append(nm)
            elif isinstance(x, (list, tuple)) and x:
                nm = _nx(str(x[0])).title()
                if nm: out.append(nm)
    elif isinstance(ls, dict):
        for v in ls.values():
            out.extend(_coerce_learnset(v))
    elif isinstance(ls, str):
        nm = _nx(ls).title()
        if nm: out.append(nm)
    return out

def species_learnset(spec_lc):
    spdb = STATE.get("species_db", {})
    sp = spdb.get(_lc(spec_lc)) or {}
    arr = _coerce_learnset(sp.get("learnset") or [])
    if spec_lc == "articuno":
        need = [m for m in ["Gust","Powder Snow"] if m not in arr]; arr = need + arr
    elif spec_lc == "zapdos":
        need = [m for m in ["Peck","Thundershock"] if m not in arr]; arr = need + arr
    elif spec_lc == "moltres":
        need = [m for m in ["Ember","Wing Attack"] if m not in arr]; arr = need + arr
    return arr

def typing_sig(mon):
    t = (mon or {}).get("types") or []
    t1 = _tt(t[0]) if len(t)>0 else ""
    t2 = _tt(t[1]) if len(t)>1 else ""
    return (t1, t2)

def total_of(mon):
    try: return int((mon or {}).get("total",0))
    except Exception: return 0

def _best_by_typing(roster):
    best = {}
    for m in roster or []:
        sig = typing_sig(m)
        cur = best.get(sig)
        if cur is None or total_of(m) > total_of(cur):
            best[sig] = m
    return best

PERSIST_TO_DISK = False


st.set_page_config(page_title="FR/LG Companion App", layout="wide")

st.markdown("""
<style>
:root {
  --mv-font: 14px;
  --mv-pad-y: 6px;
  --mv-pad-x: 10px;
  --grid-underline-light: #e5e7eb;
  --grid-underline-dark: rgba(255,255,255,0.12);
  --arrow-up: #22c55e;
  --arrow-down: #ef4444;
}

.sprite-inline{
  vertical-align: middle;
  image-rendering: pixelated;
  margin-right: 8px;
}

/* Container shrinks to content instead of filling the screen */
.moves-grid{
  display: inline-block;
  width: fit-content;
  max-width: 100%;
  margin: 6px 0;
}
@supports not (width: fit-content){
  .moves-grid{ width: max-content; }
}

/* Let the table size itself to content; no fixed layout, no forced 100% width */
.moves-grid table{
  border-collapse: collapse;
  table-layout: auto;
  width: auto;
}

.moves-grid thead th{
  position: sticky;
  top: 0;
  background: transparent;
  z-index: 1;
  font-weight: 600;
  text-align: left;
}

/* Pokédex: shared card header content (background handled by container styling) */
.dex-card-head{
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 10px;
}

.dex-card-title{
  font-weight: 800;
  font-size: 15px;
  line-height: 1.15;
}

.dex-card-meta{
  opacity: 0.92;
  font-size: 12px;
  margin-top: 2px;
}

.dex-card-meta b{
  font-weight: 800;
}

.moves-grid th, .moves-grid td{
  padding: var(--mv-pad-y) var(--mv-pad-x);
  font-size: var(--mv-font);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  border-bottom: 1px solid var(--grid-underline-light) !important;
}

.moves-grid .mv-score .up   { color: var(--arrow-up); font-weight: 700; }
.moves-grid .mv-score .down { color: var(--arrow-down); font-weight: 700; }
.moves-grid .small{ opacity: .85; }

/* keep rows visually neutral */
.moves-grid tbody tr td{ background: transparent !important; }
.moves-grid tbody tr:nth-of-type(odd) td{ background: transparent !important; }
.moves-grid tbody tr:hover td{ background: transparent !important; }

@media (prefers-color-scheme: dark) {
  .moves-grid th, .moves-grid td { color: #fff !important; }
  .moves-grid th, .moves-grid td { border-bottom-color: var(--grid-underline-dark) !important; }
}
@media (prefers-color-scheme: light) {
  .moves-grid th, .moves-grid td { color: #111 !important; }
  .moves-grid th, .moves-grid td { border-bottom-color: var(--grid-underline-light) !important; }
}

/* Opponent Pokémon cards on Battle page */
.opp-card {
  /* default gradient, can be overridden per-card via inline CSS vars */
  --opp-bg1: rgba(148,163,184,0.22);
  --opp-bg2: rgba(15,23,42,0.0);

  border-radius: 14px;
  padding: 10px 12px;
  border: 1px solid rgba(148,163,184,.7);
  margin-bottom: 10px;
  display: flex;
  gap: 10px;
  align-items: center;
  background: radial-gradient(circle at top left, var(--opp-bg1), var(--opp-bg2));
  cursor: pointer;
  position: relative;
}

/* Right-aligned Select-button area inside the card */
.opp-card-select {
  margin-left: auto;
  display: flex;
  align-items: center;
  justify-content: flex-end;
}

/* The in-card Select button */
.opp-card-select-btn {
  display: inline-block;
  padding: 4px 14px;
  border-radius: 9999px;
  border: 1px solid rgba(255,255,255,0.4);
  font-size: 13px;
  font-weight: 600;
  text-decoration: none;
  color: #ffffff;
  background: rgba(37,99,235,0.95);
  box-shadow: 0 4px 8px rgba(0,0,0,0.25);
  transition: transform 0.05s ease-out, box-shadow 0.05s ease-out, background 0.05s ease-out;
}

.opp-card-select-btn:hover {
  background: rgba(59,130,246,1);
  box-shadow: 0 6px 14px rgba(0,0,0,0.35);
  transform: translateY(-1px);
}

.opp-card-select-btn:active {
  transform: translateY(0);
  box-shadow: 0 2px 4px rgba(0,0,0,0.3);
}

.opp-card-selected {
  border-color: rgba(56,189,248,1);
  border-width: 2px;
  box-shadow: 0 0 0 2px rgba(56,189,248,0.9), 0 0 12px rgba(56,189,248,0.6);
}

.opp-card-sprite img {
  image-rendering: pixelated;
}

.opp-card-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 13px;
}

.opp-card-name {
  font-weight: 700;
  font-size: 14px;
}

.opp-card-types {
  opacity: 0.92;
}

.opp-card-total {
  font-size: 12px;
  opacity: 0.9;
}

.opp-card-moves {
  font-size: 11px;
  opacity: 0.9;
}

.opp-card-moves-label {
  font-weight: 600;
}

/* VS cards (Battle: Your team vs Opponent) */
.vs-card {
  --opp-bg1: rgba(148,163,184,0.22);
  --opp-bg2: rgba(15,23,42,0.0);

  border-radius: 14px;
  padding: 12px 12px 10px 12px;
  border: 1px solid rgba(148,163,184,.7);
  background: radial-gradient(circle at top left, var(--opp-bg1), var(--opp-bg2));
  margin-bottom: 10px;
}

.vs-card-header {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 8px;
}

.vs-card-sprite img {
  image-rendering: pixelated;
}

.vs-card-title {
  font-weight: 800;
  font-size: 15px;
  line-height: 1.15;
}

.vs-card-meta {
  opacity: 0.92;
  font-size: 12px;
  margin-top: 2px;
}

.vs-card-scoreline {
  font-size: 12px;
  margin-top: 4px;
  opacity: 0.95;
}

.vs-card-grid-title {
  font-weight: 700;
  font-size: 12px;
  margin-top: 8px;
  margin-bottom: 4px;
  opacity: 0.95;
}

.evo-row-card{
  --evo-top1: rgba(148,163,184,0.22);
  --evo-top2: rgba(15,23,42,0.0);
  --evo-bot1: rgba(148,163,184,0.22);
  --evo-bot2: rgba(15,23,42,0.0);

  position: relative;
  overflow: hidden;

  border-radius: 14px;
  padding: 10px 12px;
  border: 1px solid rgba(148,163,184,.7);
  margin: 8px 0;
}

/* Header labels row: no gradient, just a neutral bar */
.evo-header-bar{
  border-radius: 12px;
  padding: 8px 12px;
  border: 1px solid rgba(148,163,184,.55);
  background: rgba(255,255,255,0.55);
  margin: -2px 0 10px 0;
}
@media (prefers-color-scheme: dark){
  .evo-header-bar{
    background: rgba(15,23,42,0.35);
    border-color: rgba(148,163,184,.55);
  }
}

/* Paint the two halves behind content */
/* TOP half = current Pokémon */
.evo-row-card{
  --evo-top1: rgba(148,163,184,0.22);
  --evo-top2: rgba(15,23,42,0.0);
  --evo-bot1: rgba(148,163,184,0.22);
  --evo-bot2: rgba(15,23,42,0.0);

  border-radius: 14px;
  padding: 10px 12px;
  border: 1px solid rgba(148,163,184,.7);
  margin: 8px 0;
  overflow: hidden;
  position: relative;

  /* TOP half + BOTTOM half, always */
  background:
    radial-gradient(circle at top left,    var(--evo-top1), var(--evo-top2)) top left / 100% 50% no-repeat,
    radial-gradient(circle at bottom left, var(--evo-bot1), var(--evo-bot2)) bottom left / 100% 50% no-repeat;
}

/* Gradient band for CURRENT (non-evolved) Pokémon area */
.evo-current-band{
  --cur1: rgba(148,163,184,0.22);
  --cur2: rgba(15,23,42,0.0);

  border-radius: 14px;
  padding: 10px 12px;
  border: 1px solid rgba(148,163,184,.7);
  margin: 8px 0 10px 0;
  background: radial-gradient(circle at top left, var(--cur1), var(--cur2));
}

.evo-current-title{
  display:flex;
  align-items:center;
  gap: 10px;
  font-weight: 800;
  font-size: 15px;
  margin-bottom: 8px;
}

/* Use same 6-col grid for the header labels */
.evo-grid.evo-head > div{
  font-weight: 700;
  opacity: 0.95;
}

/* Keep your grid above the gradient layer */
.evo-grid{
  position: relative;
  z-index: 1;

  display: grid;
  grid-template-columns: 3fr 2fr 2fr 3fr 2fr 2fr;
  gap: 10px;
  align-items: center;
}

/* ==========================
   Evolution Watch: REAL Streamlit evolve button (blue when active, grey when disabled)
   IMPORTANT: Style/position by the Streamlit key wrapper, NOT by :has(marker)
   ========================== */

/* STYLE: only buttons whose key starts with evo_btn__ */
div[class*="st-key-evo_btn__"] button{
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;

  width: 150px !important;
  min-width: 150px !important;

  padding: 8px 16px !important;
  border-radius: 9999px !important;

  background: linear-gradient(180deg, #3b82f6 0%, #1d4ed8 100%) !important;
  border: 2px solid rgba(255,255,255,0.75) !important;

  color: #ffffff !important;
  font-weight: 800 !important;
  font-size: 13px !important;
  letter-spacing: 0.2px !important;

  box-shadow: 0 10px 18px rgba(0,0,0,0.35) !important;
  text-shadow: 0 1px 1px rgba(0,0,0,0.25) !important;

  transform: translateY(0) !important;
  transition: transform .08s ease-out, box-shadow .08s ease-out, filter .08s ease-out !important;
}

div[class*="st-key-evo_btn__"] button:hover{
  filter: brightness(1.08) saturate(1.05) !important;
  transform: translateY(-1px) !important;
  box-shadow: 0 14px 26px rgba(0,0,0,0.42) !important;
}

div[class*="st-key-evo_btn__"] button:active{
  transform: translateY(0) !important;
  box-shadow: 0 8px 14px rgba(0,0,0,0.35) !important;
  filter: brightness(0.98) !important;
}

div[class*="st-key-evo_btn__"] button:disabled{
  opacity: 0.40 !important;
  background: rgba(100,116,139,0.95) !important;
  border: 2px solid rgba(255,255,255,0.35) !important;
  box-shadow: none !important;
  text-shadow: none !important;
  cursor: not-allowed !important;
}

/* POSITION: visually place the evolve button inside the row card under Action */
div[class*="st-key-evo_btn__"]{
  /* DO NOT stretch across the row */
  width: fit-content !important;
  display: flex !important;

  /* Push wrapper to the far right */
  margin-left: auto !important;
  justify-content: flex-end !important;

  /* Pull upward so it overlays inside the evo-row-card “Action” cell */
  margin-top: -92px !important;
  margin-bottom: 0px !important;

  /* This controls how far from the right edge it sits */
  padding-right: 110px !important;

  z-index: 50 !important;
  position: relative !important;
}

/* Evolution Watch: keep the row cards INSIDE the bordered container (no bleed left/right) */
div[data-testid="stVerticalBlockBorderWrapper"]:has(.evo-card-marker),
div[data-testid="stContainer"]:has(.evo-card-marker){
  padding-left: 10px !important;
  padding-right: 10px !important;
  padding-bottom: 12px !important;
}

/* Hard override: row card must not use negative margins or overflow past container */
div[data-testid="stVerticalBlockBorderWrapper"]:has(.evo-card-marker) .evo-row-card,
div[data-testid="stContainer"]:has(.evo-card-marker) .evo-row-card{
  box-sizing: border-box !important;
  width: 100% !important;
  max-width: 100% !important;

  margin-left: 0px !important;
  margin-right: 0px !important;

  /* keep spacing nice without escaping the border */
  margin-bottom: 10px !important;
  overflow: hidden !important;
}

/* Evolution Watch: this is the REAL vertical offset knob */
div[data-testid="stVerticalBlockBorderWrapper"]:has(.evo-card-marker) .evo-inner-pad,
div[data-testid="stContainer"]:has(.evo-card-marker) .evo-inner-pad{
  box-sizing: border-box !important;
  width: 100% !important;
  max-width: 100% !important;
  padding-left: 12px !important;
  padding-right: 12px !important;

  position: relative !important;
  top: -12px !important;   /* <-- MOVE UP/DOWN HERE */
}

/* Hard clamp: row card must not go full-bleed */
.evo-inner-pad .evo-row-card{
  box-sizing: border-box !important;
  width: 100% !important;
  max-width: 100% !important;
  margin-left: 0 !important;
  margin-right: 0 !important;
}

/* ==========================
   POKÉDEX CARD GRADIENTS (robust)
   Works by making the *card container itself* the positioning context,
   then absolutely positioning the marker to cover the whole card.
   ========================== */

/* Streamlit has used different wrappers over versions; support both */
div[data-testid="stVerticalBlockBorderWrapper"]:has(.dex-grad-marker),
div[data-testid="stContainer"]:has(.dex-grad-marker){
  position: relative !important;
  overflow: hidden !important;
  border-radius: 14px !important;
  background: transparent !important;
}

/* Some versions paint a background on the inner block — neutralize it */
div[data-testid="stVerticalBlockBorderWrapper"]:has(.dex-grad-marker) > div,
div[data-testid="stContainer"]:has(.dex-grad-marker) > div{
  background: transparent !important;
}

/* IMPORTANT: prevent intermediate wrappers from becoming containing blocks */
div[data-testid="stVerticalBlockBorderWrapper"]:has(.dex-grad-marker) div[data-testid="stMarkdownContainer"],
div[data-testid="stVerticalBlockBorderWrapper"]:has(.dex-grad-marker) div[data-testid="stMarkdown"],
div[data-testid="stContainer"]:has(.dex-grad-marker) div[data-testid="stMarkdownContainer"],
div[data-testid="stContainer"]:has(.dex-grad-marker) div[data-testid="stMarkdown"]{
  position: static !important;
  background: transparent !important;
}

/* Put all normal content above the gradient */
div[data-testid="stVerticalBlockBorderWrapper"]:has(.dex-grad-marker) div[data-testid="stVerticalBlock"] > *,
div[data-testid="stContainer"]:has(.dex-grad-marker) div[data-testid="stVerticalBlock"] > *{
  position: relative !important;
  z-index: 1 !important;
}

/* The gradient layer itself */
.dex-grad-marker{
  /* defaults (will be overridden by t1-/t2- classes below) */
  --opp-bg1: rgba(148,163,184,0.80);
  --opp-bg2: rgba(0,0,0,0);

  position: absolute !important;
  inset: 0 !important;

  z-index: 0 !important;
  pointer-events: none !important;

  display: block !important;
  border-radius: 14px !important;

  background: radial-gradient(circle at top left, var(--opp-bg1), var(--opp-bg2)) !important;
}

/* ---- Primary type sets BG1 ---- */
.dex-grad-marker.t1-Fire{     --opp-bg1: rgba(248,113,113,0.85); }
.dex-grad-marker.t1-Water{    --opp-bg1: rgba(56,189,248,0.85); }
.dex-grad-marker.t1-Electric{ --opp-bg1: rgba(250,204,21,0.90); }
.dex-grad-marker.t1-Grass{    --opp-bg1: rgba(52,211,153,0.85); }
.dex-grad-marker.t1-Ice{      --opp-bg1: rgba(125,211,252,0.90); }
.dex-grad-marker.t1-Fighting{ --opp-bg1: rgba(248,113,113,0.90); }
.dex-grad-marker.t1-Poison{   --opp-bg1: rgba(192,132,252,0.90); }
.dex-grad-marker.t1-Ground{   --opp-bg1: rgba(234,179,8,0.90); }
.dex-grad-marker.t1-Flying{   --opp-bg1: rgba(129,140,248,0.90); }
.dex-grad-marker.t1-Psychic{  --opp-bg1: rgba(244,114,182,0.90); }
.dex-grad-marker.t1-Bug{      --opp-bg1: rgba(190,242,100,0.90); }
.dex-grad-marker.t1-Rock{     --opp-bg1: rgba(253,186,116,0.90); }
.dex-grad-marker.t1-Ghost{    --opp-bg1: rgba(167,139,250,0.90); }
.dex-grad-marker.t1-Dragon{   --opp-bg1: rgba(96,165,250,0.90); }
.dex-grad-marker.t1-Dark{     --opp-bg1: rgba(31,41,55,0.95); }
.dex-grad-marker.t1-Steel{    --opp-bg1: rgba(148,163,184,0.90); }
.dex-grad-marker.t1-Normal{   --opp-bg1: rgba(209,213,219,0.85); }

/* ---- Secondary type sets BG2 ---- */
.dex-grad-marker.t2-Fire{     --opp-bg2: rgba(239,68,68,0.75); }
.dex-grad-marker.t2-Water{    --opp-bg2: rgba(59,130,246,0.75); }
.dex-grad-marker.t2-Electric{ --opp-bg2: rgba(234,179,8,0.80); }
.dex-grad-marker.t2-Grass{    --opp-bg2: rgba(34,197,94,0.75); }
.dex-grad-marker.t2-Ice{      --opp-bg2: rgba(59,130,246,0.75); }
.dex-grad-marker.t2-Fighting{ --opp-bg2: rgba(220,38,38,0.80); }
.dex-grad-marker.t2-Poison{   --opp-bg2: rgba(168,85,247,0.80); }
.dex-grad-marker.t2-Ground{   --opp-bg2: rgba(202,138,4,0.80); }
.dex-grad-marker.t2-Flying{   --opp-bg2: rgba(59,130,246,0.80); }
.dex-grad-marker.t2-Psychic{  --opp-bg2: rgba(236,72,153,0.80); }
.dex-grad-marker.t2-Bug{      --opp-bg2: rgba(132,204,22,0.80); }
.dex-grad-marker.t2-Rock{     --opp-bg2: rgba(234,179,8,0.80); }
.dex-grad-marker.t2-Ghost{    --opp-bg2: rgba(129,140,248,0.80); }
.dex-grad-marker.t2-Dragon{   --opp-bg2: rgba(37,99,235,0.80); }
.dex-grad-marker.t2-Dark{     --opp-bg2: rgba(15,23,42,0.90); }
.dex-grad-marker.t2-Steel{    --opp-bg2: rgba(75,85,99,0.80); }
.dex-grad-marker.t2-Normal{   --opp-bg2: rgba(156,163,175,0.75); }

</style>
""", unsafe_allow_html=True)

# =============================================================================
# Constants
# =============================================================================
TYPES = [
    "Normal","Fire","Water","Electric","Grass","Ice","Fighting","Poison",
    "Ground","Flying","Psychic","Bug","Rock","Ghost","Dragon","Dark","Steel"
]

TYPE_EMOJI = {
    "Normal":"➖","Fire":"🔥","Water":"💧","Electric":"⚡","Grass":"🌿","Ice":"❄️",
    "Fighting":"🥊","Poison":"☠️","Ground":"⛰️","Flying":"🪽","Psychic":"🔮",
    "Bug":"🐛","Rock":"🪨","Ghost":"👻","Dragon":"🐉","Dark":"🌑","Steel":"⚙️"
}

# Soft gradients per primary type for opponent cards
# Stronger, Pokémon-themed gradients per primary type
TYPE_GRADIENT = {
    "Fire":     ("rgba(248,113,113,0.85)", "rgba(239,68,68,0.75)"),
    "Water":    ("rgba(56,189,248,0.85)",  "rgba(59,130,246,0.75)"),
    "Electric": ("rgba(250,204,21,0.90)",  "rgba(234,179,8,0.80)"),
    "Grass":    ("rgba(52,211,153,0.85)",  "rgba(34,197,94,0.75)"),
    "Ice":      ("rgba(125,211,252,0.90)", "rgba(59,130,246,0.75)"),
    "Fighting": ("rgba(248,113,113,0.90)", "rgba(220,38,38,0.80)"),
    "Poison":   ("rgba(192,132,252,0.90)", "rgba(168,85,247,0.80)"),
    "Ground":   ("rgba(234,179,8,0.90)",   "rgba(202,138,4,0.80)"),
    "Flying":   ("rgba(129,140,248,0.90)", "rgba(59,130,246,0.80)"),
    "Psychic":  ("rgba(244,114,182,0.90)", "rgba(236,72,153,0.80)"),
    "Bug":      ("rgba(190,242,100,0.90)", "rgba(132,204,22,0.80)"),
    "Rock":     ("rgba(253,186,116,0.90)", "rgba(234,179,8,0.80)"),
    "Ghost":    ("rgba(167,139,250,0.90)", "rgba(129,140,248,0.80)"),
    "Dragon":   ("rgba(96,165,250,0.90)",  "rgba(37,99,235,0.80)"),
    "Dark":     ("rgba(31,41,55,0.95)",    "rgba(15,23,42,0.90)"),
    "Steel":    ("rgba(148,163,184,0.90)", "rgba(75,85,99,0.80)"),
    "Normal":   ("rgba(209,213,219,0.85)", "rgba(156,163,175,0.75)"),
}

DEFAULT_CARD_GRADIENT = ("rgba(148,163,184,0.80)", "rgba(75,85,99,0.70)")

def _gradient_style_for_types(t1: Optional[str], t2: Optional[str]) -> str:
    primary_type = normalize_type(t1) or normalize_type(t2) or "Normal"
    secondary_type = normalize_type(t2)

    if secondary_type and secondary_type != primary_type:
        g1a, _ = TYPE_GRADIENT.get(primary_type, DEFAULT_CARD_GRADIENT)
        _, g2b = TYPE_GRADIENT.get(
            secondary_type,
            TYPE_GRADIENT.get(primary_type, DEFAULT_CARD_GRADIENT),
        )
        g1 = g1a
        g2 = g2b
    else:
        g1a, _ = TYPE_GRADIENT.get(primary_type, DEFAULT_CARD_GRADIENT)
        g1 = g1a
        g2 = "rgba(0,0,0,0)"

    return f"--opp-bg1:{g1};--opp-bg2:{g2};"

def _evo_gradient_vars(prefix: str, t1: Optional[str], t2: Optional[str]) -> str:
    """
    Build CSS vars for evo row halves.
    prefix: "evo-top" or "evo-bot"
    sets: --evo-top1/2 or --evo-bot1/2
    """
    primary_type = normalize_type(t1) or normalize_type(t2) or "Normal"
    secondary_type = normalize_type(t2)

    if secondary_type and secondary_type != primary_type:
        g1a, _ = TYPE_GRADIENT.get(primary_type, DEFAULT_CARD_GRADIENT)
        _, g2b = TYPE_GRADIENT.get(
            secondary_type,
            TYPE_GRADIENT.get(primary_type, DEFAULT_CARD_GRADIENT),
        )
        g1 = g1a
        g2 = g2b
    else:
        g1a, _ = TYPE_GRADIENT.get(primary_type, DEFAULT_CARD_GRADIENT)
        g1 = g1a
        g2 = "rgba(0,0,0,0)"

    # prefix is expected to be "evo-top" or "evo-bot"
    return f"--{prefix}1:{g1};--{prefix}2:{g2};"

def _cur_band_vars(t1: Optional[str], t2: Optional[str]) -> str:
    primary_type = normalize_type(t1) or normalize_type(t2) or "Normal"
    secondary_type = normalize_type(t2)

    if secondary_type and secondary_type != primary_type:
        g1a, _ = TYPE_GRADIENT.get(primary_type, DEFAULT_CARD_GRADIENT)
        _, g2b = TYPE_GRADIENT.get(
            secondary_type,
            TYPE_GRADIENT.get(primary_type, DEFAULT_CARD_GRADIENT),
        )
        g1 = g1a
        g2 = g2b
    else:
        g1a, _ = TYPE_GRADIENT.get(primary_type, DEFAULT_CARD_GRADIENT)
        g1 = g1a
        g2 = "rgba(0,0,0,0)"

    return f"--cur1:{g1};--cur2:{g2};"

# Global sprite size (px) so every sprite uses the same visual size
SPRITE_SIZE = 96
TRAINER_SPRITE_SIZE = 128

def type_emoji(t: Optional[str]) -> str:
    return TYPE_EMOJI.get(normalize_type(t) or "", "❔")

STONE_EMOJI = {
    "Fire Stone": "🔥", "Water Stone": "💧", "Thunder Stone": "⚡",
    "Leaf Stone": "🍃", "Moon Stone": "🌙", "Sun Stone": "☀️"
}
def stone_with_emoji(name: str) -> str:
    return f"{STONE_EMOJI.get(name, '🪨')} {name}" if name else name

TRADE_EVOLVE_LEVEL = 37
def stone_items_for_scope() -> list[str]:
    base = ["Fire Stone","Water Stone","Thunder Stone","Leaf Stone","Moon Stone"]
    if dex_max() == 386:
        base.append("Sun Stone")
    return base

# ==== Player starter -> Rival starter mapping (FR/LG logic) ====
STARTER_OPTIONS = ["Bulbasaur", "Charmander", "Squirtle"]
RIVAL_FOR_PLAYER = {
    "Bulbasaur": "Charmander",
    "Charmander": "Squirtle",
    "Squirtle":   "Bulbasaur",
}

# Lines used to detect which rival variant an encounter belongs to
BULBA_LINE   = {"bulbasaur","ivysaur","venusaur"}
CHAR_LINE    = {"charmander","charmeleon","charizard"}
SQUIRT_LINE  = {"squirtle","wartortle","blastoise"}

# ==== Rival helpers (FR/LG) ====
def _starter_to_line(starter_lc: str) -> set:
    s = starter_lc.lower()
    if s == "bulbasaur": return BULBA_LINE
    if s == "charmander": return CHAR_LINE
    if s == "squirtle": return SQUIRT_LINE
    return set()

def _counter_line_for(starter_lc: str) -> set:
    # Counter is the rival’s line
    m = {
        "bulbasaur": CHAR_LINE,
        "charmander": SQUIRT_LINE,
        "squirtle": BULBA_LINE,
    }
    return m.get(starter_lc.lower(), CHAR_LINE)

def is_rival_encounter(enc: dict) -> bool:
    lbl = (enc or {}).get("label", "") or ""
    base = (enc or {}).get("base_label", "") or ""
    t = f"{lbl} {base}".lower()
    # Heuristics: common labels used for the FRLG rival
    return any(k in t for k in ("rival", "blue", "gary"))

def _filter_rival_encounters(encs: list[dict], starter_name: str) -> list[dict]:
    need = _counter_line_for(starter_name)
    out = []
    for e in encs or []:
        mons = e.get("mons", [])
        # keep this rival encounter if **every** starter-line mon inside matches the counter line
        keep = False
        for m in mons:
            n = (m.get("species") or "").lower().replace("♀","f").replace("♂","m")
            if n in need:
                keep = True
                break
        if keep:
            out.append(e)
    return out

# ==== Version exclusives (base species only) ====
FR_EXCLUSIVE_BASES = {
    "Ekans","Oddish","Growlithe","Scyther","Electabuzz",
    "Shellder","Psyduck","Caterpie","Koffing","Mankey"
}
LG_EXCLUSIVE_BASES = {
    "Sandshrew","Bellsprout","Vulpix","Pinsir","Magmar",
    "Staryu","Slowpoke","Weedle","Grimer","Meowth"
}

def _version_mode() -> str:
    return (STATE.get("settings", {}) or {}).get("version", "combined")

def _is_allowed_by_version(base_name: str) -> bool:
    mode = _version_mode()
    if mode == "firered":
        return base_name not in LG_EXCLUSIVE_BASES
    if mode == "leafgreen":
        return base_name not in FR_EXCLUSIVE_BASES
    return True  # combined

OFFENSE_SCORE = {4.0: 4, 2.0: 2, 1.0: 0, 0.5: -2, 0.25: -4, 0.0: -5}
DEFENSE_SCORE  = {4.0:-4, 2.0:-2, 1.0: 0, 0.5:  2, 0.25:  4, 0.0:  5}

# ==== Starter -> sheet tab (gid) ====
# Bulbasaur -> Venusaur tab, Charmander -> Charizard tab, Squirtle -> Blastoise tab
STARTER_GID = {
    "Bulbasaur":  "422900446",  # Venusaur tab GID
    "Charmander": "775328099",  # Charizard tab GID
    "Squirtle":   "349723268",  # Blastoise tab GID
}

# Single sheet document id, we’ll always override gid based on starter
DEFAULT_SHEET_DOC = "1frqW2CeHop4o0NP6Ja_TAAPPkGIrvxkeQJBfyxFggyk"
DEFAULT_SHEET_URL = f"https://docs.google.com/spreadsheets/d/{DEFAULT_SHEET_DOC}/edit#gid=0"

STATE_PATH = "state.json"
STATE_BAK  = "state.backup.json"

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
# Persistence (per-user only; no server writes)
# =============================================================================
STATE_PATH = "state.json"
STATE_BAK  = "state.backup.json"

def _default_state() -> Dict:
    return {
        "moves_db": {},
        "species_db": {},
        "roster": [],
        "locks": [],
        "caught_counts": {},
        "fulfilled": [],
        "stone_bag": {},
        "fainted": [],
        "settings": {
            "unique_sig": True,
            "starter": "Bulbasaur",
            "default_level": 5,
            "hide_spinner": True,
            "catch_unlimited": False,
            "version": "combined",
            "visible_pages": {
                "pokedex": True, "battle": True, "evo": True,
                "opponents": False, "moves": False, "species": False,
                "saveload": True, "settings": True
            }
        },
        "opponents": {"meta":{"sheet_url":"","last_loaded":""},"encounters":[], "cleared":[]},
        "last_battle_pick": [0,0]
    }

def _atomic_write_json(path: str, data: Dict):
    # Kept for optional future use; not called while PERSIST_TO_DISK=False
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
    with open(STATE_BAK, "w", encoding="utf-8") as fb:
        fb.write(payload); fb.flush(); os.fsync(fb.fileno())

def save_state(state: Dict):
    # No-op unless you deliberately flip the flag
    if not PERSIST_TO_DISK:
        return
    try:
        _atomic_write_json(STATE_PATH, state)
    except Exception:
        try:
            with open(STATE_BAK, "w", encoding="utf-8") as fb:
                json.dump(state, fb, indent=2, ensure_ascii=False)
        except Exception:
            pass

def load_state() -> Dict:
    # Always start fresh per session when isolation is required
    if not PERSIST_TO_DISK:
        return _default_state()
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "rb") as f:
                return json.loads(decode_bytes(f.read()))
        except Exception:
            pass
    if os.path.exists(STATE_BAK):
        try:
            with open(STATE_BAK, "rb") as f:
                return json.loads(decode_bytes(f.read()))
        except Exception:
            pass
    return _default_state()

def migrate_state(state: Dict) -> Dict:
    state.setdefault("stone_bag", {})
    stg = state.setdefault("settings", {})
    stg.setdefault("default_level", 5)
    stg.setdefault("unique_sig", True)
    stg.setdefault("hide_spinner", True)
    stg.setdefault("catch_unlimited", False)
    stg.setdefault("version", "combined") 
    stg.setdefault("starter", "Bulbasaur") # NEW
    stg.setdefault("dex_scope", "151")  # "151" or "386"
    vis = stg.setdefault("visible_pages", {})
    for k, v in _default_state()["settings"]["visible_pages"].items():
        vis.setdefault(k, v)

    opp = state.setdefault("opponents", {"meta":{"sheet_url":"","last_loaded":""},"encounters":[],"cleared":[]})
    opp.setdefault("meta", {"sheet_url":"","last_loaded":""})
    opp.setdefault("encounters", [])
    opp.setdefault("cleared", [])

    state.setdefault("last_battle_pick", [0,0])
    state.setdefault("fainted", [])
    try:
        rguids = {m.get("guid") for m in state.get("roster", [])}
        state["fainted"] = [g for g in state["fainted"] if g in rguids]
    except Exception:
        pass
    return state

# Per-session state container
if "STATE" not in st.session_state:
    st.session_state["STATE"] = migrate_state(_default_state())

STATE = st.session_state["STATE"]

# =============================================================================
# Cached web fetchers
# =============================================================================
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

@st.cache_data(show_spinner=False)
def get_showdown_learnsets_cached() -> dict:
    return fetch_json("https://play.pokemonshowdown.com/data/learnsets.json")

@st.cache_data(show_spinner=False)
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


FRLG_L1_OVERRIDES = {
  'articuno': ['Gust','Powder Snow'],
  'zapdos':   ['Peck','Thundershock'],
  'moltres':  ['Ember','Wing Attack'],
}
# Species-specific FR/LG removals (level-up moves that do not exist in FR/LG)
FRLG_REMOVE_MOVES = {
    # Charmander line never learns Rage by level-up in FR/LG
    "charmander": {"Rage"},
    "charmeleon": {"Rage"},
    "charizard": {"Rage"},
}
# Extra FRLG level corrections to guarantee core early moves exist
FRLG_LEVEL_ADD = {
    "charmander": {7: ["Ember"]},
    "bulbasaur":  {7: ["Vine Whip"]},
    "squirtle":   {7: ["Bubble"]},
}

def _apply_frlg_overrides(species_name: str, levelmap: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """
    Enforce FRLG-specific corrections on the level-up learnset:
      - Legendary birds get correct L1 moves (Gust/Powder Snow, Peck/Thundershock, Ember/Wing Attack)
      - Starters get guaranteed early moves at canonical FRLG levels (e.g., Ember at 7 for Charmander)
    """
    sk = species_key(species_name)
    out = {str(int(k)): list(v) for k, v in (levelmap or {}).items()}

    # L1 bird overrides
    for mv in FRLG_L1_OVERRIDES.get(sk, []):
        _merge_into_levelmap(out, 1, mv)

    # Specific early moves (e.g., Ember at 7)
    for lv, mvs in FRLG_LEVEL_ADD.get(sk, {}).items():
        for mv in mvs:
            _merge_into_levelmap(out, int(lv), mv)

    # Keep only damaging, unique, sorted
    clean = {}
    for k, arr in out.items():
        seen = set(); lst = []
        for m in arr:
            nm = (lookup_move(m) or {}).get("name", clean_move_token(m))
            if not nm or not move_is_damaging(nm):
                continue
            lk = nm.lower()
            if lk in seen:
                continue
            seen.add(lk); lst.append(nm)
        if lst:
            clean[str(int(k))] = sorted(lst)
    return clean


def rebuild_learnset_for(species_name: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    gen3 = get_gen3_data_cached()
    keys = list(gen3.keys())
    sk = species_key(species_name)
    gk = sk if sk in gen3 else next((k for k in keys if species_key(k) == sk), None)

    # 1) Base: FR/LG-aligned Gen3 level-up dump (your existing logic)
    if gk and isinstance(gen3.get(gk, {}).get("level", {}), dict):
        for lv, mv in gen3[gk]["level"].items():
            seq = mv if isinstance(mv, list) else [mv]
            for m in seq:
                rec = lookup_move(m)
                nm = rec["name"] if rec else clean_move_token(m)
                if nm and move_is_damaging(nm):
                    _merge_into_levelmap(out, int(re.sub(r"\D","",str(lv)) or "0"), nm)

    # 2) Merge-in Pokémon Showdown only for Gen 3 level-up (3Lxx)
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
            for s in sources:
                m = re.match(r"^3L(\d+)$", str(s))
                if m:
                    levels.append(int(m.group(1)))
            if not levels:
                continue
            rec = MOVES_BY_ID.get(move_id(move_id_key))
            nm = rec["name"] if rec else clean_move_token(move_id_key)
            if not nm or not move_is_damaging(nm):
                continue
            for lv in levels:
                _merge_into_levelmap(out, lv, nm)

    # 3) Apply FR/LG species-specific removals (e.g., strip Rage from the Charmander line)
    rm = FRLG_REMOVE_MOVES.get(ps_id(species_name), set())
    if rm:
        for k in list(out.keys()):
            out[k] = [m for m in out[k] if m not in rm]
            if not out[k]:
                del out[k]

    # 4) Enforce FR/LG L1 overrides for legendary birds (so Articuno gets Gust/Powder Snow at Lv1)
    ov = FRLG_L1_OVERRIDES.get(ps_id(species_name))
    if ov:
        for name in ov:
            _merge_into_levelmap(out, 1, name)

    # keep only non-empty levels
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

@st.cache_data(show_spinner=False)
def build_state_from_web_cached(maxdex: int) -> Dict:
    pokedex = get_pokedex_cached()

    moves_db = {}
    for rec in MOVES_MASTER.values():
        moves_db[norm_key(rec["name"])] = {
            "name": rec["name"],
            "type": normalize_type(rec.get("type", "")),
            "meta": {"species_scope": str(maxdex)}
        }

    species_db = {}
    for sid, sd in pokedex.items():
        if not is_base_for_scope(sd, pokedex, maxdex):
            continue

        name = sd.get("name", sid)
        types_raw = sd.get("types", [])
        t1, t2 = purge_fairy_types_pair(types_raw)
        base = sd.get("baseStats", {})
        total = int(sum(base.values())) if base else 0

        learnset = rebuild_learnset_for(name) or {}

        species_db[species_key(name)] = {
            "name": name,
            "types": [t1, t2],
            "total": total,
            "learnset": learnset,
        }

    return {
        "moves_db": moves_db,
        "species_db": species_db,
        "roster": STATE.get("roster", []),
        "locks": STATE.get("locks", []),
        "caught_counts": STATE.get("caught_counts", {}),
        "fulfilled": STATE.get("fulfilled", []),
        "stone_bag": STATE.get("stone_bag", {}),
        "settings": STATE.get("settings", {}),
        "opponents": STATE.get("opponents", {"meta":{"sheet_url":"","last_loaded":""},"encounters":[], "cleared":[]}),
        "last_battle_pick": STATE.get("last_battle_pick", [0,0]),
    }

def ensure_species_in_db(name: str, scope_maxdex: Optional[int] = None) -> bool:
    """
    Ensure a species exists in STATE['species_db'].

    scope_maxdex:
      - None  → use dex_max() (respects Pokédex scope, 151/386)
      - 386   → use full Gen 3, regardless of current scope (used for opponents)
    """
    if scope_maxdex is None:
        scope_maxdex = dex_max()

    sk = species_key(name)
    if sk in STATE["species_db"]:
        return True

    dex = get_pokedex_cached()
    maxdex = int(scope_maxdex)

    def _find_record(target_name: str):
        rec = dex.get(ps_id(target_name))
        if rec and rec.get("forme"):
            rec = None
        if rec and not (isinstance(rec.get("num"), int) and 1 <= rec.get("num") <= maxdex):
            rec = None
        if rec:
            return rec

        # fallback: scan by normalized name
        for _, r in dex.items():
            if not r:
                continue
            if ps_id(r.get("name", "")) != ps_id(target_name):
                continue
            if r.get("forme"):
                continue
            num = r.get("num")
            if isinstance(num, int) and 1 <= num <= maxdex:
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

    STATE["species_db"][species_key(nm)] = {
        "name": nm,
        "types": [t1, t2],
        "total": total,
        "learnset": learnset,
    }
    save_state(STATE)
    return True

def base_key_for(name: str) -> str:
    dex = get_pokedex_cached()
    cur = dex.get(ps_id(name))
    if not cur:
        return species_key(name)
    maxdex = dex_max()
    while True:
        pre = cur.get("prevo")
        if not pre:
            break
        pre_rec = dex.get(ps_id(pre))
        if not pre_rec or pre_rec.get("forme"):
            break
        num = pre_rec.get("num")
        if isinstance(num, int) and 1 <= num <= maxdex:
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

    # We use the full Pokédex to detect which cell is a valid species name
    try:
        dex = get_pokedex_cached() or {}
    except Exception:
        dex = {}
    maxdex = 386

    def _looks_like_species(cell: str) -> bool:
        val = clean_invisibles(cell).strip()
        if not val:
            return False
        sid = ps_id(val)
        if not sid:
            return False
        sd = dex.get(sid)
        if not sd:
            # small fallback: scan by normalized name
            for rec in dex.values():
                if not rec:
                    continue
                nm = rec.get("name", "")
                if ps_id(nm) == sid:
                    sd = rec
                    break
        if not sd:
            return False
        if sd.get("forme"):
            return False
        num = sd.get("num")
        return isinstance(num, int) and 1 <= num <= maxdex

    for r in rows:
        rownum += 1

        # guarantee at least 10 columns (trainer, …, 4 moves)
        if len(r) < 10:
            r = r + [""] * (10 - len(r))

        # empty row? skip
        if not any((c or "").strip() for c in r):
            continue

        # trainer cell
        raw_trainer = (r[0] or "")
        trainer_cell = clean_invisibles(raw_trainer).strip()

        # Normalize to collapse small differences into the same base label
        norm_base = re.sub(r"\s+", " ", trainer_cell).strip()

        # New encounter starts whenever trainer cell is non-empty
        if norm_base:
            base_name = norm_base
            count = name_counts.get(base_name, 0) + 1
            name_counts[base_name] = count
            suffix = f" #{count}" if count > 1 else ""
            label_unique = f"{base_name}{suffix}"
            current_enc = {"label": label_unique, "base_label": base_name, "mons": []}
            encounters_list.append(current_enc)

        # Find Pokémon species and (nearby) level in this row
        poke = ""
        lvl_str = ""

        # All but last 4 columns are metadata (location, notes, level, species, etc.)
        upper_bound = max(1, len(r) - 4)
        for idx in range(1, upper_bound):
            cell = r[idx]
            if _looks_like_species(cell):
                poke = clean_invisibles(cell).strip()
                # try next few columns for a level (digits)
                for j in range(idx + 1, min(idx + 4, upper_bound)):
                    lv_cand = clean_invisibles(r[j]).strip()
                    if re.search(r"\d+", lv_cand or ""):
                        lvl_str = lv_cand
                        break
                break

        # if we still have no trainer context or no Pokémon, skip the row
        if not current_enc or not poke:
            continue

        # level parsing
        try:
            m = re.findall(r"\d+", lvl_str or "")
            level = int(m[0]) if m else 1
        except Exception:
            level = 1

        # species record, fetch if needed
        sk = species_key(poke)
        sp = STATE["species_db"].get(sk)
        if not sp:
            if ensure_species_in_db(poke, scope_maxdex=386):
                sp = STATE["species_db"].get(sk)
            if not sp:
                # unknown species? skip this row
                continue

        # Column G (index 6) holds the *exact* move this Pokémon uses in the sheet.
        # We take only that cell, keep it if it is a damaging move, and type it.
        # Columns G–J (indices 6–9) hold up to 4 moves for this Pokémon.
        # We take those cells, keep only damaging + allowed moves, and type them.
        typed_moves: List[Tuple[str, str]] = []
        seen_moves: set[str] = set()

        # Limit to 4 move columns: G, H, I, J → indices 6–9
        for col in range(6, min(len(r), 10)):
            raw_cell = clean_invisibles(r[col]).strip()
            if not raw_cell:
                continue

            info = lookup_move(raw_cell)
            # Canonical name if we know it, otherwise raw text
            move_name = (info.get("name", raw_cell) if info else raw_cell)
            key = move_name.lower()

            # Avoid duplicates and excluded moves
            if key in seen_moves:
                continue
            if not move_is_damaging(move_name) or key in FRLG_EXCLUDE_MOVES:
                continue

            # Determine move type (from lookup or cached moves db)
            mtype = normalize_type(
                (info.get("type") if info else None)
                or STATE["moves_db"].get(norm_key(move_name), {}).get("type", "")
            )
            if not mtype:
                continue

            ensure_move_in_db(move_name, default_type=mtype)
            typed_moves.append((move_name, mtype))
            seen_moves.add(key)
        mon = {
            "species": sp["name"],
            "level": int(level),
            "types": purge_fairy_types_pair(sp["types"]),
            "moves": typed_moves,
            "source_row": rownum,
            "total": sp["total"],
        }
        current_enc["mons"].append(mon)

    # filter empty encounters (and accidental “exp” / “extra exp” labels)
    return [
        enc
        for enc in encounters_list
        if enc.get("mons")
        and not re.match(
            r"^\s*(?:extra\s+)?exp(?:erience)?\b",
            (enc.get("base_label", "") or "").lower(),
        )
    ]

@st.cache_data(show_spinner=False)
def _parse_csv_to_encounters(csv_text: str) -> List[Dict]:
    # Cache the CSV-to-encounters parse. Same output as load_venusaur_sheet.
    return load_venusaur_sheet(csv_text)

@st.cache_data(show_spinner=False)
def _build_encounters_for(starter: str, sheet_url: str) -> List[Dict]:
    main_gid = STARTER_GID.get(starter, STARTER_GID["Bulbasaur"])
    main_csv = parse_sheet_url_to_csv(sheet_url, preferred_gid=main_gid)
    enc_main = _parse_csv_to_encounters(fetch_text(main_csv)) if main_csv else []

    all_rivals = []
    for s in STARTER_OPTIONS:
        g = STARTER_GID.get(s)
        csv_u = parse_sheet_url_to_csv(sheet_url, preferred_gid=g)
        if not csv_u:
            continue
        encs = _parse_csv_to_encounters(fetch_text(csv_u))
        all_rivals.extend([e for e in encs if is_rival_encounter(e)])

    rivals_filtered = _filter_rival_encounters(all_rivals, starter)

    # Keep everything from the starter tab (including Rival fights if present)
    by_label = {}
    for e in enc_main:
        by_label[e["label"]] = e

    # Add cross-tab Rival variants only when the label isn't already present
    for e in rivals_filtered:
        by_label.setdefault(e["label"], e)

    merged = list(by_label.values())

    # Preserve starter-tab order first, then any extra rivals we added
    main_labels = [e["label"] for e in enc_main]
    tail = [e for e in merged if e["label"] not in main_labels]
    return enc_main + tail


def _reload_opponents_for_current_settings():
    try:
        url = (STATE.get("opponents", {}).get("meta", {}).get("sheet_url") or DEFAULT_SHEET_URL)
        starter = (STATE.get("settings", {}) or {}).get("starter", "Bulbasaur")
        encounters = _build_encounters_for(starter, url)
        STATE["opponents"]["encounters"] = encounters
        STATE["opponents"]["meta"]["sheet_url"] = url
        STATE["opponents"]["meta"]["last_loaded"] = f"starter={starter}"
        STATE["last_battle_pick"] = [0, 0]  # reset stale indices
        save_state(STATE)
    except Exception:
        pass

def autoload_opponents_if_empty():
    try:
        if STATE["opponents"]["encounters"]:
            return
        starter = (STATE.get("settings", {}) or {}).get("starter", "Bulbasaur")
        encounters = _build_encounters_for(starter, DEFAULT_SHEET_URL)
        if encounters:
            STATE["opponents"]["encounters"] = encounters
            STATE["opponents"]["meta"]["sheet_url"] = DEFAULT_SHEET_URL
            STATE["opponents"]["meta"]["last_loaded"] = f"starter={starter}"
            save_state(STATE)
    except Exception:
        pass

# =============================================================================
# Evolutions
# =============================================================================
def available_evos_for(species_name: str) -> List[Dict]:
    dex = get_pokedex_cached()
    maxdex = dex_max()
    opts: List[Dict] = []
    me = dex.get(ps_id(species_name))
    if not me: return []
    for e in me.get("evos", []) or []:
        tgt = dex.get(ps_id(e))
        if not tgt: continue
        if tgt.get("forme"): continue
        if not (isinstance(tgt.get("num"), int) and 1 <= tgt.get("num") <= maxdex): continue
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

def dex_max() -> int:
    return 386 if (STATE.get("settings", {}).get("dex_scope", "151") == "386") else 151

def is_base_for_scope(sd: dict, dex: dict, maxdex: int) -> bool:
    num = sd.get("num")
    if not isinstance(num, int) or not (1 <= num <= maxdex):
        return False
    if sd.get("forme"):
        return False
    prevo = sd.get("prevo")
    if not prevo:
        return True
    pre = dex.get(ps_id(prevo))
    pnum = pre.get("num") if pre else None
    return not (isinstance(pnum, int) and 1 <= pnum <= maxdex)

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
        if not STATE.get("species_db"):
            base = build_state_from_web_cached(dex_max())
            STATE["moves_db"] = base["moves_db"]
            STATE["species_db"] = base["species_db"]
            # no save_state here; per-session only
        step += 1; bar.progress(int(step/6*100), text="Species ready")
        autoload_opponents_if_empty(); step += 1; bar.progress(int(step/6*100), text="Opponents ready")
    finally:
        bar.progress(100, text="Ready")
        progress.empty()

ensure_bootstrap_ready()
if not STATE.get("species_db"):
    base = build_state_from_web_cached(dex_max())
    STATE["moves_db"] = base["moves_db"]
    STATE["species_db"] = base["species_db"]
    STATE["meta"] = base.get("meta", {"species_scope": str(dex_max())})  # <<< ADD THIS LINE

# =============================================================================
# UI helpers
# =============================================================================
@st.cache_data(show_spinner=False)
def _dex_num_for_name_cached(name: str) -> Optional[int]:
    """
    Map a species name to its Pokédex number (within current scope) so we can
    build a stable sprite URL. Uses the same Showdown pokedex data as the rest
    of the app.
    """
    if not name:
        return None
    try:
        dex = get_pokedex_cached() or {}
    except Exception:
        return None

    # Clean up weird spaces / non-breaking spaces from sheet etc.
    cleaned = clean_invisibles(str(name))
    sid = ps_id(cleaned)
    maxdex = 386

    rec = dex.get(sid)
    if rec and isinstance(rec.get("num"), int) and 1 <= rec["num"] <= maxdex and not rec.get("forme"):
        return rec["num"]

    # Fallback: scan by normalized name
    for r in dex.values():
        if not r:
            continue
        nm = r.get("name", "")
        if ps_id(nm) == sid and not r.get("forme"):
            num = r.get("num")
            if isinstance(num, int) and 1 <= num <= maxdex:
                return num
    return None


def _bulba_frlg_sprite_url(num: int) -> Optional[str]:
    """
    Build a Bulbagarden Archives URL for the FRLG-style sprite.

    1–151   → Spr_3f_XXX.png (FRLG Kanto set)
    152–386 → Spr_3r_XXX.png (rest of the Gen 3 FRLG set)

    Uses Special:FilePath so we don't rely on the MD5 upload path.
    """
    try:
        if not isinstance(num, int) or num < 1 or num > 386:
            return None

        prefix = "3f" if num <= 151 else "3r"
        fname = f"Spr_{prefix}_{num:03d}.png"
        safe_name = quote(fname.replace(" ", "_"))
        return f"https://archives.bulbagarden.net/wiki/Special:FilePath/{safe_name}"
    except Exception:
        return None

def sprite_url_for_species(name: str) -> Optional[str]:
    """
    Return a FRLG-style front sprite URL for any Gen 3 species (1–386).
    Uses Bulbagarden Archives 'Spr_3r_XXX.png' with computed MD5 path.
    """
    num = _dex_num_for_name_cached(name)
    if not num:
        return None
    return _bulba_frlg_sprite_url(num)

def sprite_img_html(name: str, size: int = None) -> str:
    """
    Small inline <img> tag for use in st.markdown(..., unsafe_allow_html=True).
    Uses a global SPRITE_SIZE so everything stays consistent.
    """
    url = sprite_url_for_species(name)
    if not url:
        return ""
    s = SPRITE_SIZE if size is None else size
    safe_name = (name or "").replace('"', "&quot;")
    return (
        f'<img src="{url}" class="sprite-inline" '
        f'width="{s}" height="{s}" alt="{safe_name} sprite"/>'
    )

# Canonical FRLG trainer classes and how to detect them from a sheet label
FRLG_TRAINER_CLASS_KEYWORDS = [
    ("Rival", ["rival", "blue", "gary"]),
    ("Champion", ["champion "]),  # space avoids matching 'champion 2' as a class name

    # Very specific classes first to avoid mis-hits
    ("Team Rocket Grunt", ["rocket grunt", "rocket gr.", "rocket  "]),
    ("Team Rocket Grunt", ["rocket", "grunt"]),  # generic rocket/grunt

    ("Scientist", ["scientist", "gideon"]),  # Gideon should be Scientist

    # Gendered classes (we refine later)
    ("Cooltrainer", ["cooltrainer", "cool trainer"]),
    ("Cooltrainer", ["cool couple", "coolcouple", "cool_couple"]),
    ("Swimmer", ["swimmer"]),

    # Common basic overworld classes
    ("Youngster", ["youngster"]),
    ("Bug Catcher", ["bug catcher"]),
    ("Lass", ["lass"]),
    ("Camper", ["camper"]),
    ("Picnicker", ["picnicker"]),
    ("Fisherman", ["fisher", "fisherman"]),
    ("Engineer", ["engineer"]),
    ("Hiker", ["hiker"]),
    ("Sailor", ["sailor", "saillor"]),
    ("Bird Keeper", ["bird keeper"]),
    ("Blackbelt", ["blackbelt", "black belt"]),
    ("Beauty", ["beauty"]),
    ("Gentleman", ["gentleman", "gentlman"]),
    ("Twins", ["twins"]),
    ("Young Couple", ["young couple", "youngcouple"]),
    ("Sis and Bro", ["sis and bro", "sis & bro"]),
    ("Psychic", ["psychic"]),
    ("Pokémaniac", ["pokémaniac", "pokemaniac"]),
    ("Super Nerd", ["super nerd"]),
    ("Juggler", ["juggler"]),
    ("Tamer", ["tamer"]),
    ("Gamer", ["gamer", "gambler"]),
    ("Cue Ball", ["cue ball"]),
    ("Rocker", ["rocker"]),
    ("Biker", ["biker"]),
    ("Burglar", ["burglar"]),
    ("Aroma Lady", ["aroma lady"]),
    ("Tuber", ["tuber"]),
    ("Cool Couple", ["cool couple"]),
    ("Channeler", ["channeler", "channeller"]),
    ("Crush Girl", ["crush girl"]),
    ("Crush Kin", ["crush kin"]),
    ("Pokémon Ranger", ["pokemon ranger", "pokémon ranger"]),
    ("Pokémon Breeder", ["pokemon breeder", "pokémon breeder"]),
    ("Painter", ["painter"]),
    ("Lady", ["lady "]),
    ("Ruin Maniac", ["ruin maniac"]),

    # Gym Leaders & E4 – same as before
    ("Gym Leader Brock", ["brock"]),
    ("Gym Leader Misty", ["misty"]),
    ("Gym Leader Lt. Surge", ["lt surge", "lt. surge"]),
    ("Gym Leader Erika", ["erika"]),
    ("Gym Leader Koga", ["koga"]),
    ("Gym Leader Sabrina", ["sabrina"]),
    ("Gym Leader Blaine", ["blaine"]),
    ("Gym Leader Giovanni", ["giovanni"]),
    ("Elite Four Lorelei", ["lorelei"]),
    ("Elite Four Bruno", ["bruno"]),
    ("Elite Four Agatha", ["agatha"]),
    ("Elite Four Lance", ["lance"]),
]

@st.cache_data(show_spinner=False)
def trainer_class_from_label(label: str) -> str:
    """
    Map a sheet label like 'Youngster Joey #2' or 'Rocket Grunt 3'
    to a canonical FRLG trainer class string.
    Adds gender detection for Cooltrainer & Swimmer based on name.
    """
    s = (label or "").lower()
    base_label = s

    # 1) Base class from keyword table
    base_cls = None
    for cls, keys in FRLG_TRAINER_CLASS_KEYWORDS:
        if any(k in base_label for k in keys):
            base_cls = cls
            break

    # 2) If no match, fallback to "Trainer"
    if not base_cls:
        tokens = base_label.split()
        if not tokens:
            return "Trainer"
        return tokens[0].capitalize()

    # 3) Refine gendered classes based on name
    if base_cls in ("Cooltrainer", "Swimmer", "Team Rocket Grunt", "Psychic", "Pokémon Ranger"):
        # Try to extract a name/gender token from the label.
        # Strategy: last alphabetic token in the label, so this works for:
        #   "Cool Trainer Leroy #2", "Swimmer Anna", "Team Rocket Grunt F", etc.
        parts = base_label.replace("#", " ").split()
        name_token = None
        for tok in reversed(parts):
            t = tok.strip(",.")
            if t.isalpha():
                name_token = t
                break

        female_markers = {"michelle", "anna", "jessica", "sarah", "amber", "megan", "linda", "f", "♀"}
        male_markers = {"leroy", "kevin", "mark", "gary", "john", "m", "♂"}

        # Default suffix style depends on class
        if base_cls in ("Psychic", "Pokémon Ranger"):
            cls_m = base_cls + " M"
            cls_f = base_cls + " F"
        else:
            cls_m = base_cls + "♂"
            cls_f = base_cls + "♀"

        if name_token:
            n = name_token.lower()
            if n in female_markers:
                return cls_f
            if n in male_markers:
                return cls_m

        # Fallback based on explicit 'F' or 'M' substrings in the whole label
        if any(tok in base_label for tok in (" f ", "(f)", "♀")):
            return cls_f
        if any(tok in base_label for tok in (" m ", "(m)", "♂")):
            return cls_m

        # Default to male variant where it exists
        if base_cls == "Team Rocket Grunt":
            return cls_m
        if base_cls in ("Cooltrainer", "Swimmer"):
            return cls_m

        # Fallback based on explicit 'F' or 'M' in the label text
        if any(tok in base_label for tok in (" f ", "(f)", "♀")):
            return cls_f
        if any(tok in base_label for tok in (" m ", "(m)", "♂")):
            return cls_m

        # Default: male, because that’s the more common sprite
        return cls_m

    return base_cls

# Use Bulbagarden Archives FRLG trainer sprites.
# We go through Special:FilePath so we don't need the hashed upload path.
FRLG_TRAINER_SPRITE_BASE = "https://archives.bulbagarden.net/wiki/Special:FilePath"

# IMPORTANT:
# I’m not going to invent filenames I can’t guarantee.
# We *know* "Spr FRLG Picnicker.png" exists from your example, so we use it
# as a safe generic fallback. If you want per-class sprites, add more entries
# here with the exact Bulbagarden filenames.
FRLG_TRAINER_SPRITES: Dict[str, str] = {
    # Generic fallback
    "Trainer":  "Spr FRLG Picnicker.png",

    # Common overworld classes
    "Youngster":        "Spr FRLG Youngster.png",
    "Bug Catcher":      "Spr FRLG Bug Catcher.png",
    "Lass":             "Spr FRLG Lass.png",
    "Camper":           "Spr FRLG Camper.png",
    "Picnicker":        "Spr FRLG Picnicker.png",
    "Hiker":            "Spr FRLG Hiker.png",
    "Fisherman":        "Spr FRLG Fisherman.png",
    "Engineer":         "Spr FRLG Engineer.png",
    "Sailor":           "Spr FRLG Sailor.png",
    "Bird Keeper":      "Spr FRLG Bird Keeper.png",
    "Blackbelt":        "Spr FRLG Black Belt.png",
    "Beauty":           "Spr FRLG Beauty.png",
    "Psychic":          "Spr FRLG Psychic.png",
    "Scientist":        "Spr FRLG Scientist.png",
    "Pokémaniac":       "Spr FRLG PokéManiac.png",
    "Super Nerd":       "Spr FRLG Super Nerd.png",
    "Juggler":          "Spr FRLG Juggler.png",
    "Tamer":            "Spr FRLG Tamer.png",
    "Gamer":            "Spr FRLG Gamer.png",
    "Cue Ball":         "Spr FRLG Cue Ball.png",
    "Rocker":           "Spr FRLG Rocker.png",
    "Biker":            "Spr FRLG Biker.png",
    "Gentleman":        "Spr FRLG Gentleman.png",
    "Twins":            "Spr FRLG Twins.png",
    "Young Couple":     "Spr FRLG Young Couple.png",
    "Sis and Bro":      "Spr FRLG Sis and Bro.png",
    "Burglar":          "Spr FRLG Burglar.png",
    "Aroma Lady":       "Spr FRLG Aroma Lady.png",
    "Tuber":            "Spr FRLG Tuber.png",
    "Cool Couple":      "Spr FRLG Cool Couple.png",
    "Channeler":        "Spr FRLG Channeler.png",
    "Crush Girl":       "Spr FRLG Crush Girl.png",
    "Crush Kin":        "Spr FRLG Crush Kin.png",
    "Psychic F":        "Spr FRLG Psychic F.png",
    "Psychic M":        "Spr FRLG Psychic M.png",
    "Pokémon Ranger F": "Spr FRLG Pokémon Ranger F.png",
    "Pokémon Ranger M": "Spr FRLG Pokémon Ranger M.png",
    "Pokémon Breeder":  "Spr FRLG Pokémon Breeder.png",
    "Painter":          "Spr FRLG Painter.png",
    "Lady":             "Spr FRLG Lady.png",
    "Ruin Maniac":      "Spr FRLG Ruin Maniac.png",
    "Cooltrainer♂":         "Spr FRLG Cooltrainer M.png",
    "Cooltrainer♀":         "Spr FRLG Cooltrainer F.png",
    "Swimmer♂":             "Spr FRLG Swimmer M.png",
    "Swimmer♀":             "Spr FRLG Swimmer F.png",

    # Rocket Grunts: gendered + generic fallback
    "Team Rocket Grunt♂":   "Spr FRLG Team Rocket Grunt M.png",
    "Team Rocket Grunt♀":   "Spr FRLG Team Rocket Grunt F.png",
    "Team Rocket Grunt":    "Spr FRLG Team Rocket Grunt M.png",

    # Blue / Rival fallbacks (special logic already picks the exact ones by meeting)
    "Rival":            "Spr FRLG Blue 1.png",
    "Champion":         "Spr FRLG Blue 3.png",

    # Gym Leaders & E4 – basic mapping
    "Gym Leader Brock":     "Spr FRLG Brock.png",
    "Gym Leader Misty":     "Spr FRLG Misty.png",
    "Gym Leader Lt. Surge": "Spr FRLG Lt Surge.png",
    "Gym Leader Erika":     "Spr FRLG Erika.png",
    "Gym Leader Koga":      "Spr FRLG Koga.png",
    "Gym Leader Sabrina":   "Spr FRLG Sabrina.png",
    "Gym Leader Blaine":    "Spr FRLG Blaine.png",
    "Gym Leader Giovanni":  "Spr FRLG Giovanni.png",
    "Elite Four Lorelei":   "Spr FRLG Lorelei.png",
    "Elite Four Bruno":     "Spr FRLG Bruno.png",
    "Elite Four Agatha":    "Spr FRLG Agatha.png",
    "Elite Four Lance":     "Spr FRLG Lance.png",
}

# Exact filenames for Blue's FRLG trainer sprites
BLUE_SPRITE_VARIANTS: Dict[str, str] = {
    "blue1": "Spr FRLG Blue 1.png",
    "blue2": "Spr FRLG Blue 2.png",
    "blue3": "Spr FRLG Blue 3.png",
}

# Optional explicit overrides for known labels if the sheet ever changes wording
BLUE_LABEL_OVERRIDES: Dict[str, str] = {
    # keys are lowercase substrings in the *base_label* or label
    # "ss anne rival" → Blue 2
    "ss anne rival": "blue2",
    "ss anne blue": "blue2",
    # Champion fight(s)
    "champion rival": "blue3",
    "champion blue": "blue3",
}


def _blue_sprite_filename_for_meeting(label: str) -> Optional[str]:
    """
    Return the correct 'Spr FRLG Blue X.png' for this encounter label.

    1) If the label matches a known override (SS Anne, Champion, etc.), use that.
    2) Otherwise, count how many Blue/Rival encounters appear before this one
       in STATE['opponents']['encounters'] and pick a sprite tier:

         first 3 → Blue 1
         next 4  → Blue 2
         rest    → Blue 3
    """
    label_str = str(label or "")
    s_label = label_str.lower()

    # Normalise out punctuation so "S.S. Anne" matches "ss anne"
    s_clean = re.sub(r"[^a-z0-9 ]", "", s_label)

    # 1) Explicit label overrides
    try:
        for key, variant in BLUE_LABEL_OVERRIDES.items():
            if key in s_clean:
                return BLUE_SPRITE_VARIANTS.get(variant)
    except Exception:
        pass

    # 2) Meeting index fallback based on encounter order
    try:
        encs = (STATE.get("opponents", {}) or {}).get("encounters", []) or []
    except Exception:
        encs = []

    meeting = 0
    for enc in encs:
        base = f"{enc.get('label','')} {enc.get('base_label','')}".lower()
        if any(k in base for k in ("rival", "blue", "gary")):
            meeting += 1
            if enc.get("label") == label_str:
                if meeting <= 3:
                    return BLUE_SPRITE_VARIANTS["blue1"]
                elif meeting <= 7:
                    return BLUE_SPRITE_VARIANTS["blue2"]
                else:
                    return BLUE_SPRITE_VARIANTS["blue3"]

    return None

def trainer_sprite_url(label: str) -> Optional[str]:
    """
    Return a Bulbagarden FRLG trainer sprite URL based on the encounter label.

    Special case: Blue (Rival/Champion) uses his 'Blue 1/2/3' sprites chosen
    by how many times you've met him in the current opponents list.
    """
    if not label:
        return None

    label_str = str(label)
    s = label_str.lower()

    # ---- Special handling: Blue / Rival ----
    # Match the same keywords as is_rival_encounter: 'rival', 'blue', 'gary'.
    if any(k in s for k in ("rival", "blue", "gary")):
        fname = _blue_sprite_filename_for_meeting(label_str)
        if fname:
            title = fname.replace(" ", "_")
            return f"{FRLG_TRAINER_SPRITE_BASE}/{quote(title)}"

    # ---- Normal class-based mapping ----
    cls = trainer_class_from_label(label_str)
    filename = FRLG_TRAINER_SPRITES.get(cls) or FRLG_TRAINER_SPRITES.get("Trainer")
    if not filename or not FRLG_TRAINER_SPRITE_BASE:
        return None

    title = filename.replace(" ", "_")
    return f"{FRLG_TRAINER_SPRITE_BASE}/{quote(title)}"

def trainer_sprite_img_html(label: str, size: int = None) -> str:
    url = trainer_sprite_url(label)
    if not url:
        return ""
    s = TRAINER_SPRITE_SIZE if size is None else size
    safe_label = (label or "").replace('"', "&quot;")
    return (
        f'<img src="{url}" class="sprite-inline" '
        f'width="{s}" height="{s}" alt="{safe_label} trainer sprite"/>'
    )

def _frlg_allowed_damaging_moves_set() -> set:
    """
    Union of *all* damaging FRLG-legal moves across in-scope species:
    - Level-up (3Lxx)
    - TM/HM (3M)
    - Tutor (3T)
    Plus anything already seen on roster/opponents (so we never drop user data).
    Cached per dex scope for speed.
    """
    cache = STATE.setdefault("_allowed_moves_cache", {})
    scope_key = f"scope_{dex_max()}"
    if scope_key in cache:
        return set(cache[scope_key])

    allowed = set()

    # Pull from canonical FRLG resolver (includes 3L, 3M, 3T)
    try:
        for sp in (STATE.get("species_db", {}) or {}).values():
            name = sp.get("name", "")
            for mv in (legal_moves_for_species_chain(name) or []):
                nm = (lookup_move(mv) or {}).get("name", clean_move_token(mv))
                if nm and move_is_damaging(nm):
                    allowed.add(nm)
    except Exception:
        pass

    # Also include any damaging moves already present on user state
    try:
        for mon in STATE.get("roster", []):
            for nm, _tp in mon.get("moves", []) or []:
                if nm and move_is_damaging(nm):
                    allowed.add(nm)
    except Exception:
        pass
    try:
        for enc in STATE.get("opponents", {}).get("encounters", []) or []:
            for mon in enc.get("mons", []) or []:
                for nm, _tp in mon.get("moves", []) or []:
                    if nm and move_is_damaging(nm):
                        allowed.add(nm)
    except Exception:
        pass

    cache[scope_key] = list(sorted(allowed))
    return allowed

def all_damaging_moves_sorted() -> List[str]:
    allowed = _frlg_allowed_damaging_moves_set()
    return sorted(allowed, key=lambda s: s.lower())

def canonical_typed(move_name: str) -> Optional[Tuple[str, str]]:
    """
    Normalise a move name to (canonical_name, type).

    - Filters out FRLG_EXCLUDE_MOVES.
    - Only keeps damaging moves (per move_is_damaging).
    - Does *not* require the move to already be in the global FRLG allowed set,
      so opponent sheet moves are never silently dropped just because the
      union hasn't seen them yet.
    """
    nm = clean_move_token(move_name or "")
    if not nm or nm == "(none)":
        return None

    if nm.lower() in FRLG_EXCLUDE_MOVES:
        return None

    info = lookup_move(nm)
    canonical = (info.get("name") if info else nm)
    if canonical.lower() in FRLG_EXCLUDE_MOVES:
        return None

    # Only keep damaging moves
    if not move_is_damaging(canonical):
        return None

    mtype = normalize_type(
        (info.get("type") if info else None)
        or STATE["moves_db"].get(norm_key(nm), {}).get("type", "")
    )
    if not mtype:
        return None

    return (canonical, mtype)

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


def _typing_signature(mon):
    t = mon.get("types") or []
    return (normalize_type(t[0]) if len(t)>0 else "", normalize_type(t[1]) if len(t)>1 else "")

def _best_unique_team(roster, K=6):
    ranked = sorted(roster, key=lambda m: int(m.get("total",0)), reverse=True)
    final = []
    seen = set()
    for mon in ranked:
        sig = _typing_signature(mon)
        if sig in seen: 
            continue
        final.append(mon); seen.add(sig)
        if len(final) == K: 
            return final
    for mon in ranked:
        if mon in final:
            continue
        final.append(mon)
        if len(final) == K:
            return final
    return final[:K]

def _best_by_typing(roster_list):
    best = {}
    for m in roster_list:
        sig = _typing_signature(m)
        cur = best.get(sig)
        if cur is None or int(m.get("total",0)) > int(cur.get("total",0)):
            best[sig] = m
    return best

def _typing_signature(mon):
    t = mon.get("types") or []
    t1 = normalize_type(t[0]) if len(t) > 0 else ""
    t2 = normalize_type(t[1]) if len(t) > 1 else ""
    return (t1, t2)

def finalize_team_unique(roster, K=6, preselected=None):
    # Choose best-by-total unique typings; allow dupes only if needed to reach K
    ranked = sorted(roster or [], key=lambda m: int(m.get("total", 0)), reverse=True)
    final = []
    seen = set()
    # Respect preselected picks (locks/tiebreak results) as long as they don't duplicate a typing already taken
    pre = list(preselected or [])
    guids = {m.get("guid") for m in pre}
    for mon in ranked:
        if mon in pre or mon.get("guid") in guids:
            sig = _typing_signature(mon)
            if sig in seen:
                continue
            final.append(mon); seen.add(sig)
            if len(final) == K: return final
    # Fill with best unique typings
    for mon in ranked:
        sig = _typing_signature(mon)
        if sig in seen: 
            continue
        final.append(mon); seen.add(sig)
        if len(final) == K: return final
    # If still short, allow duplicates by best total
    for mon in ranked:
        if mon in final:
            continue
        final.append(mon)
        if len(final) == K: return final
    return final[:K]

def render_settings():
    st.header("Settings")

    # Reset session
    if st.button("Reset this session (start fresh)", key="reset_session_btn"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        try:
            st.cache_data.clear()
        except Exception:
            pass
        st.rerun()
    st.caption("Per-user session only. No data is written to the server.")
    st.markdown("---")

    # Catch unlimited
    cu_cur = bool(STATE.get("settings", {}).get("catch_unlimited", False))
    cu_new = st.checkbox("Catch unlimited Pokémon", value=cu_cur,
                         help="If enabled, the Add list ignores species catch limits.")
    if cu_new != cu_cur:
        STATE["settings"]["catch_unlimited"] = bool(cu_new)
        save_state(STATE)
        st.success("Updated: Catch unlimited Pokémon")
        do_rerun()

    # Version selector
    vmap_disp2key = {"Combined": "combined", "FireRed": "firered", "LeafGreen": "leafgreen"}
    vmap_key2disp = {v:k for k,v in vmap_disp2key.items()}
    cur_mode = STATE.get("settings", {}).get("version", "combined")
    disp_default = vmap_key2disp.get(cur_mode, "Combined")
    disp_pick = st.radio("Game version filter", ["Combined","FireRed","LeafGreen"],
                         index=["Combined","FireRed","LeafGreen"].index(disp_default))
    new_mode = vmap_disp2key[disp_pick]
    if new_mode != cur_mode:
        STATE["settings"]["version"] = new_mode
        save_state(STATE)
        st.success(f"Version set to {disp_pick}")
        do_rerun()

    # Starter selector (controls Rival variants + sheet tab)
    starter_cur = (STATE.get("settings", {}) or {}).get("starter", "Bulbasaur")
    starter_new = st.selectbox(
        "Your starter",
        STARTER_OPTIONS,
        index=STARTER_OPTIONS.index(starter_cur) if starter_cur in STARTER_OPTIONS else 0,
        help="Used to pick the correct Rival team and sheet tab."
    )
    starter_sprite_html = sprite_img_html(starter_new)
    if starter_sprite_html:
        st.markdown(starter_sprite_html, unsafe_allow_html=True)

    if starter_new != starter_cur:
        STATE["settings"]["starter"] = starter_new
    if starter_new != starter_cur:
        STATE["settings"]["starter"] = starter_new
        save_state(STATE)
        _reload_opponents_for_current_settings()
        st.success(f"Starter set to {starter_new}. Opponents reloaded.")
        do_rerun()

        # Pokédex scope (151 vs 386)
    scope_cur = (STATE.get("settings", {}).get("dex_scope", "151"))
    scope_disp = "Gen 1–3 (386)" if scope_cur == "386" else "Kanto 151"
    scope_pick = st.radio(
        "Pokédex scope",
        ["Kanto 151", "Gen 1–3 (386)"],
        index=["Kanto 151", "Gen 1–3 (386)"].index(scope_disp),
        help="Restricts base species and the Add list to 151 or 386. Your roster is kept."
    )
    scope_new = "386" if scope_pick == "Gen 1–3 (386)" else "151"
    if scope_new != scope_cur:
        STATE["settings"]["dex_scope"] = scope_new
        base = build_state_from_web_cached(dex_max())
        STATE["moves_db"] = base["moves_db"]
        STATE["species_db"] = base["species_db"]
        STATE["meta"] = base.get("meta", {"species_scope": str(dex_max())})
        save_state(STATE)
        st.success(f"Pokédex scope set to {scope_pick}. Reloaded species database.")
        do_rerun()


def render_pokedex():
    st.header("Pokédex")

    # Top controls in two columns
    left, right = st.columns(2)

    with left:
        with st.expander("Sync Pokédex levels", expanded=True):
            lvl = st.number_input(
                "Set Pokédex level to",
                1, 100,
                int(STATE.get("settings", {}).get("default_level", 20)),
                key="sync_all_level_target",
            )
            if st.button("Apply", key="sync_levels"):
                new_lvl = int(lvl)

                # Update model levels
                for m0 in STATE.get("roster", []):
                    m0["level"] = new_lvl
                    # Keep the UI controls in sync immediately
                    lvl_key0 = f"lvl_{m0.get('guid')}"
                    st.session_state[lvl_key0] = new_lvl

                # Also update the default used for new additions/prefill
                STATE.setdefault("settings", {})["default_level"] = new_lvl
                save_state(STATE)

                st.success(f"Levels synced to {new_lvl}.")
                do_rerun()

    with right:
        with st.expander("Add Pokémon to Pokédex", expanded=True):
            entries = available_species_entries()
            names = [n for n,_ in entries]
            labels = {n: l for n,l in entries}
            if not names:
                st.caption("No eligible species to add right now.")
            else:
                choices = ["(choose)"] + names
                species_name = st.selectbox(
                    "Add Pokémon",
                    choices,
                    index=0,
                    format_func=lambda n: labels.get(n, n) if n in labels else n,
                    key="add_species",
                )
                if species_name == "(choose)":
                    st.caption("Pick a Pokémon to auto-fill moves.")
                else:
                    sprite_html = sprite_img_html(species_name)
                    if sprite_html:
                        st.markdown(sprite_html, unsafe_allow_html=True)
                    lvl = int(STATE.get("settings",{}).get("default_level", 20))
                    sk = species_key(species_name)
                    sp = STATE["species_db"][sk]
                    try:
                        proposed = get_prefill_moves(sp, lvl) or []
                    except Exception:
                        proposed = []
                    proposed = [m for m in proposed if m]
                    while len(proposed) < 4:
                        proposed.append("(none)")
                    proposed = proposed[:4]

                    # Reset selects when species changes
                    prev_species = st.session_state.get("add_species_prev")
                    if prev_species != species_name:
                        for j in range(4):
                            st.session_state[f"add_mv_{j+1}"] = proposed[j] if j < len(proposed) else "(none)"
                        st.session_state["add_species_prev"] = species_name

                    all_moves = ["(none)"] + (legal_moves_for_species_chain(species_name) or [])
                    c1, c2, c3, c4 = st.columns(4)
                    picks = []
                    for j, col in enumerate((c1, c2, c3, c4), start=1):
                        cur = st.session_state.get(f"add_mv_{j}", proposed[j-1])
                        opts = ["(none)"] + (legal_moves_for_species_chain(species_name) or [])
                        if cur not in opts and cur.lower() not in FRLG_EXCLUDE_MOVES:
                            opts.insert(1, cur)
                        sel = col.selectbox(
                            f"Move {j}",
                            opts,
                            key=f"add_mv_{j}",
                        )
                        picks.append(sel)

                    if st.button("Add to Pokédex", key="add_btn"):
                        chosen = list(picks) if picks else list(proposed or [])
                        entry_moves = []
                        for mv in chosen:
                            if not mv or mv == "(none)":
                                continue
                            ct = canonical_typed(mv)
                            if ct and ct not in entry_moves:
                                ensure_move_in_db(ct[0], default_type=ct[1])
                                entry_moves.append(ct)
                        if not entry_moves:
                            for mv in (get_prefill_moves(sp, lvl) or []):
                                ct = canonical_typed(mv)
                                if ct and ct not in entry_moves:
                                    ensure_move_in_db(ct[0], default_type=ct[1])
                                    entry_moves.append(ct)
                        entry = {
                            'guid': new_guid(),
                            'species_key': sk,
                            'species': sp['name'],
                            'level': lvl,
                            'types': purge_fairy_types_pair(sp['types']),
                            'total': sp['total'],
                            'moves': entry_moves,
                        }
                        STATE['roster'].append(entry)
                        save_state(STATE)
                        st.success("Added {} at Lv {} with {}".format(sp['name'], lvl, ', '.join([m for m,_ in entry_moves]) if entry_moves else 'no moves'))
                        base_sk = base_key_for(sp["name"])
                        req = required_catches_for_species(sp["name"])
                        cc = STATE.get("caught_counts", {})
                        fset = set(STATE.get("fulfilled", []))
                        fev = set(STATE.get("fulfilled_ever", []))
                        cc[base_sk] = int(cc.get(base_sk, 0)) + 1
                        have_roster = sum(1 for m in STATE.get("roster", []) if base_key_for(m.get("species","")) == base_sk)
                        have = max(int(cc.get(base_sk,0)), have_roster)
                        if have >= req:
                            fset.add(base_sk); fev.add(base_sk)
                        STATE["caught_counts"] = cc
                        STATE["fulfilled"] = sorted(list(fset))
                        STATE["fulfilled_ever"] = sorted(list(fev))
                        save_state(STATE)
                        st.success(f"Added {sp['name']}")
                        do_rerun()

    st.markdown("---")

    # Team and Rest lists
    roster = list(STATE.get("roster", []))
    locks = set(STATE.get("locks", []))
    roster.sort(key=lambda m: ((m.get("guid") not in locks), -(m.get("total") or 0), m.get("species","")))


    # --- Gated tiebreaker (operate on unique-typing view) ---
    K = 6
    team = []

    if not roster:
        team = []
    else:
        # Base ranking
        ranked = sorted(roster, key=lambda m: int(m.get("total", 0)), reverse=True)

        # Reduce to one best Pokémon per exact typing if unique-typing is ON
        unique_on = bool(STATE.get("settings", {}).get("unique_sig", True))
        if unique_on:
            by_sig = _best_by_typing(ranked)  # exact typing -> best mon by total
            ranked_view = sorted(by_sig.values(), key=lambda m: int(m.get("total",0)), reverse=True)
        else:
            ranked_view = ranked

        if len(ranked_view) <= K:
            team = ranked_view[:K]
        else:
            border_idx = K - 1
            border_score = int(ranked_view[border_idx].get("total") or 0)

            # Equal-score indices on the unique-typing view
            eq_idx = [i for i, m in enumerate(ranked_view)
                      if int(m.get("total") or 0) == border_score]

            # If the equal block does not cross the boundary, no UI
            if not eq_idx or max(eq_idx) < K:
                team = ranked_view[:K]
            else:
                # Equal-score block that *crosses* the cutoff
                span_lo = min(eq_idx)
                span_hi = max(eq_idx)

                # Everything strictly above the block is auto-in
                must_take = ranked_view[:span_lo]
                slots = max(0, K - len(must_take))

                # Candidates are the entire equal-score block
                cands = list(ranked_view[span_lo:span_hi + 1])

                # Safety: remove signatures already taken by must_take
                taken_sigs = {_typing_signature(m) for m in must_take}
                cands = [m for m in cands if _typing_signature(m) not in taken_sigs]

                if len(cands) <= slots or slots <= 0:
                    team = list(must_take)
                    for m_ in cands:
                        if len(team) >= K: break
                        team.append(m_)
                    for m_ in ranked_view[span_hi + 1:]:
                        if len(team) >= K: break
                        if m_ not in team:
                            team.append(m_)
                else:
                    # Tiebreaker UI
                    labels, by_label = [], {}
                    for m in cands:
                        t = m.get("types") or ["—", "—"]
                        t1 = t[0] if len(t) > 0 else "—"
                        t2 = t[1] if len(t) > 1 else "—"
                        lbl = f"{m['species']} — {t1}/{t2} — Total {m.get('total') or 0}"
                        labels.append(lbl); by_label[lbl] = m["guid"]

                    group_key = f"uniq_{K}_{span_lo}_{span_hi}__{'__'.join(sorted([m.get('guid','') for m in cands]))}"
                    picks_state = STATE.setdefault("tbreak_picks", {})
                    chosen_ids = (picks_state.get(group_key) or [])[:slots]

                    if slots == 1:
                        def_idx = 0
                        if chosen_ids:
                            chosen_lbl = next((l for l,g in by_label.items() if g == chosen_ids[0]), None)
                            if chosen_lbl in labels:
                                def_idx = labels.index(chosen_lbl)
                        sel_lbl = st.radio("Tiebreaker candidates", labels, index=def_idx, key=f"tbreak_{group_key}_1")
                        chosen_ids = [by_label[sel_lbl]]
                    else:
                        sels, remaining = [], labels[:]
                        for sidx in range(slots):
                            default_lbl = next((l for l,g in by_label.items()
                                                if sidx < len(chosen_ids) and g == chosen_ids[sidx]),
                                               remaining[0] if remaining else labels[0])
                            sel = st.radio(f"Tiebreaker — pick {sidx+1}/{slots}", remaining,
                                           index=(remaining.index(default_lbl) if default_lbl in remaining else 0),
                                           key=f"tbreak_{group_key}_{sidx+1}")
                            sels.append(sel)
                            remaining = [l for l in remaining if l != sel]
                        chosen_ids = [by_label[s] for s in sels]

                    if picks_state.get(group_key) != chosen_ids:
                        picks_state[group_key] = chosen_ids
                        STATE["tbreak_picks"] = picks_state
                        save_state(STATE); do_rerun()

                    team = list(must_take)
                    for m_ in cands:
                        if len(team) >= K: break
                        if m_.get("guid") in chosen_ids and m_ not in team:
                            team.append(m_)
                    for m_ in ranked_view[span_hi + 1:]:
                        if len(team) >= K: break
                        if m_ not in team:
                            team.append(m_)

    # Enforce unique typing against the full roster (keeps chosen picks)
    team = finalize_team_unique(roster, K, preselected=team)

    # NEW: hard-enforce locks after finalize (locks always make the team)
    locked_mons = [m for m in roster if m.get("guid") in locks]
    for lm in locked_mons:
        if lm not in team:
            team.append(lm)

    # If we exceeded K, keep all locked first, then best others until K
    if len(team) > K:
        locked_part = [m for m in team if m.get("guid") in locks]
        other_part  = [m for m in team if m.get("guid") not in locks]
        team = locked_part + other_part
        team = team[:K]

    st.session_state["active_team"] = team
    # --- end gated tiebreaker ---

    st.markdown("---")

    st.subheader("Team")

    for i, mon in enumerate(team, start=1):
        gid = mon.get("guid")
        t = mon.get("types") or ["—", "—"]
        t1 = t[0] if len(t) > 0 else "—"
        t2 = t[1] if len(t) > 1 else "—"

        # --- CARD (visual only; gradient lives here) ---
        with st.container(border=True):
            _dex_card_container_style(gid, t1, t2)

            header_html = f"""
              <div class="dex-card-head">
                <div>{sprite_img_html(mon['species'])}</div>
                <div>
                  <div class="dex-card-title">{i}. {mon['species']} • Lv{int(mon.get('level', 1))}</div>
                  <div class="dex-card-meta">
                    {type_emoji(t1)} {t1}{f" / {t2}" if t2 else ""} • <b>Total {int(mon.get('total', 0))}</b>
                  </div>
                </div>
              </div>
            """
            st.markdown(header_html, unsafe_allow_html=True)

        # Small spacing between the visual card and controls
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

        # --- INTERACTIONS (below card; no gradient) ---
        c_lock, c_lv, c_apply = st.columns([1.3, 1.4, 1.0])

        is_locked = gid in STATE.get("locks", [])
        locked_new = c_lock.checkbox("🔒 Lock", value=is_locked, key=f"lock_{gid}", help="Lock to team")

        lvl_key = f"lvl_{gid}"
        if lvl_key not in st.session_state:
            st.session_state[lvl_key] = int(mon.get("level", 1))

        c_lv.number_input(
            "Lv",
            min_value=1,
            max_value=100,
            step=1,
            key=lvl_key,
            label_visibility="collapsed",
        )

        if c_apply.button("Apply", key=f"apply_lvl_{gid}"):
            new_lv = int(st.session_state.get(lvl_key, mon.get("level", 1)))
            mon["level"] = new_lv
            st.session_state[lvl_key] = new_lv
            save_state(STATE)
            st.success("Level updated.")
            do_rerun()

        if locked_new != is_locked:
            L = set(STATE.get("locks", []))
            if locked_new:
                L.add(gid)
            else:
                L.discard(gid)
            STATE["locks"] = sorted(list(L))
            save_state(STATE)
            do_rerun()

        with st.expander(f"Edit / Remove {mon['species']}", expanded=False):
            picks = [(x[0] if isinstance(x, (list, tuple)) else x) for x in mon.get('moves', [])] + ["(none)"] * 4
            picks = picks[:4]
            cols4 = st.columns(4)
            for j in range(4):
                cur = picks[j]
                opts = ['(none)'] + (legal_moves_for_species_chain(mon.get('species', '')) or [])
                if cur not in opts and cur.lower() not in FRLG_EXCLUDE_MOVES:
                    opts.insert(1, cur)
                sel = cols4[j].selectbox(
                    f"Move {j+1}",
                    opts,
                    index=(opts.index(cur) if cur in opts else 0),
                    key=f"team_mv_{gid}_{j}",
                )
                picks[j] = sel
                typed = canonical_typed(sel)
                cols4[j].caption(f"Type: {typed[1] if typed else '—'}")

            c1, c2 = st.columns(2)
            with c1:
                if st.button("Save Pokémon Moves", key=f"team_save_{gid}"):
                    entry_moves = []
                    for name in picks:
                        ct = canonical_typed(name)
                        if ct:
                            entry_moves.append(ct)
                            ensure_move_in_db(ct[0], default_type=ct[1])
                    mon["moves"] = normalize_moves_list(entry_moves)
                    save_state(STATE)
                    st.success("Saved moves.")
                    do_rerun()

            with c2:
                if st.button("Remove from Pokédex", key=f"rm_pokedex_team_{gid}"):
                    base_sk = base_key_for(mon.get("species", ""))
                    req = required_catches_for_species(mon.get("species", ""))
                    fset = set(STATE.get("fulfilled", []))
                    cc = STATE.get("caught_counts", {})

                    STATE["locks"] = [g for g in STATE.get("locks", []) if g != gid]
                    STATE["roster"] = [m for m in STATE.get("roster", []) if m.get("guid") != gid]

                    cc[base_sk] = max(0, int(cc.get(base_sk, 0)) - 1)
                    if cc[base_sk] >= req:
                        fset.add(base_sk)
                    else:
                        fset.discard(base_sk)

                    STATE["caught_counts"] = cc
                    STATE["fulfilled"] = sorted(list(fset))
                    save_state(STATE)
                    do_rerun()

                st.markdown("---")

    st.subheader("Rest of Pokédex")
    team_ids = {m.get('guid') for m in team if m.get('guid')}
    rest = [m for m in roster if m.get('guid') not in team_ids]

    if not rest:
        st.caption("None.")
    else:
        for mon in rest:
            gid = mon.get("guid")
            t = mon.get("types") or ["—", "—"]
            t1 = t[0] if len(t) > 0 else "—"
            t2 = t[1] if len(t) > 1 else "—"

            # --- CARD (visual only; gradient lives here) ---
            with st.container(border=True):
                _dex_card_container_style(gid, t1, t2)

                header_html = f"""
                  <div class="dex-card-head">
                    <div>{sprite_img_html(mon['species'])}</div>
                    <div>
                      <div class="dex-card-title">{mon['species']} • Lv{int(mon.get('level', 1))}</div>
                      <div class="dex-card-meta">
                        {type_emoji(t1)} {t1}{f" / {t2}" if t2 else ""} • <b>Total {int(mon.get('total', 0))}</b>
                      </div>
                    </div>
                  </div>
                """
                st.markdown(header_html, unsafe_allow_html=True)

            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

            # --- INTERACTIONS (below card; no gradient) ---
            c_lock, c_lv, c_apply = st.columns([1.3, 1.4, 1.0])

            is_locked = gid in STATE.get("locks", [])
            locked_new = c_lock.checkbox("🔒 Lock", value=is_locked, key=f"lock_{gid}", help="Lock to team")

            lvl_key = f"lvl_{gid}"
            if lvl_key not in st.session_state:
               st.session_state[lvl_key] = int(mon.get("level", 1))

            c_lv.number_input(
                "Lv",
                min_value=1,
                max_value=100,
                step=1,
                key=lvl_key,
                label_visibility="collapsed",
            )

            if c_apply.button("Apply", key=f"apply_lvl_{gid}"):
                new_lv = int(st.session_state.get(lvl_key, mon.get("level", 1)))
                mon["level"] = new_lv
                st.session_state[lvl_key] = new_lv
                save_state(STATE)
                st.success("Level updated.")
                do_rerun()

            if locked_new != is_locked:
                L = set(STATE.get("locks", []))
                if locked_new:
                    L.add(gid)
                else:
                    L.discard(gid)
                STATE["locks"] = sorted(list(L))
                save_state(STATE)
                do_rerun()

            with st.expander(f"Edit / Remove {mon['species']}", expanded=False):
                picks = [(x[0] if isinstance(x, (list, tuple)) else x) for x in mon.get('moves', [])] + ["(none)"] * 4
                picks = picks[:4]
                cols4 = st.columns(4)
                for j in range(4):
                    cur = picks[j]
                    opts = ['(none)'] + (legal_moves_for_species_chain(mon.get('species', '')) or [])
                    if cur not in opts and cur.lower() not in FRLG_EXCLUDE_MOVES:
                        opts.insert(1, cur)
                    sel = cols4[j].selectbox(
                        f"Move {j+1}",
                        opts,
                        index=(opts.index(cur) if cur in opts else 0),
                        key=f"rest_mv_{gid}_{j}",
                    )
                    picks[j] = sel
                    typed = canonical_typed(sel)
                    cols4[j].caption(f"Type: {typed[1] if typed else '—'}")

                c1, c2 = st.columns(2)
                with c1:
                    if st.button("Save Pokémon Moves", key=f"rest_save_{gid}"):
                        entry_moves = []
                        for name in picks:
                            ct = canonical_typed(name)
                            if ct:
                                entry_moves.append(ct)
                                ensure_move_in_db(ct[0], default_type=ct[1])
                        mon["moves"] = normalize_moves_list(entry_moves)
                        save_state(STATE)
                        st.success("Saved moves.")
                        do_rerun()

                with c2:
                    if st.button("Remove from Pokédex", key=f"rm_pokedex_bench_{gid}"):
                        base_sk = base_key_for(mon.get("species", ""))
                        req = required_catches_for_species(mon.get("species", ""))
                        fset = set(STATE.get("fulfilled", []))
                        cc = STATE.get("caught_counts", {})

                        STATE["locks"] = [g for g in STATE.get("locks", []) if g != gid]
                        STATE["roster"] = [m for m in STATE.get("roster", []) if m.get("guid") != gid]

                        cc[base_sk] = max(0, int(cc.get(base_sk, 0)) - 1)
                        if cc[base_sk] >= req:
                            fset.add(base_sk)
                        else:
                            fset.discard(base_sk)

                        STATE["caught_counts"] = cc
                        STATE["fulfilled"] = sorted(list(fset))
                        save_state(STATE)
                        do_rerun()

        st.markdown("---")
    
def _dex_card_container_style(gid: str, t1: str, t2: str) -> None:
    p = normalize_type(t1) or normalize_type(t2) or "Normal"
    s = normalize_type(t2)

    cls = ["dex-grad-marker", f"t1-{p}"]
    if s and s != p:
        cls.append(f"t2-{s}")

    st.markdown(
        f"<span class=\"{' '.join(cls)}\"></span>",
        unsafe_allow_html=True,
    )

def available_species_entries() -> List[Tuple[str, str]]:
    """Return (name, label) options for the Add Pokémon list.
    - Only base Kanto species (hide evolutions).
    - Version filter (Combined/FireRed/LeafGreen).
    - If 'Catch unlimited' ON, ignore count-based gating.
    - Show 'Name (Trade Piece)' for 2-of-2 species (no counts).
    - Preserve '[trade reward]' tag.
    - Hide Mew from Add list when disabled in Settings.
    """
    # Guard: rebuild if species DB scope mismatches current setting
    want = str(dex_max())
    if (STATE.get("meta", {}).get("species_scope") != want):
        base = build_state_from_web_cached(dex_max())
        STATE["moves_db"] = base["moves_db"]
        STATE["species_db"] = base["species_db"]
        STATE["meta"] = base.get("meta", {"species_scope": want})

    catch_unlimited = bool(STATE.get("settings", {}).get("catch_unlimited", False))
    # (rest of your function stays exactly as you have it)

    # Collapse roster to base
    rcounts: Dict[str, int] = {}
    for m in STATE.get("roster", []):
        base_sk = base_key_for(m.get("species", ""))
        rcounts[base_sk] = rcounts.get(base_sk, 0) + 1

    ever = set(STATE.get("fulfilled_ever", []))
    entries: List[Tuple[str, str]] = []

    for sk, sp in STATE.get("species_db", {}).items():
        name = sp.get("name", "")
        if not name:
            continue

        # Base-only: skip evolutions
        try:
            if base_key_for(name) != species_key(name):
                continue
        except Exception:
            if sp.get("evolves_from"):
                continue

        # Version filter on base display name
        if not _is_allowed_by_version(name):
            continue

        base_sk = base_key_for(name)
        req = int(required_catches_for_species(name))
        have = int(rcounts.get(base_sk, 0))

        # Visibility rules
        if catch_unlimited:
            visible = True
        else:
            if req == 2:
                if base_sk in ever:
                    visible = (have == 0)
                else:
                    visible = (have < 2)
            else:
                visible = (have < req)

        if not visible:
            continue

        # Label: Trade Piece for 2-of-2 species, no numeric counters
        is_trade_piece = (req == 2)
        base_label = f"{name} (Trade Piece)" if is_trade_piece else name

        # Preserve trade-reward tag
        tag = " (trade reward)" if base_sk in TRADE_REWARD_SPECIES else ""
        label = f"{base_label}{tag}"
        entries.append((name, label))

    entries.sort(key=lambda t: t[0])
    return entries
    
def _format_battle_result_line(name: str, your_total: int, opp_total: int, offense: int, defense: int, total: int) -> str:
    """Minimal battle line per user spec: no move parentheticals."""
    return f"{name} — (Your Total: {your_total} vs Opp Total: {opp_total}) — Offense: {offense} | Defense: {defense} → Total {total}"

def _mult_emoji(mult: float) -> str:
    # Emojis for all x? except 0x
    mapping = {4.0:"💥", 2.0:"🟢", 1.0:"⚪", 0.5:"🔻", 0.25:"⛔"}
    return mapping.get(float(mult), "")

def _grade_class(mult: float) -> str:
    if mult >= 2.0: return "good"
    if mult == 1.0: return "neutral"
    if mult == 0.0: return "zero"
    return "bad"

def _moves_grid_html(rows, offense: bool) -> str:
    rows = [r for r in (rows or []) if (r.get("move") or "").strip() and (r.get("type") or "").strip()]
    if not rows:
        return "<div class='small'>—</div>"

    if offense:
        rows2 = sorted(rows, key=lambda x: (-x["score"], x["move"] or ""))
        best_val = max(r["score"] for r in rows2)
    else:
        rows2 = sorted(rows, key=lambda x: (x["score"], x["move"] or ""))
        best_val = min(r["score"] for r in rows2)

    html = [
        "<div class='moves-grid'><table>",
        "<thead><tr><th>Move</th><th>Type</th><th>Eff.</th><th>Score</th></tr></thead><tbody>"
    ]

    for r in rows2:
        mv = r.get("move") or "—"
        tp = normalize_type(r.get("type") or "") or "?"
        mult = float(r.get("mult", 1.0))
        score = int(r.get("score", 0))
        eff_txt = (f"{int(mult)}x" if mult in (2.0, 4.0) else ("0x" if mult == 0.0 else f"{mult:g}x"))
        type_emo = type_emoji(tp)
        star = " ★" if (score == best_val and mv != "—") else ""

        if score > 0:
            arrow_html = "<span class='up'>&uarr;</span>"
        elif score < 0:
            arrow_html = "<span class='down'>&darr;</span>"
        else:
            arrow_html = "•"

        html.append(
            "<tr>"
            f"<td>{mv}{star}</td>"
            f"<td>{type_emo}&nbsp;<span class='small'>{tp}</span></td>"
            f"<td>{eff_txt}</td>"
            f"<td class='mv-score'>{score} {arrow_html}</td>"
            "</tr>"
        )

    html.append("</tbody></table></div>")
    return "".join(html)

def _render_moves_grid(rows, offense: bool):
    st.markdown(_moves_grid_html(rows, offense=offense), unsafe_allow_html=True)

def render_battle():
    st.header("Battle")
    team = st.session_state.get("active_team", STATE["roster"][:6])

    # Handle revive-all before any fainted widgets are created
    if st.session_state.pop("_revive_all", False):
        for _m in team:
            st.session_state[f"fainted_{_m.get('guid')}"] = False

    if not team:
        st.info("Build a team on the Pokédex page.")
        return

    # === Mark fainted and hide from battle ===
    fainted_set = set(STATE.get("fainted", []))

    with st.expander("Team status: mark fainted / revive", expanded=False):
        cols = st.columns(max(1, min(6, len(team))))
        changed = False

        for i, mon in enumerate(team):
            col = cols[i % len(cols)]
            gid = mon.get("guid")
            is_fainted = gid in fainted_set
            ckey = f"fainted_{gid}"

            img_col, chk_col = col.columns([1, 2])
            img_html = sprite_img_html(mon.get("species", "?"))
            if img_html:
                img_col.markdown(img_html, unsafe_allow_html=True)
            else:
                img_col.write("")

            label = f"{'☠️' if is_fainted else '🟢'} {mon.get('species','?')} fainted"
            new_val = chk_col.checkbox(label, value=is_fainted, key=ckey)
            if new_val and not is_fainted:
                fainted_set.add(gid)
                changed = True
            elif not new_val and is_fainted:
                fainted_set.discard(gid)
                changed = True

        bcol1, _ = st.columns([1, 4])
        if bcol1.button("Revive all", key="revive_all_btn"):
            STATE["fainted"] = []
            save_state(STATE)
            st.session_state["_revive_all"] = True
            do_rerun()

        if changed or set(STATE.get("fainted", [])) != fainted_set:
            STATE["fainted"] = sorted(list(fainted_set))
            save_state(STATE)
            do_rerun()

    team = [m for m in team if m.get("guid") not in STATE.get("fainted", [])]
    if not team:
        st.warning("All your team members are marked fainted. Unmark some to battle.")
        return

    # Load encounters if empty
    if not STATE["opponents"]["encounters"]:
        st.warning("No opponents loaded yet. Trying to load your default sheet…")
        autoload_opponents_if_empty()
    if not STATE["opponents"]["encounters"]:
        st.error("Could not load opponents automatically.")
        return

    # Pick trainer + mon (instant updates; no form, no button)
    enc_options = [f"{i+1}. {enc['label']}" for i, enc in enumerate(STATE["opponents"]["encounters"])]
    cur_enc_idx, cur_mon_idx = STATE.get("last_battle_pick", [0, 0])

    cur_enc_idx = max(0, min(cur_enc_idx, len(enc_options) - 1)) if enc_options else 0
    pick = st.selectbox("Encounter (trainer)", enc_options, index=cur_enc_idx, key="battle_enc_select")

    selected_enc_idx = enc_options.index(pick) if enc_options else 0

    # If trainer changed, store new selection and rerun
    if selected_enc_idx != cur_enc_idx:
        STATE["last_battle_pick"] = [selected_enc_idx, 0]
        save_state(STATE)
        do_rerun()

    # Always resolve the encounter from the current index
    enc = STATE["opponents"]["encounters"][selected_enc_idx]
    mons = enc.get("mons", []) or []
    mon_count = len(mons)

    # Current selected mon index from state (for highlight)
    cur_mon_idx = 0
    if mon_count:
        cur_mon_idx = max(
            0,
            min(STATE.get("last_battle_pick", [0, 0])[1], mon_count - 1),
        )
        
        # Trainer sprite above their team
    trainer_label = enc.get("label", "?")
    trainer_html = trainer_sprite_img_html(trainer_label, size=TRAINER_SPRITE_SIZE)
    if trainer_html:
        st.markdown(trainer_html, unsafe_allow_html=True)

    st.markdown("**Their Pokémon**")
    if not mons:
        st.caption("Trainer has no Pokémon.")
    else:
        mon_count = len(mons)
        card_idx = 0

        # Simple responsive layout:
        # 1–3: 1 row
        # 4–6: 2 rows
        # 7+: 3 rows, distributed as evenly as possible
        if mon_count <= 3:
            row_layout = [mon_count]
        elif mon_count <= 6:
            top = mon_count // 2
            bottom = mon_count - top
            row_layout = [top, bottom]
        else:
            per = mon_count // 3
            rem = mon_count % 3
            row_layout = [
                per + (1 if rem > 0 else 0),
                per + (1 if rem > 1 else 0),
                per,
            ]

        for row_cols in row_layout:
            if card_idx >= mon_count:
                break
            cols = st.columns(row_cols)
            for col_pos in range(row_cols):
                if card_idx >= mon_count:
                    break

                mon = mons[card_idx]
                idx = card_idx
                card_idx += 1

                is_selected = (idx == cur_mon_idx)
                card_classes = "opp-card opp-card-selected" if is_selected else "opp-card"

                species = mon.get("species", "?")
                level = int(mon.get("level", 1))
                total = int(mon.get("total", 0))

                types_pair = purge_fairy_types_pair(mon.get("types") or [])
                t1, t2 = types_pair[0], types_pair[1]

                if t1:
                    type_text = f"{type_emoji(t1)} {t1}"
                else:
                    type_text = "—"
                if t2:
                    type_text += f" / {t2}"

                moves = mon.get("moves") or []
                moves_txt = ", ".join([f"{mv} ({tp})" for mv, tp in moves]) if moves else "—"

                sprite_html = sprite_img_html(species, size=128)

                primary_type = normalize_type(t1) or normalize_type(t2) or "Normal"
                secondary_type = normalize_type(t2)

                if secondary_type and secondary_type != primary_type:
                    g1a, _ = TYPE_GRADIENT.get(primary_type, DEFAULT_CARD_GRADIENT)
                    _, g2b = TYPE_GRADIENT.get(
                        secondary_type,
                        TYPE_GRADIENT.get(primary_type, DEFAULT_CARD_GRADIENT),
                    )
                    g1 = g1a
                    g2 = g2b
                else:
                    g1a, _ = TYPE_GRADIENT.get(primary_type, DEFAULT_CARD_GRADIENT)
                    g1 = g1a
                    g2 = "rgba(0,0,0,0)"

                style = f"--opp-bg1:{g1};--opp-bg2:{g2};"

                card_html = f"""
                  <div class="{card_classes}" style="{style}">
                    <div class="opp-card-sprite">{sprite_html}</div>
                    <div class="opp-card-main">
                      <div class="opp-card-name">{species} • Lv{level}</div>
                      <div class="opp-card-types">{type_text}</div>
                      <div class="opp-card-total">Total: {total}</div>
                      <div class="opp-card-moves">
                        <span class="opp-card-moves-label">Moves:</span> {moves_txt}
                      </div>
                    </div>
                  </div>
                """

                with cols[col_pos]:
                    # Full card including in-card Select button (HTML link)
                    st.markdown(card_html, unsafe_allow_html=True)

                    # In-app select (no URL navigation / no new tab / no new session)
                    _sp, _btn = st.columns([4, 1])
                    with _btn:
                        if st.button("Select", key=f"opp_pick_{selected_enc_idx}_{idx}", type="primary"):
                            STATE["last_battle_pick"] = [selected_enc_idx, idx]
                            save_state(STATE)
                            do_rerun()

    # === Clamp indices and build opponent header ===
    selected_enc_idx, selected_mon_idx = STATE.get("last_battle_pick", [0, 0])

    enc_count = len(STATE["opponents"]["encounters"])
    if enc_count == 0:
        st.error("No encounters loaded.")
        return
    selected_enc_idx = max(0, min(selected_enc_idx, enc_count - 1))
    enc = STATE["opponents"]["encounters"][selected_enc_idx]

    mon_count = len(enc.get("mons", []))
    if mon_count == 0:
        st.error("Selected encounter has no Pokémon.")
        return
    selected_mon_idx = max(0, min(selected_mon_idx, mon_count - 1))

    opmon = enc["mons"][selected_mon_idx]
    opp_label = f"{enc.get('label', '?')} — {opmon.get('species', '?')} Lv{opmon.get('level', 1)}"
    opp_types = tuple(purge_fairy_types_pair(opmon.get("types") or []))
    t1, t2 = opp_types
    opp_pairs = list(opmon.get("moves", []))
    opp_total = int(opmon.get("total", 0))
    moves_str = ", ".join([f"{n}({t})" for n, t in opp_pairs]) if opp_pairs else "—"
    # No separate text header here; trainer + sprite + actions are below.
    current_opp_name = opmon.get("species", "?")

    b1, b2 = st.columns(2)
    if b1.button("✅ Beat Pokémon (remove just this one)"):
        try:
            # Decide next selection BEFORE mutating lists
            next_enc_idx = selected_enc_idx
            next_mon_idx = selected_mon_idx

            if len(enc["mons"]) == 1:
                # This was the only mon: remove the trainer, then stay on the same index (which becomes the next trainer)
                STATE["opponents"]["cleared"].append(
                    {
                        "id": new_guid(),
                        "what": "trainer",
                        "trainer": enc["label"],
                        "count": 1,
                        "data": enc,
                        "pos": selected_enc_idx,
                    }
                )
                STATE["opponents"]["encounters"].pop(selected_enc_idx)
                total = len(STATE["opponents"]["encounters"])
                next_enc_idx = max(0, min(selected_enc_idx, total - 1))
                next_mon_idx = 0
            else:
                # Remove just the selected mon and point to whatever slid into that slot
                beaten = enc["mons"].pop(selected_mon_idx)
                STATE["opponents"]["cleared"].append(
                    {
                        "id": new_guid(),
                        "what": "pokemon",
                        "trainer": enc["label"],
                        "species": beaten.get("species"),
                        "level": beaten.get("level"),
                        "row": beaten.get("source_row"),
                        "data": beaten,
                        "pos": selected_enc_idx,
                        "index": selected_mon_idx,
                    }
                )
                next_mon_idx = min(selected_mon_idx, len(enc["mons"]) - 1)

            save_state(STATE)
            STATE["last_battle_pick"] = [next_enc_idx, next_mon_idx]
            save_state(STATE)
            do_rerun()
        except Exception as e:
            st.error(f"Failed to remove: {e}")

    if b2.button("🧹 Beat Trainer (remove entire encounter)"):
        try:
            STATE["opponents"]["cleared"].append(
                {
                    "id": new_guid(),
                    "what": "trainer",
                    "trainer": enc["label"],
                    "count": len(enc["mons"]),
                    "data": enc,
                    "pos": selected_enc_idx,
                }
            )
            STATE["opponents"]["encounters"].pop(selected_enc_idx)

            total = len(STATE["opponents"]["encounters"])
            next_enc_idx = max(0, min(selected_enc_idx, total - 1))
            STATE["last_battle_pick"] = [next_enc_idx, 0]
            save_state(STATE)
            do_rerun()
        except Exception as e:
            st.error(f"Failed to remove trainer: {e}")

    with st.expander("Cleared log (latest 15)", expanded=False):
        log = STATE["opponents"].get("cleared", [])
        if not log:
            st.caption("— empty —")
        else:
            current_labels = {enc2["label"] for enc2 in STATE["opponents"]["encounters"]}
            for i, item in enumerate(list(reversed(log[-15:]))):
                if item.get("what") == "pokemon":
                    label = (
                        f"• Beat Pokémon: {item.get('species')} (Lv{item.get('level')}) — "
                        f"Trainer: {item.get('trainer')}"
                    )
                    can_undo = item.get("trainer") in current_labels
                else:
                    label = (
                        f"• Beat Trainer: {item.get('trainer')} — "
                        f"removed {item.get('count', 0)} Pokémon"
                    )
                    can_undo = item.get("trainer") not in current_labels
                cols = st.columns([6, 1])
                cols[0].write(label)
                if can_undo:
                    if cols[1].button("Undo", key=f"undo_{item.get('id', i)}"):
                        if item.get("what") == "pokemon":
                            for enc2 in STATE["opponents"]["encounters"]:
                                if enc2["label"] == item["trainer"]:
                                    enc2.setdefault("mons", []).append(item["data"])
                                    break
                        else:
                            if item["trainer"] not in {e["label"] for e in STATE["opponents"]["encounters"]}:
                                STATE["opponents"]["encounters"].insert(
                                    min(
                                        int(item.get("pos", 0)),
                                        len(STATE["opponents"]["encounters"]),
                                    ),
                                    item["data"],
                                )
                        save_state(STATE)
                        st.success("Undo applied.")
                        do_rerun()

    # === special type-math exceptions (fixed/set-HP, OHKO): ignore resist/weak; keep immunities ===
    # Gen 3 set: Seismic Toss, Night Shade, Dragon Rage, SonicBoom, Psywave, Super Fang, Endeavor,
    #            Fissure, Guillotine, Horn Drill, Sheer Cold
    IMMUNITY_ONLY_MOVES = {
        "seismictoss",
        "nightshade",
        "dragonrage",
        "sonicboom",
        "psywave",
        "superfang",
        "endeavor",
        "fissure",
        "guillotine",
        "horndrill",
        "sheercold",
    }

    def _immunity_only_mult(move_type: str, defender_types: tuple) -> float:
        """Apply ONLY immunities (0x) from the type chart; ignore resist/weak."""
        mt = normalize_type(move_type) or "Normal"
        for dt in defender_types:
            if not dt:
                continue
            d = normalize_type(dt) or "Normal"
            if TYPE_CHART.get(mt, {}).get(d, 1.0) == 0.0:
                return 0.0
        return 1.0

    def _type_mult_for_move(move_name: str, move_type: str, defender_types: tuple) -> float:
        """
        Fixed/set-HP and OHKO moves ignore type effectiveness except immunities.
        Everything else uses normal type chart.
        """
        if move_name and move_id(move_name) in IMMUNITY_ONLY_MOVES:
            return _immunity_only_mult(move_type, defender_types)
        return get_mult(move_type, defender_types)

    def compute_best_offense(my_moves, opp_types):
        detail = []
        best_score = -9999
        best_move = None
        best_mult = 1.0
        for mv, t in my_moves:
            mult = _type_mult_for_move(mv, t, opp_types)
            sc = score_offense(mult)
            detail.append({"move": mv, "type": t, "mult": mult, "score": sc})
            if sc > best_score:
                best_score, best_move, best_mult = sc, mv, mult
        if best_move is None:
            best_score, best_move, best_mult = 0, None, 1.0
        return (best_score, best_move, best_mult), detail

    def compute_their_best_vs_me(opp_moves, my_types):
        detail = []
        if not opp_moves:
            return (0, None, 1.0), detail
        best_score = 9999
        best_move = None
        best_mult = 1.0
        for mv, t in opp_moves:
            mult = _type_mult_for_move(mv, t, my_types)
            sc = score_defense(mult)
            detail.append({"move": mv, "type": t, "mult": mult, "score": sc})
            if sc < best_score:
                best_score, best_move, best_mult = sc, mv, mult
        return (best_score, best_move, best_mult), detail

    # --- compute results ---
    results = []
    for mon in team:
        tpair = purge_fairy_types_pair(mon["types"])
        my_types = (tpair[0], tpair[1])
        my_total = int(mon.get("total", 0))

        sp = STATE["species_db"].get(
            mon.get("species_key") or species_key(mon["species"]), {}
        )
        if not sp.get("learnset"):
            sp["learnset"] = rebuild_learnset_for(sp.get("name", mon["species"]))
            STATE["species_db"][species_key(sp.get("name", mon["species"]))] = sp
            save_state(STATE)

        my_moves = [(mv, normalize_type(tp) or "") for mv, tp in (mon.get("moves") or [])]
        if not my_moves and sp.get("learnset"):
            learned = last_four_moves_by_level(sp["learnset"], int(mon["level"]))
            typed = []
            for m in learned:
                ct = canonical_typed(m)
                if ct:
                    typed.append(ct)
            my_moves = typed

        (off_sc, off_move, off_mult), off_rows = compute_best_offense(my_moves, opp_types)
        (def_sc, def_move, def_mult), def_rows = compute_their_best_vs_me(opp_pairs, my_types)
        total = off_sc + def_sc

        results.append(
            {
                "mon": mon,
                "my_total": my_total,
                "opp_total": opp_total,
                "off": (off_sc, off_move, off_mult),
                "def": (def_sc, def_move, def_mult),
                "off_rows": off_rows,
                "def_rows": def_rows,
                "total_score": total,
            }
        )

    results.sort(
        key=lambda r: (
            r.get("total_score", 0),
            int((r.get("mon") or {}).get("total", 0)),
        ),
        reverse=True,
    )
    
    st.markdown("---")
    st.subheader(f"Your team vs {current_opp_name}")

    # Opponent header info (same opponent every time; grid differs per your mon)
    opp_species = opmon.get("species", "?")
    opp_level   = int(opmon.get("level", 1))
    opp_total   = int(opmon.get("total", 0))
    opp_types_p = purge_fairy_types_pair(opmon.get("types") or [])
    opp_t1, opp_t2 = opp_types_p[0], opp_types_p[1]

    opp_type_text = f"{type_emoji(opp_t1)} {opp_t1}" if opp_t1 else "—"
    if opp_t2:
        opp_type_text += f" / {opp_t2}"

    opp_moves_txt = ", ".join([f"{mv} ({tp})" for mv, tp in (opp_pairs or [])]) if opp_pairs else "—"
    opp_style = _gradient_style_for_types(opp_t1, opp_t2)
    opp_sprite_html = sprite_img_html(opp_species)

    for rank, r in enumerate(results, start=1):
        mon = r["mon"]
        off_sc, off_move, off_mult = r["off"]
        def_sc, def_move, def_mult = r["def"]
        total = r["total_score"]
        my_total = r["my_total"]

        my_types_p = purge_fairy_types_pair(mon.get("types") or [])
        my_t1, my_t2 = my_types_p[0], my_types_p[1]
        my_type_text = f"{type_emoji(my_t1)} {my_t1}" if my_t1 else "—"
        if my_t2:
            my_type_text += f" / {my_t2}"

        my_style = _gradient_style_for_types(my_t1, my_t2)
        my_sprite_html = sprite_img_html(mon.get("species", "?"))

        left_html = f"""
          <div class="vs-card" style="{my_style}">
            <div class="vs-card-header">
              <div class="vs-card-sprite">{my_sprite_html}</div>
              <div>
                <div class="vs-card-title">{rank}. {mon.get('species','?')} • Lv{int(mon.get('level',1))}</div>
                <div class="vs-card-meta">{my_type_text} • Total {my_total}</div>
                <div class="vs-card-scoreline">
                  (Your Total: {my_total} vs Opp Total: {opp_total}) •
                  Offense <b>{off_sc}</b> | Defense <b>{def_sc}</b> → <b>Total {total}</b>
                </div>
              </div>
            </div>
            <div class="vs-card-grid-title">Your moves vs them</div>
            {_moves_grid_html(r.get("off_rows"), offense=True)}
          </div>
        """

        right_html = f"""
          <div class="vs-card" style="{opp_style}">
            <div class="vs-card-header">
              <div class="vs-card-sprite">{opp_sprite_html}</div>
              <div>
                <div class="vs-card-title">{opp_species} • Lv{opp_level}</div>
                <div class="vs-card-meta">{opp_type_text} • Total {opp_total}</div>
                <div class="vs-card-meta"><span style="font-weight:700;">Moves:</span> {opp_moves_txt}</div>
              </div>
            </div>
            <div class="vs-card-grid-title">Their moves vs you</div>
            {_moves_grid_html(r.get("def_rows"), offense=False)}
          </div>
        """

        cL, cR = st.columns(2)
        with cL:
            st.markdown(left_html, unsafe_allow_html=True)
        with cR:
            st.markdown(right_html, unsafe_allow_html=True)

        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

# =============================================================================
# Evolution Watch page
# =============================================================================
def get_species_total(name: str) -> int:
    """
    Robust total lookup for any in-scope species.
    Priority:
      1) If present in species_db, use it.
      2) Else read from Showdown pokedex (within scope, non-forme), sum baseStats.
      3) Else on-demand add to species_db via ensure_species_in_db and re-read.
    """
    try:
        sk = species_key(name)
        rec = STATE.get("species_db", {}).get(sk)
        if rec and isinstance(rec.get("total"), int):
            return int(rec["total"])
    except Exception:
        pass

    # 2) Direct from Pokédex if possible (handles evolved targets not preloaded)
    try:
        dex = get_pokedex_cached() or {}
        maxdex = dex_max()

        def _dex_rec(n: str):
            sid = ps_id(n)
            sd = dex.get(sid)
            if sd and (sd.get("forme") or not isinstance(sd.get("num"), int)):
                sd = None
            if sd and not (1 <= sd["num"] <= maxdex):
                sd = None
            if sd:
                return sd
            # fallback: scan by normalized name
            for r in dex.values():
                if not r: 
                    continue
                nm = r.get("name", "")
                if ps_id(nm) == sid and not r.get("forme") and isinstance(r.get("num"), int) and 1 <= r["num"] <= maxdex:
                    return r
            return None

        sd = _dex_rec(name)
        if sd:
            base = sd.get("baseStats") or {}
            if base:
                return int(sum(v for v in base.values() if isinstance(v, int)))
    except Exception:
        pass

    # 3) As a last resort, add it to species_db on demand and try again
    try:
        if ensure_species_in_db(name):
            rec = STATE.get("species_db", {}).get(species_key(name))
            if rec and isinstance(rec.get("total"), int):
                return int(rec["total"])
    except Exception:
        pass

    return 0

def render_evo_watch():
    st.header("Evolution Watch")

    # Stone list depends on scope (Sun Stone only when scope == 386)
    items = stone_items_for_scope()

    # Ensure stone inventory in state contains these exact items
    stones = STATE.setdefault('stones', {})
    for _s in items:
        stones.setdefault(_s, 0)
    # purge any leftover keys not in scope (keeps state tidy if user switches 386->151)
    for k in list(stones.keys()):
        if k not in items:
            stones.pop(k, None)

    # ---- Stone inventory UI
    st.subheader("Stone inventory")
    ecols = st.columns(max(1, min(5, len(items))))
    for i, stone in enumerate(items):
        c = ecols[i % len(ecols)]
        cur = int(STATE['stones'].get(stone, 0))
        c.markdown(f"**{stone_with_emoji(stone)}**: {cur}")
        cc1, cc2 = c.columns(2)
        if cc1.button("− Remove", key=f"st_dec_{stone.replace(' ', '_')}"):
            if STATE['stones'].get(stone, 0) > 0:
                STATE['stones'][stone] -= 1
                save_state(STATE); do_rerun()
        if cc2.button("Add +", key=f"st_inc_{stone.replace(' ', '_')}"):
            STATE['stones'][stone] = int(STATE['stones'].get(stone, 0)) + 1
            save_state(STATE); do_rerun()

    # Nothing else to do if roster empty
    if not STATE.get("roster"):
        st.info("No Pokémon yet.")
        return

    # ---- Filters
    c1, c2 = st.columns(2)
    show_ready_only = c1.checkbox("Show only 'Ready' evolutions", value=False, key="evo_ready_only")

    # Force evolve toggle (ignore requirements; does not consume stones)
    st.session_state.setdefault("force_evo", False)
    force_all = c2.checkbox("Force evolve (ignore requirements)", key="force_evo")

    rebuild_moves_default = False  # keep current behavior

    def _try_evolve(mon_guid: str, evo_to: str, do_force: bool):
        # Find mon
        target_mon = None
        for _m in STATE.get("roster", []):
            if str(_m.get("guid")) == str(mon_guid):
                target_mon = _m
                break
        if not target_mon:
            st.error("Could not find that Pokémon in your roster.")
            return

        # Build evo options and find the matching row
        opts = available_evos_for(target_mon.get("species", "")) or []
        rows = [evo_row(target_mon, o) for o in opts]

        row = next((r for r in rows if str(r.get("to")) == str(evo_to)), None)
        if not row:
            st.error("That evolution option no longer exists.")
            return

        ready_now = bool(row.get("ready")) or bool(do_force)
        if not ready_now:
            st.error("Not ready to evolve.")
            return

        # Consume stone ONLY if: item evolution, ready normally, and not forced
        if row.get("method") == "item" and row.get("item") in items and row.get("ready") and not do_force:
            if int(STATE["stones"].get(row["item"], 0)) <= 0:
                st.error(f"No {row['item']} left.")
                return
            STATE["stones"][row["item"]] -= 1

        # Evolve (default: keep moves)
        if evolve_mon_record(target_mon, evo_to, rebuild_moves=False):
            save_state(STATE)
            st.success(f"Evolved into {evo_to}.")
            do_rerun()
        else:
            st.error("Evolution failed (species not in database).")

    # ---- Helpers
    def evo_row(mon: dict, opt: dict) -> dict:
        lvl = int(mon.get("level", 1))
        method = opt.get("method")
        to_name = opt.get("to", "?")

        req_txt = "—"
        status_txt = "Manual"
        ready = True
        badge_class = "b-manual"
        req_level_val = 0
        item = None

        if method == "level" and isinstance(opt.get("level"), int):
            req = int(opt["level"])
            req_txt = f"Lv {req}"
            ready = lvl >= req
            status_txt = "Ready" if ready else f"Needs Lv {req}"
            badge_class = "b-level"
            req_level_val = req

        elif method == "item":
            item = opt.get("item") or "Use item"
            req_txt = stone_with_emoji(item)
            have = int(STATE['stones'].get(item, 0))
            ready = have > 0
            status_txt = f"{'Ready' if ready else 'Need'} {item} (you have {have})"
            badge_class = "b-item"

        elif method == "trade":
            req_txt = "Trade"
            ready = lvl >= TRADE_EVOLVE_LEVEL
            status_txt = f"Ready (Lv{TRADE_EVOLVE_LEVEL})" if ready else f"Trade or reach Lv{TRADE_EVOLVE_LEVEL}"
            badge_class = "b-trade"
            req_level_val = TRADE_EVOLVE_LEVEL

        to_total = get_species_total(to_name)
        from_total = int(mon.get("total", 0))

        return {
            "to": to_name,
            "method": method or "manual",
            "req_txt": req_txt,
            "ready": bool(ready),
            "status": status_txt,
            "badge": badge_class,
            "req_level": req_level_val,
            "item": item,
            "from_total": from_total,
            "to_total": to_total,
        }

    def method_bucket(r: dict) -> int:
        if r["ready"]:
            return 0
        if r["method"] == "item":
            return 1
        if r["method"] in ("level", "trade"):
            return 2
        return 3

    # Build rows
    mon_cards = []
    for mon in STATE["roster"]:
        species = mon.get("species", "?")
        lvl = int(mon.get("level", 1))
        opts = available_evos_for(species) or []
        rows = [evo_row(mon, o) for o in opts]
        rows.sort(
            key=lambda r: (
                method_bucket(r),
                0 if r["method"] == "item" else (r["req_level"] if r["method"] in ("level", "trade") else 999),
                r["to"],
            )
        )
        mon_cards.append((mon, rows, lvl))

    def mon_bucket_and_delta(rows: list, lvl: int) -> tuple[int, int]:
        """
        Bucket for card ordering + numeric tie-break:
          0 = READY (sorted by earliest required level; items=0, trade=TRADE_EVOLVE_LEVEL)
          1 = Not ready, item-based
          2 = Not ready, level/trade (sorted by fewest levels remaining)
        """
        # READY: order by earliest required "level"
        ready_lvls = []
        for r in rows:
            if not r.get("ready"):
                continue
            m = r.get("method")
            if m == "item":
                ready_lvls.append(0)  # stones have no level; top of READY
            elif m == "level":
                ready_lvls.append(int(r.get("req_level") or 0))
            elif m == "trade":
                ready_lvls.append(int(TRADE_EVOLVE_LEVEL))
            else:
                ready_lvls.append(999)

        if ready_lvls:
            return (0, min(ready_lvls))

        # Not ready, item-based comes next (no level delta concept)
        if any(r.get("method") == "item" for r in rows):
            return (1, 0)

        # Not ready, level/trade: fewest levels remaining first
        deltas = []
        for r in rows:
            m = r.get("method")
            if m == "level":
                deltas.append(max(0, int(r.get("req_level") or 0) - int(lvl)))
            elif m == "trade":
                deltas.append(max(0, int(TRADE_EVOLVE_LEVEL) - int(lvl)))

        return (2, min(deltas) if deltas else 999)


    # Sort pokemon cards (keeps the new behind-the-scenes ordering)
    mon_cards.sort(
        key=lambda tup: (
            mon_bucket_and_delta(tup[1], tup[2])[0],
            mon_bucket_and_delta(tup[1], tup[2])[1],
            tup[0]["species"].lower(),
        )
    )

    # Render
    ncols = 1
    for i in range(0, len(mon_cards), ncols):
        cols = st.columns(ncols)
        for j in range(ncols):
            if i + j >= len(mon_cards):
                break
            mon, rows, lvl = mon_cards[i + j]
            species = mon.get("species", "?")
            use_rows = [r for r in rows if r["ready"]] if show_ready_only else rows

            with cols[j].container(border=True):
                st.markdown('<span class="evo-card-marker"></span>', unsafe_allow_html=True)
                
                # current mon types -> gradient for the marked header area
                cur_types = purge_fairy_types_pair(mon.get("types") or [])
                cur_t1, cur_t2 = cur_types[0], cur_types[1]
                band_style = _cur_band_vars(cur_t1, cur_t2)

                current_band_html = f"""
                <div class="evo-inner-pad">
                <div class="evo-current-band" style="{band_style}">
                  <div class="evo-current-title">
                    {sprite_img_html(species)}<span><strong>{species} • Lv{lvl}</strong></span>
                  </div>
                </div>

                <div class="evo-header-bar">
                  <div class="evo-grid evo-head">
                    <div>Target</div>
                    <div>Method</div>
                    <div>Requirement</div>
                    <div>Status</div>
                    <div>Totals</div>
                    <div>Action</div>
                  </div>
                </div>
                </div>
              """
                st_html(current_band_html)

                if not use_rows:
                    st.caption("No evolutions available.")
                    continue
                for idx, r in enumerate(use_rows):
                    # Current Pokémon types (top half)
                    cur_types = purge_fairy_types_pair(mon.get("types") or [])
                    cur_t1, cur_t2 = cur_types[0], cur_types[1]

                    # Target Pokémon types (bottom half) – ensure it exists
                    tgt_name = r.get("to", "?")
                    tgt_rec = STATE.get("species_db", {}).get(species_key(tgt_name))
                    if not tgt_rec:
                        ensure_species_in_db(tgt_name)
                        tgt_rec = STATE.get("species_db", {}).get(species_key(tgt_name))

                    tgt_types = purge_fairy_types_pair((tgt_rec or {}).get("types") or [])
                    tgt_t1, tgt_t2 = tgt_types[0], tgt_types[1]

                    # Use ONLY target gradient across the whole row (both halves identical)
                    top_vars = _evo_gradient_vars("evo-top", tgt_t1, tgt_t2)
                    bot_vars = _evo_gradient_vars("evo-bot", tgt_t1, tgt_t2)
                    row_style = f"{top_vars}{bot_vars}"

                    method_map = {
                        "level": "Level",
                        "item": "Use Item",
                        "trade": "Trade",
                        "manual": "Manual",
                    }
                    method_pretty = method_map.get(r.get("method") or "manual", "Manual")
                    # Totals + delta MUST be defined before row_html uses them
                    from_total = int(r.get("from_total", 0))
                    to_total   = int(r.get("to_total", 0))
                    delta      = to_total - from_total
                    delta_txt  = f"+{delta}" if delta >= 0 else str(delta)

                    # --- Build evolve action: REAL Streamlit button that updates state ---
                    guid = str(mon.get("guid", ""))
                    to_name = str(r.get("to", "")).strip()

                    ready_now = bool(r.get("ready")) or bool(force_all)
                    btn_txt = "Evolve" if ready_now else "Not ready"

                    # Only force if force_all is on AND this row isn't normally ready
                    do_force = bool(force_all) and not bool(r.get("ready"))

                    # --- Row card HTML (Action cell left empty; button is overlaid via CSS) ---
                    row_html = f"""
                      <div class="evo-inner-pad">
                        <div class="evo-row-card" style="{row_style}">
                        <div class="evo-grid">
                          <div style="display:flex; align-items:center; gap:10px;">
                            {sprite_img_html(to_name)}
                            <div>
                              <div style="font-weight:800; font-size:14px; line-height:1.15;">{to_name}</div>
                              <div style="opacity:.9; font-size:12px;">Next evolution</div>
                            </div>
                          </div>

                          <div>{method_pretty}</div>
                          <div>{r.get("req_txt","—")}</div>
                          <div>{r.get("status","")}</div>

                          <div style="font-size:12px;">
                            {from_total} → <b>{to_total}</b>
                            <span style="opacity:.9;">({delta_txt})</span>
                          </div>

                          <div class="evo-action-slot"></div>
                        </div>
                      </div>
                      </div>
                    """
                    st_html(row_html)

                    # Real button (disabled when not ready). Styled + positioned by CSS.
                    evo_key = f"evo_btn__{guid}__{species_key(to_name)}__{idx}"
                    if st.button(btn_txt, key=evo_key, disabled=not ready_now):
                        _try_evolve(guid, to_name, do_force)

def render_saveload():
    st.header("Save / Load")

    st.markdown("**Download your current progress**")
    st.download_button(
        "Download save.json",
        data=json.dumps(STATE, indent=2, ensure_ascii=False),
        file_name="save.json"
    )

    st.markdown("---")
    st.markdown("**Import a save.json**")
    up = st.file_uploader("Choose save.json", type=["json"])
    if up is not None:
        try:
            data = json.loads(up.read().decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Uploaded JSON must be an object")
            st.session_state["STATE"] = migrate_state(data)
            st.success("Save loaded into this session.")
            # Stay on Save/Load after import
            st.session_state["_page_id"] = "saveload"
            STATE.setdefault("ui", {})["page"] = "saveload"
            save_state(STATE)
            st.rerun()
        except Exception as e:
            st.error(f"Failed to load save.json: {e}")

def evo_badge(label: str, color: str) -> str:
    return f'<span style="display:inline-block;padding:2px 8px;border-radius:9999px;border:1px solid rgba(0,0,0,.1);background:{color};color:white;font-size:12px;">{label}</span>'

def evo_status_badge(txt: str) -> str:
    color = '#16a34a' if 'Ready' in txt else ('#e11d48' if 'Need' in txt else '#6b7280')
    return evo_badge(txt, color)


# =============================================================================
# Sidebar routing
# =============================================================================
PAGE_REGISTRY = [
    ("pokedex",  "Pokédex",         render_pokedex),
    ("battle",   "Battle",          render_battle),
    ("evo",      "Evolution Watch", render_evo_watch),
    ("saveload", "Save / Load",     render_saveload),  # << was "save" — fix to "saveload"
    ("settings", "Settings",        render_settings),
]

def _run_router():
    st.sidebar.title("Navigation")

    # Only show pages that are enabled in settings (if present)
    vis = (STATE.get("settings", {}).get("visible_pages", {})) or {}
    pages = [(pid, lbl, fn) for (pid, lbl, fn) in PAGE_REGISTRY if vis.get(pid, True)]

    ids    = [pid for pid, _, _ in pages]
    labels = [lbl for _, lbl, _ in pages]

    ui = STATE.setdefault("ui", {})
    cur_id = st.session_state.get("_page_id") or ui.get("page") or (ids[0] if ids else None)

    # Default to first visible page if something got out of sync
    if cur_id not in ids:
        cur_id = ids[0]

    # The radio is keyed so it won’t reset to index=0 on reruns
    idx = ids.index(cur_id)
    choice = st.sidebar.radio("Go to", labels, index=idx, key="nav_radio")

    sel_id = ids[labels.index(choice)]
    if sel_id != cur_id:
        st.session_state["_page_id"] = sel_id
        ui["page"] = sel_id
        save_state(STATE)

    # Route exactly one page per run
    fn = next(fn for pid, _, fn in pages if pid == (ui.get("page") or sel_id))
    fn()
    
# ========= start app =========
_run_router()
