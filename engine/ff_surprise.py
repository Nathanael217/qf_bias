"""FF surprise → poin bias per currency (faktor F).

Formula (disetujui user):
    poin_event = z × polaritas × bobot_impact × freshness
    z          = (actual − forecast) / σ_event        (σ dari sigma_table)
    polaritas  = +1/−1 (actual lebih tinggi = bullish/bearish currency)
    bobot_impact = high 1.0 / medium 0.5 / low 0.15
    freshness  = exp(−hari_sejak_rilis / 1.5)          (hari ini≈1, 2hr≈0.26)
Skor currency = clamp(Σ poin_event × SCALE, −1, +1).

Magnitudo selalu deterministik (hitung dari angka). Sumber hanya MENGUKUR.
σ + polaritas = PLACEHOLDER (sigma_table) sampai dikalibrasi dari histori.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from engine.sigma_table import sigma_polarity

_IMPACT_W = {"high": 1.0, "medium": 0.5, "low": 0.15, "holiday": 0.0, "": 0.0}
_FF_SCALE = 0.5          # PLACEHOLDER — skala poin→[-1,1]
_Z_CLAMP = 3.0
_FRESH_TAU_DAYS = 1.5    # PLACEHOLDER — decay freshness


def _parse_num(s: Any) -> float | None:
    """'4.8%'→4.8, '122K'→122000, '-8.0M'→-8e6, '1.79B'→1.79e9, '54.5'→54.5."""
    if s is None:
        return None
    txt = str(s).strip().replace(",", "").replace("%", "")
    if not txt or txt in ("-", "—"):
        return None
    mult = 1.0
    if txt[-1:].upper() in ("K", "M", "B", "T"):
        mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[txt[-1].upper()]
        txt = txt[:-1]
    try:
        return float(txt) * mult
    except ValueError:
        return None


def _days_ago(date_str: str, today: date) -> float | None:
    """'Wed Jun 3' → jumlah hari lalu vs today (tahun = tahun berjalan)."""
    if not date_str:
        return None
    for fmt in ("%a %b %d", "%b %d", "%a %B %d"):
        try:
            d = datetime.strptime(date_str.strip(), fmt).replace(year=today.year).date()
            return (today - d).days
        except ValueError:
            continue
    return None


def compute_ff_surprise(ff_events: list[dict], today: date | None = None) -> dict[str, dict]:
    """ff_events = output parse_a1/ parse_ff_calendar (punya name/currency/impact/actual/forecast/date).

    Return: {CCY: {"score": float[-1,1], "detail": str, "n": int, "events": [...]}}.
    """
    today = today or datetime.utcnow().date()
    agg: dict[str, float] = {}
    contrib: dict[str, list] = {}

    for e in ff_events or []:
        if not isinstance(e, dict):
            continue
        ccy = (e.get("currency") or "").upper()
        if not ccy:
            continue
        actual = _parse_num(e.get("actual"))
        forecast = _parse_num(e.get("forecast"))
        if actual is None or forecast is None:
            continue  # belum rilis / tak ada forecast → bukan surprise
        name = e.get("name", "")
        sigma, polarity = sigma_polarity(name, ccy)
        if sigma is None or polarity is None or sigma == 0:
            continue  # tak ada rule σ/polaritas → lewati (jangan tebak)
        z = max(-_Z_CLAMP, min(_Z_CLAMP, (actual - forecast) / sigma))
        impact_w = _IMPACT_W.get((e.get("impact") or "").lower(), 0.0)
        if impact_w == 0.0:
            continue
        dago = _days_ago(e.get("date", ""), today)
        fresh = math.exp(-max(0.0, dago) / _FF_TAU()) if dago is not None else 0.3
        pts = z * polarity * impact_w * fresh
        agg[ccy] = agg.get(ccy, 0.0) + pts
        contrib.setdefault(ccy, []).append(
            f"{name} z={z:+.1f}×imp{impact_w:.2f}×fr{fresh:.2f}={pts:+.2f}"
        )

    out: dict[str, dict] = {}
    for ccy, total in agg.items():
        score = max(-1.0, min(1.0, total * _FF_SCALE))
        out[ccy] = {
            "score": round(score, 4),
            "detail": " | ".join(contrib[ccy][:4]) + (f" (+{len(contrib[ccy])-4} lain)"
                                                       if len(contrib[ccy]) > 4 else ""),
            "n": len(contrib[ccy]),
        }
    return out


def _FF_TAU() -> float:
    return _FRESH_TAU_DAYS
