# QF_BIAS_BUILD: indicators_world — DBnomics non-US (EUR) + alignment guard (2026-06-03g)
"""
collectors/indicators_world.py — Isi `actual` event NON-US dari DBnomics.

KENAPA DBnomics:
  Kalender ber-actual (FMP/Finnhub) = berbayar. Sumber GRATIS + aman IP datacenter
  untuk non-US = nilai resmi (seperti FRED untuk US), diagregasi DBnomics
  (api.db.nomics.world) yang mencakup Eurostat/ONS/OECD/IMF — TANPA API key.

POLA SAMA PERSIS DENGAN indicators_us (FRED):
  actual = nilai resmi terbaru (DBnomics) ; forecast = faireconomy (sudah ada) ;
  σ = volatilitas rilis historis seri itu (proxy, PLACEHOLDER). z = (actual−forecast)/σ.
  Karena ada σ dari histori → DBnomics-sourced events IKUT menggerakkan skor (seperti FRED).

LINGKUP v1 = EUR dulu (paling tinggi dampaknya, Eurostat paling rapi). Perluas setelah verif.
BATAS KERAS: PMI (ISM/S&P Global) & sentimen (ZEW/Ifo) = berlisensi, TIDAK ada di DBnomics.
            Itu tetap bolong sampai berbayar. Yang ini menutup macro keras (CPI/unemployment).

VERIFIKASI KODE SERI (tanpa key, tinggal paste di browser):
  https://api.db.nomics.world/v22/series/Eurostat/prc_hicp_manr/M.RCH_A.CP00.EA?observations=1
  Kalau kode salah → fetch kosong → actual tetap None (aman). Diagnostik menampilkan
  seri mana yang resolve / gagal, jadi kode salah gampang dibetulkan satu baris.
"""

from __future__ import annotations

import logging
import time

import requests

from collectors.indicators_us import compute_actual_and_sigma, previous_aligned  # reuse teruji
from utils.timeutils import parse_iso_utc  # noqa: F401 (dipakai bila perlu di masa depan)

logger = logging.getLogger(__name__)

_DBN_URL = "https://api.db.nomics.world/v22/series/{provider}/{dataset}/{series}"
_TIMEOUT = 12
_THROTTLE_S = 0.4
_RETRIES = 2

# Peta indikator NON-US → seri DBnomics.
# Tiap entry: currency, match (substring nama event, lower), provider/dataset/series,
#             transform, polarity, scale, label.
# transform "level" = seri SUDAH melaporkan metrik (mis. HICP YoY%), actual = nilai terbaru.
WORLD_INDICATOR_MAP: list[dict] = [
    # --- EUR (Eurostat HICP — YoY%, cocok dgn "CPI Flash Estimate y/y") ---
    {"currency": "EUR", "match": "core cpi flash estimate y/y",
     "provider": "Eurostat", "dataset": "prc_hicp_manr", "series": "M.RCH_A.TOT_X_NRG_FOOD.EA",
     "transform": "level", "polarity": +1, "scale": 1.0, "label": "EUR Core HICP y/y"},
    {"currency": "EUR", "match": "cpi flash estimate y/y",
     "provider": "Eurostat", "dataset": "prc_hicp_manr", "series": "M.RCH_A.CP00.EA",
     "transform": "level", "polarity": +1, "scale": 1.0, "label": "EUR HICP y/y"},
    # (Perluasan berikut: GBP ONS CPI, AUD/CAD via OECD — tambah SETELAH verif kode.)
]


def _match_world(event_name: str, currency: str) -> dict | None:
    n = (event_name or "").lower()
    for spec in WORLD_INDICATOR_MAP:
        if spec["currency"] == currency and spec["match"] in n:
            return spec
    return None


def _fetch_dbnomics_series(provider: str, dataset: str, series: str) -> list[float]:
    """Ambil observasi seri DBnomics → list float DESC (terbaru dulu). [] bila gagal."""
    url = _DBN_URL.format(provider=provider, dataset=dataset, series=series)
    attempt = 0
    while attempt <= _RETRIES:
        try:
            resp = requests.get(url, params={"observations": "1"}, timeout=_TIMEOUT,
                                headers={"User-Agent": "qf_bias/1.0"})
            resp.raise_for_status()
            docs = (resp.json().get("series", {}) or {}).get("docs", [])
            if not docs:
                return []
            vals_raw = docs[0].get("value", [])
            # buang null / "NA"; DBnomics period ASC → balik jadi DESC
            vals = []
            for v in vals_raw:
                if v is None:
                    continue
                try:
                    vals.append(float(v))
                except (TypeError, ValueError):
                    continue
            return list(reversed(vals))
        except Exception as exc:
            attempt += 1
            if attempt <= _RETRIES:
                time.sleep(1.0)
            else:
                logger.warning("DBnomics %s/%s/%s gagal: %s", provider, dataset, series, exc)
    return []


def enrich_world_actuals(events: list[dict]) -> dict:
    """Isi actual + σ + polarity untuk event NON-US yang cocok mapping & sudah released.
    DBnomics tanpa key → selalu aktif. Gagal per-seri = graceful (actual tetap None).
    """
    targets = [e for e in events
               if e.get("currency") not in (None, "USD")
               and e.get("status") == "released"
               and e.get("actual") is None
               and _match_world(e.get("name", ""), e.get("currency", "")) is not None]
    if not targets:
        return {"events": events, "enriched": [], "_meta": {"note": "tidak ada event non-US cocok mapping"}}

    cache: dict[str, list[float]] = {}
    resolved, failed, mismatched = [], [], []
    for ev in targets:
        spec = _match_world(ev.get("name", ""), ev.get("currency", ""))
        key = f"{spec['provider']}/{spec['dataset']}/{spec['series']}"
        if key not in cache:
            cache[key] = _fetch_dbnomics_series(spec["provider"], spec["dataset"], spec["series"])
            time.sleep(_THROTTLE_S)
        vals = cache[key]
        if not vals:
            failed.append(key)
            continue
        actual, sigma = compute_actual_and_sigma(vals, spec["transform"], spec["scale"])
        if actual is None:
            failed.append(key)
            continue
        # GUARD alignment: previous seri harus cocok previous kalender; kalau tidak →
        # seri/vintage/timing salah → JANGAN isi (cegah actual keliru spt EUR 1.9 vs prev 3.0).
        if not previous_aligned(vals, spec["transform"], spec["scale"], ev.get("previous")):
            mismatched.append(f"{ev.get('name','?')} ({key})")
            continue
        ev["actual"] = actual
        ev["historical_std"] = sigma
        ev["surprise_polarity"] = float(spec["polarity"])
        ev["actual_source"] = f"DBnomics:{spec['provider']}"
        resolved.append(ev.get("name", key))

    return {"events": events, "enriched": resolved,
            "_meta": {"resolved": resolved, "failed_series": sorted(set(failed)),
                      "mismatched": mismatched, "matched": len(targets)}}
