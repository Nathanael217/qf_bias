# QF_BIAS — PANDUAN DEPLOY (GitHub → Streamlit Cloud)

Dashboard market bias FX/Gold/Crypto. Satu URL = semua bias + news + risk events.
Ikuti urutan ini persis. Tidak perlu terminal/coding lokal — semua via web UI.

---

## RINGKASAN: yang kamu butuhkan
1. Akun GitHub (gratis)
2. Akun Streamlit Cloud (gratis) — login pakai GitHub
3. FRED API key (gratis, 1 menit)
4. Akun Myfxbook (gratis) — untuk retail sentiment

---

## LANGKAH 1 — Ambil API key dulu (5 menit)

### 1a. FRED API key (WAJIB)
1. Buka https://fredaccount.stlouisfed.org/apikeys
2. Daftar/login (gratis).
3. Klik "Request API Key" → isi alasan singkat (mis. "personal research").
4. Salin key (string ~32 karakter). Simpan dulu di notepad.

### 1b. Myfxbook (untuk retail sentiment)
1. Daftar gratis di https://www.myfxbook.com
2. Cukup punya email + password. App akan login otomatis.
   (Tidak perlu generate token manual — cukup email & password.)

> Tanpa FRED key: bagian rates/macro kosong (R_hard lumpuh) tapi app TETAP jalan.
> Tanpa Myfxbook: retail (D) kosong tapi app TETAP jalan. Keduanya graceful.

---

## LANGKAH 2 — Upload ke GitHub (web UI, tanpa git lokal)

1. Buka https://github.com → klik **+** (kanan atas) → **New repository**.
2. Nama repo: `qf_bias` (atau bebas). Set **Private** (disarankan). Klik **Create**.
3. Di halaman repo kosong, klik **uploading an existing file**.
4. Extract `qf_bias_complete.zip` di komputermu. Di dalamnya ada folder `qf_bias/`.
   **Drag SEMUA ISI folder `qf_bias/`** (bukan foldernya, tapi isinya) ke area upload:
   - app.py, config.py, requirements.txt, .gitignore
   - folder collectors/, engine/, utils/, .streamlit/
   > GitHub web UI mempertahankan struktur folder saat drag-drop. Kalau folder tidak
   > ikut, upload per folder: klik "Add file → Upload files" lalu seret tiap folder.
5. Pastikan struktur di repo jadi begini (file di ROOT, bukan dalam subfolder qf_bias lagi):
   ```
   app.py
   config.py
   requirements.txt
   .gitignore
   collectors/  (prices.py, macro.py, cot.py, retail.py, news.py, calendar_evt.py, __init__.py)
   engine/      (scoring.py, freshness.py, pairs.py, news_overlay.py, confidence.py, __init__.py)
   utils/       (timeutils.py, cache.py, __init__.py)
   .streamlit/  (secrets.toml.example)
   ```
6. Tulis commit message ("initial qf_bias") → **Commit changes**.

> PENTING: JANGAN upload secrets.toml berisi key asli. Hanya `secrets.toml.example`
> yang boleh masuk repo. Key asli diisi di Streamlit Cloud (Langkah 4).

---

## LANGKAH 3 — Deploy di Streamlit Cloud

1. Buka https://share.streamlit.io → **Sign in with GitHub** → authorize.
2. Klik **Create app** → **Deploy a public app from a repo** (atau dari repo private).
3. Isi:
   - **Repository**: `username/qf_bias`
   - **Branch**: `main`
   - **Main file path**: `app.py`
4. Klik **Deploy**. Tunggu ~2-5 menit (install requirements).

---

## LANGKAH 4 — Isi Secrets (API keys)

1. Setelah app ter-deploy (atau saat masih building), klik menu **⋮** kanan-bawah →
   **Settings** → tab **Secrets**.
2. Paste teks ini (ganti nilai dengan punyamu):
   ```toml
   FRED_API_KEY = "key_fred_kamu"
   MYFXBOOK_EMAIL = "email_myfxbook_kamu"
   MYFXBOOK_PASSWORD = "password_myfxbook_kamu"
   ```
3. Klik **Save**. App akan reboot otomatis.

---

## LANGKAH 5 — Buka & verifikasi

1. Buka URL app (mis. `https://qf_bias-xxx.streamlit.app`). Bookmark di HP/laptop.
2. Load pertama agak lambat (~30-60 detik, normal untuk free tier cold start).
3. Cek tiap tab:
   - **Bias Board**: kartu per aset + skor + confidence + driver breakdown.
   - **Pair Scanner**: pilih base/quote → pair bias + ranking.
   - **News Feed**: headline ter-cluster (sudah dedup) + reaksi aset.
   - **Risk Events**: event akan datang + countdown WIB.
4. Lihat **status sumber** di header. Kalau ada yang "fail":
   - macro fail → cek FRED key benar.
   - retail fail → cek Myfxbook email/password; atau IP Streamlit Cloud diblok scrape (FXSSI/Dukascopy) — Myfxbook (API) biasanya tetap jalan.

---

## TROUBLESHOOTING (yang paling mungkin terjadi)

| Gejala | Penyebab | Solusi |
|--------|----------|--------|
| "ModuleNotFoundError" saat deploy | requirements.txt tidak ke-upload / salah lokasi | pastikan requirements.txt di ROOT repo |
| import error qf_bias | file dalam subfolder ganda | file harus di ROOT, bukan `qf_bias/qf_bias/...` |
| macro/rates kosong | FRED key salah/kosong | isi ulang secrets, Save, reboot |
| beberapa currency rate null | FRED series ID untuk AUD/NZD/CAD/CHF mungkin perlu ganti | buka fred.stlouisfed.org/series/{ID}; kalau 404 → cari ID benar, edit collectors/macro.py |
| COT semua "not_found" | nama kolom/URL CFTC berubah | buka FinFutWk.txt, samakan nama kolom di collectors/cot.py |
| retail FXSSI/Dukascopy fail | IP shared Streamlit diblok scrape | wajar; Myfxbook (API) jadi anchor, agreement turun otomatis |
| BTC/ETH selalu Neutral | crypto retail (Bybit L/S) belum tersambung | known limitation v1 — perlu collector Bybit L/S terpisah (nyusul) |
| app lambat tiap buka | cold start free tier | normal; load kedua instan (cache) |

---

## KETERBATASAN v1 (penting dipahami — bukan bug)

- **Semua bobot = PLACEHOLDER.** Skor belum tervalidasi backtest. Pakai sebagai
  confluence di atas TA, BUKAN sinyal arah. (Plafon jujur — lihat audit kausal.)
- **D (retail) untuk USD/currency multi-pair lemah** (proxy single-pair). XAU/BTC akurat.
- **Crypto D belum tersambung Bybit.** BTC/ETH bias hanya dari R_hard+COT.
- **News overlay keyword-based**: konservatif, sering netral. Headline ambigu di-nol-kan.
  Upgrade ke LLM = TODO (sudah ditandai di kode).
- **FRED series ID & CFTC kolom** mungkin perlu verifikasi saat pertama jalan (graceful, tidak crash).
- **cot.py belum diuji terhadap CFTC live** (ditulis sesuai spec; test saat deploy).

---

## SETELAH JALAN: mulai forward-test
Tiap pagi sebelum trading, buka URL → catat snapshot bias. Setelah beberapa minggu,
bandingkan bias vs hasil TA kamu → ini data untuk kalibrasi bobot (ganti placeholder).
Itu satu-satunya cara angka jadi bisa dipercaya.
