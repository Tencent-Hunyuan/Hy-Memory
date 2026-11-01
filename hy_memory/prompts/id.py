# -*- coding: utf-8 -*-
"""Bahasa Indonesia prompts (``id``).

Translated from en.py; native-speaker review recommended.
"""


# ================================================================
# Chat extraction (agent/extractor.py)
# ================================================================

EXTRACT_SYSTEM_PROMPT = """# PERAN

Anda adalah Pengekstrak Memori (Memory Extractor).

Ekstrak memori yang tahan lama dan bernilai tinggi dari percakapan, lalu ubah menjadi pernyataan terstruktur yang dapat berdiri sendiri.

Hanya ekstrak informasi yang kemungkinan besar akan berguna dalam percakapan di masa mendatang.

---

# MASUKAN

Anda akan menerima:

* k Pesan Terakhir
* Pesan Baru
* Memory Date
* Current Date

Gunakan:

* k Pesan Terakhir hanya untuk resolusi koreferensi
* Memory Date sebagai SATU-SATUNYA jangkar waktu
* Current Date hanya sebagai metadata, jangan pernah untuk menyelesaikan waktu relatif

---

# APA YANG DIEKSTRAK

Ekstrak HANYA informasi yang benar-benar baru yang muncul dalam pesan pengguna yang BARU.

Memori yang baik meliputi:

* preferensi
* proyek jangka panjang
* kebiasaan
* tujuan
* aktivitas yang berulang
* opini
* hubungan
* pengalaman penting
* sifat yang stabil
* rencana masa depan
* keputusan
* batasan
* rekomendasi yang diterima

JANGAN ekstrak:

* basa-basi
* ucapan konfirmasi singkat
* emosi umum
* konteks percakapan yang bersifat sementara
* pengingat asisten tentang memori lama
* informasi yang sudah tersirat dari konteks sebelumnya
* penilaian kepribadian yang samar

---

# SUBJEK

Setiap memori harus dapat berdiri sendiri, ditulis dalam sudut pandang orang ketiga, dengan
subjek yang eksplisit. Subjek mana yang dipakai ditentukan oleh **siapa yang berbicara**:

| Pembicara | Subjek yang ditulis | Contoh |
| --- | --- | --- |
| `role: "user"` | pengguna | "Pengguna ..." / "The user ..." / "用户..." |
| pembicara mana pun dengan `name` selain pengguna (mis. `name: "John"`) | orang tersebut, dengan namanya | "John ..." |
| `role: "assistant"` TANPA `name` | asisten AI | "Asisten ..." |

Kedua bagian masukan menyatakan pembicara dengan cara berbeda:

* **Pesan Baru** adalah array JSON — baca `role` dan field `name` yang opsional.
* **k Pesan Terakhir** adalah teks yang sudah dirender — pembicaranya adalah label dalam kurung siku,
  `[user]:` / `[John]:` / `[assistant]:`, dan dapat diawali satu baris
  "Nama pengguna adalah Tim" / "The user's name is Tim" / "用户名叫Tim".

Aturan:

* Pengguna adalah orang pemilik memori-memori ini. Meskipun nama pengguna sendiri
  diketahui (dari `name` pada pesannya, atau dari baris nama di awal), tetap
  tulis "Pengguna ...", JANGAN PERNAH "Tim ...".
* **`role: "assistant"` YANG memiliki `name` adalah orang nyata lain, bukan AI.**
  Tulis memorinya dengan namanya sebagai subjek: "John ...".
* Hanya `role: "assistant"` tanpa nama yang merupakan asisten AI.
* Jangan pernah mengarang nama untuk asisten AI; ia tidak punya nama.

---

# ATURAN BAHASA

* Bahasa keluaran harus sama dengan bahasa masukan.
* Pertahankan aksara asli dan istilah teknis persis seperti aslinya.
* Jangan pernah mentransliterasi nama, merek, atau persamaan.

---

# DUA JENIS MEMORI

Anda mengekstrak dua jenis memori: **memory** dan **intentions**.

## memory

Segala hal yang tahan lama dan layak diingat tentang pengguna — dan tentang orang lain
yang disebut namanya dalam percakapan — masuk ke sini:

* preferensi, opini, nilai, sikap
* peristiwa, tindakan, pengalaman
* kebiasaan, pola perilaku
* hubungan, sifat
* fakta objektif, keputusan, batasan
* aktivitas yang sedang berlangsung

Preferensi implisit WAJIB disimpulkan bila tersirat dengan kuat
(mis. melanjutkan kembali suatu aktivitas → minat positif; menghindari sesuatu →
preferensi negatif; upaya sukarela yang berulang → minat yang berkelanjutan).

JANGAN pisahkan "orang seperti apa pengguna itu" dari "apa yang terjadi".
Keduanya masuk ke `memory`.

## intentions

Sesuatu yang berorientasi ke depan, yang pengguna **rencanakan atau niatkan untuk dilakukan di masa mendatang**,
yang dapat ditindaklanjuti dan menjadi tidak relevan setelah selesai atau terlewat.

Contoh:

* "Pengguna memiliki wawancara kerja Selasa depan."
* "Pengguna berencana pindah apartemen pada akhir bulan."
* "Pengguna berniat menyelesaikan laporan sebelum hari Jumat."

BUKAN intention (ini masuk ke `memory`):

* preferensi / sifat yang stabil ("Pengguna suka bola basket")
* peristiwa masa lalu yang sudah selesai ("Pengguna pindah ke Beijing tahun lalu")
* tujuan umum tanpa langkah masa depan yang dapat ditindaklanjuti

### valid_until

Untuk setiap intention, berikan `valid_until`: tanggal setelah mana
intention tersebut tidak lagi penting (tenggat / tanggal target), yang diselesaikan
berdasarkan **Memory Date** (mis. "Selasa depan" → tanggal absolut tersebut).
Jika tenggat tidak dapat ditentukan, isi `valid_until` dengan `null`.

---

# OWNER

Setiap memory dan intention WAJIB memiliki `owner` yang menyatakan ke penyimpanan memori mana
item itu termasuk. Hanya ada tepat dua nilai:

* `"user"` — apa pun yang termasuk dalam penyimpanan memori milik pengguna sendiri. Ini mencakup
  fakta/preferensi/sifat tentang pengguna itu sendiri, DAN fakta tentang orang nyata lain
  yang disebut namanya dalam percakapan, karena hal-hal itu tetap merupakan sesuatu yang
  seharusnya disimpan oleh memori pengguna.
* `"agent"` — informasi yang diberikan oleh asisten AI (sebuah `role: "assistant"` tanpa nama)
  (rekomendasi, konfirmasi, hal-hal yang ia teliti), atau tindakan di masa mendatang
  yang pengguna ingin dilakukan oleh **asisten** ("ingatkan saya 8 jam lagi").

Orang yang memiliki nama TIDAK PERNAH `owner: "agent"` — ia orang nyata, bukan AI,
meskipun pesannya membawa `role: "assistant"`. Memorinya adalah
`owner: "user"` dengan namanya sebagai subjek konten.

Tentukan `owner` per item. Jika ragu, gunakan default `"user"`.

---

# ATURAN WAKTU

Selesaikan waktu relatif menggunakan Memory Date.

Contoh:

* "minggu depan"
* "bulan lalu"
* "besok"

Jika waktu tidak dapat diselesaikan dengan aman:

* hapus ungkapan waktu relatif
* biarkan memori tanpa keterangan waktu

Jangan mengarang kronologi.

**Peristiwa yang baru saja terjadi tetaplah peristiwa — simpan.** Ketika pengguna melaporkan sesuatu yang
baru saja selesai ("baru pulang", "baru selesai", "tadi pagi", "kemarin"), tanggal
selesainya adalah Memory Date. Tuliskan **peristiwa itu sendiri** beserta tanggal tersebut; jangan
meleburkannya ke dalam pernyataan latar belakang.

* "Saya baru pulang dari liburan bersama keluarga"
  → "Pengguna pulang dari liburan keluarga pada <Memory Date>."
  BUKAN → "Pengguna suka berlibur bersama keluarga." (tanggal kepulangan adalah jawaban atas "kapan
  mereka pulang", dan membuangnya membuat pertanyaan itu tidak dapat dijawab)

Jika suatu peristiwa memiliki awal dan akhir yang disebutkan secara terpisah dalam percakapan,
catat keduanya alih-alih meleburkannya menjadi satu rentang.

---

# ATURAN KUALITAS

Setiap memori harus:

* dapat berdiri sendiri
* lengkap secara konteks
* mudah ditemukan kembali (retrieval-friendly)
* padat informasi

Pertahankan detail penting:

* nama
* produk
* lokasi
* angka
* persamaan
* batasan

Jangan pernah menggeneralisasi hal yang spesifik menjadi sebuah kategori. Tulis apa yang benar-benar dikatakan.

* "Banff" → tetap "Banff", BUKAN "Pegunungan Rocky"
* "seekor border collie berumur 3 tahun bernama Luna" → pertahankan ras, umur, dan namanya

**Detail spesifik yang diberikan oleh pembicara lain tetap dihitung.** Sebuah fakta sering terbentuk lintas
giliran bicara: pembicara lain menyebutkan hal tersebut, pengguna mengonfirmasinya. Aturan subjek
menentukan memori *milik siapa* itu — aturan tersebut tidak membenarkan pembuangan detail.

* [Sam]: "Apakah kamu sempat main ski di Banff?"
  [user]: "Iya, tempatnya bagus banget buat main ski!"
  → "Pengguna bermain ski di Banff dan merasa saljunya luar biasa."
  BUKAN → "Pengguna menyukai olahraga musim dingin." (tempat dan aktivitasnya sama-sama
  sudah ditetapkan, masing-masing oleh satu pembicara)

Hindari memori yang terpotong-potong dan kurang konteks.

Pilih jumlah memori sesedikit mungkin yang tetap mempertahankan kebutuhan
pencarian yang berbeda di masa mendatang.

* Pemadatan berarti menggabungkan, bukan menghilangkan. Setiap proposisi eksplisit yang tahan lama
  dalam pesan baru harus muncul tepat satu kali dalam keluaran.
* Gabungkan detail bila memiliki subjek yang sama dan peristiwa, keadaan, preferensi,
  rencana, atau keputusan dunia nyata yang sama. Satu peristiwa yang koheren boleh memakai 1–3 kalimat.
* Jangan menuliskan sekaligus pernyataan yang luas dan pernyataan ulang yang lebih sempit dari bukti yang sama.
* Pisahkan hanya bila subjeknya berbeda, peristiwanya terjadi secara independen, atau
  memorinya menggambarkan keadaan yang berbeda.
* Simpan sebab, rekomendasi, atau akibat bersama fakta yang dijelaskannya bila
  memisahkannya akan menghilangkan hubungan tersebut.
* Jangan pernah menggabungkan kejadian bertanggal yang terpisah hanya karena topiknya sama.
* Pertahankan presisi waktu dari sumbernya. Jangan ubah frasa samar seperti "akhir pekan
  lalu" atau "musim panas lalu" menjadi tanggal kalender yang pasti kecuali tanggalnya eksplisit
  atau dapat ditentukan secara unik; "akhir pekan sebelum <Memory Date>" dapat diterima.

---

# TAGS

Setiap memori harus menyertakan 1–3 tag yang ringkas.
- Kata kunci topik tetap huruf kecil (travel, health, work).
- Nama diri mempertahankan huruf kapital (Jasper, Canada, Banff, Prius).

Contoh:

* work
* travel
* Jasper
* Canada
* health

---
"""

STRUCTURE_METADATA_PROMPT = """

---

# ORGANISASI STRUKTURAL

Untuk setiap `memory`, keluarkan juga:

* `topic_paths`: daftar yang kompatibel dengan versi lama berisi nol hingga tiga jalur
  semantik dari yang luas ke yang spesifik.
* `event_facet`: objek yang hanya berisi `subject`, `participants`, `action`,
  `objects`, `locations`, dan `state`; state bernilai `planned`, `ongoing`,
  `completed`, atau `unknown`.
* `topic_assignments`: nol hingga enam keanggotaan yang spesifik per subjek. Setiap item adalah
  `{subject, role, path}`, dengan `path` berupa daftar segmen `{name, type}` dan
  `type` salah satu dari `topic`, `facet`, `series`, atau `episode`. Gunakan `topic` untuk
  domain yang luas, `facet` untuk aktivitas konkret/aspek semantik, `series` hanya
  untuk kejadian berulang dari garis aktivitas/keadaan konkret yang sama, dan
  `episode` hanya untuk satu kejadian dunia nyata yang berbatas. Sebuah peristiwa bersama boleh memiliki
  satu assignment per partisipan eksplisit sambil tetap mempertahankan satu memori kanonis.
  Urutan type mengikuti `topic -> facet -> series -> episode`; tingkatan boleh dilewati tetapi
  type tidak boleh berulang atau mundur.

Pengekstrak ini hanya melihat penulisan saat ini sehingga tidak boleh mengeluarkan
segmen `series`. Keluarkan Topic/Facet/Episode di sini. Tahap reconcile nanti dapat menetapkan
Series setelah membandingkan kejadian-kejadian independen yang sudah tersimpan.

Ini adalah organisasi pada saat penulisan. Jangan menyimpulkan Series/Episode dari kedalaman jalur,
jangan membuat hierarki dari nama diri atau tag yang luas, dan jangan menetapkan seseorang hanya
karena ia muncul bersamaan dalam konteks yang tidak berkaitan. Berhentilah pada tingkat paling spesifik
yang didukung dan gunakan nilai kosong bila ragu. Jangan pernah menyandikan bentuk kueri seperti
"kapan", "berapa banyak", "siapa", bentuk jamak, atau kata urutan.
"""

EXTRACT_FEW_SHOT = """
---

# CONTOH

## Contoh 1 — Batasan: konteks lama hanya untuk resolusi, tidak pernah diekstrak ulang

k Pesan Terakhir:
[{"role": "user", "content": "Aku lagi lihat-lihat anjing adopsi di penampungan hewan di pusat kota."},
 {"role": "assistant", "content": "Wah, menyenangkan sekali — sudah ketemu yang kamu suka?"}]
Pesan Baru:
[{"role": "user", "content": "Iya, kemarin aku jemput dia — border collie umur 3 tahun namanya Luna."}]
Memory Date: 2025-04-10

Keluaran:
```json
{
  "memory": [
    {"content": "Pengguna mengadopsi seekor border collie berumur 3 tahun bernama Luna dari penampungan hewan di pusat kota pada 2025-04-09.", "tags": ["pets", "family"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
Pesan lama hanya menyelesaikan rujukan "dia" dan memberikan konteks ("penampungan hewan di pusat kota"). JANGAN keluarkan memori basi seperti "pengguna sedang mempertimbangkan untuk mengadopsi anjing" — hanya fakta yang benar-benar baru (adopsi yang sudah terjadi) yang diekstrak, dengan "kemarin" diselesaikan berdasarkan Memory Date.

## Contoh 2 — Ringkas balasan asisten yang panjang

Pesan Baru:
[{"role": "user", "content": "Tolong tuliskan esai singkat tentang mengapa kerja jarak jauh meningkatkan produktivitas."},
 {"role": "assistant", "content": "Tentu — pembuka: 'Kerja jarak jauh telah mengubah cara tim bekerja...' [~2000 kata isi dihilangkan] ...penutup: 'Singkatnya, otonomi dan berkurangnya gangguan mendorong hasil kerja yang lebih tinggi.'"}]
Memory Date: 2025-03-22

Keluaran:
```json
{
  "memory": [
    {"content": "Asisten menyusun esai singkat untuk pengguna yang berargumen bahwa kerja jarak jauh meningkatkan produktivitas karena otonomi dan berkurangnya gangguan mendorong hasil kerja yang lebih tinggi.", "tags": ["writing", "remote-work"], "owner": "agent"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
Balasan asisten panjang. Jangan pernah menyimpan esai secara utuh kata per kata — saring menjadi topik + tesis. owner adalah "agent" karena asistenlah yang menghasilkannya. Konten kata per kata yang membengkak membuat memori tidak dapat digunakan.

## Contoh 3 — Ekstraksi gabungan: basic_info + intention + rencana agent yang diringkas

Pesan Baru:
[{"role": "user", "content": "Halo, saya Kenji. Saya berencana jalan-jalan ke Jepang bulan depan — bisa tolong buatkan itinerary 7 hari?"},
 {"role": "assistant", "content": "# Rute Emas Klasik Jepang 7 Hari (Tokyo → Kyoto → Nara → Osaka)\\nTerbang ke Tokyo, naik Shinkansen ke selatan menuju Kyoto, perjalanan sehari ke Nara, pulang dari Osaka (Kansai).\\n\\n## Hari 1: Tiba di Tokyo\\n- Mendarat di Haneda/Narita, titip tas di hotel dekat Stasiun Tokyo.\\n- Sore hari: Kuil Sensoji (Asakusa), jalan-jalan di Jalan Nakamise, coba taiyaki.\\n- Malam: Shibuya Crossing + rooftop Shibuya Sky untuk matahari terbenam; makan malam di izakaya (yakitori, sake).\\n\\n## Hari 2: Pasar & landmark Tokyo — [dihilangkan]\\n## Hari 3: Shinkansen ke Kyoto, Fushimi Inari, Gion — [dihilangkan]\\n## Hari 4: Kuil-kuil Kyoto (Kiyomizu-dera, Kinkaku-ji, Ginkaku-ji) — [dihilangkan]\\n## Hari 5: Perjalanan sehari ke Nara (rusa Taman Nara, Todai-ji) — [dihilangkan]\\n## Hari 6: Osaka (Kastel Osaka, jajanan kaki lima Dotonbori) — [dihilangkan]\\n## Hari 7: Wisata Osaka + berangkat dari Bandara Kansai — [dihilangkan]\\n\\nTips: beli JR Pass 7 hari sebelum tiba. Ada yang ingin disesuaikan?"}]
Memory Date: 2025-09-15

Keluaran:
```json
{
  "memory": [
    {"content": "Asisten membuat itinerary 7 hari ke Jepang untuk pengguna dengan rute emas klasik: Tokyo (Asakusa, Shibuya), Shinkansen ke Kyoto (Fushimi Inari, Kiyomizu-dera, Kinkaku-ji, Gion), perjalanan sehari ke Nara (taman rusa, Todai-ji), lalu Osaka (Kastel Osaka, Dotonbori), berangkat pulang dari Bandara Kansai.", "tags": ["travel", "japan", "itinerary"], "owner": "agent"}
  ],
  "intentions": [
    {"content": "Pengguna berencana melakukan perjalanan 7 hari ke Jepang pada Oktober 2025.", "tags": ["travel", "japan"], "valid_until": "2025-10-31", "owner": "user"}
  ],
  "basic_info": {"name": "Kenji"}
}
```
Satu giliran bicara menghasilkan tiga keluaran di wadah yang berbeda: nama masuk ke basic_info, perjalanan adalah intention berorientasi ke depan dengan tenggat, dan itinerary panjang multi-bagian dari asisten diringkas menjadi SATU memori agent yang padat — rute dan persinggahan utama per kota, BUKAN poin-poin harian, tips, atau daftar makanan. Menyimpan seluruh balasan kata per kata akan membuat memori membengkak; menyimpan kerangkanya membuatnya tetap dapat dikueri.

## Contoh 4 — Pesan selanjutnya yang mengonfirmasi/mengadopsi rekomendasi sebelumnya adalah fakta tersendiri

Pesan Baru:
[{"role": "user", "content": "Anakku yang umur 6 tahun nggak mau makan sayur sama sekali. Ada ide?"},
 {"role": "assistant", "content": "Coba blender bayam atau wortel ke dalam saus pasta — warna dan rasanya hampir hilang, dan banyak anak mau memakannya tanpa sadar."},
 {"role": "user", "content": "Wah bagus, nanti malam kami coba. Ngomong-ngomong, jam tidur yang baik untuk umur segitu jam berapa?"},
 {"role": "assistant", "content": "Untuk anak 6 tahun, usahakan tidur antara pukul 19.00–21.00 dengan durasi tidur sekitar 10–11 jam."}]
Memory Date: 2025-06-20

Keluaran:
```json
{
  "memory": [
    {"content": "Pengguna memiliki anak berumur 6 tahun yang menolak makan sayur, dan menerima saran asisten untuk memblender bayam atau wortel ke dalam saus pasta agar tersamarkan.", "tags": ["parenting", "food"], "owner": "user"},
    {"content": "Asisten menyarankan kepada pengguna bahwa anak berumur 6 tahun sebaiknya tidur antara pukul 19.00–21.00 dengan durasi tidur sekitar 10–11 jam.", "tags": ["parenting", "sleep"], "owner": "agent"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
Pada pesan ketiga pengguna berkata "nanti malam kami coba" — secara eksplisit mengadopsi saran sebelumnya. Penerimaan itu bernilai dan harus dicatat ("menerima saran asisten untuk ..."), bukan dibuang sebagai basa-basi. Saran jam tidur baru saja diberikan dan belum diadopsi, sehingga dicatat sebagai saran (owner "agent").

## Contoh 5 — Konteks RAG/sisipan: jangan pernah menyimpan teks yang disisipkan

Pesan Baru:
[{"role": "user", "content": "[Konteks yang diambil: Menara Eiffel tingginya 330 m dan selesai dibangun pada 1889.] Berdasarkan itu, saya akan melamar pacar saya di puncak Menara Eiffel musim panas ini."}]
Memory Date: 2025-05-02

Keluaran:
```json
{
  "memory": [],
  "intentions": [
    {"content": "Pengguna berencana melamar pacarnya di puncak Menara Eiffel pada musim panas 2025.", "tags": ["relationship", "travel"], "valid_until": "2025-08-31", "owner": "user"}
  ],
  "basic_info": {}
}
```
Konteks yang diambil dalam kurung siku (tinggi dan tahun menara) adalah materi referensi yang disisipkan oleh RAG, BUKAN sesuatu yang pengguna ceritakan tentang dirinya — JANGAN simpan. Hanya rencana pengguna sendiri yang berorientasi ke depan yang diekstrak. Teks yang disisipkan oleh sistem/RAG adalah konteks untuk model, tidak pernah menjadi memori.

## Contoh 6 — Materi referensi yang dibagikan: ekstrak isinya, bukan tindakan membagikannya

Pesan Baru:
[{"role": "user", "content": "Tolong ingat resep pangsit nenekku supaya nggak hilang. Isian: 500 g daging babi giling, 300 g sawi putih, 2 sdm kecap asin, 1 sdm minyak wijen, jahe seruas jempol dicincang. Sawi digarami 15 menit lalu diperas sampai kering. Rebus pangsit, tambahkan air dingin dua kali, matang kalau sudah mengapung."},
 {"role": "assistant", "content": "Baik, sudah disimpan!"}]
Memory Date: 2025-07-01

Keluaran:
```json
{
  "memory": [
    {"content": "Resep pangsit nenek pengguna: isian 500 g daging babi giling, 300 g sawi putih (digarami 15 menit lalu diperas sampai kering), 2 sdm kecap asin, 1 sdm minyak wijen, dan jahe seruas jempol yang dicincang; rebus pangsit, tambahkan air dingin dua kali, dan pangsit matang ketika sudah mengapung.", "tags": ["food", "recipe", "family"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
Ekstrak isi faktualnya (bahan, takaran, langkah menggarami dan memeras, cara merebus) — BUKAN "pengguna membagikan resep" atau "pengguna meminta agar resep diingat". Tindakan membagikan tidak bernilai; isinyalah yang dapat dikueri.

## Contoh 7 — Angka dan atribut terstruktur dipertahankan persis

Pesan Baru:
[{"role": "user", "content": "Mencatat PR home-gym siklus ini: Squat 140kg x 3, Bench 100kg x 5, Deadlift 180kg x 2, Overhead Press 60kg x 6. Berat badan 78kg, 3 set per gerakan, istirahat 3 menit."},
 {"role": "assistant", "content": "Tercatat — angka yang mantap!"}]
Memory Date: 2026-01-20

Keluaran:
```json
{
  "memory": [
    {"content": "PR angkat beban pengguna saat ini adalah Squat 140kg x 3, Bench 100kg x 5, Deadlift 180kg x 2, dan Overhead Press 60kg x 6, dengan berat badan 78kg.", "tags": ["fitness", "strength"], "owner": "user"},
    {"content": "Pengguna berlatih dengan 3 set per gerakan dan istirahat 3 menit di antara set.", "tags": ["fitness", "training"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
Setiap angka dan satuan (kg, repetisi, set, waktu istirahat) dipertahankan persis — membuangnya akan menghancurkan informasi yang paling dapat dikueri. Pisahkan menjadi dua topik yang bersih (PR vs. protokol latihan), tetapi jangan pernah membulatkan atau menghilangkan detail spesifiknya.

## Contoh 8 — Percakapan panjang dengan banyak topik: jangan berhenti setelah topik pertama

Pesan Baru:
[{"role": "user", "content": "Minggu yang luar biasa — akhirnya aku sidang disertasi PhD hari Selasa!"},
 {"role": "assistant", "content": "Selamat besar! Bagaimana perasaanmu?"},
 {"role": "user", "content": "Lega! Aku juga sudah tanda tangan sewa apartemen di Berlin dan pindah bulan depan. Terus aku mulai belajar bahasa Jerman di Duolingo — streak 30 hari. Pasanganku, Sofia, untuk sementara tetap di Madrid, dan itu berat."}]
Memory Date: 2025-11-08

Keluaran:
```json
{
  "memory": [
    {"content": "Pengguna menjalani sidang disertasi PhD pada 2025-11-04.", "tags": ["education", "milestone"], "owner": "user"},
    {"content": "Pengguna menandatangani kontrak sewa apartemen di Berlin.", "tags": ["housing"], "owner": "user"},
    {"content": "Pengguna sedang belajar bahasa Jerman di Duolingo dan memiliki streak 30 hari.", "tags": ["language", "learning"], "owner": "user"},
    {"content": "Pasangan pengguna, Sofia, untuk sementara tetap tinggal di Madrid, dan pengguna merasa hal itu berat.", "tags": ["relationship"], "owner": "user"}
  ],
  "intentions": [
    {"content": "Pengguna berencana pindah ke Berlin bulan depan (sekitar Desember 2025).", "tags": ["housing", "relocation"], "valid_until": "2025-12-31", "owner": "user"}
  ],
  "basic_info": {}
}
```
Empat topik atau lebih — sidang disertasi, apartemen di Berlin, belajar bahasa Jerman, dan pasangan di Madrid. Jangan berhenti setelah topik pertama. Perhatikan bahwa satu giliran bicara dipecah menjadi fakta yang sudah selesai (sewa ditandatangani) DAN sebuah intention (kepindahan yang akan datang), dan detail emosional-tetapi-substantif tentang Sofia dipertahankan karena membawa konteks hubungan yang bertahan lama.

## Contoh 9 — Dua orang nyata: `name` pada pesan asisten berarti orang nyata

Pesan Baru:
[{"role": "user", "name": "Tim", "content": "Baru pulang dari gym panjat tebing, sudah tiga kali minggu ini. Akhirnya berhasil menyelesaikan rute overhang itu."},
 {"role": "assistant", "name": "John", "content": "Keren! Aku malah berenang — bahuku masih sakit sejak bulan lalu."}]
Memory Date: 2025-08-14

Keluaran:
```json
{
  "memory": [
    {"content": "Pengguna rutin panjat tebing tiga kali seminggu dan baru-baru ini berhasil menyelesaikan rute overhang yang sulit.", "tags": ["fitness", "climbing"], "owner": "user"},
    {"content": "John berenang sebagai pengganti olahraga lain karena bahunya masih sakit akibat cedera sekitar Juli 2025.", "tags": ["fitness", "health"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {"name": "Tim"}
}
```
John membawa `role: "assistant"` tetapi ia memiliki `name`, sehingga ia adalah orang nyata lain, BUKAN AI: memorinya memakai "John ..." sebagai subjek dan tetap `owner: "user"` (termasuk dalam penyimpanan memori pengguna). Memori pengguna sendiri tetap "Pengguna ..." meskipun kita tahu namanya Tim — jangan pernah "Tim rutin panjat tebing". `basic_info.name` berasal dari `name: "Tim"` pada pesan pengguna, yang bersifat otoritatif meskipun Tim tidak pernah menyebutkan namanya sendiri secara langsung.

## Contoh 10 — Contoh tandingan: atribut orang lain tidak pernah masuk ke basic_info

Pesan Baru:
[{"role": "user", "name": "Tim", "content": "What have you been up to?"},
 {"role": "assistant", "name": "John", "content": "I'm a nurse at a hospital in Berlin, working night shifts. Pretty exhausting."}]
Memory Date: 2025-09-02

Keluaran:
```json
{
  "memory": [
    {"content": "John is a nurse at a hospital in Berlin, works night shifts, and finds it exhausting.", "tags": ["work", "health"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {"name": "Tim"}
}
```
Pekerjaan dan lokasi John adalah atribut MILIKNYA, sehingga masuk ke `memory` dengan "John" sebagai subjek. Atribut itu TIDAK BOLEH masuk ke `basic_info.occupation` / `basic_info.location` — `basic_info` khusus untuk profil pengguna sendiri. Hanya `name` (Tim, dari field `name` pada pesan pengguna) yang termasuk di sana. Perhatikan bahwa bahasa keluaran mengikuti bahasa masukan (contoh ini berbahasa Inggris).

## Contoh 11 — Contoh tandingan: basa-basi tidak memiliki basic_info

Pesan Baru:
[{"role": "user", "content": "Halo"},
 {"role": "assistant", "content": "Halo! Nama pengguna adalah Pengguna. Ada yang bisa saya bantu?"}]
Memory Date: 2025-09-03

Keluaran:
```json
{
  "memory": [],
  "intentions": [],
  "basic_info": {}
}
```
Pengguna tidak menyatakan apa pun tentang dirinya. Kalimat asisten bukan giliran bicara pengguna, dan "Pengguna" adalah placeholder, bukan nama. Keluarkan `{}` — jangan pernah `{"name": "Pengguna"}` atau `{"name": "user"}`.

---
"""

EXTRACT_PROMPT = """## k Pesan Terakhir

{last_messages}

---

## Pesan Baru

{content}

---

## Memory Date

{memory_at}

---

## Current Date

{current_date}

---

# Format Keluaran

Kembalikan HANYA sebuah objek JSON di dalam blok berpagar ```json.

```json
{{
  "memory": [
    {{
      "content": "...",
      "tags": ["..."],
      "owner": "user"
    }}
  ],
  "intentions": [
    {{
      "content": "...",
      "tags": ["..."],
      "valid_until": "YYYY-MM-DD",
      "owner": "user"
    }}
  ],
  "basic_info": {{}}
}}
```

Aturan:

* `memory` = info tahan lama yang layak diingat: preferensi, opini, sikap,
  sifat, peristiwa, rencana, pengalaman, kebiasaan, keputusan — tentang pengguna atau
  tentang orang nyata lain yang disebut namanya, dengan subjek ditulis sesuai "# SUBJEK"
* `intentions` = hal berorientasi ke depan yang pengguna (atau orang lain yang disebut namanya) rencanakan /
  niatkan untuk dilakukan di masa mendatang, dengan `valid_until` = tenggat/tanggal target
  (diselesaikan berdasarkan Memory Date) atau `null` jika tidak ada
* `owner` (wajib pada setiap memory & intention) = `"user"` atau `"agent"`
  (lihat "# OWNER" di atas; default `"user"` jika ragu)
* `basic_info` = **hanya profil pengguna sendiri**. Sertakan field profil
  yang stabil (mis. nama, umur, lokasi, pekerjaan, pemberi kerja) yang **dinyatakan secara eksplisit**
  dalam pesan pengguna yang BARU. Jangan pernah menyimpulkan, jangan pernah mengarang. Jika tidak ada field yang dinyatakan
  secara eksplisit, keluarkan `{{}}`.
  * ATURAN MUTLAK: isi sebuah field HANYA jika pengguna menyatakannya dalam giliran `user`.
    Apa pun yang hanya dikatakan oleh asisten (termasuk asisten yang mengutip
    memori sebelumnya seperti "Nama pengguna adalah X") TIDAK dihitung. Jika tidak ada yang
    memenuhi syarat, keluarkan `"basic_info": {{}}`.
  * JANGAN PERNAH menulis nama field itu sendiri atau placeholder sebagai nilai ("user",
    "The user", "Pengguna", "用户", "name", "N/A", "unknown", "none", "-"). Hilangkan field tersebut.
  * Contoh negatif: basa-basi seperti `[user]: Halo` / `[assistant]: Ada yang bisa
    saya bantu?`, atau `[user]: Balas persis dengan: PONG` → `"basic_info": {{}}`.
  * Pengecualian untuk `name`: field `name` pada pesan milik pengguna sendiri, atau baris
    di awal "Nama pengguna adalah X" / "The user's name is X" / "用户名叫X", adalah sumber otoritatif untuk
    `name` — meskipun pengguna tidak pernah mengucapkannya secara langsung.
  * JANGAN PERNAH memasukkan atribut orang nyata lain ke sini. Jika John (pesan ber-role asisten
    yang membawa `name`) berkata "Saya perawat di Berlin", itu adalah `memory`
    ("John adalah perawat di Berlin."), BUKAN `basic_info.occupation` / `.location`.
* Tidak ada informasi duplikat antarbagian
* Tags harus berisi 1–3 kata kunci huruf kecil yang ringkas
* Gunakan array kosong `[]` atau `{{}}` jika tidak ada yang diekstrak
* Kembalikan hanya JSON yang valid

Keluarkan JSON sekarang."""

EXTRACT_PROMPT_NO_INTENTION = """## k Pesan Terakhir

{last_messages}

---

## Pesan Baru

{content}

---

## Memory Date

{memory_at}

---

## Current Date

{current_date}

---

# Format Keluaran

Kembalikan HANYA sebuah objek JSON di dalam blok berpagar ```json.

```json
{{
  "memory": [
    {{
      "content": "...",
      "tags": ["..."],
      "owner": "user"
    }}
  ],
  "basic_info": {{}}
}}
```

Aturan:

* `memory` = info tahan lama yang layak diingat: preferensi, opini, sikap,
  sifat, peristiwa, pengalaman, kebiasaan, keputusan, **serta rencana masa depan / tindakan
  yang akan datang** — tentang pengguna atau tentang orang nyata lain yang disebut namanya, dengan
  subjek ditulis sesuai "# SUBJEK". **Jangan** membuat wadah intentions terpisah.
* `owner` (wajib pada setiap memory) = `"user"` atau `"agent"`
  (lihat "# OWNER" di atas; default `"user"` jika ragu)
* `basic_info` = **hanya profil pengguna sendiri**. Sertakan field profil
  yang stabil (mis. nama, umur, lokasi, pekerjaan, pemberi kerja) yang **dinyatakan secara eksplisit**
  dalam pesan pengguna yang BARU. Jangan pernah menyimpulkan, jangan pernah mengarang. Jika tidak ada field yang dinyatakan
  secara eksplisit, keluarkan `{{}}`.
  * ATURAN MUTLAK: isi sebuah field HANYA jika pengguna menyatakannya dalam giliran `user`.
    Apa pun yang hanya dikatakan oleh asisten (termasuk asisten yang mengutip
    memori sebelumnya seperti "Nama pengguna adalah X") TIDAK dihitung. Jika tidak ada yang
    memenuhi syarat, keluarkan `"basic_info": {{}}`.
  * JANGAN PERNAH menulis nama field itu sendiri atau placeholder sebagai nilai ("user",
    "The user", "Pengguna", "用户", "name", "N/A", "unknown", "none", "-"). Hilangkan field tersebut.
  * Contoh negatif: basa-basi seperti `[user]: Halo` / `[assistant]: Ada yang bisa
    saya bantu?`, atau `[user]: Balas persis dengan: PONG` → `"basic_info": {{}}`.
  * Pengecualian untuk `name`: field `name` pada pesan milik pengguna sendiri, atau baris
    di awal "Nama pengguna adalah X" / "The user's name is X" / "用户名叫X", adalah sumber otoritatif untuk
    `name` — meskipun pengguna tidak pernah mengucapkannya secara langsung.
  * JANGAN PERNAH memasukkan atribut orang nyata lain ke sini. Jika John (pesan ber-role asisten
    yang membawa `name`) berkata "Saya perawat di Berlin", itu adalah `memory`
    ("John adalah perawat di Berlin."), BUKAN `basic_info.occupation` / `.location`.
* Tidak ada informasi duplikat antarbagian
* Tags: 1–3 kata kunci ringkas; tag topik huruf kecil; nama diri mempertahankan huruf kapital
* Gunakan array kosong `[]` atau `{{}}` jika tidak ada yang diekstrak
* Kembalikan hanya JSON yang valid — **jangan pernah** sertakan key `intentions`

Keluarkan JSON sekarang."""


# ================================================================
# Chat reconcile (agent/reconciler.py)
# ================================================================

SEARCH_QUERY_PROMPT = """Anda adalah pembuat kueri pencarian. Diberikan daftar memori yang baru diekstrak, buatlah sekumpulan kueri pencarian singkat yang dapat digunakan untuk menemukan memori lama yang berkaitan di dalam basis data vektor.

Tujuannya adalah memaksimalkan recall — temukan memori lama yang secara semantik berkaitan dengan memori baru, meskipun susunan katanya sangat berbeda.

## Memori baru:
{new_memories}

## Instruksi

Buat kueri pencarian yang mencakup:
- Topik, entitas, dan tema utama yang disebutkan dalam memori
- Versi yang diparafrasekan atau diabstraksikan dari konsep inti
- Konsep terkait yang mungkin ada dalam penyimpanan memori pengguna

Keluarkan sebuah array JSON berisi string kueri (5-15 kueri, singkat dan terfokus):

["kueri1", "kueri2", "kueri3", ...]

Keluarkan hanya array JSON, tanpa teks lain."""

RECONCILE_PROMPT = """Anda adalah sistem manajemen memori.

Tugas Anda adalah menggabungkan memori baru ke dalam basis memori yang sudah ada sambil menjaganya tetap:

* tanpa kehilangan informasi (lossless)
* tidak saling bertentangan
* ringkas
* sangat mudah ditemukan kembali

---

# Operasi

## ADD

Gunakan ketika informasinya sepenuhnya baru.

```json
{{
  "op": "ADD",
  "content": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

---

## UPDATE

Gunakan ketika:

* memori baru kompatibel dengan memori yang sudah ada
* keduanya menggambarkan topik yang sama
* penggabungan meningkatkan kelengkapan atau kemudahan ditemukan kembali

Aturan:

* gabungkan tanpa kehilangan informasi
* pertahankan semua informasi yang berguna
* JANGAN membuat duplikat
* jika keduanya berbeda pada suatu nilai, pertahankan nilai dari memori dengan
  `memory_at` yang lebih akhir; jangan pernah biarkan memori yang lebih lama menimpa nilai yang lebih baru

### Kejadian yang sama vs. kejadian lain

Gunakan **UPDATE** hanya ketika memori baru menambahkan detail pada kejadian dunia nyata
yang sama atau fakta tahan lama yang sudah tercatat.

Jika orang yang sama melakukan jenis aktivitas/peristiwa yang sama lagi pada waktu lain
(perjalanan lain, pertandingan lain, cedera lain, pembelian lain, masalah kesehatan lain, dll.), simpan kejadian tersebut
sebagai **ADD** terpisah. Pertahankan tanggal dan detailnya sendiri. Ketika sebuah memori yang sudah ada
jelas merupakan kejadian lain dalam rangkaian konkret yang sama, tautkan dengan
`related: [{{"memory_id":"...","relation":"same_series"}}]`.

Jangan menggabungkan kejadian bertanggal yang terpisah menjadi satu memori panjang berisi hitungan/linimasa.
Lapisan retrieval membangun linimasa dari memori L2 yang saling tertaut.

```json
{{
  "op": "UPDATE",
  "memory_id": "...",
  "content": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

---

## SUPERSEDE

Gunakan HANYA ketika:

* memori baru bertentangan dengan memori yang sudah ada
* keduanya tidak mungkin benar pada saat yang sama
* memori lama kini akan memberikan jawaban yang salah tentang keadaan saat ini
* `memory_at` memori baru sama dengan atau LEBIH AKHIR daripada `memory_at` memori yang sudah ada

Contoh:

* kota berubah
* pemberi kerja berubah
* status hubungan berubah

JANGAN gunakan untuk:

* tambahan detail
* preferensi yang kompatibel
* perbedaan susunan kata
* kesetaraan semantik
* memori baru yang `memory_at`-nya LEBIH AWAL daripada memori yang sudah ada — itu adalah
  keadaan yang lebih lama dan tidak boleh menggantikan yang lebih baru (lihat "Urutan waktu")

Aturan:

* content HANYA boleh berisi keadaan yang baru
* JANGAN menyalin informasi yang tidak berubah
* sistem menyimpan node lama dan menautkannya ke rantai evolusi secara otomatis; Anda tidak perlu menangani riwayat

```json
{{
  "op": "SUPERSEDE",
  "memory_id": "...",
  "content": "...",
  "supersede_reason": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

### Melipat beberapa memori yang sudah ada menjadi satu rantai

Jika DUA ATAU LEBIH memori yang sudah ada melacak dimensi yang SAMA dan memori baru adalah
perubahan terbaru pada dimensi tersebut — `memory_at`-nya lebih akhir daripada `memory_at` setiap memori
yang dilipat — Anda boleh melipatnya menjadi satu rantai
evolusi. Gunakan `memory_ids` (daftar terurut, TERLAMA → TERBARU) sebagai pengganti satu
`memory_id`:

```json
{{
  "op": "SUPERSEDE",
  "memory_ids": ["<oldest_id>", "<newer_id>"],
  "content": "<hanya keadaan terbaru>",
  "supersede_reason": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

Memori yang tercantum dirantai dari terlama → terbaru dan memori baru menjadi
kepala rantai. `content` tetap HANYA menggambarkan keadaan terbaru — jangan pernah menyalin
teks memori-memori lama ke dalamnya; memori lama tetap dapat dikueri dalam riwayat rantai.

---

# Aturan

* Pertahankan semua informasi yang bermakna.
* Lewati duplikat yang persis sama.
* Utamakan penggabungan fragmen yang berkaitan.
* Kejadian bertanggal yang terpisah tetap menjadi ADD terpisah; gunakan `same_series` hanya untuk
  subjek yang sama dan jenis aktivitas/peristiwa konkret yang sama.
* Jaga agar setiap memori terfokus pada satu topik yang koheren.
* Ikuti "Urutan waktu" di bawah.
* **JANGAN PERNAH menggabungkan atau melakukan supersede lintas subjek yang berbeda.** Subjek adalah siapa pun
  yang dibicarakan oleh konten: "Pengguna ..." vs "John ..." vs "Mary ...". Memori tentang
  orang yang berbeda adalah fakta yang independen meskipun kalimatnya selebihnya
  identik — "Pengguna suka kopi" dan "John suka kopi" adalah dua memori
  yang terpisah, tidak pernah saling UPDATE atau SUPERSEDE.
* `owner` (`user`/`agent`): salin dari memori yang sedang ditulis. JANGAN PERNAH menggabungkan atau melakukan supersede lintas owner yang berbeda — fakta milik pengguna dan fakta milik asisten berbeda meskipun topiknya sama.
* Sebuah SUPERSEDE menargetkan SATU memori (`memory_id`) atau melipat beberapa memori pada dimensi yang sama menjadi satu rantai (`memory_ids`, terlama→terbaru). Sebuah UPDATE menargetkan tepat SATU memori.
* Jangan pernah menyentuh memori lama yang sama dari lebih dari satu op.
* Bahasa keluaran sama dengan bahasa memori masukan.

---

# Urutan waktu

Setiap memori, baru maupun lama, memiliki `memory_at`: kapan hal itu terjadi. Memori
TIDAK selalu ditulis sesuai urutan terjadinya — riwayat bisa diimpor belakangan,
dan sebuah memori bisa ditambahkan saat riwayat yang lebih lama masih diproses. Karena itu,
memori baru bisa LEBIH LAMA daripada memori yang sudah ada.

* Tentukan memori mana yang lebih baru HANYA berdasarkan `memory_at`. "Memori baru" berarti "sedang
  ditulis sekarang", bukan "lebih mutakhir".
* Memori yang lebih lama tidak boleh menggantikan yang lebih baru: tidak ada SUPERSEDE terhadap memori
  yang lebih baru, dan tidak ada UPDATE yang menimpa nilai yang lebih baru dengan nilai yang lebih lama.
* Jika memori baru lebih lama daripada memori yang sudah ada yang bertentangan dengannya, pertahankan
  memori yang sudah ada sebagai keadaan saat ini. Lakukan ADD untuk memori baru sebagai keadaan masa lalu, ditulis
  dalam bentuk lampau beserta waktunya (misalnya "Pada 2023 pengguna tinggal di Shanghai"),
  atau jangan keluarkan apa pun jika tidak menambah informasi.
* `memory_at: null` berarti waktunya tidak diketahui. Tidak diketahui BUKAN berarti lebih baru: jangan pernah melakukan SUPERSEDE terhadap
  memori yang memiliki `memory_at` yang diketahui dengan memori yang `memory_at`-nya null.
* Jangan mengarang kronologi.

---

# Opsional: `related`

Pada op apa pun Anda BOLEH menambahkan array `related` yang mencatat bagaimana memori yang sedang ditulis
berkaitan dengan memori yang SUDAH ADA. Ini tidak mengubah apa yang disimpan — hanya
mencatat sebuah tautan.

```json
{{
  "op": "ADD",
  "content": "...",
  "owner": "user",
  "tags": ["..."],
  "related": [{{"memory_id": "<existing_id>", "relation": "causes"}}]
}}
```

Hanya empat relasi ini yang diizinkan:

* `causes` — kedua memori berada dalam hubungan sebab/akibat
  (mis. "mulai lari setiap hari" → "lutut mulai sakit")
* `elaborates` — menambahkan detail pada, atau merupakan perkembangan lanjutan dari, memori yang sudah ada
* `contrasts` — keduanya bertentangan tetapi KEDUANYA tetap benar (jika yang satu membuat yang lain
  salah, itu adalah SUPERSEDE, bukan tautan `contrasts`)
* `same_series` — kejadian bertanggal terpisah dari jenis aktivitas atau peristiwa konkret yang sama
  untuk subjek yang sama (misalnya, dua perjalanan ski yang berbeda)

Aturan:

* Hanya tautkan ke memori yang muncul di "Memori yang sudah ada" — jangan pernah mengarang id.
* Hanya keluarkan tautan ketika hubungannya **eksplisit dan spesifik**. Dua memori
  yang sekadar membahas topik luas yang sama BUKAN sebuah relasi — jangan sertakan `related`.
* `same_series` mensyaratkan subjek yang sama dan tindakan/peristiwa konkret yang berulang;
  tag luas yang sama seperti health, travel, sport, food, atau work tidak cukup.
* Hilangkan field ini sepenuhnya ketika tidak ada yang perlu ditautkan. Nol tautan adalah kasus yang normal.
* Jangan pernah menggunakan `related` sebagai pengganti UPDATE / SUPERSEDE.

---
{few_shot_section}
###################
Data Masukan
###################

Tanggal saat ini: {current_date}

## Memori yang sudah ada

{existing_memories}

## Memori baru

{new_memories}

---

# Keluaran

Kembalikan HANYA sebuah array JSON di dalam blok kode berpagar ```json.

Jika tidak ada perubahan yang diperlukan:

```json
[]
```

Sekarang hasilkan array JSON tersebut."""

RECONCILE_STRUCTURE_METADATA_PROMPT = """

Kontrak keluaran struktural tambahan:

Untuk setiap hasil ADD, UPDATE, atau SUPERSEDE, pertahankan atau perhalus `topic_paths`,
`event_facet`, dan `topic_assignments`. `event_facet` hanya berisi `subject`,
`participants`, `action`, `objects`, `locations`, dan `state`.
`topic_assignments` berisi entri `{subject, role, path}` yang spesifik per subjek;
setiap segmen path adalah `{name, type}` dan type adalah `topic|facet|series|episode`.
Urutan type mengikuti `topic -> facet -> series -> episode`; tingkatan boleh dilewati tetapi
type tidak boleh berulang atau mundur.
Gunakan Series hanya untuk kejadian berulang dari satu garis aktivitas/keadaan konkret dan
Episode hanya untuk satu kejadian dunia nyata yang berbatas. Jangan pernah menyimpulkan type ini dari
kedalaman, tag luas yang sama, atau kemunculan bersamaan yang tidak berkaitan. Ketika sebuah memori yang sudah ada
telah memiliki path spesifik-subjek yang setara secara semantik, gunakan kembali nama node
dan type-nya persis alih-alih membuat cabang sinonim. Untuk meminta penggunaan ulang yang persis, keluarkan
`structure_references: [{"memory_id":"<existing_id>", "subject":"...",
"through":"topic|facet|series|episode"}]`. ID yang dirujuk harus muncul di
memori yang sudah ada. `through` berarti salin prefiks path yang sudah ada itu sampai dengan
type struktural yang disebutkan; sufiks lebih dalam yang ditulis secara eksplisit tetap baru. Penggunaan ulang
dilarang ketika subjek, garis aktivitas/keadaan konkret, atau batas Episode
berbeda. Field-field ini menggambarkan memori, tidak pernah bentuk kueri di masa mendatang seperti
"kapan", "berapa banyak", atau "siapa".

Series adalah relasi pada tahap reconcile: tetapkan hanya ketika memori baru dan
setidaknya satu memori lama yang independen merupakan kejadian berbeda dari garis
aktivitas/keadaan konkret yang sama. Beberapa proposisi dari penulisan saat ini tidak
membuktikan pengulangan.
"""

RECONCILE_FEW_SHOT = """
###################
Contoh
###################

## Contoh 1 — Satu memori baru: sebagian bertentangan dengan rantai yang sudah ada, sebagian tidak berkaitan

Memori yang sudah ada:
[
  {"memory_id": "m1", "content": "Pengguna bekerja sebagai product manager di Stripe.", "owner": "user", "history_versions": [{"content": "Pengguna bekerja sebagai product manager di sebuah startup fintech."}]}
]
Memori baru:
[
  {"content": "Pengguna telah keluar dari Stripe dan kini menjadi product lead di Notion, serta belakangan mulai menekuni kerajinan tembikar di akhir pekan.", "owner": "user"}
]

Keluaran:
```json
[
  {"op": "SUPERSEDE", "memory_id": "m1", "content": "Pengguna bekerja sebagai product lead di Notion.", "supersede_reason": "pemberi kerja berubah dari Stripe ke Notion", "owner": "user", "tags": ["work"]},
  {"op": "ADD", "content": "Pengguna belakangan mulai menekuni kerajinan tembikar di akhir pekan.", "owner": "user", "tags": ["hobby"]}
]
```
SATU memori baru membawa dua hal: perubahan pekerjaan (dimensi yang sama dengan rantai pekerjaan m1 yang sudah memiliki riwayat → SUPERSEDE, menjadi kepala rantai yang baru) dan detail yang tidak berkaitan (tembikar → ADD). Pecah satu memori itu menjadi op yang tepat untuk setiap bagian — kepala rantai yang baru HANYA menggambarkan keadaan pekerjaan, tidak pernah tembikar.

## Contoh 2 — Lipat dua memori lama yang tidak tertaut + perubahan baru menjadi satu rantai 3 node

Memori yang sudah ada:
[
  {"memory_id": "a1", "content": "Pengguna sedang belajar bermain gitar.", "owner": "user", "memory_at": "2024-02-10 09:00"},
  {"memory_id": "a2", "content": "Pengguna kini bisa memainkan beberapa lagu utuh dengan gitar.", "owner": "user", "memory_at": "2024-09-05 09:00"}
]
Memori baru:
[
  {"content": "Pengguna kini tampil bermain gitar di acara open-mic lokal.", "owner": "user"}
]

Keluaran:
```json
[
  {"op": "SUPERSEDE", "memory_ids": ["a1", "a2"], "content": "Pengguna tampil bermain gitar di acara open-mic lokal.", "supersede_reason": "kemampuan gitar berkembang: belajar → memainkan lagu → tampil langsung", "owner": "user", "tags": ["music", "hobby"]}
]
```
a1 dan a2 tidak pernah tertaut, tetapi keduanya melacak dimensi yang sama (perkembangan bermain gitar) dan memori baru adalah langkah terbaru. Lipat dengan `memory_ids` yang diurutkan dari terlama→terbaru; keduanya menjadi rantai 3 node (a1 ← a2 ← kepala baru). `content` kepala rantai HANYA menggambarkan keadaan saat ini — tidak mengulang "belajar" atau "beberapa lagu".

## Contoh 3 — Kepala rantai baru TIDAK BOLEH menyalin konten memori lama

Memori yang sudah ada:
[
  {"memory_id": "c1", "content": "Pengguna tinggal di apartemen sewaan satu kamar tidur di Shibuya, Tokyo, membayar ¥180,000/bulan, senang karena hanya 5 menit jalan kaki ke stasiun, tetapi mengeluh tempatnya bising di malam hari dan tidak punya tempat parkir.", "owner": "user"}
]
Memori baru:
[
  {"content": "Pengguna baru saja membeli dan pindah ke rumah tiga kamar tidur di pinggiran Yokohama dengan taman dan garasi untuk dua mobil.", "owner": "user"}
]

Keluaran:
```json
[
  {"op": "SUPERSEDE", "memory_id": "c1", "content": "Pengguna membeli dan pindah ke rumah tiga kamar tidur di pinggiran Yokohama, dengan taman dan garasi untuk dua mobil.", "supersede_reason": "tempat tinggal berubah dari apartemen sewaan di Shibuya menjadi rumah milik sendiri di Yokohama", "owner": "user", "tags": ["housing"]}
]
```
Memori baru melakukan SUPERSEDE terhadap c1 dan menjadi kepala rantai. `content` kepala rantai HANYA menggambarkan rumah baru di Yokohama. JANGAN menyeret detail khas apartemen lama (Shibuya, sewa ¥180,000, jarak jalan kaki ke stasiun, keluhan bising/parkir) ke node baru — detail itu jelas milik tempat tinggal lama, bukan yang baru. Detail itu tetap ada di riwayat rantai dan tetap dapat ditemukan kembali; menyalinnya akan membuat kepala rantai bertentangan dengan dirinya sendiri (menyewa di Shibuya SEKALIGUS memiliki rumah di Yokohama).

## Contoh 4 — Subjek yang berbeda tidak pernah digabungkan, semirip apa pun susunan katanya

Memori yang sudah ada:
[
  {"memory_id": "d1", "content": "Pengguna minum kopi hitam setiap pagi.", "owner": "user"}
]
Memori baru:
[
  {"content": "John minum kopi hitam setiap pagi.", "owner": "user"},
  {"content": "Pengguna kini minum kopi hitam dua kali sehari, pagi dan setelah makan siang.", "owner": "user"}
]

Keluaran:
```json
[
  {"op": "ADD", "content": "John minum kopi hitam setiap pagi.", "owner": "user", "tags": ["food"]},
  {"op": "UPDATE", "memory_id": "d1", "content": "Pengguna minum kopi hitam dua kali sehari, pada pagi hari dan setelah makan siang.", "owner": "user", "tags": ["food"]}
]
```
Kedua memori baru hampir identik susunan katanya tetapi memiliki subjek yang BERBEDA. "John ..." adalah tentang orang lain, sehingga merupakan ADD biasa — tidak pernah UPDATE atau SUPERSEDE terhadap d1, meskipun d1 menyatakan hal yang hampir sama tentang pengguna. Hanya memori yang subjeknya juga "Pengguna" yang boleh digabungkan dengan d1.

## Contoh 5 — Peristiwa berulang: naikkan hitungan dan pertahankan linimasa (UPDATE, bukan ADD)

Memori yang sudah ada:
[
  {"memory_id": "i1", "content": "John mencederai pergelangan kakinya musim lalu dan perlu istirahat serta fisioterapi.", "owner": "user", "memory_at": "2023-05-21 19:48"}
]
Memori baru:
[
  {"content": "John terkilir lagi pergelangan kakinya dalam pertandingan baru-baru ini.", "owner": "user", "memory_at": "2023-10-13 13:50"}
]

Keluaran:
```json
[
  {"op": "UPDATE", "memory_id": "i1", "content": "John telah dua kali mencederai pergelangan kakinya: pertama pada musim lalu (sekitar musim 2022–2023), yang memerlukan istirahat dan fisioterapi; kedua dalam sebuah pertandingan sekitar 2023-10-13.", "owner": "user", "tags": ["health", "basketball", "injury"]}
]
```
Jenis peristiwa yang sama terjadi lagi → UPDATE memori lama. Nyatakan **hitungannya** dan daftarkan setiap kejadian beserta waktunya bila diketahui. JANGAN melakukan ADD fakta kedua "John mencederai pergelangan kakinya", dan JANGAN melakukan SUPERSEDE (cedera pertama tidak berhenti menjadi benar).
"""



# ================================================================
# Working / coding memory (coding/)
# ================================================================

CODING_EXTRACT_PROMPT = """\
Anda mengekstrak memori KERJA (working memory) yang tahan lama dari satu segmen sesi pelaksanaan tugas.
Sesi "kerja" adalah tugas praktis apa pun di mana pengguna mengoperasikan komputer /
sistem melalui tools — ini mencakup KEDUANYA: pekerjaan perangkat lunak/kode (menulis, debugging,
refactoring, build, deploy, penggunaan API/library) DAN pekerjaan sistem & operasional
(menjalankan perintah shell/PowerShell, analisis atau pembersihan disk/penyimpanan, operasi file
& folder, instalasi/konfigurasi perangkat lunak, penyiapan lingkungan/perangkat,
pemeriksaan kesehatan hardware/disk, backup, penjadwalan/otomasi, pemrosesan data).

Anda menghasilkan DUA lapisan dari segmen yang SAMA:

  EXPERIENCES (prosedural, "bagaimana melakukannya") — metode yang dapat digunakan ulang, diekstrak hanya
  jika lolos AMBANG NILAI di bawah. Satu paket (task, search_keys, solution,
  boundary_envs).

  FACT (episodik, "apa yang terjadi kali ini") — TEPAT SATU catatan tentang sesi
  INI: apa yang diinginkan pengguna, bagaimana pengerjaannya, bagaimana hasilnya. Tidak ada
  ambang nilai, tetapi jumlahnya tidak pernah lebih dari satu.

Kedua lapisan menjawab pertanyaan yang berbeda dan disimpan secara terpisah. Metode
yang dapat digeneralisasi masuk ke EXPERIENCES; cerita sesi khusus ini —
yang hanya berlaku untuk sistem ini / proyek ini / saat ini — masuk ke FACT.

═══════════════════════════════════════════════════════════════════
AMBANG NILAI (HANYA UNTUK EXPERIENCES) — JANGAN dilewati dengan mudah.
═══════════════════════════════════════════════════════════════════
Sebuah memori WAJIB memenuhi SEMUA hal berikut:
  (a) Menyelesaikan masalah yang kemungkinan besar akan berulang bagi pengguna yang sama
  (b) Solusinya tidak sepele: menggabungkan beberapa informasi / langkah, ATAU
      membawa alasan keputusan, ATAU menangkap solusi pengganti yang didapat dengan susah payah, ATAU merupakan
      prosedur/resep yang dapat digunakan ulang untuk tugas operasional (mis. cara menganalisis &
      membersihkan penggunaan disk, cara mengatur file secara massal, cara memeriksa kesehatan
      hardware, cara menyiapkan job terjadwal)
  (c) Agent / pengguna di masa mendatang lebih suka mencarinya daripada menemukannya ulang

PENTING — tugas analisis / diagnostik / investigasi TETAP DIHITUNG:
Sebuah tugas TIDAK harus membuat perubahan atau "memperbaiki" sesuatu agar layak diingat.
Pekerjaan read-only / analitis / troubleshooting bernilai ketika PROSEDUR atau
KESIMPULANNYA dapat digunakan ulang. Secara eksplisit ekstrak:
  - Resep analisis / diagnostik: prosedur yang dapat digunakan ulang + apa yang diungkapnya
    (mis. "cara menganalisis folder mana yang menghabiskan ruang drive C" — perintah pemindaian,
    jebakan yang ditemui, dan temuannya). "Hanya menghasilkan laporan, tidak ada perubahan"
    BUKAN alasan untuk melewatkannya — METODE yang dapat digunakan ulang itulah memorinya.
  - Hasil investigasi, TERMASUK hasil negatif / tidak ditemukan
    (mis. "mencari folder X di seluruh C: dan D: dengan rg / fs_search / indeks SQL —
    tidak ditemukan di mana pun"). Jalan buntu yang tercatat menyelamatkan agent berikutnya dari mengulang
    pencarian sia-sia yang sama, sehingga lolos ambang.
JANGAN menolak sebuah memori hanya karena "asisten bisa membuat ulang
perintahnya" atau "tidak ada yang benar-benar diubah". Jika prosedur atau kesimpulannya
akan menghemat upaya di masa mendatang, simpan.

JANGAN ekstrak:
  - Operasi satu langkah yang benar-benar sepele tanpa prosedur yang dapat digunakan ulang ("mengganti nama satu file",
    "git status", "menjalankan ls sekali")
  - Masalah sekali-jadi yang sudah terselesaikan tanpa pelajaran yang dapat dialihkan
  - Informasi yang sudah ada di repo/sistem (jangan menduplikasi package.json)
  - Tugas sekali pakai per-PR / per-sesi kecuali mengandung pola yang dapat digunakan ulang
  - Tindakan MEMINTA informasi yang kurang KEPADA PENGGUNA. Meminta parameter,
    mendaftar apa yang belum diketahui, atau menggali field yang diperlukan adalah perilaku
    percakapan, bukan metode: setiap agent sudah tahu cara mengajukan pertanyaan, sehingga
    tidak ada yang perlu dicari nanti. Jangan pernah mengeluarkan experience yang task atau
    search_keys-nya berbentuk seperti "cara meminta X", "mengumpulkan Y yang kurang",
    "menggali field yang diperlukan", "apa yang perlu diklarifikasi sebelum mulai". Perhatikan bahwa ini
    hanya dikecualikan dari EXPERIENCES — bahwa sesi berakhir dengan menunggu pengguna
    adalah keadaan nyata dan termasuk dalam FACT.

Jika segmen tidak membuat kemajuan substantif karena terhenti menunggu
pengguna — asisten hanya meminta parameter, atau terhambat sebelum melakukan
pekerjaan nyata apa pun — keluarkan "experiences": [] dan tulis FACT saja. Belum ada
yang dipelajari, sehingga tidak ada metode untuk disimpan. Ini lebih penting daripada kelihatannya:
experience yang disaring dari kondisi terhenti akan terbaca sebagai keterampilan yang sah saat retrieval,
dan segmen berikutnya dari tugas yang sama akan mencocokkannya lalu bertanya lagi kepada pengguna
alih-alih melanjutkan, sehingga kondisi terhenti itu terulang sepanjang sisa
sesi. Nilai substansi berdasarkan apa yang benar-benar dilakukan oleh tools, bukan dari apakah
giliran tersebut menghasilkan teks yang lancar.

Heuristik: jika pengguna (atau agent di masa mendatang yang mengerjakan jenis tugas yang sama) ingin
hal ini dituliskan alih-alih mencari tahu lagi, simpan. Prosedur operasional
multi-langkah (bahkan prosedur sistem/ops atau analisis/pencarian read-only, bukan
hanya kode) biasanya memenuhi syarat. Buang hanya jika berupa satu perintah yang dapat ditemukan pengguna mana pun
melalui `--help` dalam 30 detik DAN tidak membawa keputusan/jebakan yang layak
diingat.

═══════════════════════════════════════════════════════════════════
FIELD EXPERIENCE
═══════════════════════════════════════════════════════════════════

task          — Nama berbentuk kueri; rumuskan seperti cara pengguna kelak BERTANYA.
                "memublikasikan rilis ke platform xx" bukan
                "publish_release_internal"
                Dapat berdiri sendiri (tanpa "ini", "file tersebut", "kita").

search_keys   — 0 hingga 5 rumusan alternatif yang menangkap aspek penting dari
                solution yang tidak akan cocok dengan `task` saja. Rumuskan sebagai pertanyaan
                atau topik yang akan DITANYAKAN pengguna di masa mendatang. Berbeda dari task.
                Hilangkan jika task sudah mencakup semuanya.

solution      — Konten lengkap dan dapat berdiri sendiri yang benar-benar dibutuhkan pengguna:
                perintah, lokasi kredensial (BUKAN NILAI kredensial),
                path file, langkah berurutan, jebakan, alasan keputusan.
                Gabungkan pekerjaan end-to-end multi-giliran menjadi SATU solution jika
                membentuk tugas yang koheren; pecah menjadi beberapa memori hanya jika
                pengguna mengerjakan tugas-tugas yang tidak berkaitan.

boundary_envs — Penetapan konkret yang menjadi sandaran solution, multi-baris "key: value":
                  runtime / SDK / library + versi
                  endpoint API atau signature method
                  versi layanan eksternal / middleware
                  NAMA env var (bukan nilainya)
                  path file konfigurasi
                  pengenal host / cluster / region
                Jika solution tidak bergantung pada lingkungan, biarkan "" (kosong).
                Tujuan: ketika SDK/middleware yang mendasarinya di-upgrade, konsumen
                di masa mendatang dapat memeriksa ini untuk memutuskan apakah solution masih berlaku.

confidence  — 0.0 hingga 1.0
  1.0   pengguna secara eksplisit menyatakan ini sebagai aturan / instruksi
  0.7   disimpulkan dengan bukti yang baik dari tindakan tools
  0.5   disimpulkan secara lemah; satu observasi; tidak pasti

═══════════════════════════════════════════════════════════════════
FIELD FACT — TEPAT SATU per segmen, tanpa ambang nilai
═══════════════════════════════════════════════════════════════════

Anda menulis SATU fact untuk seluruh segmen — catatan keadaan yang dapat dipulihkan untuk sesi
ini, bukan retrospeksi yang dipoles dan tidak pernah berupa daftar temuan terpisah. Seorang
agent di masa mendatang harus dapat melanjutkan dari catatan ini tanpa keliru menganggap saran,
inferensi, atau pencarian parsial sebagai sesuatu yang sudah terverifikasi.

subject    — SIAPA atau APA yang menjadi pokok sesi ini: satu entitas yang menjadi sasaran
             atau penerima pekerjaan. Satu frasa nomina pendek, bukan kalimat, tanpa kata kerja.
             Pilih entitas paling spesifik yang menjadi *pokok* sesi, bukan
             tool yang digunakannya:
               nama seseorang ketika sesi melayani satu orang yang disebut namanya
                 ("Dylan", "Matthew")
               file / modul / simbol ketika pekerjaan menargetkan kode
                 ("auth/middleware.ts", "parse_plan_text")
               layanan / host / perangkat / drive untuk pekerjaan ops
                 ("D:\\WXWork", "redis-cache-prod", "ci.yml")
             Ini adalah KUNCI KERAS, bukan prosa: dua sesi tentang orang yang berbeda
             atau file yang berbeda WAJIB mendapat subject yang berbeda, dan dua sesi
             tentang hal yang sama WAJIB mendapat string yang sama, dieja identik.
             Utamakan pengenal persis seperti yang muncul di trace daripada
             deskripsinya. Jika segmen benar-benar tidak memiliki satu subjek,
             keluarkan "".

             Mengapa ini penting: subject adalah gerbang yang menentukan apakah sesi
             berikutnya boleh menimpa sesi ini sebagai "tugas yang sama,
             dilanjutkan". Jika salah, keadaan satu orang diam-diam menggantikan
             keadaan orang lain.

statement  — Catatan ringkas tentang sesi INI. Dua bagian, dan bagian kedua adalah
             yang sering hilang.
             Pertahankan data dasar (givens) lingkup tugas dan batasan konkret alih-alih
             menggantinya dengan evaluasi umum atas hasilnya. Nyatakan
             setiap aturan satu kali: teks instruksi yang berulang bersaing dengan data
             yang harus disimpan oleh memori.

             BAGIAN 1 — apa yang terjadi, sekitar 300 karakter latin (~150 CJK).
             Mulai dengan tujuan dan objek yang spesifik ("mengosongkan ruang disk yang dipakai
             oleh D:\\WXWork", "menemukan folder new_file_search"), bukan "memperbaiki bug" /
             "mencari file" yang generik. Lalu kurang lebih apa yang diperiksa, dicoba,
             atau diubah. Lalu bagaimana akhirnya, dengan makna tepat salah satu dari:
             selesai, selesai sebagian, gagal, terhambat, atau
             masih menunggu verifikasi. Sertakan hasil negatif dan jalan buntu
             tanpa melebih-lebihkannya.

             BAGIAN 2 — DATA DASAR LINGKUP TUGAS: segala hal yang menjadi landasan SELURUH tugas,
             bahkan ketika segmen ini tidak menetapkannya dan bahkan ketika terasa
             terlalu mendasar untuk dinyatakan. Binding (apa arti setiap nama/simbol/label),
             nilai tetap yang diberikan oleh tugas alih-alih diturunkan, konvensi yang dipakai
             (basis indeks, satuan, zona waktu, default), dan
             hasil segmen ini sendiri BESERTA nilainya ("x = 7", bukan "nilainya
             sudah dihitung").

             Tulis BAGIAN 2 sebagai data, satu item per baris, nilai sesuai yang diberikan:
               retry budget = 5; timeout 30s; index starts at 0
               step 3 input: n=64, tolerance 1e-6
               slot B: type=private, cost 688.7-721.3 per unit
             Satu baris seharusnya hampir seluruhnya berisi nilai. Pertahankan notasi trace —
             simbol, satuan, angka, ejaan — dan buang kalimatnya: hapus
             frasa yang sekadar mengumumkan sebuah data dasar alih-alih merupakan data dasar itu sendiri ("saya
             lebih suka", "seharusnya berada di antara … dan …"). Frasa itu berulang sekali
             per item, sehingga daftar 15 item membayarnya 15 kali.
             Bawa data dasar ini pada SETIAP fact; jangan pernah menulis "seperti di atas" atau "sama seperti
             sesi sebelumnya" — retrieval mengembalikan segelintir fact berdasarkan
             kemiripan dan rujukannya mungkin tidak termasuk di antaranya.

             Usahakan total di bawah 1200 karakter. Jika BAGIAN 2 tidak muat, perbaiki
             notasinya, jangan cakupannya: statement adalah vektor retrieval SEKALIGUS
             satu-satunya field yang dapat dibaca segmen berikutnya (details tidak di-embed
             maupun disisipkan), sehingga apa pun yang dipotong darinya akan hilang. Padatkan susunan kata
             di sekitar data dasar; jangan pernah data dasarnya.

             Ujiannya: jika segmen berikutnya tidak dapat melihat materi aslinya, dapatkah
             ia melanjutkan hanya dari catatan ini? Hanya jawaban ya yang dihitung. Binding yang hilang
             tidak gagal secara kentara — namanya tetap terlihat bermakna, sehingga
             dibaca dengan makna umumnya dan jawaban yang
             mengikutinya lancar tetapi tentang pertanyaan yang berbeda.

             BAHASA — sama dengan bahasa trace sesi: statement adalah
             vektor retrieval dan kueri datang dalam bahasa kerja pengguna,
             sehingga bahasa lain menanggung penalti lintas bahasa pada setiap pencarian.

             KESETIAAN BUKTI — catat hanya tindakan yang benar-benar dijalankan dan
             bukti yang secara eksplisit ada di trace. Pertahankan
             perintah, flag, argumen, error, dan angka yang material secara verbatim.
             JANGAN PERNAH melengkapi perintah dengan flag yang lazim, dan jangan pernah mengubah
             saran atau rencana menjadi tindakan:
               "menjalankan Get-ChildItem -Force" → "menjalankan Get-ChildItem"
                 (trace tidak mengandung `-Force`; jangan mengarangnya)
             Batasi setiap temuan negatif pada apa yang benar-benar dicari, dan
             sebutkan SETIAP batas cakupan eksplisit yang dapat membatalkannya,
             terutama kegagalan izin dan batas kedalaman: pertahankan root,
             filter, dan kedalaman yang berlaku ("tidak ditemukan di bawah C:\\ dan
             D:\\ pada kedalaman 3; beberapa direktori tidak tercakup karena Access
             denied"), jangan pernah memperluasnya menjadi "tidak ada". Jangan menghilangkan satu batas pun
             agar catatan terlihat lebih rapi.
             Jangan pernah menaikkan pencarian parsial menjadi ketiadaan global.
             Upaya yang terhambat tetap mencantumkan penghambatnya.
             Pertahankan perbedaan antara dicoba, diamati, diverifikasi,
             disimpulkan, dan belum terselesaikan — ungkapkan secara alami ("mencoba … tetapi
             terhambat oleh Access denied", "diduga …, belum diverifikasi"), bukan sebagai
             label. Tanpa keluaran tool atau konfirmasi eksplisit, JANGAN PERNAH mengklaim
             perbaikan berhasil, penyebab telah ditetapkan, atau suatu path tidak ada.

             JANGAN catat sapaan atau obrolan murni — itulah satu-satunya kasus
             yang mendapat "fact": null. Segmen yang hanya menyatakan kebutuhan, atau
             terhenti sebelum melakukan pekerjaan, tetap menetapkan sesuatu.

details    — KELUARAN konkret sesi, disalin secara verbatim. `statement` adalah
             ringkasan dan ringkasan pasti membuang nilai-nilai individual;
             pencarian di kemudian hari hampir selalu menginginkan satu nilai spesifik, bukan intinya.
             Jadi hasil apa pun yang dapat dirujuk yang dihasilkan sesi ini, reproduksi
             di sini dalam bentuk dan struktur aslinya:
               rencana / jadwal / itinerary → pertahankan setiap baris, setiap hari, setiap
                 slot, dan setiap harga persis seperti yang tertulis
               blok konfigurasi atau kode → pertahankan bloknya
               keluaran material dari sebuah perintah → pertahankan baris-baris yang membawa
                 temuan (ukuran, jumlah, versi, teks error)
               hasil terstruktur (tabel, daftar record) → pertahankan baris-barisnya
             Pertahankan tata letak aslinya — jeda baris, prefiks `Day 1:` / key: value,
             satuan, simbol mata uang, id. Jangan meringkas ulang, jangan
             mengubah urutan, jangan membulatkan angka, jangan menerjemahkan.

             Salin HANYA apa yang ada di trace. Jangan pernah mengisi kekosongan: jika sebuah slot
             dibiarkan kosong, biarkan kosong atau katakan demikian — nilai yang dikarang
             lebih buruk daripada nilai yang hilang, karena akan ditemukan kembali dan digunakan.
             Batasi sekitar 4000 karakter; jika keluarannya lebih panjang, pertahankan bagian yang
             membawa nilai konkret dan buang prosa dekoratif, sambil mencatat apa yang
             dibuang.

             "" ketika sesi tidak menghasilkan artefak semacam itu (investigasi murni
             yang belum ada temuannya, atau kondisi terhenti menunggu pengguna).

             Field ini BUKAN bagian dari vektor retrieval — `statement` dan
             `subject` yang melakukan pencarian, `details` memberikan nilainya setelah ditemukan.
             Jadi jangan menulis umpan retrieval di sini, dan jangan menduplikasi
             ringkasan; tulis materi mentahnya.

files      — 0..N path FILE ATAU DIREKTORI absolut atau relatif-repo yang benar-benar
             dioperasikan atau diinvestigasi selama sesi ini. Pertahankan root yang diinvestigasi
             seperti `C:\\` dan `D:\\`. Hanya sertakan path yang
             benar-benar diakses/dicari, bukan path yang sekadar disarankan atau direncanakan;
             [] jika tidak ada.
═══════════════════════════════════════════════════════════════════
KONTEKS HOST (diberikan oleh SDK)
═══════════════════════════════════════════════════════════════════
- workspace_id ada: {workspace_present}
- branch ada: {branch_present}

Ketika workspace_id tidak ada, solution tidak boleh bergantung pada konteks
lokal proyek yang tidak dinyatakan — tuliskan repo / path / layanan apa pun yang dibutuhkan di dalam
`solution` atau `boundary_envs` agar memori tetap dapat digunakan secara mandiri.

═══════════════════════════════════════════════════════════════════
PERCAKAPAN
═══════════════════════════════════════════════════════════════════
{conversation_block}

═══════════════════════════════════════════════════════════════════
KELUARAN (hanya JSON yang ketat, tanpa markdown)
═══════════════════════════════════════════════════════════════════
{{
  "experiences": [
    {{
      "task": "...",
      "search_keys": ["...", "..."],
      "solution": "...",
      "boundary_envs": "...",
      "confidence": 0.9
    }}
  ],
  "fact": {{
    "subject": "...",
    "statement": "...",
    "files": ["..."],
    "details": "..."
  }}
}}

DEFAULT-NYA ADALAH MENGEKSTRAK. Ketika segmen melakukan pekerjaan nyata, Anda WAJIB mengeluarkan setidaknya
satu experience — bahkan jika tugasnya terlihat sederhana atau berhasil sekali jalan, tetap
tangkap esensi yang dapat digunakan ulang dari apa yang dilakukan (metode / perintah / cara melakukannya).
Ketika sesinya tipis, satu experience yang paling bernilai sudah cukup.

SATU-SATUNYA pengecualian adalah kasus terhenti di atas: jika segmen tidak melakukan pekerjaan nyata
karena terhambat meminta informasi yang kurang kepada pengguna, keluarkan
"experiences": [] dan tulis hanya fact. Jangan membuat-buat experience untuk
memenuhi instruksi ini — experience rekaan ("cara menanyakan kota
keberangkatan") lebih buruk daripada tidak ada, karena kelak akan ditemukan kembali dan diikuti. Kosong
diperbolehkan HANYA karena alasan itu, tidak pernah sebagai jalan pintas ketika pekerjaan benar-benar dilakukan.

"fact" adalah SATU objek, tidak pernah berupa daftar — satu catatan per sesi.

SELALU tulis fact. Ambang untuk fact bukan "apakah ini bernilai" melainkan "apakah
ada sesuatu yang terjadi", dan segmen yang sampai kepada Anda sudah lolos ambang itu.
Ini termasuk segmen yang tampak seperti persiapan alih-alih pekerjaan: satu
pesan pengguna yang menyatakan kebutuhan, parameter, atau keadaan awal, tanpa
tools yang dijalankan, justru merupakan materi yang tidak dapat direkonstruksi sendiri oleh sesi berikutnya
— tuliskan kebutuhan, parameter, dan keadaan awal beserta situasinya ke dalam `statement` (dengan notasi ringkas, jangan menyalin kalimat asli percakapan),
jangan melemparnya ke `details` (`details` tidak masuk ke vektor retrieval, dan sisi pembaca juga tidak lagi menyisipkannya). Gunakan
"fact": null HANYA untuk sapaan dan obrolan murni tanpa konten sama sekali.
Jika ragu, tulis fact: catatan yang tidak diperlukan hanya memakan satu baris, sedangkan catatan yang dibuang
merugikan setiap sesi berikutnya yang membutuhkannya dan tidak punya cara untuk mengetahui bahwa catatan itu pernah ada.
"""

CODING_EXTRACT_PROMPT_CODING = """\
╔═══════════════════════════════════════════════════════════════════════════╗
║ BATASAN MUTLAK — baca ini sebelum yang lain, batasan ini mengesampingkan   ║
║ rumusan apa pun di bagian selanjutnya prompt ini yang terkesan meminta     ║
║ narasi.                                                                   ║
║                                                                           ║
║ Anda TIDAK sedang menulis laporan tentang apa yang dilakukan seseorang.    ║
║ Anda sedang menulis apa yang dibutuhkan AGENT DI MASA MENDATANG untuk      ║
║ bertindak. Agent itu sudah punya tugasnya sendiri; ia tidak peduli apa     ║
║ yang diinginkan pengguna ini. Ia perlu tahu: apa yang diputuskan di sini,  ║
║ pada koordinat kode mana, apa yang dicoba dan ditolak, dan apakah sudah    ║
║ diverifikasi.                                                             ║
║                                                                           ║
║ Secara konkret, di setiap `statement` dan setiap `solution`:               ║
║   · Subjek gramatikalnya adalah KODE atau KEPUTUSAN — tidak pernah         ║
║     "pengguna" dan tidak pernah "agent". Jangan menulis "Pengguna mencoba…",║
║     "Pengguna ingin…", "Agent kemudian memeriksa…".                       ║
║   · Jangan pernah menutup dengan frasa administratif seperti "sesi telah   ║
║     selesai", "error telah dicatat", "belum ditemukan solusi". Tutup        ║
║     dengan keputusan dan status verifikasinya. Jika tidak ada yang         ║
║     diputuskan, tutup dengan apa yang SUDAH DISINGKIRKAN dan petunjuk      ║
║     berikutnya yang belum dijelajahi — itulah bagian yang dapat digunakan  ║
║     ulang.                                                                ║
║   · Pertahankan gejala konkretnya (string error, perintah yang gagal,      ║
║     keluaran yang salah) — itulah yang akan dicocokkan oleh pencarian      ║
║     di masa mendatang.                                                    ║
║                                                                           ║
║ buruk: "Pengguna mencoba menulis QTable ke RST dengan header_rows dan      ║
║        mendapat TypeError. Sesi selesai dengan error dicatat."            ║
║ baik:  "Writer RST ascii.rst menolak header_rows: `tbl.write(..., \\        ║
║        format='ascii.rst', header_rows=['name','unit'])` memunculkan        ║
║        TypeError karena RST.__init__ tidak meneruskannya, berbeda dengan    ║
║        ascii.fixed_width yang menerimanya. Belum ada perbaikan — writer     ║
║        ini membutuhkan penanganan header_rows yang sama seperti            ║
║        fixed_width."                                                      ║
╚═══════════════════════════════════════════════════════════════════════════╝

Anda mengekstrak memori KERJA (working memory) yang tahan lama dari satu segmen sesi pelaksanaan tugas.
Sesi "kerja" adalah tugas praktis apa pun di mana pengguna mengoperasikan komputer /
sistem melalui tools — ini mencakup KEDUANYA: pekerjaan perangkat lunak/kode (menulis, debugging,
refactoring, build, deploy, penggunaan API/library) DAN pekerjaan sistem & operasional
(menjalankan perintah shell/PowerShell, analisis atau pembersihan disk/penyimpanan, operasi file
& folder, instalasi/konfigurasi perangkat lunak, penyiapan lingkungan/perangkat,
pemeriksaan kesehatan hardware/disk, backup, penjadwalan/otomasi, pemrosesan data).

Anda menghasilkan DUA lapisan dari segmen yang SAMA:

  EXPERIENCES (prosedural, "bagaimana melakukannya") — metode yang dapat digunakan ulang, diekstrak hanya
  jika lolos AMBANG NILAI di bawah. Satu paket (task, search_keys, solution,
  boundary_envs).

  FACT (episodik, "apa yang ditetapkan kali ini") — TEPAT SATU catatan tentang sesi
  INI. Ini BUKAN narasi tentang apa yang dilakukan pengguna. Catatan ini merekam, dengan urutan berikut:
  tugas dan gejala konkretnya (inilah yang membuatnya dapat ditemukan nanti), koordinat
  kode, APA YANG DITETAPKAN dan apa yang ditolak, serta sejauh mana hal itu
  diverifikasi. Tidak ada ambang nilai, tetapi jumlahnya tidak pernah lebih dari satu.

Kedua lapisan menjawab pertanyaan yang berbeda dan disimpan secara terpisah. Metode
yang dapat digeneralisasi masuk ke EXPERIENCES; cerita sesi khusus ini —
yang hanya berlaku untuk sistem ini / proyek ini / saat ini — masuk ke FACT.

═══════════════════════════════════════════════════════════════════
AMBANG NILAI (HANYA UNTUK EXPERIENCES) — JANGAN dilewati dengan mudah.
═══════════════════════════════════════════════════════════════════
Sebuah memori WAJIB memenuhi SEMUA hal berikut:
  (a) Menyelesaikan masalah yang kemungkinan besar akan berulang bagi pengguna yang sama
  (b) Solusinya tidak sepele: menggabungkan beberapa informasi / langkah, ATAU
      membawa alasan keputusan, ATAU menangkap solusi pengganti yang didapat dengan susah payah, ATAU merupakan
      prosedur/resep yang dapat digunakan ulang untuk tugas operasional (mis. cara menganalisis &
      membersihkan penggunaan disk, cara mengatur file secara massal, cara memeriksa kesehatan
      hardware, cara menyiapkan job terjadwal)
  (c) Agent / pengguna di masa mendatang lebih suka mencarinya daripada menemukannya ulang

PENTING — tugas analisis / diagnostik / investigasi TETAP DIHITUNG:
Sebuah tugas TIDAK harus membuat perubahan atau "memperbaiki" sesuatu agar layak diingat.
Pekerjaan read-only / analitis / troubleshooting bernilai ketika PROSEDUR atau
KESIMPULANNYA dapat digunakan ulang. Secara eksplisit ekstrak:
  - Resep analisis / diagnostik: prosedur yang dapat digunakan ulang + apa yang diungkapnya
    (mis. "cara menganalisis folder mana yang menghabiskan ruang drive C" — perintah pemindaian,
    jebakan yang ditemui, dan temuannya). "Hanya menghasilkan laporan, tidak ada perubahan"
    BUKAN alasan untuk melewatkannya — METODE yang dapat digunakan ulang itulah memorinya.
  - Hasil investigasi, TERMASUK hasil negatif / tidak ditemukan
    (mis. "mencari folder X di seluruh C: dan D: dengan rg / fs_search / indeks SQL —
    tidak ditemukan di mana pun"). Jalan buntu yang tercatat menyelamatkan agent berikutnya dari mengulang
    pencarian sia-sia yang sama, sehingga lolos ambang.
JANGAN menolak sebuah memori hanya karena "asisten bisa membuat ulang
perintahnya" atau "tidak ada yang benar-benar diubah". Jika prosedur atau kesimpulannya
akan menghemat upaya di masa mendatang, simpan.

JANGAN ekstrak:
  - Operasi satu langkah yang benar-benar sepele tanpa prosedur yang dapat digunakan ulang ("mengganti nama satu file",
    "git status", "menjalankan ls sekali")
  - Masalah sekali-jadi yang sudah terselesaikan tanpa pelajaran yang dapat dialihkan
  - Informasi yang sudah ada di repo/sistem (jangan menduplikasi package.json)
  - Tugas sekali pakai per-PR / per-sesi kecuali mengandung pola yang dapat digunakan ulang

Heuristik: jika pengguna (atau agent di masa mendatang yang mengerjakan jenis tugas yang sama) ingin
hal ini dituliskan alih-alih mencari tahu lagi, simpan. Prosedur operasional
multi-langkah (bahkan prosedur sistem/ops atau analisis/pencarian read-only, bukan
hanya kode) biasanya memenuhi syarat. Buang hanya jika berupa satu perintah yang dapat ditemukan pengguna mana pun
melalui `--help` dalam 30 detik DAN tidak membawa keputusan/jebakan yang layak
diingat.

═══════════════════════════════════════════════════════════════════
PEKERJAAN KODE — apa yang tidak dapat diturunkan ulang oleh agent di masa mendatang, sehingga WAJIB ditangkap
═══════════════════════════════════════════════════════════════════
Ketika segmen berupa pekerjaan perangkat lunak pada sebuah repositori, metode yang dapat digunakan ulang hanyalah
separuhnya. Separuh lainnya adalah DI MANA pekerjaan itu berada dan VERSI MANA yang menang. Hal-hal itu
tidak dapat dipulihkan dengan membaca kode di kemudian hari — kode menunjukkan keadaan saat ini,
bukan di branch mana ia berada, atau apa yang sudah ditolak.

Bagian ini TIDAK meminta catatan yang lebih panjang. Bagian ini meminta Anda MEMBELANJAKAN ANGGARAN
YANG SAMA untuk token yang bernilai lebih tinggi: buang tujuan yang diulang, narasi tentang apa yang
asisten "kemudian putuskan untuk", dan kode tempelan yang hanya Anda gemakan; belanjakan
karakter-karakter itu untuk empat butir di bawah ini. Catatan yang menghilangkan branch tetapi
mengutip 30 baris diff adalah pertukaran yang buruk.

1. BRANCH / LOKASI KERJA — verbatim, di `statement` fact. Jika segmen
   menyebutkan atau melakukan checkout `feat/x` / `fix/y`, token itu WAJIB muncul. Agent
   yang melanjutkan di branch yang salah menghasilkan perubahan yang diam-diam terbuang,
   sehingga satu token ini mengungguli setiap detail lainnya.

2. VERSI MANA YANG MENANG — ketika pengguna berubah pikiran, sebutkan pemenangnya dan tandai
   yang kalah dalam satu klausa: "ditetapkan A; B versi sementara dibuang". Tidak keduanya
   secara lengkap — satu klausa. Hanya menyimpan yang terbaru itu salah: agent yang menjumpai
   kode yang ditolak harus mengenalinya, atau ia akan "memperbaiki" kode kembali ke varian yang dibuang.
   Frasa seperti `scratch that / go back to / actually, let's / no, not that` (atau padanannya, seperti
   "batal yang tadi / balik ke / sebenarnya, ayo / bukan, bukan itu")
   menandai hal ini: yang mengikutinya adalah yang berlaku, yang mendahuluinya sudah digantikan.

3. KOORDINAT PERSIS, BUKAN KODE TEMPELAN — path relatif-repo
   (`qutebrowser/utils/debug.py`, jangan pernah sekadar `debug.py`) ditambah fungsi /
   class yang disentuh. Jika sebuah signature berubah, berikan signature yang baru. Jika sebuah konstanta,
   regex, atau literal diputuskan, kutip ITU secara verbatim — tetapi kutip hanya itu, bukan
   blok di sekitarnya. Satu baris yang akurat mengalahkan satu paragraf perkiraan.

4. DIVERIFIKASI vs SEKADAR DITULIS — beberapa kata sudah cukup ("tes dijalankan dan lulus" /
   "dikonfirmasi hanya lewat git diff, tes tidak dijalankan"). "perubahan sudah masuk" dan "perubahan sudah masuk
   dan sudah dites" membuat agent di masa mendatang melakukan hal yang sangat berbeda berikutnya.

Anti-pola — meringkas tujuan dan membuang koordinatnya:
  buruk: "merefaktor logika parsing warna agar lebih rapi."
  baik:  "di feat/q4, pada qutebrowser/config/configtypes.py QtColor._parse_value:
         ditetapkan `mult = 360.0 if kind == 'h' else 255.0`; versi sementara yang
         memakai 255.0 untuk semuanya dibuang. Tes tidak dijalankan."
  (yang baik LEBIH PENDEK daripada kebanyakan yang buruk — ia hanya membelanjakan setiap karakter
   untuk sesuatu yang tidak dapat diturunkan ulang)

═══════════════════════════════════════════════════════════════════
FIELD EXPERIENCE
═══════════════════════════════════════════════════════════════════

task          — Nama berbentuk kueri; rumuskan seperti cara pengguna kelak BERTANYA.
                "memublikasikan rilis ke platform xx", bukan "publish_release_internal"
                Dapat berdiri sendiri (tanpa "ini", "file tersebut", "kita").

search_keys   — 0 hingga 5 rumusan alternatif yang menangkap aspek penting dari
                solution yang tidak akan cocok dengan `task` saja. Rumuskan sebagai pertanyaan
                atau topik yang akan DITANYAKAN pengguna di masa mendatang. Berbeda dari task.
                Hilangkan jika task sudah mencakup semuanya.

solution      — Konten lengkap dan dapat berdiri sendiri yang benar-benar dibutuhkan pengguna:
                perintah, lokasi kredensial (BUKAN NILAI kredensial),
                path file, langkah berurutan, jebakan, alasan keputusan.
                Gabungkan pekerjaan end-to-end multi-giliran menjadi SATU solution jika
                membentuk tugas yang koheren; pecah menjadi beberapa memori hanya jika
                pengguna mengerjakan tugas-tugas yang tidak berkaitan.

boundary_envs — Penetapan konkret yang menjadi sandaran solution, multi-baris "key: value":
                  runtime / SDK / library + versi
                  endpoint API atau signature method
                  versi layanan eksternal / middleware
                  NAMA env var (bukan nilainya)
                  path file konfigurasi
                  pengenal host / cluster / region
                Jika solution tidak bergantung pada lingkungan, biarkan "" (kosong).
                Tujuan: ketika SDK/middleware yang mendasarinya di-upgrade, konsumen
                di masa mendatang dapat memeriksa ini untuk memutuskan apakah solution masih berlaku.

confidence  — 0.0 hingga 1.0
  1.0   pengguna secara eksplisit menyatakan ini sebagai aturan / instruksi
  0.7   disimpulkan dengan bukti yang baik dari tindakan tools
  0.5   disimpulkan secara lemah; satu observasi; tidak pasti

═══════════════════════════════════════════════════════════════════
FIELD FACT — TEPAT SATU per segmen, tanpa ambang nilai
═══════════════════════════════════════════════════════════════════

Anda menulis SATU fact untuk seluruh segmen — catatan keadaan yang dapat dipulihkan untuk sesi
ini, bukan retrospeksi yang dipoles dan tidak pernah berupa daftar temuan terpisah. Seorang
agent di masa mendatang harus dapat melanjutkan dari catatan ini tanpa keliru menganggap saran,
inferensi, atau pencarian parsial sebagai sesuatu yang sudah terverifikasi.

statement  — Ringkasan padat tentang sesi INI. Ia memiliki dua tugas sekaligus, dan ia
             harus menjalankan KEDUANYA: ia adalah vektor retrieval (agent di masa mendatang menemukannya dengan
             mencari menggunakan laporan masalah baru, yang dirumuskan seperti cara pengguna merumuskan
             masalah), dan ia adalah muatan yang kemudian ditindaklanjuti oleh agent tersebut. Jadi:

             BUKA dengan tugas dan gejalanya, dengan kata-kata yang akan dipakai pengguna.
                Inilah yang membuatnya dapat ditemukan — jaga bentuk permukaannya tetap dekat dengan cara
                masalah itu akan dilaporkan. Sebutkan tujuan dan objek yang spesifik
                ("mengosongkan ruang disk di bawah D:\\WXWork", "menyesuaikan pengali hue
                di QtColor._parse_value pada feat/q4", "menemukan folder
                new_file_search"), jangan pernah "memperbaiki bug" / "mencari file" yang generik. Sertakan
                gejala konkretnya: string error, perintah yang gagal,
                keluaran yang salah.

             LALU berikan koordinat dan keputusannya.
                path relatif-repo, simbol, dan branch untuk pekerjaan kode. Nyatakan apa yang
                DITETAPKAN, dan dalam klausa yang sama tandai apa yang dibuang
                ("ditetapkan X; Y versi sementara dibuang"). Inilah bagian yang
                tidak dapat diturunkan ulang dengan membaca kode — kode hanya menunjukkan
                pemenangnya, tidak pernah bahwa Y pernah dicoba dan ditolak. Agent yang menjumpai
                varian yang ditolak di kemudian hari harus dapat mengenalinya, atau ia akan
                "memperbaiki" kode kembali ke versi yang dibuang.

             TUTUP dengan status buktinya.
                Tepat salah satu dari: selesai, selesai sebagian, gagal, terhambat, atau
                masih menunggu verifikasi — ditambah cara pemeriksaannya ("tes dijalankan dan
                lulus" / "dikonfirmasi hanya lewat git diff, tes tidak dijalankan"). Sertakan
                hasil negatif dan jalan buntu tanpa melebih-lebihkannya.

             ⚠️ Jangan pernah berhenti pada narasi. "Pengguna mencoba X dan mendapat error Y; belum
             ditemukan solusi" adalah catatan yang sia-sia: ia mudah ditemukan tetapi tidak memberi
             agent di masa mendatang apa pun untuk ditindaklanjuti. Jika sesi benar-benar tidak mencapai
             keputusan, sebutkan apa yang sudah disingkirkan dan apa petunjuk berikutnya yang belum dijelajahi —
             itulah bagian yang dapat digunakan ulang.

             Subjek kalimat-kalimatnya seharusnya kode dan keputusan, bukan
             "pengguna". 2 hingga 4 kalimat, prosa yang mengalir, bukan bagian berlabel.
             Batas keras ~400 karakter latin (~200 karakter CJK).

             KESETIAAN BUKTI — wajib:
             - Catat hanya tindakan yang benar-benar dijalankan dan bukti yang secara eksplisit
               ada di trace. Pertahankan perintah, flag,
               argumen, error, dan angka yang material secara verbatim. JANGAN PERNAH melengkapi
               perintah dengan flag yang lazim atau mengubah saran/rencana menjadi tindakan.
             - Pertahankan perbedaan antara dicoba, diamati, diverifikasi,
               disimpulkan, dan belum terselesaikan. Jangan menambahkan label-label itu ke dalam prosa;
               ungkapkan maknanya secara alami, mis. "mencoba … tetapi terhambat oleh
               Access denied" atau "diduga … , belum diverifikasi".
             - Tanpa keluaran tool atau konfirmasi eksplisit, JANGAN PERNAH mengklaim perbaikan
               berhasil, penyebab telah ditetapkan, sebuah file/folder tidak
               ada, atau tes lulus.
             - Batasi setiap temuan negatif pada apa yang benar-benar dicari dan
               sebutkan SETIAP batas cakupan eksplisit yang dapat membatalkannya,
               terutama kegagalan izin dan kedalaman pencarian maksimum:
               "tidak ditemukan di bagian C:\\ dan D:\\ yang telah dicari; beberapa
               direktori dilewati karena Access denied, dan pencarian juga
               dibatasi kedalamannya". Jangan menghilangkan satu batas pun agar
               ringkasan terlihat lebih rapi. Jangan pernah menaikkan pencarian parsial menjadi
               ketiadaan global.
             - Nyatakan kausalitas sebagai pasti hanya ketika trace secara langsung memverifikasinya.
               Jika tidak, pertahankan pembatasnya ("diduga" / "disimpulkan" / "belum
               diverifikasi"); jangan menulis ulang
               asumsi tes yang keliru sebagai akar penyebab sistem.

             KRITIS — teks ini ADALAH vektor retrieval, dan satu-satunya
             hal yang dicocokkan dengan kueri di masa mendatang. Ringkasan generik
             tidak berguna, karena SETIAP sesi tampak seperti ringkasan generik: ia
             akan cocok dengan setiap kueri dan tidak menjawab satu pun. Jadi Anda WAJIB menyebutkan
             jangkar konkret yang membuat sesi INI dapat ditemukan — path file
             yang nyata, nama fungsi / simbol, nama branch, perintah, string
             error, versi, key dan nilai konfigurasi, nama host / layanan.
             Jika seseorang kelak mencari salah satu detail spesifik itu,
             ringkasan inilah yang harus kembali.
               baik:  "memperbaiki `npm run build` yang gagal di CI. Membaca
                      .github/workflows/ci.yml dan package.json; menelusurinya ke webpack
                      5.74 yang terkena ERR_OSSL_EVP_UNSUPPORTED di node 18. Menaikkan
                      node pada workflow ke 20 membuat build lulus; node 18 masih
                      mereproduksi error secara lokal."
               baik:  "di branch feat/q0, pada qutebrowser/utils/debug.py signal_name:
                      PyQt<5.11 hanya mengekspos nama melalui repr(sig), dan repr muncul dalam
                      bentuk berkurung siku maupun berkurung biasa. Ditetapkan dua pola
                      yang dicoba berurutan dengan fullmatch, berhenti pada kecocokan pertama; versi
                      satu-regex dibuang. Dikonfirmasi lewat git diff, tes tidak
                      dijalankan."
               buruk: "pengguna ingin bug build diperbaiki, agent melihat beberapa file
                      konfigurasi, mengubah sebagian kode, dan memperbaikinya."
                      (tanpa path, tanpa perintah, tanpa string error, tanpa versi — tidak ada
                       yang bisa dijadikan dasar retrieval; setiap sesi terbaca persis seperti ini)
               buruk: "memperbaiki masalah kompatibilitas parsing nama sinyal di sebuah feature
                      branch."
                      (branch yang mana? file yang mana? fungsi yang mana? mana dari dua
                       versi yang bersaing yang menang? tidak ada di sini yang bertahan ketika
                       ditemukan kembali seminggu kemudian)
             Contoh kesetiaan (buruk → baik):
               "menjalankan Get-ChildItem -Force" → "menjalankan Get-ChildItem"
                 (trace tidak mengandung `-Force`; jangan mengarangnya)
               "mengonfirmasi new_file_search tidak ada" →
                 "new_file_search tidak ditemukan di bagian C:\\ dan
                  D:\\ yang telah dicari; beberapa direktori dilewati karena Access denied atau oleh batas
                  kedalaman, sehingga investigasi baru selesai sebagian"
               "payload yang identik menyebabkan error lock palsu" →
                 "skrip verifikasi berasumsi bahwa penulis terakhir selalu rank
                  yang tetap; asumsi itu tidak berlaku, sehingga apakah ada bug lock
                  masih belum diverifikasi"

             JANGAN catat sapaan atau obrolan murni. Jika segmen tidak menetapkan
             apa pun sama sekali, keluarkan "fact": null.

files      — 0..N path FILE ATAU DIREKTORI absolut atau relatif-repo yang benar-benar
             dioperasikan atau diinvestigasi selama sesi ini. Pertahankan root yang diinvestigasi
             seperti `C:\\` dan `D:\\`. Hanya sertakan path yang
             benar-benar diakses/dicari, bukan path yang sekadar disarankan atau direncanakan;
             [] jika tidak ada.

═══════════════════════════════════════════════════════════════════
KONTEKS HOST (diberikan oleh SDK)
═══════════════════════════════════════════════════════════════════
- workspace_id ada: {workspace_present}
- branch ada: {branch_present}

Ketika workspace_id tidak ada, solution tidak boleh bergantung pada konteks
lokal proyek yang tidak dinyatakan — tuliskan repo / path / layanan apa pun yang dibutuhkan di dalam
`solution` atau `boundary_envs` agar memori tetap dapat digunakan secara mandiri.

═══════════════════════════════════════════════════════════════════
PERCAKAPAN
═══════════════════════════════════════════════════════════════════
{conversation_block}

═══════════════════════════════════════════════════════════════════
KELUARAN (hanya JSON yang ketat, tanpa markdown)
═══════════════════════════════════════════════════════════════════
{{
  "experiences": [
    {{
      "task": "...",
      "search_keys": ["...", "..."],
      "solution": "...",
      "boundary_envs": "...",
      "confidence": 0.9
    }}
  ],
  "fact": {{
    "statement": "...",
    "files": ["..."]
  }}
}}

WAJIB: Anda WAJIB mengeluarkan setidaknya SATU experience. JANGAN mengembalikan daftar kosong.
Bahkan jika tugasnya terlihat sederhana atau berhasil sekali jalan, tetap tangkap
esensi yang dapat digunakan ulang dari apa yang dilakukan (metode / perintah / cara melakukannya). Hanya
satu experience yang paling bernilai yang diperlukan ketika sesinya tipis — tetapi jangan pernah
mengeluarkan array "experiences" yang kosong.

"fact" adalah SATU objek, tidak pernah berupa daftar — satu catatan per sesi. Gunakan
"fact": null hanya ketika segmen berupa obrolan murni yang tidak menetapkan apa pun.
"""

CODING_RECONCILE_PROMPT = """\
Anda memutuskan cara menggabungkan draf memori coding yang baru dengan memori pengguna yang sudah ada.

Untuk setiap draf baru, putuskan salah satu dari:
  ADD     — tidak ada memori lama yang menggambarkan tugas/aspek yang sama
  UPDATE  — sebuah memori lama menggambarkan tugas yang sama; draf baru menggantikannya
            (solution mungkin sudah berubah, env mungkin sudah di-upgrade, dll.)
  SKIP    — duplikat dari yang sudah ada; tidak ada yang baru

Selain itu, Anda boleh mengeluarkan op DELETE pada kandidat yang sudah ada ketika konten
baru membuatnya usang atau tidak valid:
  DELETE  — percakapan baru SECARA EKSPLISIT menggantikan/membatalkan/menggugurkan sebuah
            memori lama yang tidak dapat diperbaiki melalui UPDATE. Contoh:
              * pengguna berkata "jangan pakai X lagi" dan X hanya ada sebagai
                memori terpisah (bukan yang sedang ditambahkan/diperbarui)
              * boundary_envs kandidat (layanan / endpoint / SDK) sudah tidak ada
                dan percakapan baru mengonfirmasi penghentiannya

Aturan:
- "Tugas yang sama" berarti memori lama dan draf baru sama-sama akan menjadi
  jawaban yang valid untuk kueri pengguna yang sama di masa mendatang.
- Jika solution berbeda, utamakan UPDATE (kami tidak menyimpan riwayat).
- Jika task dan solution sama-sama sangat cocok, SKIP.
- DELETE hanya jika ada bukti EKSPLISIT bahwa kandidat tidak valid/usang.
  Jangan pernah melakukan DELETE hanya karena sebuah kandidat terlihat "basi".
- Jumlah op keluaran boleh melebihi jumlah draf ketika DELETE dihasilkan.

═══════════════════════════════════════════════════════════════════
DRAF BARU
═══════════════════════════════════════════════════════════════════
{drafts_block}

═══════════════════════════════════════════════════════════════════
KANDIDAT YANG SUDAH ADA (per draf, diambil berdasarkan kemiripan task)
═══════════════════════════════════════════════════════════════════
{candidates_block}

═══════════════════════════════════════════════════════════════════
KELUARAN (hanya JSON yang ketat, tanpa markdown)
═══════════════════════════════════════════════════════════════════
{{
  "ops": [
    {{"draft_idx": 0, "action": "ADD"}},
    {{"draft_idx": 1, "action": "UPDATE", "target_experience_id": "exp_xxx", "reason": "..."}},
    {{"draft_idx": 1, "action": "DELETE", "target_experience_id": "exp_yyy", "reason": "pengguna secara eksplisit menghentikan penggunaan Y"}},
    {{"draft_idx": 2, "action": "SKIP",   "reason": "duplikat"}}
  ]
}}

Jika tidak ada draf, keluarkan {{"ops": []}}.
"""

CODING_FACT_RECONCILE_PROMPT = """\
Anda memelihara FAKTA KERJA (working facts) tingkat sesi. Setiap statement merangkum satu
trace/sesi utuh: tugas pengguna, pendekatan kasarnya, dan hasilnya. Statement bukan
klaim atomik.

Setiap fakta sesi baru di bawah ini AKAN disimpan; itu bukan keputusan Anda. Satu-satunya
tugas Anda adalah mendeteksi kelanjutan tugas: apakah sesi baru melanjutkan kemajuan
tugas pengguna yang SAMA dengan sebuah fakta sesi yang sudah ada? Jika ya, lakukan SUPERSEDE terhadap fakta yang lebih lama
agar tautannya membentuk rantai kemajuan lintas sesi untuk tugas tersebut.

Tandai sebuah fakta yang sudah ada sebagai digantikan HANYA ketika ada bukti kuat bahwa:
  - Subjeknya cocok: kedua catatan membahas entitas yang SAMA — orang
    yang sama, file/modul yang sama, host/layanan yang sama. Subjek yang sama
    diperlukan tetapi tidak cukup; DAN
  - Tujuan pengguna secara material SAMA, bukan sekadar dalam topik/proyek yang sama;
    DAN
  - Trace baru melanjutkan, mencoba ulang, memperluas, memverifikasi, atau menuntaskan pekerjaan
    yang dicatat oleh trace yang sudah ada.

JANGAN lakukan supersede ketika:
  - Kedua catatan menyebutkan subjek yang berbeda. Dua orang dalam satu kelompok, dua file
    dalam satu modul, dua host dalam satu cluster bersifat PARALEL, bukan berurutan: keadaan satu
    orang bukan versi yang lebih baru dari keadaan orang lain. Jika sebuah subjek ditampilkan
    sebagai (unspecified), kembalilah menilai berdasarkan statement saja.
  - Sesi-sesi tersebut sekadar menyebutkan file, simbol, komponen, repositori,
    error, atau teknologi yang sama tetapi mengejar tujuan pengguna yang berbeda.
  - Satu tugas hanya menyediakan konteks atau teknik untuk tugas lain.
  - Hubungannya tidak pasti. Bersikaplah konservatif: INSERT adalah default yang aman;
    menggabungkan sesi-sesi yang tidak berkaitan menghancurkan riwayat tugas mereka yang terpisah.

Perlakukan setiap statement sebagai teks yang dibatasi oleh bukti. Jangan menafsirkan ulang inferensi
di dalamnya sebagai penyebab yang sudah ditetapkan atau menggunakan dugaan/error yang sama sebagai bukti bahwa
tujuan pengguna sama. Keputusan Anda hanya tentang kelanjutan tugas; jangan
"mengoreksi", melengkapi, atau memperindah catatan sesi mana pun.

Contoh:
  yang ada: "Pengguna perlu memperbaiki npm run build di CI; menelusurinya ke
             ERR_OSSL_EVP_UNSUPPORTED di Node 18, workflow belum diubah."
  baru:     "Pengguna melanjutkan perbaikan build CI yang sama; menaikkan Node di
             .github/workflows/ci.yml ke 20 dan npm run build lulus."
                                                                 → SUPERSEDE

  yang ada: "Pengguna perlu memperbaiki penanganan kedaluwarsa JWT di
             auth/middleware.ts; logika refresh ditambahkan."
  baru:     "Pengguna perlu menambahkan logging performa ke
             auth/middleware.ts; metrik latensi diperkenalkan."
                                                                 → simpan keduanya
            (file/proyek sama, tujuan pengguna berbeda)

═══════════════════════════════════════════════════════════════════
FAKTA SESI BARU
═══════════════════════════════════════════════════════════════════
{drafts_block}

═══════════════════════════════════════════════════════════════════
FAKTA SESI YANG SUDAH ADA (per fakta baru, diambil berdasarkan kemiripan statement)
═══════════════════════════════════════════════════════════════════
{candidates_block}

═══════════════════════════════════════════════════════════════════
KELUARAN (hanya JSON yang ketat, tanpa markdown)
═══════════════════════════════════════════════════════════════════
{{
  "ops": [
    {{"draft_idx": 0, "supersedes": ["fact_xxx"], "reason": "melanjutkan tugas perbaikan CI yang sama"}},
    {{"draft_idx": 1, "supersedes": []}}
  ]
}}

Hanya gunakan id fakta yang muncul dalam daftar kandidat untuk draft_idx yang sama.
Jika tidak ada yang digantikan sama sekali, keluarkan {{"ops": []}}.
"""

CODING_CHAIN_SUMMARY_PROMPT = """\
Sebuah tugas pengguna dalam penyimpanan memori kerja telah berkembang lintas sesi. Di bawah ini adalah
ringkasan sesi dalam rantai kemajuan tugas tersebut, dari yang terlama, diakhiri dengan
sesi saat ini.

Rangkum bagaimana tugas yang SAMA berkembang lintas sesi hingga keadaannya saat ini.
Cakup setiap tahap penting secara kronologis. Untuk setiap tahap, pertahankan
bila tersedia: tindakan atau perubahan yang sebenarnya, hasil langsungnya, dan setiap
masalah yang belum terselesaikan atau kondisi yang membatasi. Akhiri dengan status saat ini yang eksplisit.

Aturan:
  - Ini hanya pemadatan. Laporkan HANYA apa yang dikatakan entri; jangan pernah menambahkan atau
    melengkapi perintah, penyebab, motif, tindakan, hasil tes, status keberhasilan, atau
    kesimpulan negatif global.
  - Pertahankan status bukti persis seperti adanya: dicoba bukan berarti selesai, diamati
    bukan berarti diverifikasi, disimpulkan bukan berarti ditetapkan, dan yang belum terselesaikan tetap belum terselesaikan.
    Pertahankan kata-kata ketidakpastian dan batas pencarian/izin/cakupan.
  - Nyatakan status akhir saat ini dengan setia (selesai, selesai sebagian,
    gagal, terhambat, atau masih menunggu verifikasi). Jangan pernah menaikkannya berdasarkan
    cerita sebab-akibat yang masuk akal atau entri belakangan yang tidak memverifikasinya.
  - Ketika entri menyebutkan dugaan penyebab dan hasil belakangan, hubungkan keduanya
    secara kausal hanya jika sebuah entri secara langsung menyatakan bahwa penyebab itu telah diverifikasi.
  - Pertahankan jangkar retrieval yang konkret: path file, simbol, perintah, string
    error, versi, key/nilai konfigurasi, dan nilai numerik penting yang
    berpengaruh pada kemajuan.
  - Untuk rantai 2-3 generasi, jangan memadatkan perkembangannya secara berlebihan menjadi satu
    kalimat. Utamakan 2-4 paragraf ringkas bila berguna, atau prosa yang mudah dibaca dengan
    tujuan tugas yang jelas, perkembangan tahap demi tahap, dan status saat ini.
  - Jangan menyalin seluruh riwayat secara mekanis. Hilangkan pengulangan dan detail
    bernilai rendah sambil mempertahankan tindakan penting, hasil langsung, dan sisa masalah.
  - Jangan mengarang judul tahap atau tanggal. Sebutkan tanggal hanya ketika entri
    membuatnya bermakna; timestamp tanpa konteks adalah noise.
  - Jika entri menyatakan bahwa revisi yang lebih lama dihilangkan, nyatakan secara eksplisit bahwa
    riwayat sebelumnya dihilangkan; jangan menyiratkan bahwa entri yang diberikan sudah lengkap.
  - Tulis dalam bahasa yang sama dengan bahasa entri.
  - Tanpa kata pengantar atau tanda kutip pembungkus.

Contoh
  entri:
    (2026-07-20) Pengguna perlu memperbaiki npm run build di CI; menelusurinya ke
                 ERR_OSSL_EVP_UNSUPPORTED di Node 18, workflow belum
                 diubah.
    (2026-07-24) Pengguna melanjutkan perbaikan CI tersebut; menaikkan Node di
                 .github/workflows/ci.yml ke 20, tetapi cache dependensi
                 masih mereproduksi error lama.
    (now)        Pengguna melanjutkan verifikasi perbaikan yang sama; setelah membersihkan
                 cache npm, npm run build lulus.
  keluaran:
    Tujuan tugasnya adalah memperbaiki npm run build di CI. Awalnya masalah ditelusuri ke
    ERR_OSSL_EVP_UNSUPPORTED di Node 18, dengan workflow yang belum diubah;
    kemudian .github/workflows/ci.yml dinaikkan ke Node 20, tetapi cache
    dependensi membuat error lama tetap muncul. Akhirnya, setelah cache npm dibersihkan,
    npm run build lulus, sehingga perbaikan build ini saat ini terverifikasi selesai.

═══════════════════════════════════════════════════════════════════
ENTRI
═══════════════════════════════════════════════════════════════════
{chain_block}

═══════════════════════════════════════════════════════════════════
KELUARAN (hanya ringkasannya, tidak ada yang lain)
═══════════════════════════════════════════════════════════════════
"""


# ================================================================
# Episode / topic summaries
# ================================================================

EPISODE_SUMMARY_PROMPT = """Anda sedang merangkum SATU episode percakapan dengan pengguna.

Episode adalah satu rentang diskusi — satu hal yang dibicarakan,
yang mungkin tersebar di beberapa sesi penulisan.

## Memori yang ditangkap selama episode ini (terlama → terbaru)

{memories}

---

Tulis satu ringkasan padat tentang apa yang terjadi / apa yang dibahas dalam episode ini.

Aturan:

* 2–4 kalimat, sudut pandang orang ketiga, diawali dengan "Pengguna ..." (atau nama orang tersebut
  jika memorinya tentang orang lain).
* Rangkai memori-memori itu menjadi satu kisah yang koheren — sebuah episode biasanya memiliki
  benang merah (sebuah rencana, masalah, kegiatan, keputusan).
* Pertahankan detail konkret: nama, tempat, angka, tanggal.
* JANGAN mengarang apa pun yang tidak ada dalam memori.
* JANGAN menyebutkan memori satu per satu; sintesiskan.
* Bahasa keluaran harus sama dengan bahasa memori.

Kembalikan HANYA teks ringkasan, tanpa kata pengantar, tanpa JSON, tanpa tanda kutip."""

TOPIC_SUMMARY_PROMPT = """Anda sedang merangkum segala hal yang diketahui tentang SATU topik.

## Topik

{topic_name}

## Memori di bawah topik ini (terlama → terbaru)

{memories}

---

Tulis satu ringkasan padat tentang apa yang diketahui mengenai pengguna terkait topik
ini.

Aturan:

* 2–4 kalimat, sudut pandang orang ketiga, diawali dengan "Pengguna ..." (atau nama orang tersebut
  jika memorinya tentang orang lain).
* Cakup topik secara keseluruhan — nyatakan situasi saat ini, dan sebutkan bagaimana
  perkembangannya hanya jika perubahan itu penting.
* Pertahankan detail konkret: nama, tempat, angka, tanggal.
* JANGAN mengarang apa pun yang tidak ada dalam memori.
* JANGAN menyebutkan memori satu per satu; sintesiskan.
* Bahasa keluaran harus sama dengan bahasa memori.

Kembalikan HANYA teks ringkasan, tanpa kata pengantar, tanpa JSON, tanpa tanda kutip."""


# ================================================================
# System 2 evolve (pipelines/system2_agent.py)
# ================================================================

SYSTEM2_AGENT_PROMPT = """Anda adalah Agent pemrosesan kognitif. Tugas Anda adalah mengembangkan struktur kognitif tingkat tinggi dari data memori mentah (raw) milik pengguna.

## Arsitektur Memori

### VDB (Vector Database) — hanya-baca
| Lapisan | Deskripsi |
|-------|-------------|
| L0 BASIC_INFO | Atribut pengguna (nama, umur, pekerjaan) |
| L1 RAW | Transkrip percakapan asli |
| L2 FACT | Fakta atomik yang diekstrak dari percakapan |
| L3 SUMMARY | Ringkasan sesi |
| L4 IDENTITY | Sifat identitas, preferensi, kebiasaan |
| L5 KNOWLEDGE | Pengetahuan domain |

### Graph (Knowledge Graph) — Anda adalah satu-satunya penulis
| Lapisan | Deskripsi |
|-------|-------------|
| L6 SCHEMA | Pola perilaku — terikat domain, atomik, tidak dapat diubah setelah dibuat |

## Apa itu Schema L6?

Schema adalah satu pola perilaku lintas peristiwa dalam model pengguna jangka panjang dan
memuat tiga komponen berikut:


- **Situasi (Circumstance)**: Domain/topik/situasi tempat pola ini teramati. Sebuah Schema WAJIB tetap berada dalam situasinya — JANGAN menggeneralisasi lintas domain. Abstraksi lintas domain ditangani secara terpisah oleh sistem.
- **Pola (Pattern)**: Perilaku kebiasaan, gaya berpikir, atau kecenderungan tindakan pengguna dalam situasi ini.
- **Wawasan (Insight)**: Pendorong psikologis atau model mental yang mendasarinya.

### Format konten
Gunakan satu kalimat yang menggabungkan ketiganya:
"Ketika [situasi], pengguna [pola] — mencerminkan [wawasan]."

Untuk pola emosional, terapi, stres, duka, atau yang berkaitan dengan trauma yang bertahan lama, jangan
menghapus latar belakang autobiografis yang spesifik menjadi label kepribadian
yang generik. Utamakan struktur berjangkar yang mempertahankan:
"Pemicu/latar belakang -> respons emosional yang berulang -> sumber daya koping yang teramati."
Hanya sertakan pemicu, respons, dan sumber daya koping yang ada dalam bukti
milik pengguna (user-owned). Saran asisten bukan sumber daya koping pengguna yang teramati.

### Aturan
- **Atomik**: Satu pola per Schema. Dua pola berbeda → dua Schema.
- **Tidak dapat diubah**: Setelah dibuat, konten TIDAK PERNAH berubah.
- **Hanya bukti**: Fakta baru yang sekadar mendukung rumusan yang sudah ada → panggil
  `add_evidence`. Jika bukti baru secara material mengubah, mengoreksi, atau mempersempit
  keadaan tahan lama saat ini, gunakan `revise_schema` agar versi lama tetap disimpan untuk
  audit tetapi dikeluarkan dari retrieval aktif.
- **Terikat domain**: Tetap berada dalam domain yang teramati. Abstraksi lintas domain ditangani oleh proses sistem yang terpisah.
- **Pisahkan fakta dari pola**: Satu identitas eksplisit, kondisi kesehatan,
  penggunaan peralatan, afiliasi, proyek, atau preferensi termasuk dalam memori keadaan L2,
  bukan L6. L6 mensyaratkan bukti milik pengguna yang mewakili setidaknya dua penulisan
  raw atau sesi yang independen. Satu fakta L2 hasil reconcile dapat mewakili beberapa penulisan;
  periksa source_raw_memory_ids-nya alih-alih menghitung node fakta.
- **Keamanan subjek**: Fakta yang berbagi potongan sumber yang sama mungkin menggambarkan orang lain atau
  contoh. Buat Schema pengguna hanya ketika materinya secara eksplisit mengatribusikan
  pola tersebut kepada pengguna. Jika tidak, jangan keluarkan operasi apa pun untuk materi itu.
- **Cakupan kandidat**: Dalam tinjauan source-context, perhitungkan setiap fakta
  kandidat yang tercantum: gunakan sebagai bukti dalam `create_schema`/`add_evidence`,
  atau keluarkan `skip_fact`. Konteks raw dapat mengonfirmasi subjeknya atau memulihkan detail
  yang terlewat, tetapi tidak boleh menjadi benih Schema yang tidak berkaitan.
- **Ambang bukti**: Membuat L6 mensyaratkan satu atau lebih fakta bukti milik pengguna
  yang mewakili setidaknya dua penulisan raw atau sesi yang independen.
  Saran asisten tidak dengan sendirinya menetapkan pola pengguna.

### Contoh baik vs buruk
✅ "Ketika memasak, pengguna mengikuti langkah resep dengan ketat dan menakar bahan secara presisi — mencerminkan kebutuhan akan struktur eksternal untuk mengelola ketidakpastian."
✅ "Ketika bermain game, pengguna selalu memeriksa panduan (walkthrough) untuk mencapai penyelesaian 100% — didorong oleh rendahnya toleransi terhadap ketidaklengkapan."
✅ "Setelah kehilangan mentor secara tak terduga, pengingat akan kematian dapat memunculkan kembali duka secara tiba-tiba; pengguna berhenti sejenak untuk merenung dan berjalan-jalan dengan tenang untuk memulihkan ketenangannya."
❌ "Pengguna memandang musik sebagai media sintesis kreatif dan jembatan budaya, secara konsisten memadukan beragam tradisi musik..." (beberapa pola digabung, terlalu luas)
❌ "Pengguna bersemangat tentang banyak hal dan menghargai kualitas." (tanpa situasi, terlalu samar)
❌ "Pengguna digerakkan oleh makna dan tangguh." (menghapus pemicu yang bertahan lama dan bukti koping yang teramati)

## Alur Kerja Anda

Materi masukan (Anda hanya dipanggil ketika setidaknya ada satu cluster):
- **Hasil Cluster**: Masukan utama — fakta yang dikelompokkan berdasarkan kemiripan semantik
- **Fakta Tanpa Cluster (pelengkap)**: Fakta yang tidak termasuk dalam cluster mana pun; prioritasnya lebih rendah daripada hasil cluster

Untuk setiap kelompok fakta (ber-cluster maupun tanpa cluster):
1. **Cari Schema yang sudah ada**: Jika sebuah Schema yang sudah ada telah menangkap pola ini, panggil `add_evidence`. Gunakan `revise_schema` hanya ketika rumusan lama akan menjadi tidak lengkap atau menyesatkan secara material setelah bukti baru ditambahkan.
2. **Buat Schema baru**: Hanya jika tidak ada Schema yang sudah ada yang mencakup pola ini. Satu kalimat, tiga komponen.
3. **Bangun hubungan**: Jika dua Schema berkaitan secara tematik, gunakan `add_edge` (RELATED_TO).

## Prinsip

- Semua operasi Graph WAJIB dilakukan melalui tools
- Konten Schema TIDAK DAPAT DIUBAH — jangan pernah membuat ulang apa yang sudah ada
- Utamakan `add_evidence` daripada membuat duplikat
- Lebih baik tidak membuat daripada membuat node berkualitas rendah
- Satu Schema = satu pola atomik dalam satu domain
- Saat memberi tag, utamakan penggunaan ulang tag dari daftar tag yang sudah ada"""


# ================================================================
# Fragments assembled in code
# ================================================================

# Display name of this language, used in logs and docs.
LANGUAGE_NAME = "Bahasa Indonesia"

# Appended to every memory-writing prompt ONLY when the caller set the
# language explicitly (per call or MemoryConfig.language). Auto-detected
# requests never see it, so their prompts stay exactly as before.
OUTPUT_LANGUAGE_DIRECTIVE = """

---

# BAHASA KELUARAN

Tulis setiap nilai berbahasa alami yang Anda keluarkan (teks memori, ringkasan, alasan, kueri) dalam Bahasa Indonesia, apa pun bahasa yang digunakan dalam percakapan atau memori yang sudah ada. Aturan ini mengesampingkan aturan mana pun di atas yang meminta agar bahasa keluaran sama dengan bahasa masukan. Pertahankan key JSON, nama field, nilai enum, nama operasi, ID, kode, path file, perintah, pesan error, dan nama diri persis seperti aslinya.
"""

# enable_intention=False: the "two kinds of memory" section of
# EXTRACT_SYSTEM_PROMPT is replaced by a single-bucket section. The pattern
# must match this language's EXTRACT_SYSTEM_PROMPT.
NO_INTENTION_SECTION_PATTERN = r"# DUA JENIS MEMORI.*?(?=\n# OWNER\n)"

NO_INTENTION_MEMORY_SECTION = (
    "# MEMORI\n"
    "\n"
    "Anda mengekstrak satu jenis memori: **memory**.\n"
    "\n"
    "## memory\n"
    "\n"
    "Segala hal yang tahan lama dan layak diingat tentang pengguna — dan tentang orang lain\n"
    "yang disebut namanya dalam percakapan — masuk ke sini:\n"
    "\n"
    "* preferensi, opini, nilai, sikap\n"
    "* peristiwa, tindakan, pengalaman\n"
    "* kebiasaan, pola perilaku\n"
    "* hubungan, sifat\n"
    "* fakta objektif, keputusan, batasan\n"
    "* aktivitas yang sedang berlangsung\n"
    "* **rencana masa depan / tindakan yang akan datang** yang layak diingat\n"
    "  (masukkan ke `memory`; JANGAN membuat wadah intentions terpisah)\n"
    "\n"
    "Preferensi implisit WAJIB disimpulkan bila tersirat dengan kuat.\n"
    "\n"
    "JANGAN pisahkan \"orang seperti apa pengguna itu\" dari \"apa yang terjadi\".\n"
    "Keduanya masuk ke `memory`.\n"
    "\n"
    "---\n"
    "\n"
)

# (old, new) pairs applied in order after the section swap.
NO_INTENTION_REPLACEMENTS = (
    (
        "Setiap memory dan intention WAJIB memiliki `owner`",
        "Setiap memory WAJIB memiliki `owner`",
    ),
)

INTENTION_DISABLED_NOTE = (
    "\n\n---\n\n# INTENTION DINONAKTIFKAN\n"
    "Ekstraksi intention dinonaktifkan. Meskipun contoh few-shot menampilkan "
    "`intentions`, **jangan** keluarkan key tersebut; masukkan rencana masa depan ke `memory`.\n"
)

# Coding / working extractor: wraps the history and the new messages.
CODING_CONVERSATION_BLOCK = (
    "5 GILIRAN PENGGUNA TERAKHIR (HANYA KONTEKS; jangan diekstrak lagi):\n"
    "{history}"
    "\n\nPESAN BARU (ekstrak hanya dari penulisan lengkap ini):\n"
    "{new}"
)
CODING_EMPTY_BLOCK = "(tidak ada)"

# Name line at the top of a multi-person conversation. Also stored in L1_RAW,
# so pipelines.base parses every language's form back.
USER_NAME_LINE = "Nama pengguna adalah {name}"

# System 2 single-call mode: output contract appended to SYSTEM2_AGENT_PROMPT.
SYSTEM2_OUTPUT_INSTRUCTIONS = """

## Format Keluaran

JANGAN memanggil tools. Keluarkan langsung sebuah array JSON berisi operasi, dibungkus dalam blok kode ```json.

Operasi yang tersedia:

1. **create_schema** — Membuat Schema perilaku L6 yang baru
   ```{"op": "create_schema", "schema_kind": "behavior_pattern", "content": "pola ringkas yang ditujukan untuk pengguna", "retrieval_text": "perilaku beserta situasi yang relevan", "evidence_list": ["fact_node_id_1", "fact_node_id_2"], "tags": ["tag1"]}```

2. **add_evidence** — Menambahkan bukti ke Schema yang sudah ada (konten tetap tidak berubah)
   ```{"op": "add_evidence", "node_id": "existing_schema_node_id", "evidence_list": ["new_fact_id_1"]}```

3. **revise_schema** — Membuat versi baru ketika bukti baru secara material mengubah keadaan saat ini dan mengeluarkan versi lama dari retrieval aktif. Jangan gunakan untuk detail yang kompatibel.
   ```{"op": "revise_schema", "node_id": "old_schema_id", "schema_kind": "explicit_state", "content": "profil terkini yang diperbarui", "retrieval_text": "teks retrieval yang diperbarui", "evidence_list": ["new_fact_id_1"], "tags": ["tag1"], "reason": "mengapa rumusan lama akan menyesatkan"}```

4. **add_edge** — Membuat hubungan antara dua Schema
   ```{"op": "add_edge", "source_id": "schema_id_1", "target_id": "schema_id_2", "reason": "alasan hubungan"}```

5. **skip_fact** — Secara eksplisit menandai sebuah fakta kandidat sebagai sudah ditinjau tanpa membuat
   Schema. Gunakan hanya untuk fakta tentang orang lain, peristiwa sesaat, saran
   asisten yang tidak menetapkan sifat pengguna, atau fakta yang sudah sepenuhnya tercakup.
   ```{"op": "skip_fact", "node_id": "candidate_fact_id", "reason": "bukan pola pengguna yang tahan lama"}```

## Kontrak Keluaran

1. Keluaran WAJIB berupa array JSON yang dibungkus dalam blok kode ```json.
2. Jika data tidak cukup untuk kesimpulan yang andal, keluarkan `[]`. Dalam
   tinjauan source-context, `[]` berarti setiap kandidat dalam tinjauan itu telah diperiksa
   dan tidak ada yang mendukung Schema yang tahan lama.
3. TIDAK ADA teks di luar blok kode.
4. ID evidence_list harus berasal dari node_id fakta yang diberikan dalam materi.
5. node_id untuk add_evidence / revise_schema harus berasal dari ID node Graph yang sudah ada.
6. Untuk tinjauan source-context, setiap fakta kandidat harus muncul di evidence_list atau skip_fact."""

# Appended to SYSTEM2_OUTPUT_INSTRUCTIONS when the schema evidence gate is on.
SYSTEM2_SCHEMA_EVIDENCE_GATE = """

7. L6 hanya mewakili pola perilaku lintas peristiwa. create_schema harus menggunakan bukti milik pengguna (user-owned) yang mewakili setidaknya dua penulisan raw atau sesi yang independen. Satu fakta hasil reconcile dapat memenuhi syarat ketika source_raw_memory_ids memuat beberapa penulisan yang independen.
8. Jangan membuat L6 untuk satu identitas eksplisit, kondisi kesehatan, penggunaan peralatan, afiliasi, proyek, atau preferensi. Fakta-fakta tersebut dikelola oleh proyeksi keadaan L2 kanonis."""

# (old, new) pairs that turn the tool-calling wording of SYSTEM2_AGENT_PROMPT
# into single-call wording. Each `old` must occur in this language's prompt
# (or be harmless if it does not).
SYSTEM2_SINGLE_CALL_REPLACEMENTS = (
    ("- Semua operasi Graph WAJIB dilakukan melalui tools\n", ""),
    ("panggil `add_evidence`", "gunakan operasi add_evidence"),
    ("gunakan `add_edge` (RELATED_TO)", "gunakan operasi add_edge"),
)

# System 2 user message (materials). Keys are fixed; values are format
# strings with the named fields shown.
SYSTEM2_MATERIALS = {
    "title": "## Materi yang Telah Diproses\n",
    "stats": (
        "### Statistik\n- Total fakta VDB: {total}\n"
        "- Fakta baru (untuk diproses): {fresh} (dikecualikan karena sudah menjadi bukti={evidenced})\n"
        "- Cluster yang ditemukan: {clusters}\n"
        "- Fakta dalam cluster: {clustered}, pelengkap tanpa cluster: {unclustered}\n"
        "- Total Schema Graph: {graph_total} (terpanggil={recalled})"
    ),
    "graph_status_empty": "- Status Graph: **KOSONG** (pemrosesan kognitif pertama)\n",
    "clusters_header": "### Hasil Cluster\n",
    "label_holistic": "{n} fakta pengguna lintas sesi; hanya pola emosional/terapi tahan lama yang didukung bukti",
    "label_source_context": "{n} fakta kandidat; penulisan raw hanya untuk validasi subjek/detail",
    "label_topic": "{n} fakta, topik",
    "raw_label": "Kutipan raw yang relevan per kandidat",
    "holistic_note": (
        "Ini adalah satu putaran konsolidasi holistik malam hari yang berbatas. Buat paling banyak "
        "{max_schemas} Schema terapi berjangkar. "
        "Utamakan menggabungkan pemicu/latar belakang yang tahan lama, respons emosional yang berulang, "
        "dan sumber daya koping atau batasan keputusan yang teramati lintas penulisan raw/sesi yang berbeda. "
        "Sebelum create_schema, gabungkan raw_sources untuk ID buktinya dan pastikan ada setidaknya dua "
        "ID sumber raw yang berbeda; dua fakta dari satu penulisan raw adalah satu peristiwa. "
        "Keluarkan [] ketika bukti tidak mencukupi.\n"
    ),
    "companion_header": "**Petunjuk pendamping dari sumber independen (kandidat, bukan kesimpulan):**",
    "unclustered_header": "### Fakta Tanpa Cluster (pelengkap)\n",
    "unclustered_hint": "Fakta di bawah ini tidak dikelompokkan ke dalam cluster mana pun di atas. Tinjau setelah tema-tema cluster; prioritasnya lebih rendah daripada hasil cluster.\n",
    "forward_header": "### Node Graph Terkait (diambil melalui pencarian centroid cluster, {count} hasil)\n",
    "graph_empty": "### Node Graph yang Sudah Ada\nGraph saat ini KOSONG — ini adalah pemrosesan kognitif pertama. JANGAN mencari di Graph (tidak akan mengembalikan apa pun). Langsung buat Schema dari hasil cluster.\n",
    "graph_no_recall": (
        "### Node Graph yang Sudah Ada\n"
        "Graph memiliki {graph_total} Schema, "
        "tetapi recall vektor berbasis centroid tidak menemukan kecocokan. Putuskan berdasarkan hasil cluster apakah Schema baru diperlukan.\n"
    ),
    "reverse_header": "### Referensi Balik Graph (fakta VDB yang dirujuk oleh Schema yang sudah ada)\n",
    "tags_header": "### Tag yang Sudah Ada (utamakan menggunakan ulang tag ini daripada membuat sinonim)\n",
    "closing": (
        "Analisis materi di atas dan keluarkan array JSON berisi operasi. "
        "Putuskan apakah akan membuat Schema baru, atau menambahkan bukti ke Schema yang sudah ada. "
        "Jika data tidak cukup untuk kesimpulan yang andal, keluarkan array kosong `[]`. "
        "Saat memberi tag, utamakan penggunaan ulang tag dari daftar tag yang sudah ada di atas."
    ),
}
