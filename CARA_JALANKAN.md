# Cara Menjalankan — eyai_signature

Panduan singkat untuk menjalankan aplikasi pembersih tanda AI ini di komputer
sendiri. Semua berjalan **lokal**, tidak ada data yang dikirim ke internet.

---

## Ringkasan cepat

```bash
cd /home/zet/Projects/eyai_signature/app
./start.sh
```

Lalu buka **http://127.0.0.1:8770** di browser.

Hentikan dengan `Ctrl+C` di terminal.

---

## Prasyarat

| Kebutuhan | Keterangan |
|---|---|
| Python 3.10+ | Mesin ini sudah punya **3.14.7**. Cek: `python3 --version` |
| Repo upstream | Sudah ada di `./watermarks-remover` (submodule). Cek: `ls watermarks-remover/service/scripts/clean_file.py` |
| Browser | Chrome/Firefox/Edge versi apa pun |

**Tidak perlu** `pip install`, `npm install`, atau build apa pun. Semua memakai
pustaka standar Python.

Opsional (hasil file lebih maksimal): `exiftool`, `qpdf`, `c2patool` di `PATH`.
Tanpa itu file tetap diproses sebisanya.

---

## Menjalankan

### Cara 1 — skrip bawaan (paling mudah)

```bash
cd /home/zet/Projects/eyai_signature/app
./start.sh
```

### Cara 2 — langsung dengan Python

```bash
cd /home/zet/Projects/eyai_signature/app
python3 server.py
```

### Cara 3 — ganti host / port

```bash
python3 server.py --host 127.0.0.1 --port 8771
```

Atau lewat environment variable:

```bash
HOST=0.0.0.0 PORT=8771 python3 server.py
```

> `--host 0.0.0.0` membuat aplikasi bisa diakses dari perangkat lain di jaringan
> yang sama. Hati-hati: tidak ada autentikasi, siapa pun di jaringan itu bisa
> memakainya.

### Cara 4 — jalankan di latar belakang

```bash
cd /home/zet/Projects/eyai_signature/app
nohup python3 server.py > /tmp/eyai.log 2>&1 &
PID=$!
echo "$PID" > /tmp/eyai.pid
```

> `$!` harus ditangkap **langsung** setelah perintah `&`, dalam shell yang sama.
> Kalau ditulis `... & echo $! > file`, yang tersimpan bisa PID subshell, bukan
> Python — dan `kill` akan gagal.

Cek berjalan atau tidak:

```bash
curl -s http://127.0.0.1:8770/api/health
```

Hentikan:

```bash
kill "$(cat /tmp/eyai.pid)"
```

---

## Memakai aplikasinya

### Tab **Teks**

1. Tempel teks hasil AI ke kotak besar (Ctrl/Cmd+V).
2. Atur opsi di panel kanan (lihat tabel di bawah).
3. Klik **Bersihkan**.
4. Lihat hasil, lalu **Salin** atau **Unduh .txt**.

### Tab **File**

1. Seret file ke kotak, atau klik untuk memilih.
   Format: png, jpg, pdf, docx, xlsx, pptx, epub, odt, html, md, mp4, wav, mp3.
2. Klik **Bersihkan**.
3. Klik **Unduh hasil** (mis. `foto.cleaned.png`).

Tombol **Periksa penanda** hanya memeriksa, tidak mengubah apa pun.

---

## Arti tiap opsi

| Opsi | Default | Yang dilakukan |
|---|---|---|
| **Buang tanda khas AI** | aktif | Hapus dash panjang (`—`, `–`), luruskan kutip keriting, buang emoji & emoticon. Strip `-` biasa tidak disentuh. |
| **Netralkan frasa AI** | Konservatif | Tulis ulang frasa klise. Lihat tabel level di bawah. |
| **Deep scrub** | aktif | Sapuan kedua: bidi, tag chars, private use, noncharacter, tatweel. |
| **Verifikasi residu** | aktif | Pindai ulang hasil dan laporkan sisa karakter tak terlihat. |
| Rapikan spasi samar | aktif | Spasi Unicode tak lazim → spasi biasa. |
| Normalisasi NFKC | nonaktif | Bentuk Unicode standar (mis. `ﬁ` → `fi`). |
| Buang perekat emoji | nonaktif | Agresif: buang juga perekat emoji/joiner. |
| Buang penanda arah teks | nonaktif | Buang tanda arah RTL/LTR. |
| Homoglyph agresif | nonaktif | Huruf Kiril/fullwidth mirip Latin → ASCII. |

### Level "Netralkan frasa AI"

| Level | Yang diubah | Contoh |
|---|---|---|
| **Mati** | Tidak ada. Hanya karakter yang dibersihkan. | `in order to utilize` → tetap |
| **Konservatif** | Frasa template + dash jeda → koma | `in order to utilize` → `to use` |
| **Menengah** | Level 1 + buang kata penegas | `very good` → `good` |

Contoh nyata:

```
SEBELUM: It is important to note that we utilize a robust framework — which is
         very effective — in order to delve into the matter.
LEVEL 1: Note that we use a robust framework, which is very effective, to explore the matter.
LEVEL 2: Note that we use a robust framework, which is effective, to explore the matter.
```

Yang **tidak** diubah: rentang angka (`2019–2020`, `2019 – 2020`) dan flag CLI
(`--json`).

---

## Memakai lewat terminal (tanpa browser)

### Cek layanan

```bash
curl -s http://127.0.0.1:8770/api/health
```

### Bersihkan teks

```bash
curl -s -X POST http://127.0.0.1:8770/api/clean \
  -H 'Content-Type: application/json' \
  -d '{"text":"In order to utilize this — very good — tool.","options":{"deai_style":1}}'
```

### Bersihkan dengan level menengah

```bash
curl -s -X POST http://127.0.0.1:8770/api/clean \
  -H 'Content-Type: application/json' \
  -d '{"text":"in order to utilize very good stuff","options":{"deai_style":2}}'
```

### Hanya memeriksa (tidak mengubah)

```bash
curl -s -X POST http://127.0.0.1:8770/api/inspect \
  -H 'Content-Type: application/json' \
  -d '{"text":"Halo\u200bDunia"}'
```

### Bersihkan file

```bash
curl -s -X POST http://127.0.0.1:8770/api/clean \
  -H 'Content-Type: application/json' \
  -d "{\"filename\":\"catatan.txt\",\"file_base64\":\"$(base64 -w0 catatan.txt)\"}"
```

Hasil ada di kolom `cleaned` (teks) atau `cleaned_base64` (file).

---

## Menjalankan tes

```bash
cd /home/zet/Projects/eyai_signature
./verify.sh
```

Menjalankan server sementara di port 8770 dan mengecek **32 hal**. Hasil yang
benar diakhiri:

```
RESULT: 32 passed, 0 failed
```

Kalau port 8770 sedang dipakai, jalankan di port lain:

```bash
PORT=8775 ./verify.sh
```

---

## Troubleshooting

### `Address already in use` / port 8770 sudah dipakai

Pakai port lain:

```bash
python3 server.py --port 8771
```

Atau hentikan proses lama:

```bash
for p in $(ss -lptn 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u); do
  cmd=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null)
  case "$cmd" in *server.py*) kill $p;; esac
done
```

> Jangan pakai `pkill -f server.py` — polanya bisa cocok dengan shell Anda
> sendiri dan ikut terbunuh.

### `command not found: ./start.sh`

File belum executable, atau Anda tidak di folder yang benar:

```bash
cd /home/zet/Projects/eyai_signature/app
chmod +x start.sh
./start.sh
```

### Halaman terbuka tapi tulis "Layanan tidak terjangkau"

Server belum jalan atau port-nya beda. Cek:

```bash
curl -s http://127.0.0.1:8770/api/health
```

Kalau kosong, jalankan ulang `./start.sh`.

### Health bilang `"repo": false`

Repo upstream tidak ditemukan. Cek:

```bash
ls watermarks-remover/service/scripts/clean_file.py
```

Kalau tidak ada, ambil submodule-nya:

```bash
cd /home/zet/Projects/eyai_signature
git submodule update --init --recursive
```

Atau arahkan manual:

```bash
WATERMARKS_REPO=/path/ke/watermarks-remover python3 server.py
```

### File tidak berubah

Mungkin memang tidak ada penanda. Coba aktifkan opsi agresif (NFKC / perekat
emoji / bidi / homoglyph), atau pastikan file berekstensi yang didukung.

### Format ditolak (HTTP 422)

Format file tidak dikenali. Kalau isinya sebenarnya teks, pakai tab **Teks**.

---

## Batasan yang perlu dipahami

- **Tidak ada "kripto" yang dibobol.** Watermark teks AI bukan tanda tangan
  kriptografis. Ada dua jenis:
  1. **Penyisipan karakter Unicode** — dihapus total dan dibuktikan nol residu
     oleh alat ini.
  2. **Watermark statistik (token-sampling)** — sinyalnya tersebar di *pilihan
     kata*. Tidak ada signature untuk dihapus; hanya bisa diserang dengan
     menulis ulang teks memakai model lain. Alat ini **tidak** melakukan itu.
- Jadi alat ini bisa menjamin **teks bersih dari karakter tersembunyi** dan
  **gaya khas AI dinetralkan**, tetapi **tidak bisa menjamin lolos detektor
  vendor**.
- Watermark statistik tidak dihapus.
- Batas masukan: 25 MiB per permintaan; subprocess timeout 60 detik.
- Aplikasi ini tidak menyentuh transkrip chat asli.

---

## Struktur singkat

```
eyai_signature/
├── CARA_JALANKAN.md       # file ini
├── README.md              # gambaran proyek
├── API_CONTRACT.md        # bentuk request/response API
├── verify.sh              # 32 pemeriksaan end-to-end
├── app/
│   ├── server.py          # backend (stdlib saja)
│   ├── start.sh           # launcher
│   └── static/            # index.html, styles.css, app.js
└── watermarks-remover/    # repo upstream (jangan diubah)
```
