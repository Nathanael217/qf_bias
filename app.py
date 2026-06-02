"""
app.py — QF_BIAS Dashboard (Streamlit)
========================================
Entry point tunggal: `streamlit run app.py`

Alur (§2 Data Flow):
  1. Header — timestamp UTC+WIB, status sumber, tombol Refresh.
  2. Collectors (cached TTL) — prices, macro, cot, retail, news, calendar.
  3. Engine — compute_all_assets → confidence → compute_pairs → compute_news_delta.
  4. Display — Bias Board | Pair Scanner | News Feed | Key Risk Events | Footer.

Konvensi import (arsitektur §1.1 — root = import root, tidak ada prefix qf_bias.):
  from config import ...
  from utils.timeutils import ...
  from collectors.prices import get_prices
  dll.

Secrets: .streamlit/secrets.toml  (FRED_API_KEY, MYFXBOOK_EMAIL/PASSWORD/SESSION)
"""

from __future__ import annotations

import sys
import os

# Pastikan root repo ada di sys.path (safety net — Streamlit Cloud biasanya sudah handle ini)
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import logging
from datetime import datetime
from typing import Any

import streamlit as st

# ---------------------------------------------------------------------------
# Setup logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s: %(message)s",
)
logger = logging.getLogger("app")

# ---------------------------------------------------------------------------
# Import modul internal (setelah path setup)
# ---------------------------------------------------------------------------
try:
    from config import (
        ASSETS_ALL, ASSETS_CRYPTO, ASSETS_FX, ASSET_GOLD,
        PAIRS, PAIR_META, TTL, bias_label,
    )
    from utils.timeutils import (
        now_utc, now_wib,
        fmt_iso_utc, fmt_wib_display,
        countdown_str, minutes_until, age_minutes,
    )
    from utils.cache import clear_all_caches, ttl_cache
    from collectors.prices import get_prices as _get_prices_raw
    from collectors.macro import get_macro as _get_macro_raw, build_surprises
    from collectors.cot import get_cot as _get_cot_raw
    from collectors.retail import get_retail as _get_retail_raw
    from collectors.news import get_news as _get_news_raw
    from collectors.calendar_evt import get_calendar as _get_calendar_raw
    from engine.scoring import compute_all_assets
    from engine.news_overlay import compute_news_delta
    from engine.confidence import compute_confidence
    from engine.pairs import compute_pairs, rank_pairs
    _IMPORTS_OK = True
except Exception as _import_err:
    _IMPORTS_OK = False
    _IMPORT_ERR_MSG = str(_import_err)

# ---------------------------------------------------------------------------
# Page config (harus sebelum st.* pertama lain)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="QF_BIAS — Market Bias Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# CSS minimal
# ---------------------------------------------------------------------------
st.markdown("""
<style>
/* Bias bar container */
.bias-bar-wrap { width: 100%; background: #e5e7eb; border-radius: 4px; height: 18px; margin: 4px 0; }
.bias-bar-fill { height: 18px; border-radius: 4px; transition: width 0.3s; }

/* Badge */
.badge-ok    { background:#16a34a; color:white; padding:2px 8px; border-radius:10px; font-size:0.75rem; font-weight:600; }
.badge-fail  { background:#dc2626; color:white; padding:2px 8px; border-radius:10px; font-size:0.75rem; font-weight:600; }
.badge-warn  { background:#d97706; color:white; padding:2px 8px; border-radius:10px; font-size:0.75rem; font-weight:600; }

/* Impact badges */
.impact-high { background:#ef4444; color:white; padding:1px 7px; border-radius:8px; font-size:0.72rem; font-weight:700; }
.impact-med  { background:#f59e0b; color:white; padding:1px 7px; border-radius:8px; font-size:0.72rem; font-weight:600; }
.impact-low  { background:#6b7280; color:white; padding:1px 7px; border-radius:8px; font-size:0.72rem; font-weight:500; }

/* Bias label colors */
.label-sbull { color:#15803d; font-weight:700; }
.label-bull  { color:#16a34a; font-weight:600; }
.label-neut  { color:#6b7280; font-weight:500; }
.label-bear  { color:#dc2626; font-weight:600; }
.label-sbear { color:#991b1b; font-weight:700; }

/* Asset card */
.asset-card { border:1px solid #e5e7eb; border-radius:8px; padding:12px; margin-bottom:6px; background:#fafafa; }

/* Direction sign */
.dir-plus { color:#16a34a; font-weight:700; }
.dir-minus { color:#dc2626; font-weight:700; }
.dir-zero  { color:#9ca3af; }

/* Footer */
.footer-note { color:#6b7280; font-size:0.78rem; padding:12px; border-top:1px solid #e5e7eb; margin-top:24px; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Guard — jika import gagal
# ---------------------------------------------------------------------------
if not _IMPORTS_OK:
    st.error(f"❌ Import gagal: `{_IMPORT_ERR_MSG}`")
    st.info("Pastikan kamu menjalankan dari root direktori repo dan semua dependency terinstall.")
    st.stop()

# ===========================================================================
# CACHED COLLECTORS  (TTL dari config)
# ===========================================================================

@st.cache_data(ttl=TTL["prices"], show_spinner=False)
def cached_get_prices() -> dict:
    try:
        return _get_prices_raw()
    except Exception as exc:
        logger.error("get_prices() exception: %s", exc)
        return {"as_of_utc": fmt_iso_utc(now_utc()), "prices": {}, "_error": str(exc)}


@st.cache_data(ttl=TTL["macro"], show_spinner=False)
def cached_get_macro(calendar_events_json: str) -> dict:
    """calendar_events_json: json-encoded list agar st.cache_data bisa hash."""
    import json
    try:
        cal_events = json.loads(calendar_events_json) if calendar_events_json else []
        return _get_macro_raw(calendar_events=cal_events)
    except Exception as exc:
        logger.error("get_macro() exception: %s", exc)
        return {
            "as_of_utc": fmt_iso_utc(now_utc()),
            "rates": {}, "rate_diff": {}, "surprises": {},
            "_meta": {"sources_ok": [], "sources_failed": [f"macro error: {exc}"]},
            "_error": str(exc),
        }


@st.cache_data(ttl=TTL["cot"], show_spinner=False)
def cached_get_cot() -> dict:
    try:
        return _get_cot_raw()
    except Exception as exc:
        logger.error("get_cot() exception: %s", exc)
        return {
            "as_of_tuesday": "N/A", "released": "N/A", "days_since_snapshot": 99,
            "cot": {},
            "_meta": {"source": "CFTC TFF", "weeks_history": 0,
                      "assets_ok": [], "assets_missing": [], "stale": True},
            "_error": str(exc),
        }


@st.cache_data(ttl=TTL["retail"], show_spinner=False)
def cached_get_retail() -> dict:
    try:
        return _get_retail_raw()
    except Exception as exc:
        logger.error("get_retail() exception: %s", exc)
        return {
            "as_of_utc": fmt_iso_utc(now_utc()),
            "sources_ok": [], "sources_failed": ["all"], "retail": {},
            "_error": str(exc),
        }


@st.cache_data(ttl=TTL["news"], show_spinner=False)
def cached_get_news() -> dict:
    try:
        return _get_news_raw()
    except Exception as exc:
        logger.error("get_news() exception: %s", exc)
        return {
            "as_of_utc": fmt_iso_utc(now_utc()),
            "headlines": [], "_error": str(exc),
        }


@st.cache_data(ttl=TTL["calendar"], show_spinner=False)
def cached_get_calendar() -> dict:
    try:
        return _get_calendar_raw()
    except Exception as exc:
        logger.error("get_calendar() exception: %s", exc)
        return {
            "as_of_utc": fmt_iso_utc(now_utc()),
            "events": [], "_error": str(exc),
        }


@st.cache_data(ttl=TTL["news_overlay"], show_spinner=False)
def cached_compute_news_delta(headlines_json: str) -> tuple[dict, list]:
    """Cache news_overlay (proses mahal). Terima json string utk hashability."""
    import json
    try:
        headlines = json.loads(headlines_json) if headlines_json else []
        return compute_news_delta(headlines)
    except Exception as exc:
        logger.error("compute_news_delta() exception: %s", exc)
        return {}, []


# ===========================================================================
# HELPERS DISPLAY
# ===========================================================================

def _bias_color(score: float) -> str:
    """Return hex color untuk skor bias."""
    if score >= 25:
        return "#16a34a"   # hijau
    elif score <= -25:
        return "#dc2626"   # merah
    return "#6b7280"       # abu netral


def _bias_bar_html(score: float, width_px: int = 180) -> str:
    """Render bias bar HTML dari -100 ke +100."""
    pct_abs = min(abs(score), 100) / 100.0
    color = _bias_color(score)
    bar_pct = pct_abs * 50   # 50% = separuh bar (dari tengah)
    if score >= 0:
        left = 50
        bar_width = bar_pct
    else:
        bar_width = bar_pct
        left = 50 - bar_pct
    return (
        f'<div style="position:relative;width:{width_px}px;height:14px;'
        f'background:#e5e7eb;border-radius:4px;">'
        f'<div style="position:absolute;left:50%;top:0;width:1px;height:14px;background:#9ca3af;"></div>'
        f'<div style="position:absolute;left:{left:.1f}%;width:{bar_width:.1f}%;height:14px;'
        f'background:{color};border-radius:3px;"></div>'
        f'</div>'
    )


def _label_html(label: str) -> str:
    css_map = {
        "Strong Bullish": "label-sbull",
        "Bullish": "label-bull",
        "Neutral": "label-neut",
        "Bearish": "label-bear",
        "Strong Bearish": "label-sbear",
    }
    css = css_map.get(label, "label-neut")
    return f'<span class="{css}">{label}</span>'


def _impact_badge(impact: str) -> str:
    cls = {"HIGH": "impact-high", "MED": "impact-med", "LOW": "impact-low"}.get(impact, "impact-low")
    return f'<span class="{cls}">{impact}</span>'


def _dir_html(sign: str) -> str:
    if sign == "+":
        return '<span class="dir-plus">▲</span>'
    elif sign == "-":
        return '<span class="dir-minus">▼</span>'
    return '<span class="dir-zero">—</span>'


def _conf_bar(conf: float) -> str:
    """Confidence bar kecil sebagai pct string."""
    pct = round(conf * 100)
    col = "#15803d" if pct >= 60 else "#d97706" if pct >= 35 else "#dc2626"
    return (
        f'<span style="font-size:0.75rem;color:{col};font-weight:600;">'
        f'{pct}%</span>'
    )


# ===========================================================================
# SECTION 1 — HEADER
# ===========================================================================

def render_header(sources_status: dict[str, Any]) -> None:
    """Render judul, timestamp, status sumber, tombol Refresh."""
    ts_utc = now_utc()
    ts_wib = now_wib()

    col_title, col_ts, col_btn = st.columns([3, 4, 1.2])

    with col_title:
        st.markdown("## 📊 QF_BIAS Dashboard")

    with col_ts:
        st.markdown(
            f"**UTC:** `{fmt_iso_utc(ts_utc)}`  \n"
            f"**WIB:** `{fmt_wib_display(ts_wib)} WIB`",
        )

    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 Refresh", help="Bersihkan cache & muat ulang semua data"):
            clear_all_caches()
            st.rerun()

    # --- Status sumber ---
    if sources_status:
        badge_parts = []
        for src, status in sources_status.items():
            if status == "ok":
                badge_parts.append(f'<span class="badge-ok">✓ {src}</span>')
            elif status == "warn":
                badge_parts.append(f'<span class="badge-warn">⚠ {src}</span>')
            else:
                badge_parts.append(f'<span class="badge-fail">✗ {src}</span>')
        st.markdown(
            "<div style='display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px;'>"
            + " ".join(badge_parts)
            + "</div>",
            unsafe_allow_html=True,
        )

    st.divider()


# ===========================================================================
# SECTION 4a — BIAS BOARD
# ===========================================================================

def render_bias_board(
    asset_data: dict[str, dict],
    news_delta: dict[str, float],
    show_overlay: bool,
) -> None:
    """Render grid kartu per aset dengan bar skor, label, confidence, driver breakdown."""

    st.subheader("📈 Bias Board")

    if not asset_data:
        st.warning("Data bias aset kosong — periksa collectors.")
        return

    # Kelompokkan: FX, XAU, Crypto
    groups = [
        ("FX Majors", ASSETS_FX),
        ("Commodities", [ASSET_GOLD]),
        ("Crypto", ASSETS_CRYPTO),
    ]

    for group_name, group_assets in groups:
        st.markdown(f"**{group_name}**")
        cols = st.columns(len(group_assets))

        for col, asset in zip(cols, group_assets):
            data = asset_data.get(asset)
            if data is None:
                col.warning(f"{asset}: no data")
                continue

            baseline = data.get("bias_baseline", 0.0)
            delta = news_delta.get(asset, 0.0)

            if show_overlay:
                score = max(-100.0, min(100.0, baseline + delta))
                label_suffix = " ★"  # indikasi overlay aktif
            else:
                score = baseline
                label_suffix = ""

            label = bias_label(score)
            conf = data.get("confidence", 0.0) or 0.0
            drivers = data.get("drivers", {})

            with col:
                with st.container():
                    # Header kartu
                    st.markdown(
                        f"<div style='font-weight:700;font-size:1.05rem;'>{asset}</div>",
                        unsafe_allow_html=True,
                    )

                    # Skor
                    sign = "+" if score >= 0 else ""
                    st.markdown(
                        f"<div style='font-size:1.4rem;font-weight:700;"
                        f"color:{_bias_color(score)};'>{sign}{score:.1f}</div>",
                        unsafe_allow_html=True,
                    )

                    # Bar
                    st.markdown(_bias_bar_html(score, width_px=140), unsafe_allow_html=True)

                    # Label + confidence
                    st.markdown(
                        f"{_label_html(label + label_suffix)} &nbsp; {_conf_bar(conf)}",
                        unsafe_allow_html=True,
                    )

                    # News delta (kalau aktif & ada)
                    if show_overlay and abs(delta) > 0.1:
                        delta_str = f"+{delta:.1f}" if delta >= 0 else f"{delta:.1f}"
                        col_nd = "#16a34a" if delta >= 0 else "#dc2626"
                        st.markdown(
                            f"<div style='font-size:0.72rem;color:{col_nd};'>Δnews: {delta_str}</div>",
                            unsafe_allow_html=True,
                        )

                    # Driver breakdown (expander)
                    if drivers:
                        with st.expander("📋 Drivers", expanded=False):
                            for factor, fdata in drivers.items():
                                fscore = fdata.get("score", 0.0)
                                fweight = fdata.get("weight", 0.0)
                                fdetail = fdata.get("detail", "–")
                                fcol = _bias_color(fscore * 100)
                                st.markdown(
                                    f"<div style='font-size:0.78rem;margin-bottom:4px;'>"
                                    f"<span style='font-weight:600;'>{factor}</span> "
                                    f"<span style='color:{fcol};font-weight:700;'>{fscore:+.3f}</span> "
                                    f"<span style='color:#6b7280;'>(w={fweight:.2f})</span><br>"
                                    f"<span style='color:#374151;'>{fdetail}</span>"
                                    f"</div>",
                                    unsafe_allow_html=True,
                                )
                    else:
                        st.caption("–")

        st.markdown("<br>", unsafe_allow_html=True)


# ===========================================================================
# SECTION 4b — PAIR SCANNER
# ===========================================================================

def render_pair_scanner(
    pair_data: dict[str, dict],
    asset_data: dict[str, dict],
    show_overlay: bool,
    news_delta: dict[str, float],
) -> None:
    """Render Pair Scanner: selectbox base/quote + tabel ranking pair terkuat."""

    st.subheader("🔍 Pair Scanner")

    if not pair_data:
        st.warning("Data pair kosong — periksa engine/scoring.")
        return

    all_assets = list(ASSETS_FX) + [ASSET_GOLD] + list(ASSETS_CRYPTO)
    col_b, col_q = st.columns(2)
    with col_b:
        sel_base = st.selectbox("Base Currency", options=all_assets, index=0, key="ps_base")
    with col_q:
        sel_quote = st.selectbox("Quote Currency", options=all_assets, index=1, key="ps_quote")

    # Panel info dirender SETELAH kedua selectbox terbaca (hindari stale value).
    info_box = st.container()
    with info_box:
        if sel_base == sel_quote:
            st.warning("Base dan quote tidak boleh sama.")
        else:
            # Cari pair symbol yang cocok
            pair_sym = f"{sel_base}{sel_quote}"
            pair_rev = f"{sel_quote}{sel_base}"

            pair_found = pair_data.get(pair_sym) or pair_data.get(pair_rev)
            actual_sym = pair_sym if pair_data.get(pair_sym) else pair_rev

            if pair_found:
                p = pair_found
                score = p.get("bias_score", 0.0)

                # Kalau overlay aktif, recalculate pair bias langsung
                if show_overlay:
                    base = p.get("base", sel_base)
                    quote = p.get("quote", sel_quote)
                    base_score = (asset_data.get(base, {}).get("bias_baseline", 0.0)
                                  + news_delta.get(base, 0.0))
                    quote_score = (asset_data.get(quote, {}).get("bias_baseline", 0.0)
                                   + news_delta.get(quote, 0.0))
                    score = max(-100.0, min(100.0, base_score - quote_score))

                label = bias_label(score)
                conf = p.get("confidence", None)
                note = p.get("note", "")

                sign_str = "+" if score >= 0 else ""
                st.markdown(
                    f"<div style='font-size:1.1rem;font-weight:700;'>{actual_sym}</div>"
                    f"<div style='font-size:2rem;font-weight:700;color:{_bias_color(score)};'>"
                    f"{sign_str}{score:.1f}</div>"
                    f"{_label_html(label)}&nbsp;",
                    unsafe_allow_html=True,
                )

                if conf is not None:
                    st.markdown(f"**Confidence:** {_conf_bar(conf)}", unsafe_allow_html=True)

                st.markdown(_bias_bar_html(score, width_px=220), unsafe_allow_html=True)

                if note:
                    st.caption(f"_{note}_")
            else:
                # Hitung manual dari asset bias
                base_data = asset_data.get(sel_base)
                quote_data = asset_data.get(sel_quote)
                if base_data is not None and quote_data is not None:
                    base_b = base_data.get("bias_baseline", 0.0)
                    quote_b = quote_data.get("bias_baseline", 0.0)
                    if show_overlay:
                        base_b += news_delta.get(sel_base, 0.0)
                        quote_b += news_delta.get(sel_quote, 0.0)
                    score = max(-100.0, min(100.0, base_b - quote_b))
                    label = bias_label(score)
                    sign_str = "+" if score >= 0 else ""
                    st.markdown(
                        f"<div style='font-size:1.1rem;font-weight:700;'>"
                        f"{sel_base}/{sel_quote} <span style='font-size:0.75rem;color:#9ca3af;'>(custom)</span></div>"
                        f"<div style='font-size:2rem;font-weight:700;color:{_bias_color(score)};'>"
                        f"{sign_str}{score:.1f}</div>"
                        f"{_label_html(label)}",
                        unsafe_allow_html=True,
                    )
                    st.markdown(_bias_bar_html(score, width_px=220), unsafe_allow_html=True)
                    st.caption(f"Dihitung manual: {sel_base}({base_b:.1f}) − {sel_quote}({quote_b:.1f})")
                else:
                    st.info(f"Pair {pair_sym} tidak ada di data terkomputasi.")

    # --- Ranking tabel pair terkuat ---
    st.markdown("**📊 Ranking Pair (sort |bias|)**")
    try:
        # Kalau overlay, rebuild pair dengan overlay score
        if show_overlay:
            overlay_asset_map: dict[str, dict] = {}
            for asset, adata in asset_data.items():
                baseline = adata.get("bias_baseline", 0.0)
                overlaid = max(-100.0, min(100.0, baseline + news_delta.get(asset, 0.0)))
                overlay_asset_map[asset] = {**adata, "bias_baseline": overlaid}
            ranking_pairs = compute_pairs(overlay_asset_map)
        else:
            ranking_pairs = pair_data

        ranked = rank_pairs(ranking_pairs, top_n=len(PAIRS))

        if ranked:
            tbl_data = []
            for r in ranked:
                score_r = r.get("bias_score", 0.0)
                conf_r = r.get("confidence")
                conf_str = f"{conf_r*100:.0f}%" if conf_r is not None else "–"
                tbl_data.append({
                    "Pair": r["pair"],
                    "Score": f"{'+' if score_r>=0 else ''}{score_r:.1f}",
                    "Label": r.get("label", bias_label(score_r)),
                    "Conf": conf_str,
                    "Kalkulasi": r.get("note", ""),
                })
            import pandas as pd
            df = pd.DataFrame(tbl_data)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("Tidak ada pair yang bisa di-rank.")
    except Exception as exc:
        st.warning(f"Ranking pair gagal: {exc}")


# ===========================================================================
# SECTION 4c — NEWS FEED
# ===========================================================================

def render_news_feed(news_clusters: list[dict]) -> None:
    """Render news clusters (sudah dedup) dari engine/news_overlay."""

    st.subheader("📰 News Feed (Sudah Keluar)")

    if not news_clusters:
        st.info("Tidak ada news cluster saat ini — feed kosong atau semua event sudah decay.")
        return

    # Filter: tampilkan semua cluster, sort by age (terbaru dulu)
    sorted_clusters = sorted(news_clusters, key=lambda c: c.get("age_min", 9999))

    for cluster in sorted_clusters:
        event_title = cluster.get("event", "–")
        n_hl = cluster.get("n_headlines", 1)
        age = cluster.get("age_min", 0.0)
        direction = cluster.get("direction", {})
        mag = cluster.get("magnitude", 0.0)

        # Format umur
        if age < 1:
            age_str = "baru saja"
        elif age < 60:
            age_str = f"{int(age)}m lalu"
        else:
            h = int(age // 60)
            m = int(age % 60)
            age_str = f"{h}j {m}m lalu" if m else f"{h}j lalu"

        # Reaksi aset yang non-zero
        reactions = {a: v for a, v in direction.items() if v != "0"}

        link = cluster.get("link", "")

        with st.container():
            col_event, col_meta, col_react, col_act = st.columns([3.6, 1.4, 2.2, 1.4])

            with col_event:
                # Judul + link "Buka" kalau ada
                if link:
                    st.markdown(
                        f"<div style='font-weight:600;font-size:0.9rem;'>{event_title} "
                        f"<a href='{link}' target='_blank' style='font-size:0.72rem;color:#60a5fa;text-decoration:none;'>🔗 buka</a></div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"<div style='font-weight:600;font-size:0.9rem;'>{event_title}</div>",
                        unsafe_allow_html=True,
                    )

            with col_meta:
                st.markdown(
                    f"<div style='font-size:0.78rem;color:#6b7280;'>"
                    f"📰 {n_hl} hl &nbsp;|&nbsp; ⏱ {age_str}<br>"
                    f"mag: {mag:.2f}</div>",
                    unsafe_allow_html=True,
                )

            with col_react:
                if reactions:
                    react_parts = []
                    for asset, sign in sorted(reactions.items()):
                        react_parts.append(f"{asset}{_dir_html(sign)}")
                    st.markdown(
                        "<div style='font-size:0.82rem;display:flex;gap:6px;flex-wrap:wrap;'>"
                        + " ".join(react_parts)
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        "<span style='color:#9ca3af;font-size:0.78rem;'>–</span>",
                        unsafe_allow_html=True,
                    )

            with col_act:
                # Placeholder tombol Groq AI — aktif di sesi integrasi Groq berikutnya.
                # Saat ini menampilkan tombol disabled + caption, supaya slot UI sudah siap.
                st.button(
                    "🤖 Groq context",
                    key=f"groq_{abs(hash(event_title))%10**8}",
                    disabled=True,
                    help="Analisis konteks AI (Groq) — diaktifkan di update berikutnya. "
                         "Akan menilai: kekuatan currency saat ini, dampak news ke trader, "
                         "potensi sentimen, lalu memberi weighting terukur (engine yang hitung).",
                    use_container_width=True,
                )

            st.markdown(
                "<hr style='border:none;border-top:1px solid #1f2937;margin:4px 0;'>",
                unsafe_allow_html=True,
            )


# ===========================================================================
# SECTION 4d — KEY RISK EVENTS
# ===========================================================================

def render_key_risk_events(calendar_data: dict) -> None:
    """Render risk events: filter impact + currency, upcoming + released (dgn aktual)."""

    st.subheader("⏰ Key Risk Events")

    events = calendar_data.get("events", [])
    if calendar_data.get("_error") and not events:
        st.warning(f"Calendar fetch gagal: {calendar_data['_error']}")
        return
    if not events:
        st.info("Tidak ada event dalam window.")
        return

    # ---- FILTER BAR ----
    fcol1, fcol2, fcol3 = st.columns([2, 2, 1.5])
    with fcol1:
        impact_filter = st.multiselect(
            "Filter Impact", options=["HIGH", "MED", "LOW"],
            default=["HIGH", "MED"], key="re_impact",
        )
    with fcol2:
        # Currency yang muncul di event
        avail_ccy = sorted({e.get("currency", "?") for e in events if e.get("currency")})
        ccy_filter = st.multiselect(
            "Filter Currency/Pair", options=avail_ccy, default=[],
            key="re_ccy", help="Kosong = semua. Pilih currency utk fokus pair tertentu.",
        )
    with fcol3:
        show_released = st.toggle("Tampilkan yg sudah lewat", value=False, key="re_released")

    def _match(ev: dict) -> bool:
        if impact_filter and ev.get("impact", "LOW") not in impact_filter:
            return False
        if ccy_filter and ev.get("currency") not in ccy_filter:
            return False
        return True

    filtered = [e for e in events if _match(e)]
    upcoming = sorted([e for e in filtered if e.get("status") == "upcoming"],
                      key=lambda e: e.get("ts_utc", ""))
    released = sorted([e for e in filtered if e.get("status") == "released"],
                      key=lambda e: e.get("ts_utc", ""), reverse=True)

    def _render_row(ev: dict, is_released: bool) -> None:
        try:
            ts_wib_str = ev.get("ts_wib", "")
            currency = ev.get("currency", "–")
            impact = ev.get("impact", "LOW")
            name = ev.get("name", "–")
            forecast = ev.get("forecast")
            previous = ev.get("previous")
            actual = ev.get("actual")
            ts_utc_str = ev.get("ts_utc", "")

            if is_released:
                cd_color = "#6b7280"
                countdown = "selesai"
            else:
                mins = minutes_until(ts_utc_str) if ts_utc_str else None
                countdown = countdown_str(ts_utc_str) if ts_utc_str else "–"
                if mins is not None and mins <= 15:
                    cd_color = "#ef4444"
                elif mins is not None and mins <= 60:
                    cd_color = "#d97706"
                else:
                    cd_color = "#6b7280"

            col_time, col_impact, col_ccy, col_name, col_a, col_f, col_p = st.columns([1.6, 0.9, 0.7, 2.4, 1.1, 1.1, 1.1])
            with col_time:
                st.markdown(
                    f"<div style='font-weight:700;color:{cd_color};font-size:0.85rem;'>{countdown}</div>"
                    f"<div style='font-size:0.72rem;color:#9ca3af;'>{ts_wib_str} WIB</div>",
                    unsafe_allow_html=True)
            with col_impact:
                st.markdown(_impact_badge(impact), unsafe_allow_html=True)
            with col_ccy:
                st.markdown(f"<span style='font-weight:700;font-size:0.85rem;'>{currency}</span>",
                            unsafe_allow_html=True)
            with col_name:
                st.markdown(f"<div style='font-size:0.87rem;font-weight:600;'>{name}</div>",
                            unsafe_allow_html=True)

            # --- Helper: render satu angka besar berlabel ---
            def _stat(label: str, val, color: str = "#e5e7eb"):
                shown = val if val is not None else "–"
                return (f"<div style='text-align:center;'>"
                        f"<div style='font-size:0.62rem;color:#6b7280;text-transform:uppercase;letter-spacing:0.04em;'>{label}</div>"
                        f"<div style='font-size:1.15rem;font-weight:800;color:{color};line-height:1.2;'>{shown}</div>"
                        f"</div>")

            # ACTUAL — warna hijau/merah vs forecast (hanya kalau sudah rilis)
            a_color = "#9ca3af"
            if actual is not None and forecast is not None:
                try:
                    a_color = "#16a34a" if float(actual) >= float(forecast) else "#ef4444"
                except (TypeError, ValueError):
                    a_color = "#e5e7eb"
            elif actual is not None:
                a_color = "#e5e7eb"
            with col_a:
                st.markdown(_stat("Actual", actual, a_color), unsafe_allow_html=True)
            with col_f:
                st.markdown(_stat("Forecast", forecast, "#93c5fd"), unsafe_allow_html=True)
            with col_p:
                st.markdown(_stat("Previous", previous, "#9ca3af"), unsafe_allow_html=True)

            st.markdown("<hr style='border:none;border-top:1px solid #1f2937;margin:6px 0;'>",
                        unsafe_allow_html=True)
        except Exception as exc:
            st.caption(f"⚠ Gagal render event: {exc}")

    # ---- UPCOMING ----
    st.markdown(f"**🔜 Akan Datang ({len(upcoming)})**")
    if upcoming:
        for ev in upcoming:
            _render_row(ev, is_released=False)
    else:
        st.info("Tidak ada upcoming event sesuai filter.")

    # ---- RELEASED (opsional) ----
    if show_released:
        st.markdown(f"**✅ Sudah Lewat ({len(released)}) — dengan hasil aktual**")
        if released:
            for ev in released:
                _render_row(ev, is_released=True)
        else:
            st.info("Tidak ada event lewat sesuai filter.")


def render_score_detail(
    asset_bias_map: dict[str, dict],
    news_delta: dict[str, float],
) -> None:
    """Tab Detail Skor: breakdown lengkap perhitungan bias satu currency terpilih."""

    st.subheader("🔬 Detail Perhitungan Skor")

    if not asset_bias_map:
        st.warning("Data skor kosong — periksa engine.")
        return

    assets = list(asset_bias_map.keys())
    sel = st.selectbox("Pilih mata uang / aset", options=assets, index=0, key="detail_asset")
    data = asset_bias_map.get(sel, {})
    drivers = data.get("drivers", {})
    baseline = data.get("bias_baseline", 0.0)
    nd = news_delta.get(sel, 0.0)
    overlaid = max(-100.0, min(100.0, baseline + nd))
    conf = data.get("confidence")
    freshness = data.get("freshness_cot")
    active = data.get("active_factors", [])

    # --- Ringkasan atas: angka besar ---
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            f"<div style='font-size:0.7rem;color:#6b7280;text-transform:uppercase;'>Baseline</div>"
            f"<div style='font-size:2rem;font-weight:800;color:{_bias_color(baseline)};'>"
            f"{'+' if baseline>=0 else ''}{baseline:.1f}</div>"
            f"{_label_html(bias_label(baseline))}",
            unsafe_allow_html=True)
    with c2:
        st.markdown(
            f"<div style='font-size:0.7rem;color:#6b7280;text-transform:uppercase;'>+ News Δ</div>"
            f"<div style='font-size:2rem;font-weight:800;color:{_bias_color(nd)};'>"
            f"{'+' if nd>=0 else ''}{nd:.1f}</div>"
            f"<div style='font-size:0.72rem;color:#9ca3af;'>cap ±30</div>",
            unsafe_allow_html=True)
    with c3:
        st.markdown(
            f"<div style='font-size:0.7rem;color:#6b7280;text-transform:uppercase;'>= Skor Final</div>"
            f"<div style='font-size:2rem;font-weight:800;color:{_bias_color(overlaid)};'>"
            f"{'+' if overlaid>=0 else ''}{overlaid:.1f}</div>"
            f"{_label_html(bias_label(overlaid))}",
            unsafe_allow_html=True)

    if conf is not None:
        st.markdown(f"**Confidence:** {_conf_bar(conf)} &nbsp; "
                    f"<span style='font-size:0.78rem;color:#9ca3af;'>(kesepakatan faktor aktif: "
                    f"{', '.join(active) if active else 'tidak ada'})</span>",
                    unsafe_allow_html=True)

    st.markdown("<hr style='border:none;border-top:1px solid #1f2937;margin:10px 0;'>", unsafe_allow_html=True)

    # --- Breakdown per faktor: score × weight efektif = kontribusi ---
    st.markdown("**Kontribusi per Faktor** &nbsp; <span style='font-size:0.75rem;color:#9ca3af;'>"
                "(baseline = Σ score×weight ÷ Σ weight aktif, lalu ×100)</span>", unsafe_allow_html=True)

    rows = []
    factor_names = {"R_hard": "R_hard (makro: rate diff + surprise)",
                    "C": "C (COT positioning)",
                    "D": "D (retail sentiment, kontrarian)"}
    for fkey in ["R_hard", "C", "D"]:
        info = drivers.get(fkey, {})
        score = info.get("score", 0.0)
        w_eff = info.get("weight", 0.0)
        w_nom = info.get("weight_nominal", w_eff)
        detail = info.get("detail", "")
        contrib = score * w_eff
        is_active = abs(score) > 1e-9 and w_eff > 0
        rows.append({
            "Faktor": factor_names.get(fkey, fkey),
            "Score": f"{score:+.3f}",
            "Weight efektif": f"{w_eff:.3f}" + (f" (nom {w_nom:.2f})" if abs(w_eff-w_nom)>1e-6 else ""),
            "Kontribusi": f"{contrib:+.3f}",
            "Status": "✅ aktif" if is_active else "⚪ gate/0",
            "Penjelasan": detail,
        })
    import pandas as pd
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # --- Catatan freshness COT kalau relevan ---
    if freshness is not None and "C" in drivers:
        cnom = drivers["C"].get("weight_nominal", 0.25)
        ceff = drivers["C"].get("weight", 0.0)
        st.caption(
            f"❄️ **Freshness COT = {freshness:.3f}** → bobot C disesuaikan: "
            f"{cnom:.2f} × {freshness:.3f} = {ceff:.3f}. "
            f"(COT makin lama sejak snapshot Selasa → bobotnya makin kecil, bukan skornya.)"
        )

    # --- Rumus eksplisit ---
    with st.expander("📐 Rumus & cara baca"):
        _rumus_lines = [
            "- **Tiap faktor** menghasilkan *score* ∈ [−1, +1] (lihat kolom Penjelasan untuk asal angkanya).",
            "- **Weight efektif**: R_hard & D pakai bobot nominal; **C dikali freshness COT**.",
            "- **Kontribusi** = score × weight efektif.",
            "- **Baseline** = (Σ kontribusi faktor aktif) ÷ (Σ weight faktor aktif) × 100. Renormalisasi ini bikin faktor yang ter-*gate* (score 0) tidak menyeret hasil.",
            "- **Skor Final** = clamp(Baseline + News Δ, −100, +100).",
            "- **Confidence** = seberapa sepakat arah antar faktor aktif (bukan klaim akurasi).",
            "",
            "⚠️ Semua bobot = **placeholder** sampai backtest. Ini alat confluence, bukan sinyal arah.",
        ]
        st.markdown("\n".join(_rumus_lines))
        st.info("🤖 Penjelasan naratif via Groq AI akan ditambahkan di update berikutnya — "
                "akan menerjemahkan breakdown ini ke bahasa biasa + konteks kekuatan currency saat itu. "
                "(Groq mengukur/menjelaskan; angka tetap dari engine deterministik.)")


# ===========================================================================
# SECTION 4e — FOOTER
# ===========================================================================

def render_footer() -> None:
    st.markdown(
        "<div class='footer-note'>"
        "⚠️ <b>Alat confluence, bukan sinyal arah. TA tetap primary. "
        "Bobot belum tervalidasi (placeholder).</b><br>"
        "Confidence = kesepakatan faktor, bukan klaim akurasi arah. "
        "Semua bobot = placeholder sampai forward-test & backtest selesai. "
        "Indices = ditunda v2."
        "</div>",
        unsafe_allow_html=True,
    )


# ===========================================================================
# FUNGSI UTAMA — DATA PIPELINE + LAYOUT
# ===========================================================================

def build_sources_status(
    prices: dict,
    macro: dict,
    cot: dict,
    retail: dict,
    news: dict,
    calendar: dict,
) -> dict[str, str]:
    """Bangun dict status per sumber: 'ok' | 'warn' | 'fail'."""
    status: dict[str, str] = {}

    # Prices
    px = prices.get("prices", {})
    ok_count = sum(1 for v in px.values() if v.get("last") is not None)
    if prices.get("_error"):
        status["prices"] = "fail"
    elif ok_count < len(px) * 0.5:
        status["prices"] = "warn"
    else:
        status["prices"] = "ok"

    # Macro / FRED
    macro_meta = macro.get("_meta", {})
    macro_failed = macro_meta.get("sources_failed", [])
    if macro.get("_error") or not macro.get("rates"):
        status["FRED"] = "fail"
    elif macro_failed:
        status["FRED"] = "warn"
    else:
        status["FRED"] = "ok"

    # COT
    cot_meta = cot.get("_meta", {})
    if cot.get("_error") or cot_meta.get("stale"):
        status["COT"] = "warn"  # warn, bukan fail — freshness handle
    elif not cot.get("cot"):
        status["COT"] = "fail"
    else:
        status["COT"] = "ok"

    # Retail
    retail_ok = retail.get("sources_ok", [])
    retail_fail = retail.get("sources_failed", [])
    if retail.get("_error") or (not retail_ok and retail_fail):
        status["retail"] = "fail"
    elif retail_fail:
        status["retail"] = "warn"
    else:
        status["retail"] = "ok"

    # News RSS
    if news.get("_error") or not news.get("headlines"):
        status["news"] = "fail" if news.get("_error") else "warn"
    else:
        status["news"] = "ok"

    # Calendar
    if calendar.get("_error") and not calendar.get("events"):
        status["calendar"] = "fail"
    elif calendar.get("_error"):
        status["calendar"] = "warn"
    else:
        status["calendar"] = "ok"

    return status


def main() -> None:
    """Entry point Streamlit — satu rerun = satu siklus data pipeline + display."""

    # -----------------------------------------------------------------------
    # STEP 1 — Placeholder header (timestamp dulu, sumber diisi nanti)
    # -----------------------------------------------------------------------
    header_placeholder = st.empty()

    # -----------------------------------------------------------------------
    # STEP 2 — Fetch collectors (semua cached TTL)
    # -----------------------------------------------------------------------
    with st.spinner("Memuat data pasar…"):
        prices_data = cached_get_prices()

        # Calendar dulu — diperlukan oleh macro untuk surprises
        calendar_data = cached_get_calendar()

        # Released events → surprises untuk macro
        import json
        released_events = [
            e for e in calendar_data.get("events", [])
            if e.get("status") == "released"
               and e.get("actual") is not None
        ]
        cal_json = json.dumps(released_events)
        macro_data = cached_get_macro(cal_json)

        cot_data = cached_get_cot()
        retail_data = cached_get_retail()
        news_data = cached_get_news()

    # -----------------------------------------------------------------------
    # STEP 3 — Engine
    # -----------------------------------------------------------------------
    with st.spinner("Menghitung bias…"):
        # 3a. Compute all assets (baseline)
        try:
            asset_bias_map = compute_all_assets(
                macro=macro_data,
                cot=cot_data,
                retail=retail_data,
                prices=prices_data,
            )
        except Exception as exc:
            st.error(f"compute_all_assets() gagal: {exc}")
            asset_bias_map = {}

        # 3b. Compute confidence per aset
        for asset, adata in asset_bias_map.items():
            drivers = adata.get("drivers", {})
            # Retail agreement untuk faktor D
            retail_agreement_val: float | None = None
            try:
                _ASSET_TO_PAIR_LOOKUP = {
                    "EUR": "EURUSD", "GBP": "GBPUSD", "JPY": "USDJPY",
                    "AUD": "AUDUSD", "NZD": "NZDUSD", "CAD": "USDCAD",
                    "CHF": "USDCHF", "USD": "EURUSD",
                    "XAU": "XAUUSD", "BTC": "BTCUSD", "ETH": "ETHUSD",
                }
                pair_key = _ASSET_TO_PAIR_LOOKUP.get(asset)
                if pair_key:
                    pair_retail = retail_data.get("retail", {}).get(pair_key, {})
                    retail_agreement_val = pair_retail.get("agreement")
            except Exception:
                pass

            try:
                conf = compute_confidence(
                    driver_dict=drivers,
                    retail_agreement=retail_agreement_val,
                )
            except Exception as exc:
                logger.warning("compute_confidence[%s] gagal: %s", asset, exc)
                conf = 0.0
            adata["confidence"] = conf

        # 3c. Compute pairs (baseline)
        try:
            pair_bias_map = compute_pairs(asset_bias_map)
        except Exception as exc:
            st.error(f"compute_pairs() gagal: {exc}")
            pair_bias_map = {}

        # 3d. News delta (cached — mahal)
        try:
            headlines = news_data.get("headlines", [])
            headlines_json = json.dumps(headlines)
            news_delta_map, news_clusters = cached_compute_news_delta(headlines_json)
        except Exception as exc:
            logger.error("compute_news_delta gagal: %s", exc)
            news_delta_map = {}
            news_clusters = []

    # -----------------------------------------------------------------------
    # STEP 4 — Header (dengan status sumber lengkap)
    # -----------------------------------------------------------------------
    sources_status = build_sources_status(
        prices_data, macro_data, cot_data,
        retail_data, news_data, calendar_data,
    )

    with header_placeholder.container():
        render_header(sources_status)

    # -----------------------------------------------------------------------
    # STEP 5 — Badges sumber gagal (non-fatal)
    # -----------------------------------------------------------------------
    failed_sources = [s for s, st_val in sources_status.items() if st_val == "fail"]
    if failed_sources:
        st.warning(
            f"⚠️ Sumber gagal: **{', '.join(failed_sources)}** — "
            "data mungkin tidak lengkap. Engine tetap berjalan dengan data yang ada.",
            icon="⚠️",
        )

    # -----------------------------------------------------------------------
    # STEP 6 — Toggle Baseline vs News-Overlaid
    # -----------------------------------------------------------------------
    toggle_col, _ = st.columns([2, 5])
    with toggle_col:
        show_overlay = st.toggle(
            "★ News Overlay aktif",
            value=False,
            help="Tampilkan bias_score = baseline + news_delta (cap ±30). "
                 "Nonaktif = baseline murni dari R_hard / COT / Retail.",
        )

    # -----------------------------------------------------------------------
    # STEP 7 — Tabs display
    # -----------------------------------------------------------------------
    tab_board, tab_pairs, tab_detail, tab_news, tab_events = st.tabs([
        "📈 Bias Board",
        "🔍 Pair Scanner",
        "🔬 Detail Skor",
        "📰 News Feed",
        "⏰ Risk Events",
    ])

    with tab_board:
        try:
            render_bias_board(asset_bias_map, news_delta_map, show_overlay)
        except Exception as exc:
            st.error(f"Bias Board error: {exc}")

    with tab_pairs:
        try:
            render_pair_scanner(pair_bias_map, asset_bias_map, show_overlay, news_delta_map)
        except Exception as exc:
            st.error(f"Pair Scanner error: {exc}")

    with tab_detail:
        try:
            render_score_detail(asset_bias_map, news_delta_map)
        except Exception as exc:
            st.error(f"Detail Skor error: {exc}")

    with tab_news:
        try:
            render_news_feed(news_clusters)
        except Exception as exc:
            st.error(f"News Feed error: {exc}")

    with tab_events:
        try:
            render_key_risk_events(calendar_data)
        except Exception as exc:
            st.error(f"Key Risk Events error: {exc}")

    # -----------------------------------------------------------------------
    # STEP 8 — Footer
    # -----------------------------------------------------------------------
    render_footer()


# ===========================================================================
# ENTRY
# ===========================================================================
if __name__ == "__main__":
    main()
