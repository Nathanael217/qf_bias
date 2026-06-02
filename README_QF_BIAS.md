# QF_BIAS — Build Files (Sesi 1–5, reviewed & fixed)

## STATUS FILE
| File | Status | Catatan |
|------|--------|---------|
| config.py | ✅ unchanged | lolos review S1 |
| requirements.txt | 🔧 FIXED | +yfinance, +numpy (dipakai prices.py, tadinya hilang) |
| utils/timeutils.py | 🔧 FIXED | gabungan superset 3 versi konflik; pakai zoneinfo (bukan pytz) |
| utils/cache.py | ✅ unchanged | lolos review S1 |
| collectors/prices.py | ✅ unchanged | lolos review S2 |
| collectors/macro.py | ✅ unchanged | ⚠ verifikasi FRED series ID saat test (RBATCTR/RBNZOCR/BOCR/SARON mencurigakan) |
| collectors/cot.py | 🆕 DITULIS CLAUDE | Sonnet melewatkannya total. Ditulis ulang sesuai kontrak §4: TFF report, kategori per-aset, COT Index percentile 156-mgg, graceful. Helper self-test pass + terverifikasi integrasi dgn scoring. ⚠ get_cot() butuh test network CFTC saat deploy (URL/kolom bisa berubah). |
| collectors/retail.py | ✅ unchanged | lolos review S3; crypto L/S Bybit belum tersambung (handle di S6) |
| collectors/news.py | ✅ unchanged | lolos review S3 |
| collectors/calendar_evt.py | ✅ unchanged | lolos review S3 |
| engine/scoring.py | 🔧 FIXED | bug double-count freshness → freshness sekarang ke BOBOT C (w_C×freshness) |
| engine/freshness.py | ✅ unchanged | lolos review S4 |
| engine/pairs.py | ✅ unchanged | lolos review S4 |
| engine/news_overlay.py | 🔧 FIXED | dedup guard arah berlawanan + word-boundary keyword + canonical parse_iso_utc |
| engine/confidence.py | ✅ unchanged | lolos review S5 |

## SETUP MANUAL (sebelum deploy)
1. cot.py SUDAH ADA (ditulis Claude). Test get_cot() saat deploy — verifikasi URL CFTC & nama kolom TFF masih cocok.
2. Streamlit secrets (.streamlit/secrets.toml): FRED_API_KEY, MYFXBOOK_EMAIL+MYFXBOOK_PASSWORD (atau MYFXBOOK_SESSION).
3. Belum ada: app.py (Sesi 6).

## KNOWN LIMITATIONS v1 (placeholder sampai backtest)
- Semua WEIGHTS, threshold, τ, SCALE_FACTOR = PLACEHOLDER.
- D (retail) untuk USD/multi-pair currency lemah (proxy single-pair).
- Crypto D belum tersambung Bybit L/S → handle di app.py.
- News overlay keyword-based: konservatif (sering netral), upgrade ke LLM = TODO.
- FRED series ID sebagian perlu diverifikasi saat test.
