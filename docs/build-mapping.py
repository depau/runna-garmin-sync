#!/usr/bin/env python3
"""Propose Runna exerciseId -> Garmin {category, exercise} mappings.
Retrieval + heuristic scoring; honest confidence. Human reviews low-confidence rows.
Key constraint: match INTENSITY TYPE (weighted<->weighted, bodyweight<->bodyweight)
so rows aren't awkward to edit in Garmin Connect."""
import json, csv, re, sys

R = sys.argv[1]
runna = json.load(open(f"{R}/docs/runna-exercise-catalog.json"))["exercises"]
gar = json.load(open(f"{R}/docs/garmin-exercises.json"))
all_ids = [l.strip() for l in open(f"{R}/docs/runna-exercise-ids.txt") if l.strip()]
meta = {e["exerciseId"]: e for e in runna}

gex = [(c, e["exercise"], e["name"]) for c, lst in gar["byCategory"].items() for e in lst]

STOP = {"the","a","an","and","with","to","on","of","or","your","for","1","2","3"}
EQUIP_WORDS = {  # word in name -> intensity class
    "barbell":"weighted","dumbbell":"weighted","kettlebell":"weighted","weighted":"weighted",
    "cable":"weighted","machine":"weighted","smith":"weighted","plate":"weighted","medicine":"weighted",
    "sandbag":"weighted","landmine":"weighted","trap":"weighted","ez":"weighted","goblet":"weighted",
    "band":"banded","banded":"banded",
    "bodyweight":"bodyweight","suspension":"bodyweight","swiss":"bodyweight","stability":"bodyweight",
}
# Runna equipment code -> intensity class
RUNNA_INTENSITY = {"BW":"bodyweight","STEP":"bodyweight","BOX":"bodyweight","PUB":"bodyweight",
                   "SWISSBALL":"bodyweight","BAND":"banded","DB":"weighted","BARBELL":"weighted","KB":"weighted"}
# Runna equipment inferred from id text when metadata missing
def infer_runna_equip(rid):
    t = rid.upper()
    if t.startswith("BARBELL") or "BARBELL" in t: return "BARBELL"
    if t.startswith("DUMBBELL") or t.startswith("DB_") or "_DB_" in t or "DUMBBELL" in t: return "DB"
    if t.startswith("KB_") or "KETTLEBELL" in t or t.startswith("WEIGHTED"): return "KB"
    if t.startswith("BANDED") or "BANDED" in t or t.endswith("_BAND"): return "BAND"
    if "SWISSBALL" in t or "SWISS_BALL" in t: return "SWISSBALL"
    if "PULL_UP" in t or "CHIN_UP" in t: return "PUB"
    return "BW"  # default assume bodyweight

def tokens(s):
    s = re.sub(r"[^a-z0-9]+"," ", s.lower())
    return [w for w in s.split() if w and w not in STOP]

# categories whose movements are inherently loaded (weighted) regardless of name wording
LOADED_CATS = {"OLYMPIC_LIFT","DEADLIFT","HIP_SWING","SHRUG","BENCH_PRESS","ROW","SHOULDER_PRESS",
    "CURL","FLYE","LATERAL_RAISE","TRICEPS_EXTENSION","CARRY","LEG_CURL","SANDBAG","SLED","TIRE",
    "BATTLE_ROPE","SLEDGE_HAMMER"}
def intensity_of_garmin(cat, name):
    ws = set(tokens(name))
    for w in ws:  # explicit equipment word wins
        if w in EQUIP_WORDS: return EQUIP_WORDS[w]
    if cat == "BANDED_EXERCISES": return "banded"
    if "bodyweight" in ws: return "bodyweight"
    if cat in LOADED_CATS: return "weighted"
    return "bodyweight"

# muscleGroupBroad -> ordered Garmin categories
CROSS = {
    "WARM_UP":["WARM_UP","CARDIO","PLYO","LUNGE","HIP_STABILITY"],
    "QUADS":["SQUAT","LUNGE"],
    "GLUTES":["HIP_RAISE","HIP_STABILITY","SQUAT","LUNGE"],
    "HAMSTRING":["LEG_CURL","DEADLIFT","HIP_RAISE"],
    "CALVES":["CALF_RAISE"],
    "CORE":["CORE","PLANK","CRUNCH","SIT_UP","LEG_RAISE"],
    "CHEST":["BENCH_PRESS","PUSH_UP","FLYE"],
    "BACK":["ROW","PULL_UP","HYPEREXTENSION"],
    "SHOULDERS":["SHOULDER_PRESS","LATERAL_RAISE","SHOULDER_STABILITY","SHRUG"],
    "FULL_BODY":["TOTAL_BODY","OLYMPIC_LIFT","CARRY"],
    "PLYOS":["PLYO"],
    "EXTRA":[],  # use all
}
# id-keyword -> Garmin category hints when muscle group missing
KW_CAT = [("SQUAT","SQUAT"),("LUNGE","LUNGE"),("DEADLIFT","DEADLIFT"),("RDL","DEADLIFT"),
    ("HIP_THRUST","HIP_RAISE"),("GLUTE_BRIDGE","HIP_RAISE"),("BRIDGE","HIP_RAISE"),
    ("CALF","CALF_RAISE"),("PLANK","PLANK"),("CRUNCH","CRUNCH"),("SITUP","SIT_UP"),("SIT_UP","SIT_UP"),
    ("ROW","ROW"),("PULL_UP","PULL_UP"),("CHIN_UP","PULL_UP"),("PULLUP","PULL_UP"),
    ("BENCH_PRESS","BENCH_PRESS"),("PRESS_UP","PUSH_UP"),("PUSH_UP","PUSH_UP"),("PUSHUP","PUSH_UP"),
    ("FLY","FLYE"),("FLYE","FLYE"),("SHOULDER_PRESS","SHOULDER_PRESS"),("OVERHEAD_PRESS","SHOULDER_PRESS"),
    ("LATERAL_RAISE","LATERAL_RAISE"),("FRONT_RAISE","LATERAL_RAISE"),("SIDE_RAISE","LATERAL_RAISE"),
    ("SHRUG","SHRUG"),("CURL","CURL"),("TRICEP","TRICEPS_EXTENSION"),("DIP","TRICEPS_EXTENSION"),
    ("CLEAN","OLYMPIC_LIFT"),("SNATCH","OLYMPIC_LIFT"),("THRUSTER","TOTAL_BODY"),("BURPEE","TOTAL_BODY"),
    ("CARRY","CARRY"),("JUMP","PLYO"),("POGO","PLYO"),("SKIP","PLYO"),("BOX_JUMP","PLYO"),
    ("LEG_RAISE","LEG_RAISE"),("DEADBUG","CORE"),("RUSSIAN_TWIST","CORE"),("MOUNTAIN_CLIMB","PLANK"),
    ("HAMSTRING","LEG_CURL"),("LEG_CURL","LEG_CURL"),("CLAM","HIP_STABILITY"),("FIRE_HYDRANT","HIP_STABILITY"),
    ("ABDUCTOR","HIP_STABILITY"),("HEEL","CALF_RAISE"),("TOE_RAISE","CALF_RAISE"),("STEP_UP","LUNGE"),
    ("SWING","HIP_SWING"),("KB_SWING","HIP_SWING"),("KETTLEBELL_SWING","HIP_SWING"),
    ("DEADBUG","CORE"),("DEAD_BUG","CORE"),("SIDE_BEND","CORE"),("WALKOUT","PLANK"),("BEAR_CRAWL","PLANK"),
    ("HIP_THRUST","HIP_RAISE"),("MARCH","CARDIO"),("SIDE_PLANK","PLANK"),
]

def intensity_bonus(runna_int, gi):
    # intensity is a PREFERENCE, not a hard filter: reward a match, don't exclude a mismatch
    if runna_int == gi: return 0.30
    unloaded = {"bodyweight","banded"}
    if runna_int in unloaded and gi in unloaded: return 0.18   # banded~bodyweight, both no logged kg
    return 0.0                                                  # weighted<->bodyweight: allowed, no bonus

def candidate_categories(rid, m, runna_int):
    cats = []
    if m and m.get("muscleGroupBroad") in CROSS:
        cats = list(CROSS[m["muscleGroupBroad"]])
    t = rid.upper()
    for kw, cat in KW_CAT:
        if kw in t and cat not in cats: cats.append(cat)
    if runna_int == "banded" and "BANDED_EXERCISES" not in cats:
        cats.insert(0, "BANDED_EXERCISES")
    return cats

def squish(s):  # collapse to bare alnum for compound-word matching (deadbug ~ dead bug)
    return re.sub(r"[^a-z0-9]+","", s.lower())

def score(rid, m):
    equip = m["requires"] if (m and m.get("requires")) else infer_runna_equip(rid)
    runna_int = RUNNA_INTENSITY.get(equip, "bodyweight")
    # strip equipment words from runna tokens for name matching
    rt = [w for w in tokens(rid) if w not in ("barbell","dumbbell","db","kb","kettlebell","banded","band",
          "bw","bodyweight","weighted","swissball","step","box","single","double","arm","leg","sl","dl")]
    rt_set = set(rt) | set(tokens(rid))
    rsq = squish(rid)
    cats = candidate_categories(rid, m, runna_int)
    pool = [g for g in gex if (not cats or g[0] in cats)]
    if not pool: pool = gex
    best = None
    for c, ekey, name in pool:
        gi = intensity_of_garmin(c, name)
        nt = set(tokens(name)) | set(tokens(ekey))
        inter = rt_set & nt
        jac = len(inter) / len(rt_set | nt) if (rt_set | nt) else 0
        gsq = squish(ekey); gsq2 = squish(name)
        if rsq and rsq == gsq: sub = 0.6
        elif rsq and rsq == gsq2: sub = 0.55
        else: sub = 0
        namescore = jac + sub
        if namescore == 0: continue                  # need some name signal
        catboost = 0.15 if (cats and c == cats[0]) else 0
        enum_exact = 0.35 if ekey == rid else 0
        spec = -0.02 * len(nt - rt_set)
        s = namescore + catboost + enum_exact + spec + intensity_bonus(runna_int, gi)
        if best is None or s > best[0]:
            best = (s, c, ekey, name, runna_int, gi)
    return best, runna_int, equip, cats

# per-category generic (exercise == category if it exists, else shortest-named)
GENERIC_CAT = {}
for c, lst in gar["byCategory"].items():
    exact = [e for e in lst if e["exercise"] == c]
    GENERIC_CAT[c] = (c, exact[0]["exercise"]) if exact else (c, min(lst, key=lambda e:len(e["name"]))["exercise"])
# muscleGroupBroad -> the fallback category to draw a generic from (respects intensity where it can)
GENERIC_MUSCLE = {
    "WARM_UP":"WARM_UP","QUADS":"SQUAT","GLUTES":"HIP_RAISE","HAMSTRING":"LEG_CURL",
    "CALVES":"CALF_RAISE","CORE":"CORE","CHEST":"PUSH_UP","BACK":"ROW","SHOULDERS":"SHOULDER_PRESS",
    "FULL_BODY":"TOTAL_BODY","PLYOS":"PLYO","EXTRA":"CORE","":"TOTAL_BODY",
}
def generic_for(rid, m, cats):
    # prefer the first crosswalked category that has a generic; else muscle-group default
    for c in cats:
        if c in GENERIC_CAT: return GENERIC_CAT[c]
    mus = (m or {}).get("muscleGroupBroad","")
    return GENERIC_CAT[GENERIC_MUSCLE.get(mus, "TOTAL_BODY")]

def humanize(rid):
    return rid.replace("_"," ").title()

# ---- hand-curated overrides for verified exercises (rid -> (cat, exercise, conf, note)) ----
# None cat = deliberate degrade (no faithful Garmin equivalent; carry name in description).
CURATED = {
 "BARBELL_SQUAT": ("SQUAT","BARBELL_BACK_SQUAT","high",""),
 "BANDED_HAMSTRING_CURL": ("BANDED_EXERCISES","HAMSTRING_CURLS","high",""),
 "BANDED_TOE_RAISE": ("BANDED_EXERCISES","CALF_RAISES","low","no banded toe-raise; banded lower-leg (relaxed)"),
 "HAMSTRING_WALKOUT": ("LEG_CURL","SLIDING_LEG_CURL","med","heel walkout ≈ sliding leg curl"),
 "SL_ISO_HAMSTRING_HOLD": ("LEG_CURL","LEG_CURL","low","isometric hamstring; generic hamstring"),
 "HIP_DROP": ("HIP_STABILITY","HIP_STABILITY","low","running pelvic-drop; hip stability (same area)"),
 "FLOATING_HEEL_DROP": ("CALF_RAISE","CALF_RAISE","low","calf eccentric; generic calf raise"),
 "PRESS_UP": ("PUSH_UP","PUSH_UP","high","press-up = push-up"),
 "TRAVELLING_PRESS_UP_WALK_OUT": ("WARM_UP","WALKOUT_FROM_PUSH_UP_POSITION","med",""),
 "PRESS_UP_POSITION_WALK_OUT": ("WARM_UP","WALKOUT_FROM_PUSH_UP_POSITION","high",""),
 "PRESS_UP_POSITION_DIAGONAL_TOE_TAP": (None,None,"low","warmup drill; no equivalent"),
 "STEP_UP": ("SQUAT","STEP_UP","high",""),
 "DUMBBELL_CHEST_FLY": ("FLYE","DUMBBELL_FLYE","high",""),
 "STANDING_SIDE_RAISE": ("LATERAL_RAISE","LATERAL_RAISE","high","standing dumbbell lateral raise"),
 "STANDING_BARBELL_PRESS": ("SHOULDER_PRESS","BARBELL_SHOULDER_PRESS","high",""),
 "SINGLE_ARM_ROW": ("ROW","ONE_ARM_BENT_OVER_ROW","high",""),
 "DOUBLE_LEG_CALF_RAISE_ON_STEP": ("CALF_RAISE","STANDING_DUMBBELL_CALF_RAISE","med","double-leg weighted calf raise"),
 "SINGLE_LEG_CALF_RAISE": ("CALF_RAISE","SINGLE_LEG_STANDING_CALF_RAISE","high",""),
 "FRONT_LEG_RAISED_LUNGE": ("LUNGE","LUNGE","med","front-foot-raised split squat; generic bodyweight lunge"),
 "REAR_LEG_RAISED_LUNGE": ("LUNGE","LUNGE","med","rear-foot-elevated split squat (bodyweight); no exact match"),
 "STATIC_LUNGE": ("LUNGE","BARBELL_SPLIT_SQUAT","high","static lunge = split squat"),
 "REVERSE_LUNGE": ("LUNGE","DUMBBELL_REVERSE_LUNGE","high",""),
 "LATERAL_WALK": ("HIP_STABILITY","LATERAL_WALKS_WITH_BAND_AT_ANKLES","high",""),
 "STANDING_MARCH": (None,None,"low","warmup march; no equivalent"),
 "GLUTE_BRIDGE": ("HIP_RAISE","HIP_RAISE","high","bodyweight glute bridge = hip raise"),
 "SINGLE_LEG_GLUTE_BRIDGE": ("HIP_RAISE","SINGLE_LEG_HIP_RAISE","high",""),
 "RAISED_LEG_HIP_THRUST": ("HIP_RAISE","BARBELL_HIP_THRUST_WITH_BENCH","med",""),
 "KB_SWINGS": ("HIP_RAISE","KETTLEBELL_SWING","high",""),
 "DOUBLE_ARM_CLEAN": ("OLYMPIC_LIFT","DUMBBELL_CLEAN","high",""),
 "SINGLE_ARM_CLEAN": ("OLYMPIC_LIFT","DUMBBELL_CLEAN","med","single-arm dumbbell clean"),
 "THRUSTER": ("SHOULDER_PRESS","DUMBBELL_PUSH_PRESS","low","thruster=squat+press; no exact, using push press"),
 "CRUNCH_AND_PRESS": (None,None,"low","weighted crunch+press; no equivalent"),
 "SIDEPLANK_HIP_FLEXORS": ("PLANK","SIDE_PLANK","med",""),
 "SL_COPENHAGEN_PLANK": ("PLANK","SIDE_PLANK","med","Copenhagen plank; using side plank"),
 "DEADBUG": ("HIP_STABILITY","DEAD_BUG","high",""),
 "CLAM_SHELLS": ("BANDED_EXERCISES","CLAM_SHELLS","med","clam shell (Garmin lists it under banded)"),
 "POGO_JUMPS": ("CARDIO","JUMP_ROPE","med","ankle pogo hops ≈ jump-rope bounce (no rope)"),
 "DL_SKIPPING": ("CARDIO","JUMP_ROPE","high","double-leg rope skipping = jump rope"),
 "KNEE_DRIVE": ("WARM_UP","WALKING_HIGH_KNEES","med","running-drill knee drive"),
 "A_SKIP": ("WARM_UP","WALKING_HIGH_KNEES","med","A-skip running drill ≈ high-knee walk"),
 "B_SKIP": ("WARM_UP","WALKING_HIGH_KNEES","med","B-skip running drill ≈ high-knee walk"),
 "WALKING_A_SKIP": ("WARM_UP","WALKING_HIGH_KNEES","med","walking A-skip ≈ high-knee walk"),
 "WALKING_B_SKIP": ("WARM_UP","WALKING_HIGH_KNEES","med","walking B-skip ≈ high-knee walk"),
 "GLUTE_BRIDGE_HAM_WALKOUT": ("LEG_CURL","SLIDING_LEG_CURL","med","glute bridge + heel walkout ≈ sliding leg curl"),
 "HEEL_WALKS": (None,None,"low","heel walk (warmup); no equivalent"),
 "TOE_WALKS": (None,None,"low","toe walk (warmup); no equivalent"),
 "DIAGONAL_TOE_TAP": (None,None,"low","warmup drill; no close equivalent"),
 "WEIGHTED_DEADLIFT": ("DEADLIFT","DEADLIFT","med","generic weighted deadlift"),
 "TRICEP_DIP": ("TRICEPS_EXTENSION","BENCH_DIP","high","box/bench dip (bodyweight)"),
 "STEP_DOWN": ("SQUAT","STEP_UP","med","eccentric step-down ≈ step-up"),
 "SL_DB_RDL_KNEE_DRIVE": ("DEADLIFT","SINGLE_LEG_ROMANIAN_DEADLIFT_WITH_DUMBBELL","high","single-leg DB RDL (+knee drive)"),
}
# validate curated targets exist
gset = {(c,e["exercise"]) for c,lst in gar["byCategory"].items() for e in lst}
for rid,(c,ek,_,_) in CURATED.items():
    if c is not None and (c,ek) not in gset:
        print(f"!! CURATED typo: {rid} -> {c}/{ek} not in Garmin data", file=sys.stderr)

gname_by = {(c,e["exercise"]):e["name"] for c,lst in gar["byCategory"].items() for e in lst}
def imatch(runna_int, cat, ekey):
    return "same" if runna_int == intensity_of_garmin(cat, gname_by[(cat,ekey)]) else "relaxed"

rows = []
# union: ids harvested from the bundle + catalog + curated (covers ids Runna
# added after the 261-id harvest, e.g. GLUTE_BRIDGE_HAM_WALKOUT)
for rid in sorted(set(all_ids) | set(meta) | set(CURATED)):
    m = meta.get(rid)
    verified = "verified" if m else "inferred"
    equip = m["requires"] if (m and m.get("requires")) else infer_runna_equip(rid)
    runna_int = RUNNA_INTENSITY.get(equip, "bodyweight")
    mus = (m or {}).get("muscleGroupBroad","")
    hint = humanize(rid)
    # curated exact target
    if rid in CURATED and CURATED[rid][0] is not None:
        c, ekey, conf, note = CURATED[rid]
        rows.append([rid,c,ekey,gname_by[(c,ekey)],"curated",conf,verified,runna_int,equip,mus,
                     imatch(runna_int,c,ekey),hint,note]); continue
    best, runna_int, equip, cats = score(rid, m)
    curated_note = CURATED[rid][3] if rid in CURATED else ""
    if best is not None and best[0] >= 0.30:
        s, c, ekey, name, ri, gi = best
        conf = "high" if s >= 0.60 else ("med" if s >= 0.38 else "low")
        if not m and conf == "high": conf = "med"      # inferred capped at med
        method = "enum-exact" if ekey == rid else "heuristic"
        rows.append([rid,c,ekey,name,method,conf,verified,runna_int,equip,mus,
                     imatch(runna_int,c,ekey),hint,curated_note]); continue
    # no real name match -> same-muscle-group generic placeholder (never degrade)
    c, ekey = generic_for(rid, m, candidate_categories(rid, m, runna_int))
    note = curated_note or "no close match; generic same-muscle-group placeholder — see description"
    rows.append([rid,c,ekey,gname_by[(c,ekey)],"fallback","low",verified,runna_int,equip,mus,
                 imatch(runna_int,c,ekey),hint,note])

cols = ["runna_exerciseId","garmin_category","garmin_exercise","garmin_name","method","confidence",
        "runna_data","intensity","runna_equip","runna_muscleBroad","intensity_match","description_hint","notes"]
with open(f"{R}/src/runna_garmin_sync/runna-garmin-mapping.csv","w",newline="") as fh:
    w = csv.writer(fh); w.writerow(cols); w.writerows(rows)

from collections import Counter
print("rows:", len(rows), "| all mapped (no degrades):", all(r[1] for r in rows))
print("confidence:", dict(Counter(r[5] for r in rows)))
print("method:", dict(Counter(r[4] for r in rows)))
print("intensity_match:", dict(Counter(r[10] for r in rows)))
print("verified confidence:", dict(Counter(r[5] for r in rows if r[6]=="verified")))
