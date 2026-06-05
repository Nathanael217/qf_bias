"""FF surprise → poin bias per currency (faktor F) + util WIB + skor per-event.

Formula (disetujui user):
    poin_event = z × polaritas × bobot_impact × freshness
    z          = (actual − forecast) / σ_event   (σ + polaritas dari sigma_table)
    bobot_impact = high 1.0 / medium 0.5 / low 0.15
    freshness  = exp(−hari_sejak_rilis / 1.5)     (hari ini≈1, 2hr≈0.26 → "priced in")
Skor currency = clamp(Σ poin_event × SCALE, −1, +1).

Waktu FF sumber = UTC−5 (empiris: ADP 7:15am scraper = 8:15 ET; NFP 7:30am = 8:30 ET)
→ dikonversi ke WIB (UTC+7) = +12 jam. Lihat to_wib().
σ/polaritas/scale = PLACEHOLDER sampai dikalibrasi dari histori.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from engine.sigma_table import sigma_polarity

_IMPACT_W = {"high": 1.0, "medium": 0.5, "low": 0.15, "holiday": 0.0, "": 0.0}
_FF_SCALE = 0.5          # PLACEHOLDER — skala poin→[-1,1]
_Z_CLAMP = 3.0
_FRESH_TAU_DAYS = 1.5    # PLACEHOLDER — decay freshness (hari)
_SRC_OFFSET_H = -5       # TZ sumber scraper FF (empiris UTC−5)
_WIB_OFFSET_H = 7


def _parse_num(s: Any) -> float | None:
    """'4.8%'→4.8, '122K'→122000, '-8.0M'→-8e6, '1.79B'→1.79e9."""
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


def to_wib(date_str: str, time_str: str, year: int | None = None) -> datetime | None:
    """('Wed Jun 3','7:15am') dari sumber UTC−5 → datetime WIB (naive). None kalau gagal."""
    if not date_str:
        return None
    year = year or datetime.utcnow().year
    base = None
    for fmt in ("%a %b %d", "%b %d", "%a %B %d", "%A %b %d"):
        try:
            base = datetime.strptime(date_str.strip(), fmt).replace(year=year)
            break
        except ValueError:
            continue
    if base is None:
        return None
    hh, mm = 0, 0
    t = (time_str or "").strip().lower().replace(" ", "")
    if t and t not in ("allday", "tentative", "—", "-", ""):
        try:
            ampm = t[-2:]
            core = t[:-2] if ampm in ("am", "pm") else t
            parts = core.split(":")
            hh = int(parts[0])
            mm = int(parts[1]) if len(parts) > 1 else 0
            if ampm == "pm" and hh != 12:
                hh += 12
            if ampm == "am" and hh == 12:
                hh = 0
        except Exception:
            hh, mm = 0, 0
    src_dt = base.replace(hour=hh, minute=mm)
    return src_dt + timedelta(hours=_WIB_OFFSET_H - _SRC_OFFSET_H)


def _now_wib_naive(now: Any = None) -> datetime:
    if isinstance(now, datetime):
        return now.replace(tzinfo=None)
    # default: WIB sekarang
    return (datetime.utcnow() + timedelta(hours=_WIB_OFFSET_H))


def score_event(e: dict, now_wib: datetime | None = None) -> dict | None:
    """Skor satu event FF. None kalau bukan surprise terukur (belum rilis / tak ada σ / impact 0)."""
    now = _now_wib_naive(now_wib)
    ccy = (e.get("currency") or "").upper()
    actual = _parse_num(e.get("actual"))
    forecast = _parse_num(e.get("forecast"))
    if not ccy or actual is None or forecast is None:
        return None
    sigma, polarity = sigma_polarity(e.get("name", ""), ccy)
    if sigma is None or polarity is None or sigma == 0:
        return None
    impact_w = _IMPACT_W.get((e.get("impact") or "").lower(), 0.0)
    if impact_w == 0.0:
        return None
    z = max(-_Z_CLAMP, min(_Z_CLAMP, (actual - forecast) / sigma))
    wib = to_wib(e.get("date", ""), e.get("time", ""), now.year)
    if wib is not None:
        days_ago = max(0.0, (now - wib).total_seconds() / 86400.0)
    else:
        days_ago = 1.0
    fresh = math.exp(-days_ago / _FRESH_TAU_DAYS)
    pts = z * polarity * impact_w * fresh
    return {
        "ccy": ccy, "points": round(pts, 4), "z": round(z, 2),
        "freshness": round(fresh, 3), "days_ago": round(days_ago, 1),
        "impact_w": impact_w, "polarity": polarity,
    }


def compute_ff_surprise(ff_events: list[dict], now: Any = None) -> dict[str, dict]:
    """Agregasi per currency. now = datetime/date WIB (opsional). Return {CCY:{score,detail,n}}."""
    now_wib = _now_wib_naive(now)
    agg: dict[str, float] = {}
    contrib: dict[str, list] = {}
    for e in ff_events or []:
        if not isinstance(e, dict):
            continue
        sc = score_event(e, now_wib)
        if sc is None:
            continue
        ccy = sc["ccy"]
        agg[ccy] = agg.get(ccy, 0.0) + sc["points"]
        contrib.setdefault(ccy, []).append(
            f"{e.get('name','')} {sc['points']:+.2f} (z{sc['z']:+.1f}×fr{sc['freshness']:.2f})"
        )
    out: dict[str, dict] = {}
    for ccy, total in agg.items():
        out[ccy] = {
            "score": round(max(-1.0, min(1.0, total * _FF_SCALE)), 4),
            "detail": " | ".join(contrib[ccy][:4]) + (
                f" (+{len(contrib[ccy]) - 4} lain)" if len(contrib[ccy]) > 4 else ""),
            "n": len(contrib[ccy]),
        }
    return out
