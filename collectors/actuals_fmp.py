# QF_BIAS_BUILD: actuals_fmp — FMP economic calendar actual (key-gated, display-only) (2026-06-03)
"""
collectors/actuals_fmp.py — Isi `actual` dari Financial Modeling Prep (FMP).

KENAPA FMP, BUKAN SCRAPE:
  ISM tidak ada di FRED (dihapus 2016, licensing). Scrape ISM/Investing/TradingEconomics
  = Cloudflare 403 dari IP datacenter (persis kegagalan retail layer). FMP = API ber-key,
  AMAN dari IP datacenter, dan balikannya berisi actual/estimate/previous termasuk ISM.

DISIPLIN (penting):
  FMP memberi `actual` tapi TIDAK memberi σ. Tanpa σ, surprise tidak bisa dinormalisasi
  → kalau dipaksa masuk skor = noise. Maka modul ini HANYA mengisi `actual` untuk DITAMPILKAN
  (beat/miss). Ia TIDAK menyetel `historical_std`, sehingga app.py TIDAK memasukkannya ke
  feed surprise R_hard (aturan: hanya surprise ber-σ yang menggerakkan skor).

PRASYARAT:
  FMP_API_KEY di Streamlit Secrets (gratis di financialmodelingprep.com). Tanpa key → no-op.
  Catatan jujur: free tier FMP membatasi kuota (≈250 req/hari) dan BISA membatasi endpoint
  economic_calendar — verifikasi di deploy. Gagal/diblok = graceful (actual tetap kosong).

ENDPOINT:
  https://financialmodelingprep.com/api/v3/economic_calendar?from=YYYY-MM-DD&to=YYYY-MM-DD&apikey=KEY
  Item: {event, date "YYYY-MM-DD HH:MM:SS" (UTC), country, actual, previous, estimate, impact}
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from difflib import SequenceMatcher

import requests

from utils.timeutils import now_utc, parse_iso_utc

logger = logging.getLogger(__name__)

_FMP_URL = "https://financialmodelingprep.com/api/v3/economic_calendar"
_TIMEOUT = 12

# FMP country code → currency kita.
_COUNTRY_TO_CCY: dict[str, str] = {
    "US": "USD", "USA": "USD", "United States": "USD",
    "EU": "EUR", "EA": "EUR", "Euro Zone": "EUR", "Germany": "EUR", "France": "EUR",
    "GB": "GBP", "UK": "GBP", "United Kingdom": "GBP",
    "JP": "JPY", "Japan": "JPY",
    "AU": "AUD", "Australia": "AUD",
    "NZ": "NZD", "New Zealand": "NZD",
    "CA": "CAD", "Canada": "CAD",
    "CH": "CHF", "Switzerland": "CHF",
}


def _get_fmp_key() -> str | None:
    try:
        import streamlit as st  # type: ignore
        k = st.secrets.get("FMP_API_KEY") or st.secrets.get("fmp_api_key")
        if k:
            return str(k)
    except Exception:
        pass
    return os.environ.get("FMP_API_KEY")


def _polarity_for(name: str) -> float:
    """Heuristik arah untuk DISPLAY (bukan untuk skor): pengangguran/klaim = terbalik."""
    n = (name or "").lower()
    if any(t in n for t in ("unemployment", "jobless", "claims", "continuing claims")):
        return -1.0
    return 1.0


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        s = str(v).strip().replace("%", "").replace(",", "")
        if not s or s in ("-", "—"):
            return None
        mult = 1.0
        if s.upper().endswith("K"): mult, s = 1e3, s[:-1]
        elif s.upper().endswith("M"): mult, s = 1e6, s[:-1]
        elif s.upper().endswith("B"): mult, s = 1e9, s[:-1]
        return float(s) * mult
    except (ValueError, TypeError):
        return None


def _name_sim(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def enrich_actuals_fmp(events: list[dict], api_key: str | None = None) -> dict:
    """Isi `actual` (DISPLAY-only) untuk event released yang actual-nya masih kosong,
    dgn mencocokkan ke kalender FMP berdasar (currency, tanggal UTC sama, nama mirip).

    TIDAK menyetel historical_std → tidak masuk skor R_hard (lihat docstring).
    Tidak pernah raise. Tanpa key / gagal / diblok = events dikembalikan apa adanya.
    """
    api_key = api_key or _get_fmp_key()
    if not api_key:
        return {"events": events, "enriched": [], "_meta": {"note": "FMP_API_KEY tidak ada (lapisan FMP nonaktif)"}}

    # Hanya proses yang perlu: released + actual kosong.
    need = [e for e in events if e.get("status") == "released" and e.get("actual") is None]
    if not need:
        return {"events": events, "enriched": [], "_meta": {"note": "tidak ada event butuh actual"}}

    now = now_utc()
    frm = (now - timedelta(days=16)).strftime("%Y-%m-%d")
    to = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        resp = requests.get(_FMP_URL, params={"from": frm, "to": to, "apikey": api_key},
                            timeout=_TIMEOUT, headers={"User-Agent": "qf_bias/1.0"})
        resp.raise_for_status()
        fmp = resp.json()
        if not isinstance(fmp, list):
            return {"events": events, "enriched": [], "_meta": {"error": f"FMP balikan tak terduga: {str(fmp)[:80]}"}}
    except Exception as exc:
        logger.warning("FMP economic_calendar gagal: %s", exc)
        return {"events": events, "enriched": [], "_meta": {"error": str(exc)}}

    # Index FMP per (currency, tanggal-UTC) → list (name, actual, estimate)
    by_key: dict[tuple, list[tuple]] = {}
    for it in fmp:
        if not isinstance(it, dict):
            continue
        ccy = _COUNTRY_TO_CCY.get(str(it.get("country", "")).strip())
        actual = _to_float(it.get("actual"))
        estimate = _to_float(it.get("estimate"))
        raw_date = str(it.get("date", ""))[:10]   # YYYY-MM-DD
        if not ccy or actual is None or not raw_date:
            continue
        by_key.setdefault((ccy, raw_date), []).append((str(it.get("event", "")), actual, estimate))

    enriched: list[str] = []
    for ev in need:
        ccy = ev.get("currency")
        try:
            d = parse_iso_utc(ev.get("ts_utc", "")).strftime("%Y-%m-%d")
        except Exception:
            continue
        cands = by_key.get((ccy, d), [])
        if not cands:
            continue
        fc = ev.get("forecast")
        # pilih kandidat dgn nama paling mirip
        best, best_sim = None, 0.0
        for fname, fact, fest in cands:
            s = _name_sim(ev.get("name", ""), fname)
            if s > best_sim:
                best, best_sim = (fname, fact, fest), s
        if not best:
            continue
        # Konfirmasi: estimate FMP ≈ forecast kalender → disambiguasi nama mirip
        # (mis. "ISM Manufacturing PMI" vs "Final Manufacturing PMI" — tanggal sama, nama mirip).
        est_ok = False
        if fc is not None and best[2] is not None:
            tol = max(abs(float(fc)) * 0.01, 0.05)
            est_ok = abs(best[2] - float(fc)) <= tol
        accept = (best_sim >= 0.85) or (best_sim >= 0.62 and est_ok)
        # Kalau ada forecast tapi estimate FMP-nya bertentangan → tolak (cegah salah-ambil).
        if fc is not None and best[2] is not None and not est_ok:
            accept = False
        if accept:
            ev["actual"] = best[1]
            ev["actual_source"] = "FMP"
            ev["surprise_polarity"] = _polarity_for(ev.get("name", ""))
            # SENGAJA tidak set historical_std → display-only, tidak masuk skor.
            enriched.append(ev.get("name", "?"))

    return {"events": events, "enriched": enriched,
            "_meta": {"matched": len(enriched), "fmp_rows": len(fmp)}}
