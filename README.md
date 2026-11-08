# eyai_signature — Pembersih Tanda Watermark / AI

Aplikasi web lokal untuk membersihkan **karakter watermark AI** dari teks atau
file yang kamu miliki:

- **Layer A (deterministik)** — menghapus karakter tak terlihat: zero-width,
  spasi eksotis, penanda arah (bidi), tag chars, homoglyph (opsional).
- **Deep scrub (lapisan 2)** — sapuan kedua atas karakter yang sengaja
  dipertahankan Layer A: bidi mark/embedding, tag chars (U+E0001, U+E0020–E007F),
  private use, noncharacter, tatweel, dan karakter tak terpakai. Hasilnya
  diverifikasi ulang sampai **nol residu**.
- **Tanda khas AI (app-side)** — menghapus tanda tipografis yang terlihat dan
  khas tulisan AI: tanda pisah panjang (`—`, `–`), mengubah kutip keriting
  (`" " ' '`) jadi lurus, serta membuang emoji & emoticon.
- **Netralkan frasa AI (opsional)** — menulis ulang frasa klise keluaran model
  (`in order to` → `to`, `utilize` → `use`, dash sebagai jeda klausa → koma).
  Level menengah juga membuang kata penegas (`very`, `really`).

Alurnya **manual**: tempel teks / unggah file → bersihkan → salin / unduh hasil.
Tidak menyentuh transkrip chat asli, dan tidak menghapus watermark statistik
(token-sampling / Layer B).

---

## Prasyarat

- **Python 3.10+** (dites di 3.13/3.14). Tanpa dependensi pihak ketiga.
- Repo upstream **watermarks-remover** tersedia di `./watermarks-remover`
  (sudah disertakan). Bisa diarahkan lain lewat env `WATERMARKS_REPO`.
- (Opsional, untuk hasil file tertentu lebih maksimal) `exiftool`, `qpdf`,
  `c2patool` di PATH.

---

## Struktur

```
eyai_signature/
├── CARA_JALANKAN.md       # panduan menjalankan (mulai dari sini)
├── API_CONTRACT.md        # kontrak request/response (frozen)
├── verify.sh              # uji end-to-end (32 pemeriksaan)
├── README.md              # file ini
├── app/
│   ├── server.py          # backend stdlib + UI statis
│   ├── start.sh           # launcher praktis
│   ├── README.md          # catatan detail app
│   └── static/            # index.html, styles.css, app.js
└── watermarks-remover/    # repo upstream (jangan diubah)
```

> Baru pertama kali? Baca **[CARA_JALANKAN.md](./CARA_JALANKAN.md)**.

---

## Menjalankan

```bash
cd app
./start.sh
# atau:
python3 server.py

# ganti host/port:
python3 server.py --host 127.0.0.1 --port 8770
```

Lalu buka **http://127.0.0.1:8770** di browser.

- Host/port bisa lewat env: `HOST`, `PORT`.
- Lokasi repo upstream: env `WATERMARKS_REPO` (default `../watermarks-remover`).

---

## Cara pakai

### Tab "Teks"
1. Tempel teks dari web chat (Ctrl/Cmd+V) ke kotak besar.
2. (Opsional) atur opsi di panel **Opsi pembersihan**.
3. Klik **Bersihkan**.
4. Lihat hasil: teks bersih, ringkasan Sebelum/Sesudah, dan tanda yang ditemukan.
   Gunakan **Salin** atau **Unduh .txt**.

### Tab "File"
1. Seret file ke dropzone atau klik untuk memilih (png, jpg, pdf, docx, xlsx,
   pptx, epub, odt, html, md, mp4, wav, mp3, dll).
2. Klik **Bersihkan**.
3. Lihat laporan (jenis file, berubah/tidak, apa yang ditemukan).
4. Klik **Unduh hasil** untuk file yang sudah dibersihkan (mis. `foto.cleaned.png`).

Tombol **Periksa penanda** menjalankan pemeriksaan saja (tanpa mengubah apa pun).

---

## Opsi pembersihan

Berlaku untuk kedua tab.

| Opsi | Default | Arti |
|---|---|---|
| **Buang tanda khas AI** | aktif | Hapus tanda pisah panjang (`—`, `–`, `―`, `−`), ubah kutip keriting (`" " ' '`) jadi lurus, buang emoji & emoticon. Strip keyboard `-` **tidak** disentuh. |
| **Netralkan frasa AI** | Konservatif | Tulis ulang frasa klise AI. `Mati` / `Konservatif` / `Menengah`. Lihat bagian di bawah. |
| Deep scrub | aktif | Sapuan kedua: bidi, tag chars, private use, noncharacter, tatweel, karakter tak terpakai. |
| Verifikasi residu | aktif | Pindai ulang hasil, laporkan bila masih ada karakter tak terlihat yang lolos. |
| Rapikan spasi samar | aktif | Ganti spasi Unicode tak lazim (mis. non-breaking space) ke spasi biasa. |
| Normalisasi NFKC | nonaktif | Normalisasi Unicode NFKC setelah pembersihan. |
| Buang perekat emoji | nonaktif | Agresif: juga buang invisible "load-bearing" (perekat emoji, joiner, tag). |
| Buang penanda arah teks | nonaktif | Juga buang tanda arah RTL/LTR yang sah. |
| Homoglyph agresif | nonaktif | Petakan huruf Kiril/fullwidth Latin yang menyerupai Latin ke ASCII. |

Perilaku **"Buang tanda khas AI"**:
- Dash dihapus tanpa pengganti. Antar kata dijaga **satu spasi** supaya tidak
  menempel: `well—known` → `well known`, `A — B` → `A B`.
- Kutip keriting → kutip lurus: `"Halo"` → `"Halo"`, `it's` → `it's`.
- Emoji (termasuk rangkaian ZWJ/skin-tone) dan emoticon ASCII (`:)`, `:D`, `;-)`,
  `<3`, `xD`, `^_^`, …) dibuang; spasi ganda sisa dirapikan.
- Berlaku untuk **teks paste** dan **berkas teks** (.txt/.md). Berkas biner
  (docx/pdf/gambar/av) tidak diubah isinya.
- Bisa dimatikan: `"strip_ai_tells": false` (via UI atau API).

Perilaku **"Netralkan frasa AI"** (`deai_style`, 0/1/2):

| Level | Yang diubah |
|---|---|
| `0` Mati | Tidak ada. |
| `1` Konservatif | Frasa template (`in order to` → `to`, `due to the fact that` → `because`, `utilize` → `use`, `delve into` → `explore`, …), dash jeda klausa → koma, `--` prosa → koma. |
| `2` Menengah | Level 1 **plus** buang kata penegas (`very`, `really`, `actually`, …) dan kata ulang (`the the`). |

Yang **tidak** dilakukan level 1: mengubah susunan kalimat, memecah kalimat
panjang, mengubah istilah teknis. Rentang angka (`2019–2020`, termasuk yang
berspasi `2019 – 2020`) dan flag CLI (`--json`) tidak disentuh.

Perilaku **"Deep scrub"** (`deep_scrub`): Layer A sengaja mempertahankan beberapa
karakter "load-bearing" (perekat emoji, joiner aksara, bidi yang sah). Sapuan ini
membuang sisanya yang tidak punya fungsi sah di teks biasa:

| Grup | Contoh |
|---|---|
| `bidi` | U+061C, U+200E, U+200F, U+202A–202E, U+2066–2069 |
| `tags` | U+E0001, U+E0020–E007F (pembawa steganografi) |
| `invisible` | U+00AD, U+200B, U+2060–2064, U+FEFF, U+180E, U+034F, … |
| `private` | U+E000–U+F8FF, plane 15/16 |
| `noncharacter` | U+FDD0–FDEF, semua `x…FFFE`/`x…FFFF` |
| `tatweel` | U+0640 |

U+200C/U+200D/U+FE0E/U+FE0F **tidak** dibuang di sini (itu perekat sah; atur
lewat opsi "Buang perekat emoji").

---

## Verifikasi

```bash
cd /home/zet/Projects/eyai_signature
./verify.sh
```

Menjalankan server sementara di port `8770` (env `PORT`) dan mengecek 32 hal:
health, UI + asset, inspect/clean teks, hapus U+200B/U+00AD, clean file PNG,
format tak dikenal → HTTP 422, pembersihan tanda AI (dash/kutip/emoji), opsi
opt-out, hyphen keyboard aman, deep scrub (bidi/tag/private/noncharacter),
perlindungan ZWJ aksara sah, ketiga level `deai_style`, rentang angka & flag CLI
yang tidak boleh diubah, koersi opsi rusak, dan `node --check app.js`.

---

## HTTP API (ringkas)

- `GET  /api/health`
- `POST /api/inspect` — `{ "text": "..." }` atau `{ "filename": "...", "file_base64": "..." }`
- `POST /api/clean`   — bentuk sama, mengembalikan `cleaned` (teks) atau
  `cleaned_base64` (file)

Opsi dikirim sebagai objek `options`. Detail lengkap + bentuk respons:
[`API_CONTRACT.md`](./API_CONTRACT.md).

---

## Batasan

- Hanya membersihkan teks/berkas di aplikasi ini; tidak menyentuh transkrip chat asli.
- Teks yang hanya ada di jendela chat asli tidak bisa dibersihkan otomatis —
  itulah mengapa alurnya manual (salin → bersihkan → pakai).
- **Tidak ada "kripto" yang dibobol di sini.** Watermark teks AI bukan tanda
  tangan kriptografis yang bisa diubah kuncinya; ada dua jenis dan hanya satu
  yang bisa dibasmi lokal:
  - **Penyisipan karakter Unicode** (Layer A + deep scrub) — bisa dihapus total
    dan dibuktikan nol residu. Ini yang dikerjakan alat ini.
  - **Watermark statistik / token-sampling** (Layer B) — sinyalnya tersebar di
    *pilihan kata* saat model menulis. Tidak ada signature untuk dihapus; satu-
    satunya cara menyerangnya adalah menulis ulang teks secara substansial
    dengan model lain. Alat ini **tidak** melakukan itu, jadi tidak bisa
    menjamin teks lolos detektor vendor.
- Yang bisa dilakukan tanpa model: menghapus pembawa Unicode, dan merapikan
  **gaya** yang khas AI (frasa template, dash, kata penegas). Gaya memang salah
  satu sinyal deteksi praktis, tetapi bukan watermark statistik itu sendiri.
- Metadata file (C2PA/XMP/EXIF) dihapus untuk format yang didukung upstream.
- Format tak dikenal ditolak (HTTP 422), bukan ditebak.
- Batas masukan: 25 MiB per permintaan; subprocess timeout 60 detik.

---

## Troubleshooting

- **Port sudah dipakai** (`Address already in use`): jalankan dengan port lain,
  mis. `python3 app/server.py --port 8771`, atau hentikan proses lama:
  `pkill -f app/server.py`.
- **Ganti lokasi repo upstream**: `WATERMARKS_REPO=/path/ke/watermarks-remover python3 server.py`.
- **Berkas tidak berubah**: mungkin memang tidak ada penanda; coba aktifkan opsi
  agresif (NFKC / perekat emoji / bidi / homoglyph).
