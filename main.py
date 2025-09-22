# main.py — v2: add pandas, derived columns, CSV
import datetime as dt, math
import requests, pandas as pd

CAD_URL = "https://ssd-api.jpl.nasa.gov/cad.api"
AU_PER_LD = 1.0 / 389.174

def h_to_diameter_km(H: float, albedo: float) -> float:
    return 1329.0 / math.sqrt(albedo) * (10 ** (-H / 5.0))

def main():
    today = dt.date.today()
    date_min = today.isoformat()
    date_max = (today + dt.timedelta(days=30)).isoformat()

    params = {
        "date-min": date_min,
        "date-max": date_max,
        "body": "Earth",
        "dist-max": 0.2,
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

    # make numeric
    for col in ["dist", "dist_min", "dist_max", "v_rel", "v_inf", "h"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # derived columns
    df["dist_ld"] = (df["dist"] / AU_PER_LD).round(2)
    df["diam_km_nom"] = df["h"].apply(lambda H: h_to_diameter_km(H, 0.14) if pd.notna(H) else None)
    df["diam_km_min"] = df["h"].apply(lambda H: h_to_diameter_km(H, 0.25) if pd.notna(H) else None)
    df["diam_km_max"] = df["h"].apply(lambda H: h_to_diameter_km(H, 0.05) if pd.notna(H) else None)

    # quick flags
    df["close_<=0.05au"] = df["dist"] <= 0.05
    df["big_enough_H<=22"] = df["h"] <= 22.0

    # select & rename
    cols = ["cd","des","fullname","dist","dist_ld","v_rel","h","diam_km_nom","diam_km_min","diam_km_max","close_<=0.05au","big_enough_H<=22"]
    out = df[[c for c in cols if c in df.columns]].rename(columns={
        "cd":"close_approach_time_TDB","des":"designation","dist":"dist_au","v_rel":"v_rel_km_s","h":"H_abs_mag"
    })
    # --- Watchlist ---
    is_pha = (out["PHA_by_def"] == True) if "PHA_by_def" in out.columns else False
    is_very_close = out["dist_au"] <= 0.01
    watch = out[is_pha | is_very_close].copy()
    watch.to_csv("watchlist.csv", index=False)
    print(f"Saved watchlist.csv (rows: {len(watch)})")

if __name__ == "__main__":
    main()
