# main.py — v3: CAD + MOID from SBDB + official PHA flag (robust)
import datetime as dt, math, time, re
from typing import Optional, Dict, List

import requests
import pandas as pd

CAD_URL = "https://ssd-api.jpl.nasa.gov/cad.api"
SBDB_URL = "https://ssd-api.jpl.nasa.gov/sbdb.api"
AU_PER_LD = 1.0 / 389.174
REQUEST_SLEEP = 0.2  # be polite to SBDB

def h_to_diameter_km(H: float, albedo: float) -> float:
    """Photometric relation: D(km) = 1329/sqrt(p) * 10^(-H/5)."""
    return 1329.0 / math.sqrt(albedo) * (10 ** (-H / 5.0))

def _clean_sstr(s: str) -> str:
    """Trim, drop outer () and collapse spaces for SBDB search strings."""
    s = re.sub(r'^\s+|\s+$', '', str(s))
    s = re.sub(r'^\((.*)\)$', r'\1', s)
    s = re.sub(r'\s+', ' ', s)
    return s

def _extract_moid_from_orbit(orbit: dict) -> Optional[float]:
    """
    SBDB 'orbit' can carry elements as either:
      - dict: orbit['elements']['moid']
      - list of dicts: [{'name': 'moid', 'value': '0.0123'}, ...]
    This handles both; returns float or None.
    """
    if not isinstance(orbit, dict):
        return None

    elems = orbit.get("elements")
    moid = None

    # Case A: dict-style elements
    if isinstance(elems, dict):
        moid = elems.get("moid")
        # some variants may use different keys; try a couple fallbacks
        if moid is None:
            moid = elems.get("Earth MOID") or elems.get("moid_au")

    # Case B: list-style elements (name/value pairs)
    elif isinstance(elems, list):
        for item in elems:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip().lower()
            if name in ("moid", "earth moid"):
                moid = item.get("value") or item.get("val")
                break

    # Rare case: moid at orbit-level
    if moid is None and "moid" in orbit:
        moid = orbit.get("moid")

    # Normalize to float
    if isinstance(moid, (int, float)):
        return float(moid)
    if isinstance(moid, str):
        try:
            return float(moid)
        except ValueError:
            # strip any units/extra text, e.g. "0.0123 au"
            m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", moid)
            return float(m.group(0)) if m else None
    return None

def sbdb_lookup_moid(des_or_name: str) -> Optional[float]:
    """Return Earth MOID (au) from SBDB; None if missing or API error."""
    try:
        s = _clean_sstr(des_or_name)
        r = requests.get(SBDB_URL, params={"sstr": s, "phys-par": "false"}, timeout=30)
        r.raise_for_status()
        j = r.json()
        if not isinstance(j, dict):
            return None
        orbit = j.get("orbit", {})
        return _extract_moid_from_orbit(orbit)
    except requests.RequestException:
        return None

def main():
    # --- date window (30 days ahead) ---
    today = dt.date.today()
    date_min = today.isoformat()
    date_max = (today + dt.timedelta(days=30)).isoformat()

    # --- fetch close-approach events from CAD ---
    params = {
        "date-min": date_min,
        "date-max": date_max,
        "body": "Earth",
        "dist-max": 0.2,       # AU
        "sort": "date",
        "limit": 2000,
        "fullname": "true",
    }
    r = requests.get(CAD_URL, params=params, timeout=30)
    r.raise_for_status()
    j = r.json()
    fields, rows = j.get("fields", []), j.get("data", [])
    if not rows:
        print("No upcoming close approaches in the specified window.")
        return

    df = pd.DataFrame(rows, columns=fields)

    # --- make numeric ---
    for col in ["dist", "dist_min", "dist_max", "v_rel", "v_inf", "h"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # --- derived metrics (distance in LD, H->diameter) ---
    df["dist_ld"] = (df["dist"] / AU_PER_LD).round(2)
    df["diam_km_nom"] = df["h"].apply(lambda H: h_to_diameter_km(H, 0.14) if pd.notna(H) else None)
    df["diam_km_min"] = df["h"].apply(lambda H: h_to_diameter_km(H, 0.25) if pd.notna(H) else None)
    df["diam_km_max"] = df["h"].apply(lambda H: h_to_diameter_km(H, 0.05) if pd.notna(H) else None)

    # --- simple flags (independent of MOID) ---
    df["close_<=0.05au"] = df["dist"] <= 0.05
    df["big_enough_H<=22"] = df["h"] <= 22.0

    # --- SBDB MOID enrichment (use clean 'des' for lookup) ---
    unique_ids: List[str] = sorted(df["des"].dropna().unique().tolist())
    moids: Dict[str, Optional[float]] = {}
    for des in unique_ids:
        moids[des] = sbdb_lookup_moid(des)
        time.sleep(REQUEST_SLEEP)  # throttle so we don't hammer the API

    df["moid_au"] = df["des"].map(moids)

    # --- official PHA criterion: MOID ≤ 0.05 AU AND H ≤ 22.0 ---
    df["PHA_by_def"] = (df["moid_au"] <= 0.05) & (df["h"] <= 22.0)

    # --- sort & select columns ---
    df = df.sort_values(by=["dist", "h", "v_rel"], ascending=[True, True, False]).reset_index(drop=True)
    cols = [
        "cd","des","fullname","dist","dist_ld","v_rel","h",
        "diam_km_nom","diam_km_min","diam_km_max",
        "moid_au","close_<=0.05au","big_enough_H<=22","PHA_by_def"
    ]
    out = df[[c for c in cols if c in df.columns]].rename(columns={
        "cd":"close_approach_time_TDB",
        "des":"designation",
        "dist":"dist_au",
        "v_rel":"v_rel_km_s",
        "h":"H_abs_mag"
    })

    # --- print preview & watchlist (robust) ---
    if "PHA_by_def" in out.columns:
        is_pha = out["PHA_by_def"].fillna(False)
    else:
        is_pha = pd.Series(False, index=out.index)

    is_close = out["dist_au"] <= 0.01
    interesting = out[is_pha | is_close]

    print("\n=== Interesting (PHA or ≤0.01 AU) ===")
    print(interesting.head(15).to_string(index=False) if not interesting.empty else "(none)")

    print("\n=== Preview (first 15 overall) ===")
    print(out.head(15).to_string(index=False))

    # --- save CSVs ---
    out.to_csv("close_approaches.csv", index=False)
    interesting.to_csv("watchlist.csv", index=False)
    print("\nSaved: close_approaches.csv and watchlist.csv")

if __name__ == "__main__":
    main()