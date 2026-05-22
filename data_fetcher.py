"""
Récupération des données réelles depuis Open-Meteo.
- API principale (api.open-meteo.com) : débit + météo daily + hourly
- Fallback : données CSV historiques si l'API est inaccessible
"""
from datetime import date, timedelta

import pandas as pd
import requests

from config import CSV_DIR, HISTORY_DAYS, STATIONS

# ── URLs ──────────────────────────────────────────────────────────────────────
_MAIN_URL    = "https://api.open-meteo.com/v1/forecast"
_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Variables daily disponibles nativement
_DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "precipitation_sum",
    "river_discharge",          # GloFAS intégré à l'API principale
]

# Variables uniquement en hourly → agrégation daily
_HOURLY_VARS = [
    "surface_pressure",
    "relative_humidity_2m",
    "soil_moisture_0_to_7cm",
    "soil_moisture_7_to_28cm",
]

_DAILY_MAP = {
    "temperature_2m_max":  "t2m_max",
    "temperature_2m_min":  "t2m_min",
    "temperature_2m_mean": "t2m_mean",
    "precipitation_sum":   "precip_mm",
    "river_discharge":     "Q_api",
}
_HOURLY_MAP = {
    "surface_pressure":        "pression_hpa",
    "relative_humidity_2m":    "rh2m_pct",
    "soil_moisture_0_to_7cm":  "sm_surface",
    "soil_moisture_7_to_28cm": "sm_root",
}


# ── Biais historique ───────────────────────────────────────────────────────────

_HIST_MEANS: dict = {}

def _get_hist_means() -> dict:
    global _HIST_MEANS
    if _HIST_MEANS:
        return _HIST_MEANS
    for name, cfg in STATIONS.items():
        csv_path = CSV_DIR / cfg["csv"]
        if not csv_path.exists():
            _HIST_MEANS[name] = {"Q": 1.0, "precip": 1.0}
            continue
        df = pd.read_csv(csv_path)
        _HIST_MEANS[name] = {
            "Q":      max(df["Q"].clip(lower=0).mean(), 1e-6),
            "precip": max(df["precip_mm"].clip(lower=0).mean(), 1e-6)
                      if "precip_mm" in df else 1.0,
        }
    return _HIST_MEANS


# ── Appels API ─────────────────────────────────────────────────────────────────

def _fetch_all_daily(lat: float, lon: float,
                     past_days: int = HISTORY_DAYS) -> pd.DataFrame:
    """Un seul appel API : débit + météo daily + météo hourly agrégée."""
    params = {
        "latitude":      lat,
        "longitude":     lon,
        "daily":         ",".join(_DAILY_VARS),
        "hourly":        ",".join(_HOURLY_VARS),
        "past_days":     min(past_days, 92),   # limite API Open-Meteo
        "forecast_days": 1,
        "timezone":      "Africa/Abidjan",
    }
    r = requests.get(_MAIN_URL, params=params, timeout=60)
    r.raise_for_status()
    data = r.json()

    # ── Daily ────────────────────────────────────────────────────────────────
    df = pd.DataFrame({"date": pd.to_datetime(data["daily"]["time"])})
    for api_var, col in _DAILY_MAP.items():
        df[col] = data["daily"].get(api_var)

    # ── Hourly → agrégation daily ─────────────────────────────────────────
    df_h = pd.DataFrame({"datetime": pd.to_datetime(data["hourly"]["time"])})
    for api_var, col in _HOURLY_MAP.items():
        df_h[col] = data["hourly"].get(api_var)
    df_h["date"] = df_h["datetime"].dt.normalize()
    df_agg = (
        df_h.groupby("date")[list(_HOURLY_MAP.values())]
        .mean()
        .reset_index()
    )

    df = df.merge(df_agg, on="date", how="left")
    return df


# ── Fallback CSV ───────────────────────────────────────────────────────────────

def _load_from_csv(station_name: str,
                   past_days: int = HISTORY_DAYS) -> pd.DataFrame:
    """Charge les données depuis le CSV historique local."""
    cfg      = STATIONS[station_name]
    csv_path = CSV_DIR / cfg["csv"]
    if not csv_path.exists():
        return pd.DataFrame()

    cols = ["date", "Q", "precip_mm", "t2m_mean", "t2m_max", "t2m_min",
            "rh2m_pct", "pression_hpa", "sm_surface", "sm_root"]
    df = pd.read_csv(csv_path, parse_dates=["date"],
                     usecols=[c for c in cols if c in
                               pd.read_csv(csv_path, nrows=0).columns])
    df = df.sort_values("date").tail(past_days).reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    df["source"] = "csv_fallback"
    return df


# ── Fonction principale ────────────────────────────────────────────────────────

def fetch_station_data(station_name: str,
                       past_days: int = HISTORY_DAYS) -> pd.DataFrame:
    """
    Retourne un DataFrame avec Q corrigé + variables météo.
    Essaie l'API Open-Meteo ; bascule sur le CSV si inaccessible.

    Colonnes : date, Q, precip_mm, t2m_mean, t2m_max, t2m_min,
               rh2m_pct, pression_hpa, sm_surface, sm_root
    """
    cfg = STATIONS[station_name]
    lat, lon = cfg["lat"], cfg["lon"]
    hm = _get_hist_means()

    try:
        df = _fetch_all_daily(lat, lon, past_days=past_days)

        # Correction de biais Q
        q_hist    = hm.get(station_name, {}).get("Q", None)
        q_api_mean = df["Q_api"].dropna().mean()
        if q_hist and q_hist > 0 and q_api_mean > 0:
            df["Q"] = df["Q_api"] * (q_hist / q_api_mean)
        else:
            df["Q"] = df["Q_api"]

        # Correction de biais précipitations
        prec_hist = hm.get(station_name, {}).get("precip", None)
        prec_mean = df["precip_mm"].dropna().mean()
        if prec_hist and prec_mean and prec_mean > 0:
            ratio = prec_hist / prec_mean
            if 0.5 <= ratio <= 5.0:
                df["precip_mm"] = df["precip_mm"] * ratio

        df = df.drop(columns=["Q_api"], errors="ignore")

        # GloFAS indisponible pour ce bassin → Q toujours None → fallback CSV complet
        if df["Q"].isna().all():
            print(f"[WARN] {station_name} GloFAS Q=None — fallback CSV complet")
            df = _load_from_csv(station_name, past_days=past_days)
            if df.empty:
                df["Q"] = 0.0
        else:
            df["source"] = "openmeteo"

    except Exception as exc:
        print(f"[WARN] {station_name} API échouée ({exc}) — fallback CSV")
        df = _load_from_csv(station_name, past_days=past_days)
        if df.empty:
            raise RuntimeError(
                f"API inaccessible et CSV introuvable pour {station_name}"
            ) from exc

    # Nettoyage commun — conversion explicite en float avant clip
    df["Q"]         = pd.to_numeric(df["Q"], errors="coerce").fillna(0).clip(lower=0)
    df["precip_mm"] = pd.to_numeric(df["precip_mm"], errors="coerce").fillna(0).clip(lower=0)
    df = df.sort_values("date").reset_index(drop=True)
    return df


def fetch_all_stations(past_days: int = HISTORY_DAYS) -> dict:
    result = {}
    for name in STATIONS:
        try:
            result[name] = fetch_station_data(name, past_days=past_days)
        except Exception as exc:
            print(f"[ERROR] {name} : {exc}")
            result[name] = pd.DataFrame()
    return result
