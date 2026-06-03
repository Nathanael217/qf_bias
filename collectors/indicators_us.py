# QF_BIAS_BUILD: indicators_us — FRED actual + ADP/core-retail misattribution fix (2026-06-03e)
"""
collectors/indicators_us.py — Isi `actual` event kalender US dari FRED.

MASALAH yang diselesaikan:
  faireconomy weekly JSON TIDAK memberi `actual` (sudah dikonfirmasi dari data live —
  hanya forecast + previous). Tanpa actual, tidak ada surprise untuk dihitung.
  FRED punya nilai actual rilis untuk indikator US → kita ambil dari sana.

LINGKUP (sengaja KECIL & US-only untuk v1 — coverage lain menyusul):
  Hanya indikator yang UNITnya cocok langsung dengan forecast faireconomy,
  supaya tidak ada salah-unit diam-diam (landmine utama proyek ini).

PRINSIP (dikunci):
  - Modul ini MENGUKUR (actual, σ, arah). Engine yang MENGHITUNG poin (z → R_hard).
  - σ = proxy dari volatilitas rilis historis seri itu sendiri (PLACEHOLDER, backtestable).
    Ini BUKAN σ error-forecast sebenarnya (kita tak punya histori forecast gratis) —
    ditandai jujur, jangan dipercaya sebelum backtest.
  - Gagal per-event = graceful: actual tetap None → tidak ada kontribusi surprise → aman.

UNIT-MATCHING (kenapa set ini dipilih — semua cocok langsung dgn _parse_number kalender):
  Unemployment Rate   : UNRATE      level %      forecast "4.1%"→4.1   cocok   polarity −1
  Unemployment Claims : ICSA        level count  forecast "214K"→214000 cocok  polarity −1
  CPI m/m             : CPIAUCSL    mom %        forecast "0.3%"→0.3    cocok   polarity +1
  Core CPI m/m        : CPILFESL    mom %        forecast "0.3%"→0.3    cocok   polarity +1
  Core PCE m/m        : PCEPILFE    mom %        forecast "0.3%"→0.3    cocok   polarity +1
  Retail Sales m/m    : RSAFS       mom %        forecast "0.4%"→0.4    cocok   polarity +1
  Non-Farm Employment : PAYEMS      mom diff×1e3 forecast "118K"→118000 cocok  polarity +1
                                    (PAYEMS satuan ribuan → diff ×1000 agar = "118K")
  polarity: +1 = beat (actual>forecast) bullish currency; −1 = beat bearish (pengangguran).
"""

from __future__ import annotations

import logging
import statistics
import time
from typing import Any

import requests

# Reuse helper FRED dari macro (key + base url) — single source of truth.
from collectors.macro import _FRED_BASE_URL, _get_fred_key  # type: ignore

logger = logging.getLogger(__name__)

_FRED_TIMEOUT = 12
_FRED_RETRIES = 2
_THROTTLE_S = 0.6   # jeda anti-429 (pola sama dgn macro.py)
_N_OBS = 30         # jumlah observasi historis untuk hitung σ

# transform: cara ubah observasi FRED → metrik kalender
#   "level"   : actual = nilai terbaru;            σ = std(selisih antar-rilis)
#   "mom_pct" : actual = (v0/v1 − 1)×100;          σ = std(mom% historis)
#   "diff"    : actual = (v0 − v1) × scale;        σ = std(diff historis × scale)
# Tiap entry: (fred_series, transform, polarity, scale, label)
US_INDICATOR_MAP: list[dict[str, Any]] = [
    {"match": "unemployment rate",            "series": "UNRATE",   "transform": "level",   "polarity": -1, "scale": 1.0,    "label": "Unemployment Rate"},
    {"match": "unemployment claims",          "series": "ICSA",     "transform": "level",   "polarity": -1, "scale": 1.0,    "label": "Unemployment Claims"},
    {"match": "core cpi m/m",                 "series": "CPILFESL", "transform": "mom_pct", "polarity": +1, "scale": 1.0,    "label": "Core CPI m/m"},
    {"match": "cpi m/m",                      "series": "CPIAUCSL", "transform": "mom_pct", "polarity": +1, "scale": 1.0,    "label": "CPI m/m"},
    {"match": "core pce price index m/m",     "series": "PCEPILFE", "transform": "mom_pct", "polarity": +1, "scale": 1.0,    "label": "Core PCE m/m"},
    {"match": "core pce m/m",                 "series": "PCEPILFE", "transform": "mom_pct", "polarity": +1, "scale": 1.0,    "label": "Core PCE m/m"},
    {"match": "retail sales m/m",             "series": "RSAFS",    "transform": "mom_pct", "polarity": +1, "scale": 1.0,    "label": "Retail Sales m/m", "exclude": ["core"]},
    {"match": "jolts job openings",           "series": "JTSJOL",   "transform": "level",   "polarity": +1, "scale": 1000.0, "label": "JOLTS Job Openings"},
    {"match": "non-farm employment change",   "series": "PAYEMS",   "transform": "diff",    "polarity": +1, "scale": 1000.0, "label": "Non-Farm Payrolls", "exclude": ["adp"]},
]


def _match_indicator(event_name: str) -> dict | None:
    """Cari mapping yang cocok dgn nama event (case-insensitive substring).
    Urutan penting: 'core cpi m/m' dicek sebelum 'cpi m/m'."""
    n = (event_name or "").lower()
    for spec in US_INDICATOR_MAP:
        if spec["match"] in n and not any(x in n for x in spec.get("exclude", [])):
            return spec
    return None


def _fetch_fred_series(series_id: str, api_key: str, session: requests.Session,
                       n: int = _N_OBS) -> list[float]:
    """Ambil n observasi TERBARU (desc) dari FRED → list float (lewati '.').
    Return [] kalau gagal (graceful)."""
    params = {
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "sort_order": "desc", "limit": n, "observation_start": "2015-01-01",
    }
    attempt = 0
    while attempt <= _FRED_RETRIES:
        try:
            resp = session.get(_FRED_BASE_URL, params=params, timeout=_FRED_TIMEOUT)
            resp.raise_for_status()
            obs = resp.json().get("observations", [])
            vals = [float(o["value"]) for o in obs if o.get("value", ".") != "."]
            return vals  # urut desc (terbaru dulu)
        except requests.exceptions.HTTPError as exc:
            code = getattr(exc.response, "status_code", None)
            if code == 429:
                time.sleep(2.0); attempt += 1; continue
            logger.warning("FRED %s HTTP %s", series_id, code); return []
        except Exception as exc:
            logger.warning("FRED %s gagal: %s", series_id, exc)
            attempt += 1
            if attempt <= _FRED_RETRIES:
                time.sleep(1.5)
    return []


def compute_actual_and_sigma(vals: list[float], transform: str, scale: float) -> tuple[float | None, float | None]:
    """Dari deret FRED (desc, terbaru dulu) → (actual, σ) dalam unit kalender.

    σ = volatilitas rilis historis (proxy, PLACEHOLDER). Return (None,None) bila data kurang.
    """
    if not vals or len(vals) < 3:
        return None, None
    try:
        if transform == "level":
            actual = vals[0] * scale
            diffs = [(vals[i] - vals[i + 1]) * scale for i in range(len(vals) - 1)]
            sigma = statistics.pstdev(diffs) if len(diffs) >= 2 else None
        elif transform == "mom_pct":
            moms = [((vals[i] / vals[i + 1]) - 1.0) * 100.0 for i in range(len(vals) - 1) if vals[i + 1]]
            if not moms:
                return None, None
            actual = round(moms[0], 4)
            sigma = statistics.pstdev(moms) if len(moms) >= 2 else None
        elif transform == "diff":
            diffs = [(vals[i] - vals[i + 1]) * scale for i in range(len(vals) - 1)]
            if not diffs:
                return None, None
            actual = round(diffs[0], 2)
            sigma = statistics.pstdev(diffs) if len(diffs) >= 2 else None
        else:
            return None, None
    except (ZeroDivisionError, statistics.StatisticsError, ValueError):
        return None, None
    if sigma is not None and abs(sigma) < 1e-9:
        sigma = None
    return actual, (round(sigma, 6) if sigma is not None else None)


def enrich_us_actuals(events: list[dict], api_key: str | None = None) -> dict:
    """Isi `actual` + `historical_std` + `surprise_polarity` untuk event US yang
    sudah RELEASED & cocok mapping. Event lain tidak disentuh.

    Returns: {"events": events(diperkaya), "enriched": [list nama], "_meta": {...}}
    Tidak pernah raise. Gagal per-seri = event itu tetap actual=None (aman).
    """
    api_key = api_key or _get_fred_key()
    enriched: list[str] = []
    if not api_key:
        return {"events": events, "enriched": [], "_meta": {"error": "FRED_API_KEY tidak ada"}}

    # Hanya proses event US, status released, actual masih kosong → hemat kuota FRED.
    targets = [e for e in events
               if e.get("currency") == "USD"
               and e.get("status") == "released"
               and e.get("actual") is None
               and _match_indicator(e.get("name", "")) is not None]
    if not targets:
        return {"events": events, "enriched": [], "_meta": {"note": "tidak ada event US released yang cocok mapping"}}

    # Cache seri agar tidak fetch dua kali untuk indikator sama.
    series_cache: dict[str, list[float]] = {}
    session = requests.Session()
    fetched = 0
    for ev in targets:
        spec = _match_indicator(ev.get("name", ""))
        sid = spec["series"]
        if sid not in series_cache:
            series_cache[sid] = _fetch_fred_series(sid, api_key, session)
            fetched += 1
            time.sleep(_THROTTLE_S)
        actual, sigma = compute_actual_and_sigma(series_cache[sid], spec["transform"], spec["scale"])
        if actual is None:
            continue
        ev["actual"] = actual
        ev["historical_std"] = sigma                  # bisa None → build_surprises fallback ke raw delta
        ev["surprise_polarity"] = float(spec["polarity"])
        ev["actual_source"] = f"FRED:{sid}"
        enriched.append(ev.get("name", sid))

    return {
        "events": events,
        "enriched": enriched,
        "_meta": {"series_fetched": fetched, "matched": len(targets)},
    }
