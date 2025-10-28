Gen3 Matchup Helper (FireRed/LeafGreen)  Local App
===================================================

What you get
------------
- `app.py`  a single-file Streamlit app.
- `requirements.txt`  minimal dependencies (Streamlit only).
- The app saves your data to `state.json` in the same folder (species, learnsets, moves, roster, settings).

How to run
----------
1) Install Python 3.9+.
2) In a terminal in this folder:
   pip install -r requirements.txt
   streamlit run app.py

How to use
----------
1) Open the app in your browser (Streamlit prints a local http:// URL).

2) **Pokédex**: Add species (types + total stat). Use the Learnset Editor to add moves learned at specific levels.
   - Every time you add a move, assign its type. The app stores this in the Moves DB for re-use.

3) **Roster**: Add caught Pokémon by selecting species + level.
   - When "Auto-fill moves" is enabled, the app will compute the last four moves available at that level from the species learnset.
   - You can edit the four moves before saving (no CSV import; all input happens in the UI).

4) **Team**: Click "Save locks" if you want to force-include some members.
   - The app picks the top-Total six while enforcing unique primary types (toggle in Settings).

5) **Matchup**: Enter the opponent (types + level). Use auto-fill if you added that species learnset; otherwise type its four moves.
   - The app shows per-move multipliers and scores, picks the **single best move** for your offense and the **single best defensive outcome** versus their moves, then displays the total.

Notes
-----
- Dual-type effectiveness is computed by multiplying single-type values (Gen 3 chart; no Fairy).
- Scoring uses your table:
    Offense:  x4:+4, x2:+2, x1:0, x1/2:-2, x1/4:-4, x0:-5
    Defense:  x4:-4, x2:-2, x1:0, x1/2:+2, x1/4:+4, x0:+5

- You never need to import CSVs. All inputs are done in the app. If you want to move your data, copy `state.json`.