import type { MessageKey } from "../gui-i18n.js";
import { embeddingSwitchIndonesianMessages } from "../gui-i18n-embedding-switch.js";
import { modeIndonesianMessages } from "../gui-i18n-mode.js";

/** Indonesian (id, Anda) GUI copy. Machine-assisted; native-speaker review recommended. */
export const indonesianMessages: Record<MessageKey, string> = {
  ...modeIndonesianMessages,
  ...embeddingSwitchIndonesianMessages,
  "connectionFallback.title": "Hubungkan alat lain",
  "connectionFallback.desktopOnly":
    "Pemasangan lokal hanya tersedia di aplikasi desktop.",
  "connectionFallback.description":
    "Buat Skill pemasangan sekali pakai untuk Harness lokal yang tidak tercantum. Obrolan di browser, kontainer jarak jauh, dan komputer lain tidak dapat terhubung langsung ke perangkat ini.",
  "connectionFallback.name": "Nama Harness",
  "connectionFallback.namePlaceholder": "Misalnya, Kimi Code",
  "connectionFallback.generating": "Membuat…",
  "connectionFallback.generate": "Buat Skill pemasangan",
  "connectionFallback.generateFailed": "Tidak dapat membuat Skill pemasangan.",
  "connectionFallback.retry": "Coba lagi",
  "connectionFallback.generated":
    "Skill pemasangan sekali pakai untuk {name} telah dibuat. Berlaku hingga {time}.",
  "connectionFallback.skillLabel": "Skill pemasangan",
  "connectionFallback.copy": "Salin Skill",
  "connectionFallback.copied":
    "Skill pemasangan disalin. Tempel dan jalankan di Harness yang sesuai.",
  "connectionFallback.copyFailed":
    "Gagal menyalin. Pilih dan salin teks di atas secara manual.",
  "connectionFallback.localDescription":
    "Hanya untuk MCP lokal atau alat baris perintah. Obrolan di browser tidak dapat terhubung langsung ke perangkat ini.",
  "connectionFallback.loading": "Memuat detail koneksi lokal…",
  "connectionFallback.loadFailed": "Tidak dapat memuat detail koneksi.",
  "connectionFallback.format": "Format detail koneksi",
  "connectionFallback.promptTab": "Prompt koneksi",
  "connectionFallback.mcpTab": "Konfigurasi MCP",
  "connectionFallback.commandTab": "Perintah",
  "connectionFallback.materialLabel": "Detail koneksi lokal",
  "connectionFallback.materialBoundary":
    "Berisi jalur lokal, tetapi tidak berisi memori atau kunci. Tinjau sebelum menempelkan dan biarkan MemHub tetap berjalan.",
  "connectionFallback.materialCopied":
    "Disalin. Belum ada koneksi yang dibuat.",
  "connectionFallback.copyPrompt": "Salin prompt",
  "connectionFallback.copyConfig": "Salin konfigurasi",
  "connectionFallback.copyCommand": "Salin perintah",
  "connectionFallback.promptTemplate":
    "Hubungkan Harness lokal ini ke MemHub dengan konfigurasi MCP berikut:\n```json\n{mcp}```\n{shellBlock}\nSetelah terhubung, jalankan kueri memori sungguhan dan laporkan hasilnya secara akurat.",
  "connectionFallback.promptShell":
    "\nJika hanya perintah shell lokal yang didukung, gunakan:\n```bash\n{shell}```\n",
  "sdk.memory.title": "Pustaka memori SDK",
  "sdk.memory.status": "Status memori",
  "sdk.memory.status.active": "Tersedia",
  "sdk.memory.status.superseded": "Digantikan",
  "sdk.memory.total": "{count} memori",
  "sdk.memory.total.one": "{count} memori",
  "sdk.memory.loadFailed":
    "Tidak dapat memuat pustaka memori. Silakan coba lagi.",
  "sdk.memory.detailLoadFailed": "Tidak dapat memuat detail memori.",
  "sdk.memory.updateFailed":
    "Tidak dapat menyimpan perubahan. Silakan coba lagi.",
  "sdk.memory.deleteFailed": "Tidak dapat menghapus memori. Silakan coba lagi.",
  "sdk.memory.loading": "Memuat pustaka memori…",
  "sdk.memory.pagination": "Halaman pustaka memori SDK",
  "sdk.memory.previous": "Halaman sebelumnya",
  "sdk.memory.next": "Halaman berikutnya",
  "sdk.memory.detail": "Detail memori SDK",
  "sdk.memory.content": "Isi memori",
  "sdk.memory.save": "Simpan perubahan",
  "sdk.memory.delete": "Hapus memori",
  "sdk.memory.scene.chat": "Obrolan",
  "sdk.memory.scene.working": "Pekerjaan",
  "sdk.memory.scene.coding": "Pengodean",
  "sdk.memory.task": "Tugas",
  "sdk.memory.solution": "Solusi",
  "sdk.memory.page": "Halaman {page} dari {pages}",
  "sdk.raw.title": "Percakapan asli SDK",
  "sdk.raw.searchLabel": "Cari percakapan asli",
  "sdk.raw.searching": "Mencari…",
  "sdk.raw.search": "Cari",
  "sdk.raw.loadFailed": "Percakapan asli untuk sementara tidak tersedia.",
  "sdk.raw.searchFailed":
    "Tidak dapat mencari percakapan asli. Silakan coba lagi.",
  "sdk.raw.recent": "Percakapan asli terbaru",
  "sdk.raw.empty":
    "Belum ada percakapan asli. Hubungkan sumber agar percakapan tampil di sini.",
  "sdk.raw.total": "{count} percakapan asli",
  "sdk.raw.total.one": "{count} percakapan asli",
  "memory:当前使用基础记忆。新对话会保存为原始记录，不会生成高级记忆；以前生成的高级记忆仍会保留，但当前不会显示或参与记忆召回。":
    "Memori dasar aktif. Percakapan baru disimpan sebagai catatan asli dan tidak membuat memori lanjutan. Memori lanjutan yang ada tetap tersimpan, tetapi tidak ditampilkan atau digunakan untuk recall.",
  "memory:当前使用智能记忆。新对话会由模型整理为高级记忆。":
    "Memori cerdas aktif. Model mengolah percakapan baru menjadi memori lanjutan.",
  "memory:当前使用深度记忆。新对话会生成高级记忆，并由知识图谱和 evolve 继续整合。":
    "Memori mendalam aktif. Percakapan baru membuat memori lanjutan yang terus dikonsolidasikan oleh knowledge graph dan evolve.",
  "sdk.raw.found": "{count} hasil",
  "sdk.raw.found.one": "{count} hasil",
  "memoryInspector.emptyHint": "Lihat sumber, isi, dan riwayat perubahannya.",
  "memory.operationFailed": "Operasi memori gagal ({code})",
  "memorySpace.tabs.workbench": "Ruang kerja",
  "selectedMemory.status.confirmed": "Dibuat",
  "selectedMemory.status.active": "Aktif",
  "selectedMemory.status.deleted": "Dihapus",
  "selectedMemory.status.superseded": "Digantikan",
  "selectedMemory.status.shadow": "Tersembunyi",
  "selectedMemory.status.negated": "Dinegasikan",
  "selectedMemory.status.conflicted": "Berkonflik",
  "selectedMemory.status.archived": "Diarsipkan",
  "selectedMemory.status.unknown": "Status perlu ditinjau",
  "selectedMemory.sourceUnknown": "Sumber perlu ditinjau",
  "selectedMemory.sourceUnattributed": "Sumber tidak teridentifikasi",
  "selectedMemory.discardDraft": "Buang perubahan yang belum disimpan?",
  "selectedMemory.newVersionReadFailed":
    "Tidak dapat memuat versi baru. Draf Anda tetap disimpan.",
  "selectedMemory.updateRejected":
    "Perubahan tidak dikirim. Draf Anda tetap disimpan; perbaiki masalahnya lalu coba lagi.",
  "selectedMemory.updateUnknown":
    "Perubahan belum terkonfirmasi. Draf Anda tetap disimpan; muat ulang sebelum mencoba lagi.",
  "selectedMemory.deleteRejected":
    "Penghapusan tidak dikirim. Tinjau memori ini lalu coba lagi.",
  "selectedMemory.deleteUnknown":
    "Penghapusan belum terkonfirmasi. Tinjau memori ini; cakupan penghapusan tidak akan meluas.",
  "selectedMemory.undoRejected":
    "Pembatalan tidak dikirim. Perbaiki masalahnya lalu coba lagi.",
  "selectedMemory.undoUnknown":
    "Pembatalan belum terkonfirmasi. Tinjau versi saat ini sebelum mencoba lagi.",
  "selectedMemory.author.you": "Anda berkata",
  "selectedMemory.author.assistant": "Pesan asli asisten",
  "selectedMemory.author.unknown": "Penulis perlu ditinjau",
  "selectedMemory.version": "Versi {revision}",
  "selectedMemory.sourceLine": "Sumber {source} · {time}",
  "selectedMemory.timestamps":
    "Waktu percakapan {conversation} · Dibuat {created}",
  "selectedMemory.writeTime": "Ditulis {created}",
  "selectedMemory.edit": "Edit",
  "selectedMemory.evidence": "Bukti asli",
  "selectedMemory.evidenceLine": "{author} · Percakapan asli {time}:",
  "selectedMemory.evidenceUnavailable": "Teks asli ini sedang tidak tersedia.",
  "selectedMemory.viewOriginal": "Lihat teks asli",
  "selectedMemory.history": "Riwayat versi",
  "selectedMemory.more": "Tindakan lainnya",
  "selectedMemory.undo": "Batalkan perubahan terakhir",
  "selectedMemory.delete": "Hapus memori",
  "selectedMemory.editTitle": "Edit memori",
  "selectedMemory.originalTitle": "Percakapan asli",
  "selectedMemory.back": "Kembali ke memori",
  "selectedMemory.editDescription":
    "Edit memori ini; percakapan asli tidak berubah.",
  "selectedMemory.content": "Isi memori",
  "selectedMemory.scopeUnchanged": "Cakupan akses tidak berubah",
  "selectedMemory.conflictTitle": "Ada versi yang lebih baru",
  "selectedMemory.conflictDescription":
    "Draf Anda tetap disimpan. Tinjau versi baru sebelum mengedit.",
  "selectedMemory.viewNewVersion": "Lihat versi baru",
  "selectedMemory.continueFromVersion": "Lanjutkan mengedit dari versi ini",
  "selectedMemory.cancel": "Batal",
  "selectedMemory.save": "Simpan perubahan",
  "selectedMemory.author.user": "Anda",
  "selectedMemory.author.agent": "Asisten",
  "selectedMemory.author.tool": "Alat",
  "selectedMemory.sourceUnavailable":
    "Izin sumber berubah atau teks asli tidak tersedia.",
  "selectedMemory.related": "Memori terkait",
  "selectedMemory.viewMemory": "Lihat memori",
  "selectedMemory.deleteTitle": "Hapus memori ini?",
  "selectedMemory.deleteDescription":
    "Memori ini tidak akan digunakan lagi untuk pencarian baru. Percakapan di aplikasi sumber tidak akan dihapus.",
  "selectedMemory.sentCannotRecall":
    "Konten yang sudah dikirim ke aplikasi tidak dapat ditarik kembali dari jarak jauh.",
  "memory:{count} 条": "{count} item",
  "memory:已可用 · {count} 段原文": "Tersedia · {count} kutipan asli",
  "memory:主题视角（非事项）": "Tampilan topik (bukan urusan)",
  "memory:来源视角": "Tampilan sumber",
  "memory:{count} 条记忆 · 当前已加载": "{count} memori · Dimuat",
  "memory:最近 {count} 条": "{count} percakapan terbaru",
  "memory:已确认": "Terkonfirmasi",
  "memory:记忆库": "Pustaka memori",
  "memory:问记忆": "Tanya memori",
  "memory:历史对话": "Riwayat percakapan",
  "memory:项目记忆": "Memori proyek",
  "memory:编码偏好": "Preferensi pengodean",
  "memory:长期模式": "Pola jangka panjang",
  "memory:意图": "Niat",
  "memory:近 3 个月": "3 bulan terakhir",
  "memory:近 6 个月": "6 bulan terakhir",
  "memory:近 1 年": "1 tahun terakhir",
  "memory:全部历史": "Semua riwayat",
  "memory:{count} 条这次没能整理成功，正在重试":
    "Mencoba ulang pemrosesan · {count}",
  "memory:{count} 处理中": "Sedang diproses: {count}",
  "memory: · 处理中 {running} / 待派发 {active}":
    " · {running} diproses / {active} dalam antrean",
  "memory: · {count} 条任务状态待核对":
    " · {count} tugas perlu peninjauan status",
  "memory: · {count} 条任务状态异常，正在恢复":
    " · {count} tugas bermasalah; sedang dipulihkan",
  "memory:{count} 等待": "{count} menunggu",
  "memory:最新 {count} 条待处理": "{count} terbaru menunggu diproses",
  "memory:历史积压 {count} 条": "{count} tertunda",
  "memory:（约 {count} 分钟）": " (sekitar {count} menit)",
  "memory:（约 {count} 小时）": " (sekitar {count} jam)",
  "memory:（约 {count} 天）": " (sekitar {count} hari)",
  "memory:同步尝试 {count} 次": "{count} percobaan sinkronisasi",
  "memory:尚未尝试同步": "Sinkronisasi belum dicoba",
  "memory: · 状态更新于 {date}": " · Diperbarui {date}",
  "memory:HY Memory 当前没有这条记忆，仅重试同步不能补齐。":
    "HY Memory tidak memiliki memori ini; mencoba ulang sinkronisasi tidak dapat memulihkannya.",
  "memory:同步需要可用的本机整理模型或本地嵌入授权。":
    "Sinkronisasi memerlukan model pemrosesan lokal yang tersedia atau izin menggunakan vektor lokal.",
  "memory:这条记忆或模型版本已经变化，先刷新核对再决定下一步。":
    "Memori ini atau versi modelnya telah berubah. Muat ulang dan tinjau sebelum memutuskan langkah berikutnya.",
  "memory:这条记忆已改，搜索索引还没开始同步。":
    "Memori ini telah berubah, tetapi sinkronisasi indeks pencarian belum dimulai.",
  "memory:最近一次同步未成功；可以先重试，结果仍不确定时再刷新核对。":
    "Sinkronisasi terakhir gagal. Coba lagi terlebih dahulu; jika hasilnya masih belum pasti, muat ulang dan tinjau.",
  "memory:这条记忆已改，搜索索引还没跟上。":
    "Memori ini telah berubah, tetapi indeks pencarian belum diperbarui.",
  "memory:搜索里还是改之前那句话。":
    "Pencarian masih menampilkan teks sebelum koreksi.",
  "memory:重试同步": "Coba sinkronisasi lagi",
  "memory:配置整理模型": "Konfigurasi model pemrosesan",
  "memory:未提炼 · 在窗口外": "Belum diekstrak · Di luar rentang",
  "memory:未提炼 · 排队中": "Belum diekstrak · Dalam antrean",
  "wb.select": "Pilih",
  "memory:模型综合": "Sintesis model",
  "memory:原文摘录": "Kutipan asli",
  "memory:没有命中": "Tidak ada kecocokan",
  "memory:正在生成": "Membuat",
  "memory:正在检索记忆": "Mencari memori",
  "memory: · 基于 {count} 条已确认记忆":
    " · Berdasarkan {count} memori terkonfirmasi",
  "memory: · 基于 {count} 条已确认记忆.one":
    " · Berdasarkan {count} memori terkonfirmasi",
  "memory:整理需要处理": "Pemrosesan perlu perhatian",
  "memory:正在整理记忆": "Memproses memori",
  "memory:整理状态": "Status pemrosesan",
  "memory:自动提炼范围": "Rentang ekstraksi otomatis",
  "memory:手动提炼下一批": "Ekstrak batch berikutnya secara manual",
  "memory:刷新核对": "Muat ulang dan periksa",
  "memory:正在导入历史会话，记忆库暂时只显示一部分；导入完成后会自动补全，不需要重试。":
    "Mengimpor riwayat percakapan. Baru sebagian memori yang terlihat; daftar akan terisi otomatis setelah impor selesai.",
  "memory:选择：{content}": "Pilih: {content}",
  "memory:处理失败：{content}": "Gagal memproses: {content}",
  "memory:已选 {count} 条": "{count} dipilih",
  "memory:已选 {count} 条.one": "{count} dipilih",
  "memory:删除所选 {count} 条": "Hapus {count} yang dipilih",
  "memory:删除所选 {count} 条.one": "Hapus {count} yang dipilih",
  "memory:重试失败的 {count} 条": "Coba lagi {count} item yang gagal",
  "memory:重试失败的 {count} 条.one": "Coba lagi {count} item yang gagal",
  "memory:没有{status}的记忆。": "Tidak ada memori {status}.",
  "memory:已{action} {count} 条": "Berhasil {action} {count} item",
  "memory:已{action} {count} 条.one": "Berhasil {action} {count} item",
  "memory:，{count} 条未完成": ", {count} belum selesai",
  "memory:，{count} 条未完成.one": ", {count} belum selesai",
  "memory:未能{action}：{error}": "Tidak dapat {action}: {error}",
  "memory:重试{action}": "Coba {action} lagi",
  "memory:删除（操作）": "menghapus",
  "memory:处理（操作）": "memproses",
  "memory:{count} 条原话": "{count} kutipan asli",
  "memory:{count} 条原话.one": "{count} kutipan asli",
  "memory:{count} 次整理已完成": "{count} proses selesai",
  "memory:{count} 次整理已完成.one": "{count} proses selesai",
  "memory: · {count} 条在窗口外（原文可搜）":
    " · {count} di luar rentang (teks asli dapat dicari)",
  "memory: · {count} 条在窗口外（原文可搜）.one":
    " · {count} di luar rentang (teks asli dapat dicari)",
  "memory: · 已规划 {planned} 批，当前第 {current} 批":
    " · {planned} batch direncanakan, kini batch {current}",
  "memory:{kind}卡在 {reason}（{count} 条）":
    "{kind} terhambat oleh {reason} ({count})",
  "memory:已加载 {count} 条记忆": "{count} memori dimuat",
  "memory:已加载 {count} 条记忆.one": "{count} memori dimuat",
  "memory:回答只使用已确认记忆。每个答案都可以回到对应记忆和来源原话。":
    "Jawaban hanya menggunakan memori terkonfirmasi. Setiap jawaban tertaut ke memori dan teks aslinya.",
  "memory:选择一条记忆": "Pilih memori",
  "memory:查看它的来源、使用范围和修改历史。":
    "Lihat sumber, cakupan akses, dan riwayat perubahannya.",
  "memory:查看替代它的记忆": "Lihat memori penggantinya",
  "memory:查看它取代的旧记忆": "Lihat memori yang digantikannya",
  "memory:保存纠正": "Simpan koreksi",
  "memory:修改历史": "Riwayat perubahan",
  "memory:已可用": "Tersedia",
  "memory:处理中…": "Memproses…",
  "memory:搜索": "Cari",
  "memory:记忆治理状态": "Status memori",
  "memory:记忆空间模式": "Mode ruang memori",
  "memory:来源状态待核对，记忆库暂不可读取。":
    "Status sumber perlu ditinjau; pustaka memori belum dapat dibaca.",
  "memory:记忆库读取失败，请重试。":
    "Tidak dapat memuat pustaka memori. Silakan coba lagi.",
  "memory:重新读取记忆库": "Muat ulang pustaka memori",
  "memory:正在读取记忆库…": "Memuat pustaka memori…",
  "memory:批量操作结果": "Hasil tindakan massal",
  "memory:批量操作": "Tindakan massal",
  "memory:取消选择": "Hapus pilihan",
  "memory:向已确认记忆提问": "Tanya memori terkonfirmasi",
  "memory:搜索记忆库": "Cari di pustaka memori",
  "memory:问答记忆范围": "Cakupan pertanyaan memori",
  "memory:只问个人画像": "Tanya profil pribadi saja",
  "memory:问全部记忆（含项目）": "Tanya semua memori (termasuk proyek)",
  "memory:例如：我对真实数据有什么要求？":
    "Misalnya: apa persyaratan saya untuk data nyata?",
  "memory:搜索偏好、决定或经历": "Cari preferensi, keputusan, atau pengalaman",
  "memory:根据记忆回答": "Jawab dari memori",
  "memory:清除搜索": "Hapus pencarian",
  "wb.finding.duplicate": "Dari pesan asli yang sama",
  "wb.finding.duplicateText":
    "“{first}” dan “{second}” berasal dari pesan asli yang sama dan ditetapkan ke {firstScope} dan {secondScope}. Periksa apakah keduanya merujuk pada hal yang sama.",
  "wb.finding.dormant": "Tidak diperbarui baru-baru ini",
  "wb.finding.dormantText":
    "{count} memori tidak diubah atau disebut selama dua bulan. Memori ini mungkin perlu ditinjau.",
  "wb.finding.blocked": "Menunggu persyaratan",
  "wb.finding.blockedText":
    "{count} percakapan dalam antrean menunggu persyaratan. Teks aslinya sudah disimpan dan dapat dicari.",
  "wb.finding.blockedText.one":
    "{count} percakapan dalam antrean menunggu persyaratan. Teks aslinya sudah disimpan dan dapat dicari.",
  "wb.unclassified": "Belum dikategorikan",
  "wb.profile": "Profil pribadi",
  "wb.project": "Proyek {id}",
  "wb.topics": "Topik",
  "wb.all": "Semua",
  "wb.topicCount": "{count} topik terbentuk",
  "wb.topicCount.one": "{count} topik terbentuk",
  "wb.singletons": "; {count} tag lain hanya muncul sekali",
  "wb.singletons.one": "; {count} tag lain hanya muncul sekali",
  "wb.memories": "{count} memori",
  "wb.memories.one": "{count} memori",
  "wb.sources": "{count} sumber",
  "wb.sources.one": "{count} sumber",
  "wb.once":
    "Topik ini hanya muncul di sini. Mungkin hanya sekali terjadi, atau topik yang sama terpecah dengan nama berbeda.",
  "wb.graphTitle": "Graf hubungan {label}",
  "wb.readSummary": "Baca ringkasan urusan ini",
  "wb.summaryPrompt":
    "Jelaskan “{label}” dengan jelas: bagaimana keadaannya, apa yang sudah diputuskan, dan apa yang masih terbuka.",
  "wb.building": "Menyintesis…",
  "wb.build": "Bangun hubungan dengan HY Memory",
  "wb.drawn": "Menampilkan {shown} / {total} memori",
  "wb.askLabel": "Tanya tentang urusan ini",
  "wb.askPlaceholder":
    "Tanya tentang urusan ini; jawaban hanya menggunakan memori terkonfirmasi",
  "wb.ask": "Tanya",
  "wb.originals": "Percakapan asli",
  "wb.evidenceNote":
    "Memori ini berasal dari {count} kutipan asli di bawah, diurutkan menurut waktu",
  "wb.evidenceNote.one":
    "Memori ini berasal dari {count} kutipan asli di bawah, diurutkan menurut waktu",
  "wb.pickCircle":
    "Pilih lingkaran pada graf untuk melihat teks aslinya di sini",
  "wb.unavailable": "Teks asli sumber tidak tersedia",
  "wb.fullConversation": "Lihat percakapan lengkap",
  "wb.you": "Anda",
  "wb.assistant": "Asisten",
  "wb.noMemories":
    "Belum ada memori. Cari percakapan asli yang diimpor di Riwayat percakapan.",
  "wb.waiting":
    "Beberapa percakapan dalam antrean menunggu persyaratan. Teks aslinya sudah disimpan dan dapat dicari.",
  "wb.conditions": "Lihat yang kurang",
  "wb.findings": "Yang terungkap dari catatan Anda",
  "wb.findingsNote":
    "Dihitung dari catatan lokal. Pilih salah satu untuk menyorot memori pendukungnya pada graf.",
  "wb.showGraph": "Lihat pada graf",
  "wb.loading": "Memuat memori…",
  "wb.coreStarting":
    "Akan muncul otomatis setelah Core lokal berjalan; tidak perlu memuat ulang.",
  "wb.nothingToConnect": "Belum ada yang dapat dihubungkan.",
  "wb.skeletonNote":
    "Otorisasi sumber untuk melihat struktur sumber dan topik; memori akan muncul saat dibuat.",
  "wb.authorize": "Otorisasi sumber",
  "wb.conversations": "{count} percakapan",
  "wb.conversations.one": "{count} percakapan",
  "wb.chipChanges": "Apa yang berubah di sini baru-baru ini?",
  "wb.chipSummarize": "Ringkas ini dalam beberapa poin",
  "wb.unknownTime": "Waktu tidak diketahui",
  "wb.unknownSource": "Sumber tidak diketahui",
  "wb.transcriptGone": "Percakapan ini sudah tidak ada di pustaka.",
  "wb.edgeCount": "{label}: {count} koneksi",
  "wb.edgeCount.one": "{label}: {count} koneksi",
  "wb.noRelations":
    "HY Memory belum menemukan hubungan. Gunakan tombol di atas untuk menjalankan System 2.",
  "wb.noModelRelations":
    "Belum ada model yang dapat digunakan; hubungan HY Memory tidak tersedia.",
  "provenance.multiple": "Beberapa penulis",
  "provenance.person": "Pesan asli pengguna",
  "provenance.agent": "Pesan asli agen",
  "provenance.unknown": "Penulis tidak diketahui",
  "provenance.imported": "Memori impor · {author}",
  "provenance.manual": "Ditambahkan secara manual",
  "provenance.answered": "Dibuat dari jawaban",
  "provenance.generated": "Dibuat dari balasan AI",
  "provenance.conversation": "Dibuat dari percakapan asli",
  "arrival.region": "Status pemrosesan memori",
  "arrival.importing": "Teks asli masih diimpor",
  "arrival.messages": "{count} pesan disimpan",
  "arrival.messages.one": "{count} pesan disimpan",
  "arrival.conversations": "{count} percakapan",
  "arrival.conversations.one": "{count} percakapan",
  "arrival.details": "Lihat kemajuan dan detail",
  "arrival.backlog":
    "Data yang ada sangat banyak; diproses bertahap di latar belakang demi kualitas",
  "arrival.items": "{count} item",
  "arrival.items.one": "{count} item",
  "arrival.configure": "Konfigurasi pemrosesan memori",
  "arrival.status": "Lihat status pemrosesan",
  "arrival.history": "Lihat percakapan tersimpan",
  "arrival.connect": "Hubungkan aplikasi",
  "arrival.retained": "Teks asli yang tersimpan tetap ada di perangkat ini.",
  "arrival.description.model-required":
    "Tinjau model pemrosesan sebelum melanjutkan.",
  "arrival.description.processing":
    "Memproses percakapan terbaru terlebih dahulu; memori lainnya akan muncul bertahap",
  "arrival.description.importing":
    "Menyimpan teks asli; kemajuan impor tidak mencerminkan kemajuan pembuatan memori.",
  "arrival.description.failed":
    "Periksa penyebab kegagalan sebelum memutuskan untuk mencoba lagi.",
  "arrival.description.unavailable":
    "Informasi terkonfirmasi terakhir tetap disimpan; periksa lagi kemajuan saat ini.",
  "arrival.description.refreshing":
    "Memeriksa catatan lokal; kemajuan yang belum terkonfirmasi tidak dihitung.",
  "arrival.description.checking":
    "Konten tersimpan tetap ada; kemajuan perlu diperbarui.",
  "arrival.description.stopped":
    "Pemrosesan telah berhenti; tinjau statusnya sebelum melanjutkan.",
  "arrival.description.idle":
    "Tidak ada tugas pemrosesan yang berjalan saat ini.",
  "arrival.description.empty":
    "Hubungkan aplikasi atau impor file percakapan; teks asli akan disimpan di perangkat ini terlebih dahulu.",
  "arrival.title.refreshing": "Konten tersimpan tetap ada; memeriksa kemajuan",
  "arrival.title.checking": "Kemajuan perlu diperbarui",
  "arrival.title.importing": "Mengimpor percakapan",
  "arrival.title.processing": "Membuat memori",
  "arrival.title.continue": "Perlu perhatian",
  "arrival.title.model": "Model pemrosesan belum dikonfigurasi",
  "arrival.title.empty": "Belum ada percakapan tersimpan",
  "handoff.region": "Bawa memori ke aplikasi lain",
  "handoff.answer": "Bawa jawaban dan referensi ke aplikasi lain",
  "handoff.selected": "Bawa memori yang dipilih ke aplikasi lain",
  "handoff.title": "Bawa ke aplikasi lain",
  "handoff.closeTitle": "Tutup pratinjau",
  "handoff.close": "Tutup pratinjau pengalihan",
  "handoff.stats": "{count} memori · {characters} karakter · Tidak terpotong",
  "handoff.stats.one":
    "{count} memori · {characters} karakter · Tidak terpotong",
  "handoff.content": "Konten lengkap yang dibawa ke aplikasi lain",
  "handoff.target": "Aplikasi tujuan",
  "handoff.other": "Aplikasi lain · Hanya salin",
  "handoff.copyOnly": " · Hanya salin",
  "handoff.boundary":
    "Hanya konten di atas yang disertakan, bukan percakapan asli. Menyalin tidak mengotorisasi koneksi; salinan di aplikasi lain tidak dapat ditarik kembali.",
  "handoff.copy": "Salin konten",
  "handoff.open": "Salin dan buka {host}",
  "handoff.copyFailed":
    "Gagal menyalin; tidak ada aplikasi yang dibuka. Pilih dan salin teks di pratinjau secara manual.",
  "handoff.opened":
    "Disalin dan {host} diminta untuk dibuka. Tidak ada yang dikirim; periksa kolom input di aplikasi tujuan.",
  "handoff.notPrefilled":
    "Disalin sepenuhnya. {host} tidak dapat diisi otomatis; tempel di aplikasi tujuan. Tidak ada yang dikirim.",
  "handoff.openFailed":
    "Disalin sepenuhnya, tetapi {host} tidak dapat dibuka. Tidak ada yang dikirim.",
  "handoff.copied": "{target} disalin sepenuhnya. Tidak ada yang dikirim.",
  "handoff.pasteTarget": ", siap ditempel ke {host}",
  "wb.memory": "Memori ini",
  "wb.node": "Node ini",
  "wb.matter": "Urusan ini",
  "wb.available": "Tersedia",
  "wb.superseded": "Digantikan",
  "wb.deleted": "Dihapus",
  "wb.revision": "Versi {revision}",
  "wb.collapse": "Ciutkan",
  "wb.oldSearch": "Pencarian masih menampilkan teks sebelum koreksi.",
  "wb.missingIndex":
    "HY Memory tidak memiliki memori ini; mencoba lagi tidak dapat memulihkannya.",
  "wb.retrySync": "Coba sinkronisasi lagi",
  "wb.correctForm": "Koreksi isi memori",
  "wb.content": "Isi memori",
  "wb.correct": "Koreksi",
  "wb.saveCorrection": "Simpan koreksi",
  "wb.cancelEdit": "Batalkan pengeditan",
  "wb.undo": "Batalkan perubahan terakhir",
  "wb.delete": "Hapus",
  "wb.nodeNote":
    "Ini bukan memori; memori terhubung kepadanya. {direct} tautan langsung; {indirect} dapat dicapai dalam dua lompatan.",
  "wb.direct": "Langsung",
  "wb.twoHops": "Dua lompatan",
  "wb.idle":
    "Lingkaran mewakili memori; garis menunjukkan memori yang disebut dalam percakapan yang sama. Pilih salah satu untuk melihat asal-usulnya dan mengeditnya. Memilih urusan lain di sebelah kiri akan memperbarui graf.",
  "wb.searching": "Mencari memori…",
  "wb.generated": "Sintesis model",
  "wb.extractive": "Kutipan asli",
  "wb.noHit": "Tidak ada kecocokan",
  "wb.answerSource": "{mode} · Berdasarkan {count} memori terkonfirmasi",
  "wb.answerSource.one": "{mode} · Berdasarkan {count} memori terkonfirmasi",
  "wb.recalled": "{count} memori ditemukan",
  "wb.recalled.one": "{count} memori ditemukan",
  "wb.saveMemory": "Simpan sebagai memori",
  "wb.tooLong":
    "Jawaban ini melebihi batas penyimpanan saat ini dan tidak dapat disimpan sebagai memori.",
  "wb.summary":
    "Ini adalah ringkasan, bukan memori. Ringkasan dibuat ulang setiap kali agar tetap selaras dengan memori di bawah.",
  "wb.thisAnswer": "Jawaban ini",
  "wb.type.source": "Aplikasi sumber",
  "wb.type.topic": "Topik",
  "wb.type.schema": "Sintesis HY",
  "wb.type.memory": "Memori",
  "wb.origin.hy_relation": "Sintesis HY Memory",
  "wb.origin.same_conversation": "Percakapan yang sama",
  "wb.origin.shared_tag": "Topik yang sama",
  "wb.origin.provenance": "Sumber teks asli",
  "wb.relation.evidence_source": "Dari",
  "wb.relation.same_conversation": "Percakapan yang sama",
  "wb.relation.shared_tag": "Topik yang sama",
  "wb.relation.derived_from": "Disintesis dari",
  "wb.relation.tagged_with": "Bertag",
  "wb.relation.related_to": "Terkait",
  guideEyebrow: "Cobalah",
  guideTitle: "Lanjutkan dari percakapan terakhir Anda",
  guideBack: "Kembali ke memori",
  guideChooseScene: "Pilih skenario",
  guideSame: "Lanjutkan di percakapan baru",
  guideSameDescription:
    "Temukan keputusan sebelumnya dalam percakapan baru dengan aplikasi yang sama.",
  guideSwitch: "Lanjutkan di aplikasi lain",
  guideSwitchDescription:
    "Pekerjaan yang sama, AI yang berbeda. Bawa konteks yang relevan terlebih dahulu.",
  guideNew: "Bantu alat baru memahami saya",
  guideNewDescription:
    "Mulailah dengan satu detail pilihan Anda alih-alih memperkenalkan diri lagi.",
  guideReadError: "Saat ini tidak dapat membaca memori. Coba lagi nanti.",
  guideDetailError:
    "Memori ini tidak dapat dibaca saat ini. Pilih yang lain atau muat ulang.",
  guideChanged:
    "Memori atau izinnya telah berubah. Muat ulang dan tinjau kembali.",
  guideConnectionChanged:
    "Status koneksi berubah; tidak ada yang disalin. Pilih metode lagi.",
  guideCopiedOpened:
    "Disalin dan {host} diminta untuk dibuka. Tidak ada yang dikirim; periksa kolom input.",
  guideCopiedNotFilled:
    "Disalin sepenuhnya, tetapi tidak dapat diisi otomatis. Tempel di aplikasi tujuan; tidak ada yang dikirim.",
  guideCopiedNotOpened:
    "Disalin sepenuhnya, tetapi aplikasi tujuan tidak dapat dibuka. Tidak ada yang dikirim.",
  guideCopied:
    "Disalin sepenuhnya. Tidak ada yang dikirim dan tidak ada akses baca baru yang diberikan.",
  guideValidationError:
    "Tidak dapat memverifikasi memori atau izin saat ini. Pratinjau lama disembunyikan; muat ulang dan coba lagi.",
  guideCopyError:
    "Tidak dapat memverifikasi konten terbaru atau menyelesaikan penyalinan. Coba lagi; aplikasi tujuan tidak dibuka.",
  guideManual: "Ditambahkan di aplikasi",
  guideOriginalApp: "Aplikasi sumber",
  guideListSeparator: ", ",
  guideSaved: "{count} pesan asli disimpan di perangkat ini · ",
  "guideSaved.one": "{count} pesan asli disimpan di perangkat ini · ",
  guideReadingMemories: "Memuat memori yang telah diproses",
  guideLoaded: "{count} memori yang dapat digunakan dimuat di halaman ini",
  "guideLoaded.one":
    "{count} memori yang dapat digunakan dimuat di halaman ini",
  guideSeparate: ". Tahapan ini dihitung terpisah.",
  guideChooseBackground: "Pilih konteks",
  guideCheckSources: "Tinjau sumber",
  guideTry: "Coba di percakapan baru",
  guideLoading: "Memuat memori yang ada di perangkat ini…",
  guideEmptyTitle: "Tambahkan satu konteks yang berguna terlebih dahulu",
  guideEmptyDescription:
    "Tidak ada memori yang tersedia untuk uji coba ini. Teks asli yang diimpor bukan memori yang telah diproses, dan saran tidak dibagikan secara otomatis.",
  guideAdd: "Tambah memori",
  guideConnections: "Lihat impor dan koneksi",
  guideRefresh: "Muat ulang",
  guideBackgroundTitle: "Konteks untuk uji coba ini",
  guideRefreshExample: "Muat ulang contoh",
  guideChooseMemory: "Pilih memori",
  guideLoadedScope:
    "Pilih dari {count} memori yang dapat digunakan yang dimuat di sini; ini bukan seluruh riwayat Anda.",
  "guideLoadedScope.one":
    "Pilih dari {count} memori yang dapat digunakan yang dimuat di sini; ini bukan seluruh riwayat Anda.",
  guideCheckingEvidence: "Memeriksa bukti asli…",
  guideRevision:
    "{source} · Versi {revision} · Dibuat sistem, perlu Anda tinjau",
  guideScope: "Cakupan: ",
  guideAllApps:
    "Tersedia di semua aplikasi; belum tentu merupakan preferensi pribadi",
  guidePrivate:
    "Pengambilan eksternal nonaktif; berbagi secara manual memerlukan keputusan tersendiri",
  guideLimited: "Cakupan terbatas; pengambilan eksternal tidak diperluas",
  guideEvidence: "Bukti asli · {count} kutipan",
  "guideEvidence.one": "Bukti asli · {count} kutipan",
  guideUnknownSource: "Sumber tidak diketahui",
  guideYou: "Anda",
  guideUnknownAuthor: "Penulis tidak diketahui",
  guideUnknownTime: "Waktu tidak diketahui",
  guideUnavailable:
    "Teks asli atau izin saat ini tidak tersedia. Pilih memori lain atau muat ulang; konten ini tidak akan dibagikan.",
  guideDraftTitle: "Pembuka untuk percakapan berikutnya",
  guideChooseApp: "Pilih aplikasi untuk dicoba",
  guideCopyOnly: "Pilih aplikasi sendiri · Hanya salin",
  guideHostsError:
    "Tidak dapat membaca status koneksi. Coba lagi atau salin konteksnya saja.",
  guideNoApp:
    "Tidak ada aplikasi terhubung yang dipilih. Berbagi secara manual tidak memasang koneksi.",
  guideWired:
    "Pemeriksaan mandiri terakhir berhasil; pastikan berfungsi untuk penggunaan ini.",
  guideInstalled: "Koneksi terpasang; akses baca belum diverifikasi.",
  guideNotInstalled: "Koneksi baca belum terpasang.",
  guideManage: "Kelola koneksi aplikasi",
  guideChooseRoute: "Pilih cara membawa konteks",
  guideContext: "Bawa konteks",
  guideQuery: "Biarkan aplikasi mencari",
  guideTopic: "Topik yang akan dilanjutkan",
  guideQueryHint:
    "Kirim kueri saja; jangan masukkan jawaban yang diharapkan di atas ke percakapan baru.",
  guideNewHint:
    "Hanya bagikan memori yang Anda pilih; profil pribadi tidak dibuat secara otomatis.",
  guideContextHint:
    "Sertakan konteks ini dan kutipannya; ini bukan uji pengambilan otomatis.",
  guidePreview: "Pratinjau lengkap pembuka",
  guidePreviewPlaceholder: "Pembuka akan muncul saat bukti asli tersedia",
  guideChecking: "Memeriksa…",
  guideCopy: "Salin pembuka",
  guideCopyOpen: "Salin dan buka {host}",
  guideNoReceipt:
    "Tidak ada tanda terima dari panggilan eksternal kali ini. Apakah percakapan baru mengutip konteks yang tepat?",
  guideVerified: "Saya sudah meninjau hasilnya",
  guideMissing: "Tidak dapat menemukannya",
  guideFeedbackOk:
    "Masukan Anda: sudah ditinjau. Ini bukan catatan verifikasi otomatis.",
  guideFeedbackMissing:
    "Masukan Anda: tidak ditemukan. Periksa dahulu impor sumber, cakupan memori, dan koneksi aplikasi.",
  guideInvitation: "Undangan untuk mencoba",
  guideInvitationHint:
    "Coba lanjutkan percakapan baru dengan salah satu memori Anda.",
  guideTryOnce: "Coba",
  guideLater: "Nanti saja",
  guideDismiss: "Tutup undangan",
  healthTitle: "Diagnostik kesehatan",
  healthLocal: "Layanan lokal",
  healthBackground: "Layanan latar belakang",
  healthSources: "Total per sumber",
  healthProcessing: "Pemrosesan memori",
  healthHosts: "Koneksi AI",
  healthDetailed: "Log terperinci",
  healthDetailedTitle: "Log terperinci (untuk pemecahan masalah)",
  healthDetailedHint:
    "Nonaktif secara default. Aktifkan hanya jika diminta oleh tim dukungan untuk pemecahan masalah.",
  healthDetailedError:
    "Tidak dapat mengubah log terperinci. Silakan coba lagi.",
  healthRefresh: "Muat ulang status",
  healthIncident: "Tandai waktu masalah",
  healthIncidentDesktop: "Tandai waktu masalah di aplikasi desktop.",
  healthIncidentPrompt: "Jelaskan masalahnya secara singkat (opsional)",
  healthIncidentCopied: "ID masalah disalin: {id}",
  healthIncidentError: "Tidak dapat menandai waktu masalah. Silakan coba lagi.",
  healthReadFailed: "Untuk sementara tidak tersedia",
  healthStale: " · Data sudah usang; menampilkan hasil terakhir.",
  healthReading: "Membaca status…",
  healthRereading:
    "Memuat ulang; menampilkan pembacaan terakhir yang berhasil…",
  healthReadSuccessTime: "Pembacaan terakhir yang berhasil",
  healthReadFailedTime: "Pembacaan gagal pada",
  healthReadingSources: "Membaca status layanan lokal dan sumber…",
  healthPrevious: "Pembacaan terakhir",
  healthPreviousPrefix: "Pembacaan terakhir: ",
  healthRunning: "Berjalan",
  healthCoreVersion: "{state} · Versi {version} · Instans {instance}",
  healthCoreDisconnected: "Core lokal belum terhubung.",
  healthBackgroundUnavailable:
    "Layanan latar belakang tidak dapat diperiksa di sini. Gunakan aplikasi desktop yang didukung.",
  healthRuntime: "Core: {core} · Jembatan host: {broker}",
  healthSourceCount: "{conversations} percakapan · {messages} pesan",
  healthNoSources: "Belum ada sumber yang terhubung.",
  healthTaskCounts:
    "{running} diproses · {pending} dalam antrean · {completed} selesai · {failed} gagal",
  healthImportDetails: "Detail impor dan pemrosesan",
  healthRawTurns: "Teks asli dan giliran lengkap",
  healthRawCounts: "{raw} teks asli · {turns} giliran lengkap",
  healthHy: "Pemrosesan HY",
  healthHyCounts:
    "{calls} panggilan · {completed} selesai · {unknown} belum pasti · {failed} gagal · {cancelled} dibatalkan",
  healthOutput: "Keluaran",
  healthMemories: "{count} memori",
  "healthMemories.one": "{count} memori",
  healthWorkbuddy: "Pencarian memori WorkBuddy",
  healthWorkbuddyCounts:
    "{injected} injeksi · {timedOut} habis waktu · {unavailable} tidak tersedia",
  healthBlocked: "{kind} terhambat oleh {reason} ({count})",
  healthRetrying: "Mencoba ulang pemrosesan · {count}",
  "healthRetrying.one": "Mencoba ulang pemrosesan · {count}",
  healthNoIssues: "Tidak ada masalah yang perlu diperhatikan.",
  healthHostsUnavailable:
    "Tidak dapat dibaca di browser; periksa aplikasi desktop.",
  healthNoHosts: "Tidak ada alat AI yang didukung terdeteksi di perangkat ini.",
  healthBackgroundHealthy: "Layanan latar belakang tersedia",
  healthBackgroundStopped: "Layanan latar belakang berhenti",
  healthBackgroundDegraded: "Layanan latar belakang tersedia sebagian",
  healthBackgroundUntrusted:
    "Identitas layanan latar belakang tidak dapat diverifikasi",
  healthBackgroundUnknown: "Status latar belakang belum terkonfirmasi",
  healthStopped: "Berhenti",
  healthUnhealthy: "Tidak dapat terhubung",
  healthUntrusted: "Identitas belum diverifikasi",
  healthUnknown: "Belum terkonfirmasi",
  healthNotChecked: "Belum diperiksa",
  healthWired: "Terhubung",
  healthBlind: "Terpasang, tetapi tidak dapat mengambil memori",
  healthUnreachable: "Tidak terhubung",
  startupReadError: "Tidak dapat membaca status mulai. Silakan coba lagi.",
  startupRetry: "Coba lagi",
  startupOpening: "Membuka MemHub…",
  nativeHelp: "Bantuan",
  nativeExportLogs: "Ekspor log untuk tim dukungan",
  nativeExportError: "Tidak dapat mengekspor log. Silakan coba lagi nanti.",
  nativeLogin: "Jalankan {product} saat masuk",
  nativeTrialUnavailable: " (tidak tersedia di build ini)",
  nativeCliManaged: " (dikelola oleh CLI)",
  nativeStatusUnavailable: " (status tidak tersedia)",
  nativeDevelopmentUnavailable: " (tidak tersedia saat pengembangan)",
  nativeOpen: "Buka {product}",
  nativeQuit: "Keluar dari {product} (layanan latar belakang tetap berjalan)",
  nativeQuitStopsService:
    "Keluar dari {product} (layanan memori juga dihentikan)",
  nativeLoginError:
    "Tidak dapat mengubah pengaturan mulai saat masuk. Silakan coba lagi.",
  nativeMoveTitle: "Pindahkan MemHub ke Aplikasi terlebih dahulu",
  nativeMoveDetail:
    "Buka kembali MemHub dari Aplikasi untuk menghubungkan aplikasi lain dan memasang alat baris perintah.",
  nativeAcknowledge: "OK",
  graphWidthTitle: "Pustaka memori yang ada tidak cocok dengan model bawaan",
  graphWidthDescription:
    "Pustaka memori Anda menggunakan {stored} dimensi, tetapi model bawaan menghasilkan {expected}. Keduanya tidak dapat bekerja bersama; ekstraksi akan terus gagal sampai Anda memilih opsi.",
  graphWidthKeep:
    "Pertahankan pustaka yang ada (default): Tetap gunakan konfigurasi vektor sebelumnya; semua {count} memori dan graf tidak berubah",
  graphWidthRebuild:
    "Gunakan model bawaan: Arsipkan (tidak dihapus) graf yang ada; hubungan untuk {count} memori perlu dibangun ulang",
  graphWidthExport:
    "Ekspor dahulu, lalu putuskan: Biarkan semuanya tidak berubah untuk saat ini dan pilih lagi setelah mengekspor",
  graphWidthExported:
    "Diekspor ke {path}. Pustaka yang ada tidak berubah; Anda dapat memilih lagi.",
  graphWidthUnknown:
    "Pilihan dimensi belum terkonfirmasi. Pustaka memori yang ada tidak berubah; silakan coba lagi.",
  graphWidthConfirm: "Konfirmasi pilihan",
  importUserProgress:
    "Mengimpor riwayat percakapan Anda. {count} percakapan telah diimpor. Memori akan muncul bertahap; Anda dapat terus menggunakan aplikasi.",
  "importUserProgress.one":
    "Mengimpor riwayat percakapan Anda. {count} percakapan telah diimpor. Memori akan muncul bertahap; Anda dapat terus menggunakan aplikasi.",
  importUserModel:
    "Model belum dikonfigurasi. Percakapan baru belum akan diproses menjadi memori.",
  importUserIncomplete:
    "Sebagian konten belum sepenuhnya diproses. Periksa Pengaturan.",
  importUserSettings: "Buka Pengaturan",
  diagnosticExportDesktop: "Ekspor diagnostik dari aplikasi desktop.",
  diagnosticExported: "Log diekspor: {filename}",
  diagnosticFile: "file diagnostik",
  diagnosticExportFailed:
    "Tidak dapat mengekspor diagnostik. Silakan coba lagi.",
  debugTitle: "Log debug",
  debugCount: "{count} log dari sesi ini",
  "debugCount.one": "{count} log dari sesi ini",
  debugDiagnostics: "Diagnostik",
  debugUsageSubtitle: "Penggunaan model pemrosesan memori",
  debugTabs: "Tab diagnostik",
  debugLogsTab: "Log",
  debugUsageTab: "Penggunaan model",
  debugFullscreen: "Layar penuh",
  debugExitFullscreen: "Keluar dari layar penuh",
  debugFullscreenLabel: "Tampilkan log dalam layar penuh",
  debugExitFullscreenLabel: "Keluar dari log layar penuh",
  usageRange15m: "15 menit terakhir",
  usageRange1h: "1 jam terakhir",
  usageRange6h: "6 jam terakhir",
  usageRange24h: "24 jam terakhir",
  usageRange7d: "7 hari terakhir",
  usageRange: "Rentang waktu",
  usageRangeLabel: "Rentang waktu penggunaan model",
  usageLoading: "Memuat…",
  usageReadError: "Tidak dapat memuat",
  usageLive: "Muat ulang setiap 5 detik",
  usageTotalTokens: "Total token",
  usageRequests: "Permintaan",
  usageChartRegion: "Token per menit menurut prompt",
  usageChartTitle: "Penggunaan token menurut prompt",
  usageChartSubtitle: "Per menit · Total token",
  usageBreakdown: "Detail prompt",
  usageCurrentRange: "Rentang waktu saat ini",
  usageRequestCount: "{count} permintaan",
  "usageRequestCount.one": "{count} permintaan",
  usageEmpty:
    "Tidak ada panggilan model pemrosesan memori dalam rentang waktu ini",
  usageNoChart: "Belum ada data bagan",
  usageChartAria: "Penggunaan token per menit menurut prompt",
  debugPause: "Jeda pembaruan langsung",
  debugResume: "Lanjutkan pembaruan langsung",
  debugRefresh: "Muat ulang sekarang",
  debugClose: "Tutup Log debug",
  debugSearchPlaceholder:
    "Cari peristiwa, komponen, kode kesalahan, atau konten",
  debugSearch: "Cari log",
  debugSource: "Sumber log",
  debugAllSources: "Semua sumber",
  debugCapture: "Tingkat perekaman",
  debugDisplay: "Tingkat tampilan",
  debugAll: "Semua",
  debugMatches: "{count} kecocokan",
  "debugMatches.one": "{count} kecocokan",
  debugLive: "Diperbarui langsung",
  debugPaused: "Dijeda",
  debugTruncated: "Log terlalu banyak; dipotong",
  debugReadError: "Tidak dapat memuat",
  debugLoading: "Memuat log…",
  debugEmpty: "Tidak ada log yang cocok",
  debugOpened: "Log debug terbuka",
  debugOpen: "Buka Log debug",
  "这里需要 API Key，不是网址。请填写服务方提供的密钥。":
    "Masukkan kunci API dari penyedia layanan, bukan URL.",
  "服务商拒绝了这个 API Key（401）。请确认密钥属于当前填写的服务地址。":
    "Penyedia menolak kunci API ini (401). Pastikan kunci sesuai dengan endpoint yang dimasukkan.",
  "服务商没有这个模型名，请对照服务商的模型列表核对。":
    "Penyedia tidak mengenali nama model ini. Periksa daftar model penyedia.",
  "连不上接口地址，请检查网络和服务地址是否正确。":
    "Tidak dapat terhubung ke endpoint. Periksa jaringan dan alamat layanan.",
  未提供接口地址: "Endpoint belum diisi",
  "请填写生成模型名。": "Masukkan nama model generatif.",
  "请填写完整的 http:// 或 https:// 接口地址。":
    "Masukkan URL endpoint http:// atau https:// yang lengkap.",
  "请填写向量模型名。": "Masukkan nama model vektor.",
  "请填写完整的向量模型接口地址。":
    "Masukkan URL endpoint model vektor yang lengkap.",
  "这份配置在测试期间被改过，刚才测的是改动后的版本。配置已保存但未启用，请重新测试并启用。":
    "Konfigurasi ini berubah selama pengujian; versi yang lebih baru telah diuji. Konfigurasi disimpan tetapi belum diaktifkan. Uji dan aktifkan lagi.",
  "测试结果来自这份配置更早的版本。配置已保存但未启用，请重新测试并启用。":
    "Hasil pengujian berasal dari versi lama konfigurasi ini. Konfigurasi disimpan tetapi belum diaktifkan. Uji dan aktifkan lagi.",
  "配置已保存，连接测试未通过。请核对配置后重试，历史对话尚未发送。":
    "Konfigurasi disimpan, tetapi uji koneksi gagal. Tinjau lalu coba lagi. Riwayat percakapan tidak dikirim.",
  "配置已保存，尚未确认启用成功。请核对配置后重试。":
    "Konfigurasi disimpan, tetapi pengaktifan belum terkonfirmasi. Tinjau lalu coba lagi.",
  "配置尚未完成测试和启用，请核对后重试。":
    "Pengujian dan pengaktifan belum selesai. Tinjau konfigurasi lalu coba lagi.",
  "配置尚未保存成功，已填写内容保留，可核对后重试。":
    "Penyimpanan konfigurasi belum terkonfirmasi. Isian Anda tetap disimpan; tinjau lalu coba lagi.",
  "只需配置生成模型。向量嵌入已内置在本机，不为 System 1、System 2 分开选模型。":
    "Hanya model generatif yang perlu dikonfigurasi. Vektor sudah tersedia secara lokal; System 1 dan System 2 tidak memerlukan model terpisah.",
  "无法读取测试恢复记录，暂不发送新的测试。":
    "Tidak dapat membaca catatan pemulihan pengujian; pengujian baru tidak akan dikirim.",
  "上次模型测试结果待核对，暂不修改或启用配置。":
    "Pengujian model sebelumnya perlu ditinjau; konfigurasi belum akan diubah atau diaktifkan.",
  核对测试结果: "Tinjau hasil pengujian",
  "上次模型创建结果待核对，暂不开始新的操作。":
    "Pembuatan model sebelumnya perlu ditinjau; operasi baru belum akan dimulai.",
  核对创建结果: "Tinjau hasil pembuatan",
  "配置已保存，正在测试连接…": "Konfigurasi disimpan; menguji koneksi…",
  "连接通过，正在启用模型…": "Koneksi berhasil; mengaktifkan model…",
  刷新配置列表: "Muat ulang konfigurasi",
  自定义一个模型: "Konfigurasi model kustom",
  已保存的模型配置: "Konfigurasi model tersimpan",
  "已保存，已启用": "Disimpan dan diaktifkan",
  "已保存，连接通过": "Disimpan; koneksi berhasil",
  "已保存，测试未通过": "Disimpan; pengujian gagal",
  "已保存，待测试": "Disimpan; menunggu pengujian",
  版本: "Versi",
  "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”；已保存的配置不会丢失。":
    "Izinkan MemHub mengakses Keychain. Pilih ‘Selalu Izinkan’ saat diminta; konfigurasi tersimpan tidak akan hilang.",
  "测试只向此接口发送测试内容，不发送历史对话。":
    "Pengujian hanya mengirim konten uji ke endpoint ini, bukan riwayat percakapan.",
  重试测试并启用: "Uji ulang dan aktifkan",
  测试并启用: "Uji dan aktifkan",
  编辑配置: "Edit konfigurasi",
  新增配置: "Tambah konfigurasi",
  编辑: "Edit",
  "。修改后需重新测试并设为当前模型，内容授权不会自动更新。":
    ". Setelah mengubah, uji lagi dan tetapkan sebagai model saat ini. Izin konten tidak diperbarui secara otomatis.",
  "配置已在其他位置变化，草稿已保留。请取消编辑后重新打开核对。":
    "Konfigurasi berubah di tempat lain; draf Anda tetap disimpan. Batalkan pengeditan dan buka kembali untuk meninjau.",
  服务商预设: "Preset penyedia",
  自定义兼容服务: "Layanan kompatibel kustom",
  "只是帮你填好接口地址和一个模型名，不代表这个模型当前可用。 请对照服务商的模型列表核对后再保存。":
    "Ini mengisi endpoint dan nama model, tetapi tidak menjamin ketersediaannya. Periksa daftar model penyedia sebelum menyimpan.",
  协议: "Protokol",
  "与模型服务提供的接口协议保持一致。":
    "Sesuaikan dengan protokol yang disediakan layanan model.",
  配置名称: "Nama konfigurasi",
  "用于区分配置，不影响模型请求。":
    "Mengidentifikasi konfigurasi; tidak memengaruhi permintaan model.",
  生成模型名: "Nama model generatif",
  "例如：gpt-4o-mini…": "Misalnya: gpt-4o-mini…",
  "填写服务方提供的完整模型标识。":
    "Masukkan ID model lengkap dari penyedia layanan.",
  "高级：自行配置向量模型": "Lanjutan: konfigurasikan model vektor sendiri",
  向量配置模式: "Mode konfigurasi vektor",
  本地内置: "Model lokal bawaan",
  自定义向量模型: "Model vektor kustom",
  向量模型名: "Nama model vektor",
  "例如：text-embedding-3-small…": "Misalnya: text-embedding-3-small…",
  向量模型来源: "Penyedia model vektor",
  "本机 Ollama": "Ollama lokal",
  "同源 OpenAI-compatible（共用 API Key）":
    "Kompatibel OpenAI, asal yang sama (kunci API bersama)",
  "向量模型 Base URL": "URL dasar model vektor",
  "为避免凭据外泄，向量地址必须与生成模型同源。":
    "Untuk melindungi kredensial, endpoint vektor harus berasal dari asal yang sama dengan model generatif.",
  "使用内置本地向量模型；不会发送内容给远程向量服务。":
    "Menggunakan model vektor lokal bawaan; tidak ada konten yang dikirim ke layanan vektor jarak jauh.",
  自定义向量: "Vektor kustom",
  尚未填写模型: "Model belum diisi",
  尚未填写地址: "Alamat belum diisi",
  "填写服务接口地址，不是聊天网页地址。":
    "Masukkan endpoint API, bukan URL situs web obrolan.",
  "（可选）": " (opsional)",
  "保存后不会再次显示…": "Tidak akan ditampilkan lagi setelah disimpan…",
  "留空保留已保存密钥；填写新密钥后需重新测试和启用。":
    "Kosongkan untuk mempertahankan kunci tersimpan; uji dan aktifkan lagi setelah memasukkan kunci baru.",
  "换了服务商后需要填写新的 API Key，已保存的密钥不会带到新地址。":
    "Masukkan kunci API baru setelah mengganti penyedia; kunci tersimpan tidak dikirim ke endpoint baru.",
  "保存在系统安全存储中，不随安装包分发。":
    "Disimpan di penyimpanan aman sistem; tidak didistribusikan bersama aplikasi.",
  "关闭思考链（向支持的厂商下发 thinking-off 参数）":
    "Nonaktifkan rantai pemikiran (kirim thinking-off ke penyedia yang mendukung)",
  保存修改: "Simpan perubahan",
  保存配置: "Simpan konfigurasi",
  "正在启用配置…": "Mengaktifkan konfigurasi…",
  测试并完成配置: "Uji dan selesaikan penyiapan",
  保存并测试启用: "Simpan, uji, dan aktifkan",
  取消编辑: "Batalkan pengeditan",
  "密钥保存在系统安全存储；测试仅发送测试内容。":
    "Kunci disimpan dengan aman; pengujian hanya mengirim konten uji.",
  当前使用: "Sedang digunakan",
  未启用: "Belum diaktifkan",
  测试目标: "Target pengujian",
  "。仅发送测试内容，不发送历史对话。":
    ". Hanya konten uji yang dikirim, bukan riwayat percakapan.",
  测试连接: "Uji koneksi",
  设为当前模型: "Tetapkan sebagai model saat ini",
  删除: "Hapus",
  "粘贴 API Key": "Tempel kunci API",
  "密钥尚未保存成功，已填写内容保留，可重试。":
    "Tidak dapat menyimpan kunci. Isian Anda tetap disimpan; Anda dapat mencoba lagi.",
  生成接收方: "Penerima generasi",
  向量接收方: "Penerima vektor",
  本机: "Di perangkat ini",
  "。启用此配置后按上方选择处理历史及未来允许来源。":
    ". Setelah konfigurasi ini diaktifkan, pilihan di atas menentukan sumber lama dan mendatang yang diizinkan untuk diproses.",
  "保存 key": "Simpan kunci",
  "还没有 key？去申请": "Belum punya kunci? Ajukan",
  删除这张卡: "Hapus kartu ini",
  "密钥已配置，当前读取状态待确认。":
    "Kunci dikonfigurasi; status akses perlu dikonfirmasi.",
  "等待 key：其余都已经填好了，贴上 key 就能用。":
    "Menunggu kunci. Pengaturan lain sudah siap; tempel kunci Anda untuk melanjutkan.",
  "key 已保存，还没测过这个模型能不能用。":
    "Kunci disimpan; model ini belum diuji.",
  "Ollama（本机）": "Ollama (lokal)",
  "内置 bge-small-zh-v1.5": "bge-small-zh-v1.5 bawaan",
  通过: "Lulus",
  "HY 托管模型尚不可用": "Model terkelola HY tidak tersedia",
  "缺少 API Key": "Kunci API tidak ada",
  服务不可达: "Layanan tidak dapat dijangkau",
  "认证失败；更新 key 后再检测，会自动接着处理":
    "Autentikasi gagal; perbarui kunci dan uji lagi untuk melanjutkan pemrosesan.",
  请求频率受限: "Batas frekuensi permintaan tercapai",
  "模型账户余额不足；充值或换 key 后再检测，会自动接着处理":
    "Saldo akun model tidak cukup; isi ulang atau ganti kunci, lalu uji lagi untuk melanjutkan.",
  没有找到模型: "Model tidak ditemukan",
  "该网关不提供模型列表，已改由生成调用验证":
    "Gateway tidak memiliki daftar model; diverifikasi melalui permintaan generasi.",
  服务返回不符合协议: "Respons layanan tidak sesuai protokol",
  结构化输出未通过: "Uji keluaran terstruktur gagal",
  "需要填写 Embedding 模型": "Masukkan model vektor",
  "Embedding 模型不可用": "Model vektor tidak tersedia",
  "请先核对上次模型测试结果，暂不修改或启用配置。":
    "Tinjau dahulu pengujian model sebelumnya; konfigurasi belum akan diubah atau diaktifkan.",
  "本地恢复记录已变化，仍保持锁定。请重新打开后核对。":
    "Catatan pemulihan lokal telah berubah. Konfigurasi tetap terkunci; buka kembali dan tinjau.",
  测试结果: "Hasil pengujian",
  "暂不支持一键插入：它没有 MCP 通道，无法发放可撤销的记忆凭证。":
    "Koneksi sekali klik tidak tersedia: aplikasi ini tidak memiliki saluran MCP untuk kredensial memori yang dapat dicabut.",
  应用控制通道暂不可用: "Saluran kontrol aplikasi tidak tersedia",
  appFailureReason: " Alasan: {reason}. ",
  appFailureReread:
    "Status koneksi juga tidak dapat dimuat ulang; status yang ditampilkan mungkin sudah usang.",
  模型: "Model",
  连接其他应用: "Hubungkan aplikasi lain",
  帮助与诊断: "Bantuan dan diagnostik",
  设置分组: "Bagian pengaturan",
  记忆模型: "Model memori",
  当前模型: "Model saat ini",
  "分别管理记忆整理与搜索所使用的模型。":
    "Kelola secara terpisah model yang digunakan untuk mengolah dan mencari memori.",
  记忆整理: "Pengolahan memori",
  "把会话理解并整理成可长期使用的记忆。":
    "Mengubah percakapan menjadi memori yang dapat digunakan dalam jangka panjang.",
  记忆搜索: "Pencarian memori",
  "在需要时找到与你的问题最相关的记忆。":
    "Menemukan memori yang paling relevan dengan pertanyaan Anda saat diperlukan.",
  "本地内置（推荐）": "Bawaan lokal (disarankan)",
  "Embedding · 配置记忆整理后可自定义":
    "Embedding · Dapat disesuaikan setelah pengolahan memori dikonfigurasi",
  "Embedding · 本机处理，无需额外配置":
    "Embedding · Diproses secara lokal, tanpa konfigurasi tambahan",
  "正在读取模型…": "Memuat model…",
  模型状态暂不可读: "Status model tidak tersedia",
  尚未配置模型: "Belum ada model yang dikonfigurasi",
  修改模型: "Edit model",
  配置模型: "Konfigurasi model",
  刷新模型状态: "Muat ulang status model",
  "本机 / 受管端点": "Endpoint lokal / terkelola",
  接口地址待核对: "Endpoint perlu ditinjau",
  "模型状态未能读取，请重试。":
    "Tidak dapat membaca status model. Silakan coba lagi.",
  "模型状态刷新未成功，仍显示上次确认的模型状态；请重试。":
    "Tidak dapat memuat ulang status model. Menampilkan status terkonfirmasi terakhir; silakan coba lagi.",
  "上次模型测试或创建还待核对，配置暂时不能改动。请先在下方模型配置区核对结果； 现有配置和原文不会被改动。":
    "Pengujian atau pembuatan model sebelumnya perlu ditinjau. Periksa hasilnya di bawah sebelum mengedit. Konfigurasi dan teks asli Anda tidak berubah.",
  用模型沉淀记忆: "Buat memori dengan model",
  "开启后，导入和同步到本机的会话会交给当前模型整理成记忆，会产生模型调用。 停止后不再调用模型、不再整理记忆，但会话仍会照常导入和保存；已排队的内容会留着，重新开启后继续。 模型配置始终保留，重新开启不需要重新填写。":
    "Saat aktif, percakapan yang diimpor dan disinkronkan diproses menjadi memori oleh model saat ini, dengan menggunakan panggilan model. Saat nonaktif, percakapan tetap diimpor dan disimpan; pekerjaan dalam antrean dilanjutkan saat Anda mengaktifkannya lagi. Konfigurasi model Anda tetap tersimpan.",
  "启用未能确认，仍显示上次确认的状态；请重试。":
    "Pengaktifan belum terkonfirmasi. Menampilkan status terkonfirmasi terakhir; silakan coba lagi.",
  "停止未能确认，模型可能仍在整理记忆，请重试。":
    "Penghentian belum terkonfirmasi. Model mungkin masih memproses memori; silakan coba lagi.",
  "当前安装还不能读取这个开关，请确认后台服务已启动后重试。":
    "Instalasi ini tidak dapat membaca sakelar tersebut. Pastikan layanan latar belakang berjalan lalu coba lagi.",
  关于: "Tentang",
  "此应用还没有读取授权，请先授权它的对话目录。":
    "Aplikasi ini belum memiliki izin baca. Otorisasi direktori percakapannya terlebih dahulu.",
  "此应用的读取授权已撤销，请重新授权。":
    "Izin baca aplikasi ini telah dicabut. Otorisasi lagi.",
  "此应用的对话目录已被重建，请重新授权。":
    "Direktori percakapan aplikasi ini telah dibangun ulang. Otorisasi lagi.",
  "此应用的对话目录现在读不到，请确认应用已安装并产生过对话。":
    "Direktori percakapan aplikasi ini tidak dapat dibaca. Pastikan aplikasi terpasang dan memiliki percakapan.",
  "此应用的对话目录已变更，请按最新目录重新授权。":
    "Direktori percakapan aplikasi ini berubah. Otorisasi direktori yang baru.",
  "此应用还没有读取授权，请先授权它的对话目录。完成后再开启持续同步。":
    "Aplikasi ini belum memiliki akses baca. Otorisasi direktori percakapannya, lalu aktifkan sinkronisasi berkelanjutan.",
  "此应用的读取授权已撤销，请重新授权。完成后再开启持续同步。":
    "Akses baca aplikasi ini telah dicabut. Otorisasi lagi, lalu aktifkan sinkronisasi berkelanjutan.",
  "此应用的对话目录已被重建，请重新授权。完成后再开启持续同步。":
    "Direktori percakapan aplikasi ini telah dibangun ulang. Otorisasi lagi, lalu aktifkan sinkronisasi berkelanjutan.",
  "此应用的对话目录现在读不到，请确认应用已安装并产生过对话。完成后再开启持续同步。":
    "Direktori percakapan aplikasi ini tidak dapat dibaca. Pastikan aplikasi terpasang dan memiliki percakapan, lalu aktifkan sinkronisasi berkelanjutan.",
  "此应用的对话目录已变更，请按最新目录重新授权。完成后再开启持续同步。":
    "Direktori percakapan aplikasi ini berubah. Otorisasi direktori yang baru, lalu aktifkan sinkronisasi berkelanjutan.",
  "此应用的读取授权已失效，请重新授权后再开启持续同步。":
    "Akses baca aplikasi ini telah kedaluwarsa. Otorisasi lagi sebelum mengaktifkan sinkronisasi berkelanjutan.",
  "此应用的授权或目录已变化，已按最新状态显示，请确认后再试。":
    "Izin atau direktori aplikasi ini berubah. Tinjau status terbaru lalu coba lagi.",
  模型配置表单: "Formulir konfigurasi model",
  "部分运行状态暂时无法读取，当前显示最后读到的状态，请重试。":
    "Sebagian informasi status tidak tersedia. Menampilkan status terakhir yang diketahui; silakan coba lagi.",
  "当前安装还不能设置登录后后台启动。":
    "Instalasi ini tidak dapat mengatur mulai saat masuk.",
  "登录后后台启动设置未能确认，请重试。":
    "Pengaturan mulai saat masuk belum terkonfirmasi. Silakan coba lagi.",
  "当前安装还不能退出界面，请从菜单栏退出。":
    "Instalasi ini tidak dapat menutup jendela. Gunakan bilah menu.",
  "退出界面未能确认，请重试。":
    "Penutupan jendela belum terkonfirmasi. Silakan coba lagi.",
  登录后启动后台服务: "Mulai layanan latar belakang saat masuk",
  "无需打开界面，持续同步、整理和提供记忆。":
    "Terus sinkronkan, proses, dan sediakan memori tanpa membuka aplikasi.",
  导出日志: "Ekspor log",
  "把本机运行日志打包成一个文件，发给开发排查。":
    "Kemas semua log lokal. Log dapat memuat isi percakapan atau jalur lokal; bagikan hanya kepada pengembang tepercaya.",
  "确定退出 One Memory 界面吗？": "Tutup jendela MemHub?",
  "后台服务会继续同步、整理和提供记忆。":
    "Layanan latar belakang akan terus menyinkronkan, memproses, dan menyediakan memori.",
  取消: "Batal",
  退出界面: "Tutup jendela",
  应用详情: "Detail aplikasi",
  返回应用: "Kembali ke aplikasi",
  同步开关: "Sakelar sinkronisasi",
  "开启后处理历史待办，并持续接收此应用的新会话。":
    "Saat aktif, memproses riwayat yang tertunda dan terus menerima percakapan baru dari aplikasi ini.",
  "正在读取已有对话，首次开启可能需要一两分钟，请不要重复点击。":
    "Membaca percakapan yang ada. Proses pertama mungkin memerlukan satu atau dua menit; jangan klik lagi.",
  "将补充冻结期间的新对话；原有 pending Job 继续处理。":
    "Percakapan yang diterima selama dibekukan akan dilengkapi; pekerjaan tertunda yang ada tetap diproses.",
  一键插入应用: "Koneksi aplikasi sekali klik",
  "安装记忆连接，并默认允许此应用使用已授权记忆。":
    "Pasang koneksi memori dan izinkan aplikasi ini menggunakan memori yang diotorisasi secara default.",
  允许使用记忆: "Izinkan akses memori",
  连接状态: "Status koneksi",
  已接通: "Terhubung",
  "已安装，但取不到记忆": "Terpasang, tetapi tidak dapat mengambil memori",
  未接通: "Tidak terhubung",
  状态待核对: "Status perlu ditinjau",
  检测中: "Memeriksa",
  未开启关联: "Koneksi nonaktif",
  "正在开启处理…": "Memulai pemrosesan…",
  "正在冻结处理…": "Membekukan pemrosesan…",
  处理中: "Memproses",
  已冻结: "Dibekukan",
  "此应用的处理开关结果未能确认，其他应用不受影响。":
    "Sakelar pemrosesan untuk aplikasi ini belum terkonfirmasi. Aplikasi lain tidak terpengaruh.",
  "暂时无法刷新来源状态，请重试；未更改同步或读取权限。":
    "Tidak dapat memuat ulang status sumber. Coba lagi; izin sinkronisasi dan baca tidak diubah.",
  "暂时无法读取连接状态，当前状态待核对；请重试。":
    "Tidak dapat membaca status koneksi. Status saat ini perlu ditinjau; silakan coba lagi.",
  "已接通。已经打开的对话不会中途出现这个工具，请新开一轮。":
    "Terhubung. Alat ini tidak akan muncul di percakapan yang sudah terbuka; mulai percakapan baru.",
  "本次连接探测失败，当前状态待核对；未把上次读取当作本次成功。":
    "Pemeriksaan koneksi ini gagal. Status saat ini perlu ditinjau; hasil sebelumnya tidak dianggap sebagai keberhasilan baru.",
  上次读取: "Pembacaan terakhir",
  appSettingsTitle: "Pengaturan aplikasi {name}",
  appMemoryScope:
    "Izinkan {name} mencari memori dalam cakupan yang diotorisasi.",
  appRestart:
    "Alat memori belum ada di percakapan {name} saat ini. Keluar sepenuhnya lalu buka kembali {name}; alat akan muncul di percakapan baru.",
  appConnectFailed:
    "Koneksi sekali klik untuk aplikasi ini belum selesai. Aplikasi lain tidak terpengaruh. {reason}",
  appDisconnectFailed:
    "Koneksi tidak dapat dihapus, dan akses memori tidak dapat dicabut. {name} masih dapat menggunakan memori; silakan coba lagi. {reason}",
  appPermissionFailed:
    "Akses memori untuk aplikasi ini belum dapat dikonfirmasi. Silakan coba lagi. {reason}",
  appRevokedModified:
    "Akses memori telah dicabut, sehingga {name} tidak dapat lagi mencari memori. File konfigurasi telah diubah, sehingga MemHub tidak menghapusnya secara otomatis; hapus koneksi MemHub secara manual.",
  appRevokedRetained:
    "Akses memori telah dicabut, sehingga {name} tidak dapat lagi mencari memori. File koneksi tidak dihapus; tinjau kembali.",
  appRevokedReview:
    "Akses memori telah dicabut, sehingga {name} tidak dapat lagi mencari memori. Penghapusan penuh belum dapat dikonfirmasi; tinjau jalur berikut secara manual: {paths}.",
  appRemovalReview:
    "Penghapusan koneksi penuh belum dapat dikonfirmasi. Tinjau jalur berikut secara manual: {paths}.",
  appProfilesLabel: "Profil {name}",
  appProfileOn: "{profile}: terhubung",
  appProfileOff: "{profile}: belum terhubung",
  appProfilesHint:
    "Sakelar ini memasang koneksi memori dan memberi akses untuk setiap profil yang terdeteksi, lalu mencabut semuanya saat dimatikan. Selama sakelar menyala, profil baru akan terhubung otomatis.",
  appProfilesNone:
    "Tidak ada profil {name} yang terdeteksi. Buat profil, lalu kembali ke halaman ini.",
  appProfilesConnectFailed:
    "Profil berikut tidak dapat dihubungkan: {profiles}. Profil lainnya tetap dipertahankan. {reason}",
  appProfilesDisconnectFailed:
    "Profil berikut tidak dapat diputuskan dan masih dapat memakai memori: {profiles}. Coba lagi. {reason}",
  appProfilesRevokedRetained:
    "Akses memori untuk profil berikut telah dicabut, tetapi tidak semua file koneksinya dihapus; tinjau: {profiles}.",
  appProfileNameUnsupported:
    "nama profil hanya boleh berisi huruf kecil, angka, dan tanda hubung, maksimal 32 karakter",
  "apps.autoHostProfilesFailed":
    "Profil {name} berikut tidak dapat terhubung otomatis ({profiles}){reason}.",
  "Cursor 的连接凭证已更新。请完全退出并重新打开 Cursor，再开始新对话。":
    "Kredensial koneksi Cursor telah berubah. Keluar sepenuhnya lalu buka kembali Cursor sebelum memulai percakapan baru.",
  连接其他工具: "Hubungkan alat lain",
  "本机连接材料仅在桌面 App 中可用。":
    "Detail koneksi lokal hanya tersedia di aplikasi desktop.",
  "仅本机 MCP / 命令工具。纯网页聊天不支持直接连接本机。":
    "Hanya untuk MCP lokal atau alat baris perintah. Obrolan di browser tidak dapat terhubung langsung ke perangkat ini.",
  "列表内工具请用上方一键安装，此处仅供未列出的工具。":
    "Gunakan penyiapan sekali klik di atas untuk alat yang tercantum. Bagian ini untuk alat yang tidak tercantum.",
  "正在读取本机连接配置…": "Memuat detail koneksi lokal…",
  "未能读取连接配置。": "Tidak dapat memuat detail koneksi.",
  重试: "Coba lagi",
  连接材料格式: "Format detail koneksi",
  连接提示词: "Prompt koneksi",
  "MCP 配置": "Konfigurasi MCP",
  命令: "Perintah",
  本机连接材料: "Detail koneksi lokal",
  "包含本机路径，不含记忆和密钥。片段会运行本机 MemHub 连接程序；粘贴前请确认，MemHub 需保持运行。":
    "Berisi jalur lokal, tetapi tidak berisi memori atau kunci. Cuplikan ini menjalankan konektor MemHub lokal; tinjau sebelum menempelkan dan biarkan MemHub tetap berjalan.",
  "已复制，尚未建立连接。": "Disalin; belum ada koneksi yang dibuat.",
  "复制失败，可在上方选中文本复制。":
    "Gagal menyalin. Pilih dan salin teks di atas secara manual.",
  复制提示词: "Salin prompt",
  复制配置: "Salin konfigurasi",
  复制命令: "Salin perintah",
  记忆库分页: "Halaman pustaka memori",
  上一页: "Halaman sebelumnya",
  下一页: "Halaman berikutnya",
  "把熟悉的背景，带到这里": "Bawa konteks Anda ke sini",
  "你的记忆，从这里接上": "Satukan memori Anda",
  "这张卡还没测过。点一次「设为当前模型」会先自动测，如果还是不行，说明端点、key 或模型名有一处不对。":
    "Konfigurasi ini belum diuji. Menetapkannya sebagai model saat ini akan menjalankan pengujian terlebih dahulu; jika masih gagal, periksa endpoint, kunci, dan nama model.",
  "这类模型接口暂时用不了，换一张卡。":
    "Jenis endpoint model ini belum didukung. Gunakan konfigurasi lain.",
  "这张卡缺了向量化模型，补上才能启用。":
    "Konfigurasi ini memerlukan model vektor sebelum dapat diaktifkan.",
  "这张卡已经不在了，可能刚被删掉。刷新一下。":
    "Konfigurasi ini tidak tersedia lagi. Mungkin telah dihapus; muat ulang daftar.",
  "这张卡刚被改过。刷新一下再试，免得覆盖掉别处的改动。":
    "Konfigurasi ini baru saja berubah. Muat ulang sebelum mencoba lagi agar tidak menimpa perubahan lain.",
  onboardingModelOperationFailed: "Operasi gagal: {code}",
  onboardingModelSkipDestination: "Lewati dan buka Aplikasi",
  首次设置进度: "Kemajuan penyiapan awal",
  导入历史: "Impor riwayat",
  连接应用: "Hubungkan aplikasi",
  沉淀记忆: "Buat memori",
  本地存储: "Disimpan secara lokal",
  "对话与记忆保存在本机；使用云模型或已授权的助手时，相关内容会发送给对应服务。":
    "Percakapan dan memori disimpan di perangkat ini. Konten yang relevan dikirim ke model cloud atau asisten yang diotorisasi saat Anda menggunakannya.",
  "来源位置、身份或状态已变化，请重新选择并核对。尚未提交本次授权。":
    "Lokasi, identitas, atau status sumber berubah. Pilih dan tinjau lagi. Otorisasi ini belum dikirim.",
  "来源位置、身份或状态已变化，正在等待本次提交回执。不会自动扩大已提交范围。":
    "Lokasi, identitas, atau status sumber berubah. Menunggu konfirmasi pengiriman ini; cakupan yang dikirim tidak akan meluas secara otomatis.",
  测试实例不接入应用: "Instans uji tidak terhubung ke aplikasi",
  "请先把 MemHub 拖到“应用程序”并从那里重新打开，才能接入应用。":
    "Pindahkan MemHub ke Aplikasi dan buka kembali dari sana sebelum menghubungkan aplikasi.",
  "部分应用未能完成接入，已成功的设置会保留，可进入应用后单独处理。":
    "Beberapa aplikasi tidak dapat dihubungkan. Pengaturan yang berhasil tetap disimpan; tangani sisanya satu per satu di Aplikasi.",
  onboardingConnectionPartial:
    "{success} aplikasi terhubung; {failed} belum selesai: {names}. Anda dapat menanganinya satu per satu di Aplikasi.",
  "首次设置的完成状态未能保存。已确认的导入回执不受影响；下次启动可能再次出现此页。":
    "Tidak dapat menyimpan status penyelesaian penyiapan awal. Tanda terima impor yang terkonfirmasi tidak terpengaruh; halaman ini mungkin muncul lagi nanti.",
  "首次设置的完成状态尚未确认。已确认的导入回执不受影响；下次启动可能再次出现此页。":
    "Penyelesaian penyiapan awal belum terkonfirmasi. Tanda terima impor yang terkonfirmasi tidak terpengaruh; halaman ini mungkin muncul lagi nanti.",
  "位置检测未完成，尚未读取历史。可以重新检测，或先进入工作台。":
    "Deteksi lokasi belum selesai, dan riwayat belum dibaca. Periksa lagi atau lanjutkan ke ruang kerja.",
  "暂时无法核对来源，尚未提交授权。选择已保留，可以重试。":
    "Sumber tidak dapat ditinjau saat ini, sehingga otorisasi belum dikirim. Pilihan Anda tetap disimpan; Anda dapat mencoba lagi.",
  "本次没有来源获准导入。请核对下方原因后重试，或到应用页查看状态。":
    "Tidak ada sumber yang diotorisasi untuk impor ini. Tinjau alasannya di bawah lalu coba lagi, atau periksa statusnya di Aplikasi.",
  "开始导入的结果尚未确认，选择已保留。可以重试，或进入应用页核对。":
    "Dimulainya impor belum terkonfirmasi. Pilihan Anda tetap disimpan; coba lagi atau periksa di Aplikasi.",
  未完成: "Belum selesai",
  核对中: "Memeriksa",
  仍进入应用页: "Tetap lanjut ke Aplikasi",
  仍进入工作台: "Tetap lanjut ke ruang kerja",
  仍进入设置: "Tetap lanjut ke Pengaturan",
  重试保存并进入: "Coba simpan lagi dan lanjutkan",
  "稍后添加，先进入": "Tambahkan nanti dan lanjutkan",
  "正在开始导入…": "Memulai impor…",
  开始导入: "Mulai impor",
  "不用从头介绍自己。从常用的 App 开始，把已有背景带过来。":
    "Anda tidak perlu memulai dari awal. Bawa konteks dari aplikasi yang sering Anda gunakan.",
  "确认前只检查应用信息，不读取对话，也不安装连接。":
    "Sebelum konfirmasi, hanya informasi aplikasi yang diperiksa. Percakapan tidak dibaca dan koneksi tidak dipasang.",
  "本机 AI 应用": "Aplikasi AI di perangkat ini",
  重新检测位置: "Periksa lokasi lagi",
  "检测中…": "Memeriksa…",
  重新检测: "Periksa lagi",
  候选应用: "Aplikasi yang tersedia",
  "正在检测其他应用…": "Mencari aplikasi lain…",
  检测未完成: "Pemeriksaan belum selesai",
  已全选: "Semua dipilih",
  全选: "Pilih semua",
  onboardingImportHistory: "Impor riwayat dari {name}",
  正在核对本机保存位置: "Memeriksa lokasi penyimpanan lokal",
  已发现: "Ditemukan",
  位置待核对: "Lokasi perlu ditinjau",
  "当前没有候选来源。可以稍后在应用页添加历史包。":
    "Tidak ada sumber yang ditemukan. Anda dapat menambahkan arsip riwayat nanti dari Aplikasi.",
  "候选来源尚未加载。可以检测位置，或先进入工作台。":
    "Sumber belum dimuat. Periksa lokasi atau lanjutkan ke ruang kerja.",
  本次导入: "Impor ini",
  "个 App 的历史对话": "riwayat aplikasi",
  "把零散的会话，": "Satukan percakapan",
  汇成完整的记忆: "menjadi memori",
  "少一些重复交代，多一些理解。":
    "Lebih sedikit pengulangan. Lebih banyak pemahaman.",
  onboardingMoreApps: "Gulir untuk melihat lainnya · {count} aplikasi lagi",
  "onboardingMoreApps.one":
    "Gulir untuk melihat lainnya · {count} aplikasi lagi",
  onboardingMoreImportApps: "Gulir horizontal · {count} aplikasi lagi",
  onboardingImportChecking:
    "Memeriksa hasil impor; pilihan Anda tetap tersimpan",
  onboardingAllApps: "Semua {count} aplikasi ditampilkan",
  默认位置: "Lokasi default",
  自定义位置: "Lokasi kustom",
  尚未检测: "Belum diperiksa",
  未发现: "Tidak ditemukan",
  无法访问: "Tidak dapat diakses",
  位置无效: "Lokasi tidak valid",
  "需要更新 Core": "Core perlu diperbarui",
  "候选位置不存在，可能尚无历史或路径不同。":
    "Lokasi ini tidak ada. Mungkin belum ada riwayat, atau jalurnya berbeda.",
  "无法检查此位置，请核对目录访问权限后重试。":
    "Tidak dapat memeriksa lokasi ini. Tinjau akses direktori lalu coba lagi.",
  "此位置未通过安全检查，不能读取。":
    "Lokasi ini tidak lolos pemeriksaan keamanan dan tidak dapat dibaca.",
  "当前 Core 未提供可确认的位置身份，需更新后再选择。":
    "Core saat ini tidak menyediakan identitas lokasi yang dapat diverifikasi. Perbarui sebelum memilih.",
  暂不支持历史导入: "Impor riwayat belum didukung",
  "当前来源不可读取，请重新检测。":
    "Sumber saat ini tidak dapat dibaca. Periksa lagi.",
  "来源目录暂时不可读取，请核对位置和权限。":
    "Direktori sumber untuk sementara tidak dapat dibaca. Periksa lokasi dan izinnya.",
  "来源目录身份已变化，请返回重新检测。":
    "Identitas direktori sumber berubah. Kembali dan periksa lagi.",
  "来源权限已变化，请返回重新确认。":
    "Izin sumber berubah. Kembali dan konfirmasi lagi.",
  "此来源尚未获得读取授权。": "Sumber ini belum memiliki akses baca.",
  "自定义来源位置需要重新确认。":
    "Lokasi sumber kustom perlu dikonfirmasi lagi.",
  "当前 Core 未启用历史导入。":
    "Impor riwayat tidak diaktifkan di Core saat ini.",
  onboardingImportRefused:
    "Impor tidak diotorisasi ({reason}). Tinjau sumber lalu coba lagi.",
  确认自定义位置: "Konfirmasi lokasi kustom",
  关闭位置确认: "Tutup konfirmasi lokasi",
  "此位置来自当前配置，并非应用默认目录。确认位置不会开始读取历史。":
    "Lokasi ini berasal dari konfigurasi Anda saat ini, bukan direktori default aplikasi. Mengonfirmasi lokasi tidak memulai pembacaan riwayat.",
  onboardingConfirmHistory: "Konfirmasi bahwa ini lokasi riwayat {name}",
  确认此位置: "Konfirmasi lokasi ini",
  "对话正在汇入，记忆从这里开始":
    "Percakapan sedang masuk. Memori dimulai di sini",
  "导入进行中 · 顺手连接应用": "Mengimpor · Hubungkan aplikasi Anda",
  "对话已保存，记忆尚待生成。": "Percakapan disimpan; memori belum dibuat.",
  "导入在后台继续，无需等待。":
    "Impor berlanjut di latar belakang. Tidak perlu menunggu.",
  "正在重新核对来源；已保存数量仍按任务回执显示。":
    "Memeriksa ulang sumber; jumlah tersimpan tetap mengikuti tanda terima pekerjaan.",
  "本次历史导入已暂停，已保存的对话还在。":
    "Impor riwayat ini dijeda. Percakapan tersimpan tetap tersedia.",
  "导入等待处理，已保存的对话还在。":
    "Impor menunggu diproses. Percakapan tersimpan tetap tersedia.",
  停止后续操作: "Hentikan tindakan selanjutnya",
  暂停导入: "Jeda impor",
  继续导入: "Lanjutkan impor",
  "配置模型，沉淀记忆": "Konfigurasi model untuk membuat memori",
  导入与原文: "Impor dan teks asli",
  "本次导入已结束。已保存的对话无需重复导入。":
    "Impor ini selesai. Percakapan tersimpan tidak perlu diimpor ulang.",
  "对话先保存在本机。你可以同时设置后续连接。":
    "Percakapan disimpan secara lokal terlebih dahulu. Sementara itu, Anda dapat menyiapkan koneksi.",
  "导入不会自动开放应用读取权限。配置模型后，才会开始生成记忆。":
    "Impor tidak otomatis memberi aplikasi akses baca. Memori baru dibuat setelah Anda mengonfigurasi model.",
  onboardingHistoryWindow:
    "Ditemukan data percakapan dalam jumlah besar. Untuk membatasi penggunaan dan menjaga keselarasan linimasa aplikasi, impor ini hanya mencakup 3 hari terakhir untuk semua aplikasi.",
  导入说明: "Informasi impor",
  正在导入你的会话: "Mengimpor percakapan Anda",
  "后台将持续为你导入，可以放心进入下一步":
    "Impor akan berlanjut di latar belakang. Anda dapat melanjutkan.",
  未开始的导入: "Impor yang tidak dapat dimulai",
  "未能开始导入，其他已接受任务不受影响。":
    "Impor tidak dapat dimulai. Pekerjaan lain yang telah diterima tidak terpengaruh.",
  查看未导入的应用: "Lihat aplikasi yang belum diimpor",
  让记忆继续生长: "Biarkan memori Anda terus berkembang",
  连接你的应用: "Hubungkan aplikasi Anda",
  "适用于本次选择且支持的应用。":
    "Untuk aplikasi terpilih yang mendukung koneksi ini.",
  一键接入应用: "Hubungkan aplikasi dalam satu langkah",
  "安装记忆连接，让所选应用用上 MemHub 中的相关记忆。":
    "Pasang koneksi memori agar aplikasi terpilih dapat menggunakan memori yang relevan di MemHub.",
  "当前使用独立测试数据目录，不会修改本机应用的连接配置。":
    "Instans ini menggunakan direktori data uji terpisah dan tidak akan mengubah koneksi aplikasi lokal.",
  "点击底部按钮后，后台分别接入各应用；某个应用失败不影响其他应用。":
    "Setelah Anda mengeklik tombol di bawah, aplikasi dihubungkan satu per satu di latar belakang; kegagalan satu aplikasi tidak memengaruhi yang lain.",
  "暂时无法连接应用，当前不会安装连接。":
    "Aplikasi tidak dapat dihubungkan saat ini. Tidak ada koneksi yang akan dipasang.",
  onboardingBatchStopped:
    "Tindakan selanjutnya dihentikan; periksa tanda terima untuk tindakan yang sudah dikirim. ",
  onboardingBatchResult:
    "{confirmed} terkonfirmasi, {failed} belum terkonfirmasi.",
  "已确认项保留，请核对未确认任务后重试。":
    " Tindakan yang terkonfirmasi tetap disimpan. Periksa pekerjaan yang belum terkonfirmasi sebelum mencoba lagi.",
  本次应用导入进度: "Kemajuan impor untuk aplikasi terpilih",
  正在导入: "Mengimpor",
  已暂停: "Dijeda",
  已中断: "Terputus",
  导入遇到问题: "Impor mengalami masalah",
  导入完成: "Impor selesai",
  已取消: "Dibatalkan",
  已保存: "Disimpan",
  onboardingAppProgress: "Kemajuan impor untuk {name}",
  onboardingSavedMessages: "{count} pesan disimpan",
  onboardingScannedOutsideRange:
    "{count} item dipindai; tidak ada percakapan dalam rentang waktu",
  onboardingScannedNothingToSave:
    "{count} item dipindai; tidak ada percakapan untuk disimpan",
  onboardingScannedFilteringRecent:
    "{count} item dipindai; memfilter 3 hari terakhir",
  onboardingScannedFiltering: "{count} item dipindai; memfilter",
  onboardingScannedNotSaved: "{count} item dipindai; belum disimpan",
  没有可保存的历史对话: "Tidak ada riwayat percakapan untuk disimpan",
  读取授权不可用: "Akses baca tidak tersedia",
  onboardingReadErrors: " · {count} kesalahan baca",
  "配置详情，按需要调整": "Detail konfigurasi, jika diperlukan",
  "连接未通过，配置已保留": "Koneksi gagal; konfigurasi tetap disimpan",
  "选好模型，让记忆开始沉淀": "Pilih model untuk mulai membuat memori",
  "连接设置已保存\n成功项保留，不重复执行。":
    "Pengaturan koneksi disimpan.\nLangkah yang berhasil tetap disimpan dan tidak akan diulang.",
  "已选择：持续同步 · 一键接入":
    "Dipilih: sinkronisasi berkelanjutan · koneksi satu langkah",
  "沉淀记忆 · 使用你选择的模型": "Buat memori · Dengan model pilihan Anda",
  "当前无法启用模型，已填写内容保留。":
    "Model tidak dapat diaktifkan saat ini. Isian Anda tetap disimpan.",
  "模型连接测试通过，可以启用并开始沉淀记忆。":
    "Koneksi model berhasil. Anda dapat mengaktifkannya untuk membuat memori.",
  "先测试模型连通性；测试通过后再启用。":
    "Uji koneksi model terlebih dahulu; aktifkan setelah pengujian berhasil.",
  "跳过，先进入应用": "Lewati dan buka Aplikasi",
  "正在测试模型…": "Menguji model…",
  测试模型连通性: "Uji koneksi model",
  "正在启用…": "Mengaktifkan…",
  确认启用: "Konfirmasi dan aktifkan",
  核对提交状态: "Periksa status pengiriman",
  模型配置: "Konfigurasi model",
  模型参数: "Parameter model",
  "选择服务商，填入 API Key。核对已有配置后再继续。":
    "Pilih penyedia dan masukkan kunci API. Tinjau konfigurasi yang ada sebelum melanjutkan.",
  模型服务商: "Penyedia model",
  选择服务商: "Pilih penyedia",
  "已安全保存，留空保留":
    "Disimpan dengan aman; kosongkan untuk mempertahankan",
  "输入服务商提供的 API Key": "Masukkan kunci API dari penyedia Anda",
  "连接未通过，请检查 API Key、模型与接口地址后重试。配置已保留。":
    "Koneksi gagal. Periksa kunci API, model, dan endpoint, lalu coba lagi. Konfigurasi Anda tetap disimpan.",
  "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”。":
    "Izinkan MemHub mengakses Keychain. Pilih ‘Selalu Izinkan’ saat diminta.",
  "留空保留已保存密钥；填写新密钥后需重新测试。":
    "Kosongkan untuk mempertahankan kunci tersimpan; uji lagi setelah memasukkan kunci baru.",
  "密钥保存在系统安全存储中，不写入对话或日志。":
    "Kunci disimpan di penyimpanan aman sistem, bukan di percakapan atau log.",
  配置详情与高级设置: "Detail konfigurasi dan pengaturan lanjutan",
  服务商待选择: "Penyedia belum dipilih",
  模型待填写: "Model belum diisi",
  本地向量模型: "Model vektor lokal",
  向量模型待填写: "Model vektor belum diisi",
  思考已关闭: "Pemikiran dinonaktifkan",
  思考未关闭: "Pemikiran tidak dinonaktifkan",
  "预设已填好，只有自定义服务或特殊需求才需要修改。":
    "Preset sudah terisi. Ubah hanya untuk layanan kustom atau kebutuhan khusus.",
  返回服务商选择: "Kembali ke pilihan penyedia",
  模型名称: "Nama model",
  "Anthropic（暂不支持）": "Anthropic (belum didukung)",
  "HY 托管（当前不可用）": "Terkelola HY (saat ini tidak tersedia)",
  向量模型: "Model vektor",
  "本地 ONNX · 内置": "ONNX lokal · Bawaan",
  "超时（秒）": "Batas waktu (detik)",
  最大并发: "Konkurensi maksimum",
  上下文窗口: "Jendela konteks",
  "自动 / 可选": "Otomatis / opsional",
  关闭思考模式: "Nonaktifkan mode pemikiran",
  "自定义向量配置可展开；远程向量服务须遵守凭据边界。":
    "Anda dapat membuka pengaturan vektor kustom. Layanan vektor jarak jauh harus mematuhi batas kredensial.",
  "切换服务商或模型后，需要重新测试并启用。":
    "Setelah mengganti penyedia atau model, uji dan aktifkan lagi.",
  向量模型名称: "Nama model vektor",
  向量协议: "Protokol vektor",
  "同源 OpenAI-compatible（共用 Key）":
    "Kompatibel OpenAI, asal yang sama (kunci bersama)",
  "向量 Base URL": "URL dasar vektor",
  "远程向量地址须与生成服务同源；本机地址仍需校验。":
    "Endpoint vektor jarak jauh harus berasal dari asal yang sama dengan layanan generatif; endpoint lokal tetap perlu divalidasi.",
  "放弃旧记录，重新配置": "Buang catatan lama dan konfigurasi ulang",
  模型提交状态: "Status pengiriman model",
  "正在测试连接…": "Menguji koneksi…",
  "正在提交保存与启用，请等待回执…":
    "Menyimpan dan mengaktifkan; menunggu konfirmasi…",
  "操作结果未确认，已填写内容保留，请核对后重试。":
    "Hasil belum terkonfirmasi. Isian Anda tetap disimpan; periksa sebelum mencoba lagi.",
  本次沉淀: "Penyiapan memori ini",
  让记忆从这里开始: "Mulai buat memori di sini",
  对话来源: "Sumber percakapan",
  尚无已选来源: "Belum ada sumber yang dipilih",
  onboardingSourcesSummary: "{names} dan total {count} aplikasi",
  onboardingImportedConversations:
    "{count} percakapan diimpor; sakelar sumber tetap mengatur apa yang disinkronkan ke perangkat ini nanti.",
  "处理服务：": "Layanan pemrosesan:",
  待选择: "Belum dipilih",
  "相关对话会发送给所选服务商。":
    "Percakapan yang relevan akan dikirim ke penyedia yang dipilih.",
  "测试连接只发送测试内容。启用后会使用此模型整理已导入和之后同步的会话。":
    "Uji koneksi hanya mengirim konten uji. Setelah diaktifkan, model ini memproses percakapan yang diimpor dan disinkronkan selanjutnya.",
  "当前不能在此启用模型。": "Model tidak dapat diaktifkan di sini saat ini.",
  "配置已修改，需要重新测试。": "Konfigurasi berubah. Uji lagi.",
  onboardingIntegerRange: "Masukkan bilangan bulat dari {min} hingga {max}.",
  "请填写配置名称。": "Masukkan nama konfigurasi.",
  "请填写实际可用的模型 ID。": "Masukkan ID model yang tersedia.",
  "请填写有效的模型接口地址。": "Masukkan URL endpoint model yang valid.",
  "请填写 API Key。": "Masukkan kunci API.",
  "请填写向量模型 ID。": "Masukkan ID model vektor.",
  "配置结果尚未确认，请在模型设置中核对后重试。":
    "Hasil konfigurasi belum terkonfirmasi. Periksa di pengaturan Model sebelum mencoba lagi.",
  "模型连接测试通过，可以启用。":
    "Uji koneksi model berhasil. Anda dapat mengaktifkannya.",
  "连接未通过，请核对模型、密钥和接收方。":
    "Koneksi gagal. Periksa model, kunci, dan penerima.",
  "配置或测试结果暂未确认，请核对后重试。":
    "Hasil konfigurasi atau pengujian belum terkonfirmasi. Periksa sebelum mencoba lagi.",
  "索引更新没有完成，仍在使用原来的搜索模型。请检查连接后重试。":
    "Pembaruan indeks belum selesai. Model pencarian sebelumnya masih digunakan. Periksa koneksi lalu coba lagi.",
  "上次模型测试结果仍待核对，请稍后重试。":
    "Hasil pengujian model sebelumnya masih perlu diperiksa. Coba lagi nanti.",
  "模型写入暂时关闭。请等待索引维护完成后重试。":
    "Penulisan model dijeda sementara. Tunggu hingga pemeliharaan indeks selesai, lalu coba lagi.",
  "模型未能确认启用，请在设置中核对。":
    "Tidak dapat memastikan bahwa model telah diaktifkan. Periksa di Pengaturan.",
  "已保存的模型配置暂时无法读取，不会重复创建。请重试刷新。":
    "Konfigurasi model yang tersimpan untuk sementara tidak dapat dibaca. Duplikat tidak akan dibuat. Coba muat ulang lagi.",
  所选连接已启用: "Koneksi aplikasi terpilih diaktifkan",
  本次未选择应用连接: "Tidak ada koneksi aplikasi yang dipilih kali ini",
  尚未填写接收方: "Penerima belum diisi",
  本机内置向量模型: "Model vektor lokal bawaan",
  "操作未确认，草稿已保留。":
    "Tindakan belum terkonfirmasi. Draf Anda tetap disimpan.",
  "操作已完成，但配置列表暂未刷新。已保留操作回执，请刷新核对，不要重复保存。":
    "Operasi selesai, tetapi daftar konfigurasi belum diperbarui. Tanda terima tetap disimpan; muat ulang untuk meninjaunya. Jangan simpan lagi.",
  "暂时无法刷新模型配置，已确认的配置仍保留。请重试刷新。":
    "Tidak dapat memuat ulang konfigurasi model. Konfigurasi terkonfirmasi tetap disimpan; coba muat ulang lagi.",
  "未找到上次创建记录。确认放弃旧记录后可重新配置；系统不会自动重试。":
    "Catatan pembuatan sebelumnya tidak ditemukan. Buang catatan tersebut untuk mengonfigurasi ulang; tidak ada percobaan ulang otomatis.",
  "创建结果仍待核对，不会重复创建。请稍后再次核对。":
    "Hasil pembuatan masih perlu ditinjau. Tidak akan dibuat duplikat; periksa lagi nanti.",
  "已发现模型配置，旧创建记录仍保留。请先核对已有配置。":
    "Konfigurasi model ditemukan. Catatan pembuatan lama tetap disimpan; tinjau konfigurasi yang ada terlebih dahulu.",
  "旧创建记录尚未安全解除，配置仍保持锁定。请重新核对。":
    "Catatan pembuatan lama belum dihapus dengan aman. Konfigurasi tetap terkunci; tinjau lagi.",
  "配置未保存，请检查模型地址和参数后重试；已填写内容保留。":
    "Konfigurasi tidak disimpan. Periksa endpoint model dan pengaturannya sebelum mencoba lagi; isian Anda tetap disimpan.",
  "创建结果尚未确认，请核对已保存结果，不要重复提交。":
    "Hasil pembuatan belum terkonfirmasi. Tinjau hasil tersimpan; jangan kirim lagi.",
  "模型配置已变化，旧测试结果不会用于启用，请重新核对配置。":
    "Konfigurasi model berubah. Hasil pengujian lama tidak akan digunakan untuk mengaktifkannya; tinjau konfigurasi lagi.",
  "暂时无法核对测试服务，本次未发送测试，请恢复连接后重试。":
    "Tidak dapat memverifikasi layanan pengujian. Tidak ada pengujian yang dikirim; pulihkan koneksi lalu coba lagi.",
  "另一个模型的测试结果尚未确认，请先在该模型上核对测试结果。":
    "Hasil pengujian model lain belum terkonfirmasi. Tinjau hasil model tersebut terlebih dahulu.",
  "测试结果尚未确认。再次点击测试连接将核对原操作，不会重复发送测试。":
    "Hasil pengujian belum terkonfirmasi. Mengeklik Uji koneksi lagi akan meninjau operasi semula tanpa mengirim pengujian baru.",
  "请先点击测试连接，通过后再设为当前模型。":
    "Uji koneksi terlebih dahulu; tetapkan sebagai model saat ini hanya setelah berhasil.",
  "这是当前正在使用的模型，请先切换到别的模型再删除。":
    "Model ini sedang digunakan. Beralih ke model lain sebelum menghapusnya.",
  "模型配置操作未成功，请核对输入后重试。":
    "Operasi konfigurasi model tidak berhasil. Periksa isian Anda lalu coba lagi.",
  "它没有 MCP 通道，无法发放可撤销的记忆凭证":
    "Aplikasi ini tidak memiliki saluran MCP untuk kredensial memori yang dapat dicabut",
  当前安装不支持一键插入: "Koneksi sekali klik tidak didukung di instalasi ini",
  桌面端的连接组件不可用: "Komponen koneksi desktop tidak tersedia",
  安装后没有拿到安装回执: "Tanda terima instalasi tidak diterima",
  记忆使用权限没有获批: "Akses memori tidak disetujui",
  配置文件被改过: "File konfigurasi telah diedit",
  "有无法确认的残留，需要人工核对":
    "Ada sisa yang perlu ditinjau secara manual",
  权限存储暂不可用: "Penyimpanan izin tidak tersedia",
  "请先把 MemHub 拖到“应用程序”文件夹，再接入应用":
    "Pindahkan MemHub ke Aplikasi sebelum menghubungkan aplikasi",
  本机运行时暂不可用: "Runtime lokal tidak tersedia",
  桌面端读取接入状态超时: "Pembacaan status koneksi desktop habis waktu",
  "apps.apps": "Aplikasi",
  "apps.allApps": "Semua aplikasi",
  "apps.home": "Beranda aplikasi",
  "apps.back": "Kembali ke beranda",
  "apps.overview": "Ikhtisar memori",
  "apps.memories": "Memori tersimpan",
  "apps.rawConversations": "Percakapan asli",
  "apps.rawSaving": "Menyimpan",
  "apps.rawSavingCompact": "Menyimpan",
  "apps.rawSavingStatus": "Status penyimpanan",
  "apps.rawSavingDetail": "Percakapan asli disimpan tanpa model generatif",
  "apps.rawNoExcerpt": "Tidak ada kutipan pengguna yang dapat ditampilkan",
  "apps.rawConversation": "Percakapan asli",
  "apps.rawSaved": "Percakapan asli disimpan",
  "apps.rawEmpty": "Belum ada percakapan asli yang disimpan",
  "apps.rawCheckedEmpty":
    "Diperiksa: tidak ada pembaruan percakapan untuk ditampilkan",
  "apps.memoryCheckedEmpty":
    "Diperiksa: tidak ada pembaruan memori untuk ditampilkan",
  "apps.recentEmptyCompact": "Belum ada pembaruan",
  "apps.rawWaiting": "Percakapan asli yang disimpan akan muncul di sini",
  "apps.checkedRecent": "Konten saat ini telah diperiksa",
  "apps.currentMode": "Mode saat ini",
  "apps.mode.lite": "Memori dasar (lite)",
  "apps.mode.pro": "Memori pintar (pro)",
  "apps.mode.ultra": "Memori mendalam (ultra)",
  "apps.readingCount": "Memeriksa memori lokal…",
  "apps.rawReadingCount": "Memeriksa percakapan asli…",
  "apps.startingRetry":
    "Layanan latar belakang sedang dimulai; ini akan diperbarui otomatis",
  "apps.countWillUpdate": "Jumlah memori akan diperbarui otomatis",
  "apps.rawCountWillUpdate": "Jumlah percakapan asli akan diperbarui otomatis",
  "apps.showingPrevious":
    "Menampilkan data sebelumnya; ini akan diperbarui otomatis",
  "apps.unreadable": "Tidak tersedia",
  "apps.unit": "memori",
  "apps.scope": "Digunakan sesuai izin aplikasi",
  "apps.processing": "Status pemrosesan",
  "apps.running": "Memproses",
  "apps.paused": "Dijeda",
  "apps.stoppedNew": "Tidak ada memori baru yang sedang dibuat",
  "apps.checkingProcessing": "Memeriksa status pemrosesan",
  "apps.checkingStatus": "Memeriksa",
  "apps.unchecked": "Perlu ditinjau",
  "apps.organizingNew": "Memproses percakapan baru",
  "apps.recentUpdate": "Pembaruan terbaru",
  "apps.checkingRecent": "Memeriksa",
  "apps.recentWillUpdate": "Pembaruan terbaru akan muncul otomatis",
  "apps.notGenerated": "Belum dibuat",
  "apps.saved": "Memori disimpan",
  "apps.separate": "Riwayat dan memori diproses terpisah",
  "apps.recentCall": "Panggilan terbaru",
  "apps.activityUnavailable": "Aktivitas tidak tersedia",
  "apps.firstBatch": "Memori pertama",
  "apps.callsUnavailable": "Tidak dapat membaca riwayat panggilan",
  "apps.memoryUnavailable": "Ikhtisar memori belum siap",
  "apps.importing": "Mengimpor percakapan sebelum membuat memori",
  "apps.firstRunning": "Membuat memori pertama",
  "apps.firstQueued": "Menyiapkan memori pertama",
  "apps.readingActivity": "Menyiapkan ikhtisar memori",
  "apps.emptyMemories": "Belum ada memori yang dibuat",
  "apps.callReadFailure":
    "Anda tetap dapat melihat memori tersimpan. Mencoba lagi tidak akan membuat panggilan baru.",
  "apps.memoryReadFailure":
    "Ini akan diperbarui saat layanan pulih. Anda juga dapat mencoba lagi sekarang.",
  "apps.savingConversations": "Percakapan masih disimpan di latar belakang.",
  "apps.waitingMemories": "Riwayat disimpan. Memori baru akan muncul di sini.",
  "apps.reread": "Coba lagi",
  "apps.viewCalls": "Lihat riwayat",
  "apps.viewMemory": "Lihat memori",
  "apps.backgroundDown":
    "Layanan latar belakang tidak berjalan; aplikasi tidak dapat mencari memori saat ini",
  "apps.restart": "Mulai ulang",
  "apps.runtime": "Status runtime",
  "apps.backgroundRunning": "Berjalan di latar belakang",
  "apps.connectionUnchecked": "Koneksi perlu ditinjau",
  "apps.disconnected": "Layanan terputus",
  "apps.authorizedOnly": "Hanya memproses konten yang diotorisasi",
  "apps.sync": "Sinkronisasi percakapan",
  "apps.organizing": "Pemrosesan memori",
  "apps.access": "Akses aplikasi",
  "apps.checkingAccess": "Pemeriksaan koneksi aplikasi",
  "apps.checking": "Memeriksa…",
  "apps.recheck": "Periksa lagi",
  "apps.accessState": "Status koneksi aplikasi",
  "apps.staleAccess":
    "Tidak dapat membaca status koneksi aplikasi; menampilkan status terakhir yang diketahui.",
  "apps.reconnect": "Hubungkan ulang",
  "apps.myApps": "Aplikasi saya",
  "apps.list": "Daftar aplikasi",
  "apps.syncEnabled": "Sinkronisasi berkelanjutan aktif",
  "apps.recallAllowed": "Dapat meminta memori yang diotorisasi",
  "apps.historyAllowed": "Akses riwayat diotorisasi",
  "apps.connectionPending": "Koneksi belum selesai",
  "apps.details": "Lihat detail",
  "apps.listUnavailable": "Daftar aplikasi tidak tersedia",
  "apps.emptyApps": "Belum ada aplikasi untuk ditampilkan",
  "apps.checkingNow": "Memeriksa",
  "apps.checkingState": "Memeriksa status",
  "apps.notEnabled": "Belum diaktifkan",
  "apps.enableFailed": "Tidak dapat mengaktifkan",
  "apps.unknownTime": "Waktu tidak diketahui",
  "apps.today": "Hari ini",
  "apps.newMemory": "Memori baru",
  "apps.updatedMemory": "Memori diperbarui",
  "apps.unlinked": "Belum terhubung",
  "apps.lastRead": "Pembacaan terakhir: ",
  "apps.recentSync": "Terakhir disinkronkan {time}",
  "apps.connectedCount": "{count} aplikasi terhubung",
  "apps.connectedCount.one": "{count} aplikasi terhubung",
  "apps.pendingCount": "{count} aplikasi perlu ditinjau",
  "apps.pendingCount.one": "{count} aplikasi perlu ditinjau",
  "apps.mixedCount":
    "{count} aplikasi terhubung · Menunggu tinjauan: {pending}",
  "apps.mixedCount.one":
    "{count} aplikasi terhubung · Menunggu tinjauan: {pending}",
  "apps.unknownAccess":
    "Status koneksi aplikasi tidak tersedia; jumlah aplikasi terhubung tidak dapat dikonfirmasi.",
  "apps.unknownPermissions":
    "Tidak dapat membaca izin memori untuk {names}; status koneksi belum terkonfirmasi.",
  "apps.enabledCount": "{count} aplikasi diaktifkan",
  "apps.enabledCount.one": "{count} aplikasi diaktifkan",
  "apps.appCount": "{count} aplikasi",
  "apps.appCount.one": "{count} aplikasi",
  "apps.viewAll": "Lihat semua {count} aplikasi",
  "apps.viewAll.one": "Lihat semua {count} aplikasi",
  "apps.checkedAt": "Terakhir diperiksa: {time}",
  "apps.revision": "Versi {count}",
  "apps.conversationTime": "Waktu percakapan",
  "apps.importTime": "Waktu impor",
  "apps.writeTime": "Waktu penulisan",
  "apps.updateTime": "Waktu pembaruan",
  "apps.incompleteCalls":
    " · Sebagian panggilan tidak tercatat; daftar mungkin tidak lengkap",
  "apps.timeoutWarning":
    "{host} tidak dapat mencari memori baru-baru ini. Layanan latar belakang mungkin sedang sibuk dan seharusnya pulih otomatis.",
  "apps.autoHostFailed": "{name} tidak dapat terhubung otomatis{reason}.",
  "apps.failureReason": ": {reason}",
  "apps.autoSyncFailed":
    "Sinkronisasi berkelanjutan tidak dapat diaktifkan otomatis untuk {name}. {reason}",
  "apps.noAutoRetry": "Tidak akan dicoba ulang secara otomatis.",
  "apps.cardUnavailable":
    "{label} untuk sementara tidak tersedia. Fitur lain tidak terpengaruh, dan seharusnya pulih otomatis.",
  "apps.navMemories": "Memori",
  "apps.navigation": "Navigasi utama",
  "apps.skip": "Lewati ke konten utama",
  "apps.local": "Disimpan secara lokal",
  "apps.localHint":
    "Memori disimpan di perangkat ini. Konten yang relevan dikirim ke layanan saat Anda menggunakan model cloud atau asisten yang diotorisasi.",
  "memory:记忆抽取": "Ekstraksi memori",
  "memory:关系归纳": "Sintesis hubungan",
  "memory:深度整理": "Pemrosesan mendalam",
  "memory:跨域整理": "Pemrosesan lintas domain",
  "memory:当前模型已变化": "Model saat ini berubah",
  "memory:本地会话内容已变化": "Isi percakapan lokal berubah",
  "memory:这段对话含过长原文，暂未整理；原文已保留":
    "Percakapan ini berisi teks yang terlalu panjang untuk diproses; teks asli tetap disimpan",
  "memory:记忆库分页": "Halaman pustaka memori",
  "memory:上一页": "Halaman sebelumnya",
  "memory:下一页": "Halaman berikutnya",
  "memory:等待可用生成模型": "Menunggu model generatif yang tersedia",
  "memory:还没有启用生成模型": "Belum ada model generatif yang diaktifkan",
  "memory:缺少生成模型 API Key": "Kunci API model generatif tidak ada",
  "memory:模型凭据暂不可用": "Kredensial model untuk sementara tidak tersedia",
  "memory:本机凭据存储暂不可用":
    "Penyimpanan kredensial lokal untuk sementara tidak tersedia",
  "memory:需要授权访问本机钥匙串。请重新打开 App 并完成一次授权":
    "Akses Keychain perlu diotorisasi. Buka kembali aplikasi dan setujui akses.",
  "memory:读取本机钥匙串超时。重新保存一遍模型配置即可恢复":
    "Pembacaan Keychain habis waktu. Simpan lagi konfigurasi model untuk memulihkannya.",
  "memory:写入本机钥匙串超时。重新保存一遍模型配置即可恢复":
    "Penulisan Keychain habis waktu. Simpan lagi konfigurasi model untuk memulihkannya.",
  "memory:模型认证失败。更新 key 后，到设置里点一次检测，会自动接着处理":
    "Autentikasi model gagal. Perbarui kunci dan jalankan pemeriksaan di Pengaturan untuk melanjutkan pemrosesan.",
  "memory:模型账户余额不足。充值或换 key 后，到设置里点一次检测，会自动接着处理":
    "Saldo akun model tidak cukup. Isi ulang atau ganti kunci, lalu jalankan pemeriksaan di Pengaturan untuk melanjutkan pemrosesan.",
  "memory:模型服务拒绝访问。更新权限后，到设置里点一次检测，会自动接着处理":
    "Layanan model menolak akses. Perbarui izin dan jalankan pemeriksaan di Pengaturan untuk melanjutkan pemrosesan.",
  "memory:模型服务限流": "Layanan model membatasi frekuensi permintaan",
  "memory:模型服务暂不可用": "Layanan model untuk sementara tidak tersedia",
  "memory:模型服务响应超时": "Layanan model habis waktu",
  "memory:模型请求不兼容，请核对配置":
    "Permintaan model tidak kompatibel; periksa konfigurasi",
  "memory:模型请求未成功": "Permintaan model gagal",
  "memory:HY Memory 不可用": "HY Memory tidak tersedia",
  "memory:HY Memory 启动失败": "HY Memory gagal dimulai",
  "memory:暂时没能整理，稍后会自动重试":
    "Belum dapat diproses; akan dicoba ulang otomatis",
  "memory:这次没能整理成功，正在重试":
    "Tidak dapat diproses kali ini; mencoba ulang",
  "memory:生成模型换过了，这批会重新整理":
    "Model generatif berubah; item ini akan diproses ulang",
  "memory:整理方式换过了，这批会重新整理":
    "Pengaturan pemrosesan berubah; item ini akan diproses ulang",
  "memory:已提炼": "Diekstrak",
  "memory:旧版确认记录": "Catatan konfirmasi lama",
  "memory:创建": "Dibuat",
  "memory:纠正": "Koreksi",
  "memory:调整使用范围": "Cakupan akses diubah",
  "memory:撤销修改": "Perubahan dibatalkan",
  "memory:模型更新": "Pembaruan model",
  "memory:被新记忆取代": "Digantikan oleh memori baru",
  "memory:其他操作": "Tindakan lain",
  "memory:删除（记录）": "Penghapusan",
  "memory:修改已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。":
    "Perubahan disimpan, tetapi daftar terbaru tidak dapat dimuat. Jawaban lama tidak digunakan lagi; muat ulang daftar.",
  "memory:纠正已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。":
    "Koreksi disimpan, tetapi daftar terbaru tidak dapat dimuat. Jawaban lama tidak digunakan lagi; muat ulang daftar.",
  "memory:未收到操作结果，请重试。":
    "Tidak ada hasil yang diterima. Silakan coba lagi.",
  "memory:批量处理结果已保留，但列表刷新失败，请重新读取记忆库。":
    "Hasil tindakan massal tetap disimpan, tetapi daftar tidak dapat diperbarui. Muat ulang pustaka memori.",
  "memory:批量处理结果已保留，但列表刷新失败，稍后会自动重新读取。":
    "Hasil tindakan massal tetap disimpan, tetapi daftar tidak dapat diperbarui. Daftar akan dimuat ulang otomatis nanti.",
  "memory:召回范围已保存，但列表刷新失败。请稍后重新打开记忆页。":
    "Cakupan pengambilan disimpan, tetapi daftar tidak dapat diperbarui. Buka kembali Memori nanti.",
  "memory:原始对话暂时无法读取": "Tidak dapat memuat percakapan asli",
  "memory:没有匹配的原始对话": "Tidak ada percakapan asli yang cocok",
  "memory:还没有导入的原始对话": "Belum ada percakapan asli yang diimpor",
  "memory:查看已读内容": "Lihat konten tersimpan",
  "memory:已保存原文": "Teks asli disimpan",
  "memory:刷新已读内容": "Muat ulang konten tersimpan",
  "memory:正在核对本机已保存记录…": "Memeriksa catatan lokal tersimpan…",
  "memory:原始消息": "Pesan asli",
  "memory:会话": "Percakapan",
  "memory:用户发言": "Pesan pengguna",
  "memory:Agent 发言": "Pesan agen",
  "memory:作者未知": "Penulis tidak diketahui",
  "memory:原始时间未知": "Waktu asli tidak diketahui",
  "memory:工具记录": "Catatan alat",
  "memory:原始时间来自已保存记录。":
    "Waktu asli berasal dari catatan tersimpan.",
  "memory:已保存的原文样例": "Contoh teks asli tersimpan",
  "memory:最多展示 3 段，不代表全部历史或推荐记忆。":
    "Maksimal 3 contoh; bukan seluruh riwayat atau memori yang disarankan.",
  "memory:查看完整对话": "Lihat percakapan lengkap",
  "memory:原文较长，此处仅展示前 600 个字符。":
    "Teks asli panjang; hanya 600 karakter pertama yang ditampilkan di sini.",
  "memory:这个来源还没有保存原文。":
    "Belum ada teks asli yang disimpan dari sumber ini.",
  "memory:完整对话": "Percakapan lengkap",
  "memory:关闭完整对话": "Tutup percakapan lengkap",
  "memory:用户": "Pengguna",
  "memory:暂时无法读取已保存内容，旧回执已收起。请检查授权或重试。":
    "Tidak dapat membaca konten tersimpan. Tanda terima sebelumnya ditutup; periksa izin atau coba lagi.",
  "memory:这段原文暂不可读，可能已被撤回授权。请刷新回执。":
    "Teks asli ini tidak tersedia; aksesnya mungkin telah dicabut. Muat ulang tanda terima.",
  "memory:外部记忆召回范围": "Cakupan pencarian memori eksternal",
  "memory:外部记忆召回": "Pencarian memori eksternal",
  "memory:调整召回范围": "Sesuaikan cakupan pencarian",
  "memory:所有关联工具": "Semua alat terhubung",
  "memory:已停止外部记忆召回": "Pencarian memori eksternal dihentikan",
  "memory:原有限定范围": "Cakupan terbatas sebelumnya",
  "memory:选择召回范围": "Pilih cakupan pencarian",
  "memory:停止外部记忆召回": "Hentikan pencarian memori eksternal",
  "memory:正在保存…": "Menyimpan…",
  "memory:保存召回范围": "Simpan cakupan pencarian",
  "memory:取消范围修改": "Batalkan perubahan cakupan",
  "memory:原文检索仍按来源授权；已带出的内容不会撤回。":
    "Pencarian teks asli tetap mengikuti izin sumber; konten yang sudah diberikan tidak akan ditarik kembali.",
  "memory:记忆已更新，请重新打开这条记忆后再调整范围。":
    "Memori telah berubah. Buka kembali sebelum menyesuaikan cakupan.",
  "memory:范围修改未提交，当前范围保持不变。请修正后重试。":
    "Perubahan cakupan tidak dikirim. Cakupan saat ini tidak berubah; perbaiki masalahnya lalu coba lagi.",
  "memory:保存结果尚未确认，请重试。":
    "Hasil penyimpanan belum terkonfirmasi. Silakan coba lagi.",
  "memory:原话已保存在本机；当前内容、模型接收方或授权版本尚未通过核对，未继续整理。":
    "Teks asli disimpan di perangkat ini; konten, penerima model, atau versi izin tidak lolos peninjauan, sehingga pemrosesan tidak dilanjutkan.",
  "memory:HY 未返回可核对的已有记忆，未创建替代内容；原始记录已保留，可重试处理。":
    "HY tidak mengembalikan memori yang dapat diverifikasi, dan tidak ada pengganti yang dibuat. Catatan asli tetap disimpan; Anda dapat mencoba memproses lagi.",
  "memory:历史引用尚未绑定到原始来源，当前批次未完成，原话已保留。":
    "Kutipan riwayat belum ditautkan ke sumber aslinya. Batch ini belum selesai; teks asli tetap disimpan.",
  "memory:记忆内容未通过来源与作者核验，当前批次未完成，原话已保留。":
    "Isi memori tidak lolos verifikasi sumber dan penulis. Batch ini belum selesai; teks asli tetap disimpan.",
  "memory:所选记忆已不可读取，请重新读取记忆库。":
    "Memori yang dipilih tidak dapat dibaca lagi. Muat ulang pustaka memori.",
  "memory:还没连上本地 Core，正在重试…":
    "Core lokal belum terhubung; mencoba ulang…",
  "memory:记忆内容或使用权限已变化，回答已停止。请重新提问。":
    "Isi memori atau izin akses berubah, sehingga jawaban dihentikan. Tanyakan lagi.",
  "memory:这条记忆的来源授权已撤回，暂时不能读取。":
    "Akses sumber untuk memori ini telah dicabut, sehingga tidak dapat dibaca saat ini.",
  "memory:记忆已被更新，请刷新后重试。":
    "Memori ini telah diperbarui. Muat ulang lalu coba lagi.",
  "memory:已删除的记忆不能修改。": "Memori yang dihapus tidak dapat diedit.",
  "memory:这条记忆已经删除。": "Memori ini telah dihapus.",
  "memory:这条记忆已被更新的记忆取代，只能查看或删除。":
    "Memori ini telah digantikan oleh memori yang lebih baru dan hanya dapat dilihat atau dihapus.",
  "memory:没有更早的修改可以撤销。":
    "Tidak ada perubahan sebelumnya yang dapat dibatalkan.",
  "memory:来源原话已不存在，无法建立这条记忆。":
    "Teks sumber asli sudah tidak ada, sehingga memori ini tidak dapat dibuat.",
  "memory:这次操作与已完成的请求不一致。":
    "Tindakan ini tidak cocok dengan permintaan yang telah selesai.",
  "memory:输入内容不符合要求。": "Isian tidak memenuhi persyaratan.",
  "memory:本地记忆库正忙，请稍后重试。":
    "Pustaka memori lokal sedang sibuk. Silakan coba lagi nanti.",
  "memory:记忆": "Memori",
  "memory:记忆空间": "Ruang memori",
  "memory:全部应用": "Semua aplikasi",
  "memory:来源": "Sumber",
  "memory:主题": "Topik",
  "memory:归纳": "Sintesis",
  "memory:全部记忆": "Semua memori",
  "memory:来源待核对": "Sumber perlu ditinjau",
  "memory:所选来源待核对": "Sumber yang dipilih perlu ditinjau",
  "memory:未识别来源": "Sumber tidak teridentifikasi",
  "memory:这个来源下还没有记忆": "Belum ada memori dari sumber ini",
  "memory:只在最相关的 {count} 条记忆里查找关键词":
    "Kata kunci hanya dicocokkan di antara {count} memori paling relevan",
  "memory:未知来源": "Sumber tidak diketahui",
  "memory:记忆读取失败": "Tidak dapat memuat memori",
  "memory:正在读取…": "Memuat…",
  "memory:正在更新…": "Memperbarui…",
  "memory:读取失败": "Tidak dapat memuat",
  "memory:正在核对来源…": "Memeriksa sumber…",
  "memory:正在导入历史会话，记忆将陆续出现":
    "Mengimpor percakapan sebelumnya; memori akan muncul bertahap",
  "memory:记忆工作台": "Ruang kerja memori",
  "memory:来源与主题": "Sumber dan topik",
  "memory:记忆或原始对话": "Memori atau percakapan asli",
  "memory:原始对话": "Percakapan asli",
  "memory:地图组织方式": "Pengelompokan peta",
  "memory:筛选地图记忆": "Filter memori peta",
  "memory:搜索原始对话": "Cari percakapan asli",
  "memory:搜索记忆": "Cari memori",
  "memory:筛选记忆…": "Filter memori…",
  "memory:向记忆提问": "Ajukan pertanyaan ke memori",
  "memory:询问全部已授权可用记忆…":
    "Tanya tentang semua memori yang diotorisasi dan tersedia…",
  "memory:发送问题": "Kirim pertanyaan",
  "memory:刷新地图记忆": "Muat ulang memori peta",
  "memory:关闭新增记忆提示": "Tutup pemberitahuan memori baru",
  "memory:地图记忆列表": "Daftar memori peta",
  "memory:记忆读取失败，请重试。":
    "Tidak dapat memuat memori. Silakan coba lagi.",
  "memory:重新读取地图记忆": "Muat ulang memori peta",
  "memory:没有匹配的记忆": "Tidak ada memori yang cocok",
  "memory:本页没有匹配的主题记忆":
    "Tidak ada memori topik yang cocok di halaman ini",
  "memory:还没有已生成的记忆": "Belum ada memori yang dibuat",
  "memory:地图记忆分页": "Halaman memori peta",
  "memory:上一页记忆": "Halaman memori sebelumnya",
  "memory:下一页记忆": "Halaman memori berikutnya",
  "memory:记忆关系图": "Graf memori",
  "memory:记忆关联": "Koneksi memori",
  "memory:刚导入的原话，还不是沉淀后的记忆":
    "Teks asli yang baru diimpor, belum menjadi memori",
  "memory:来源归属": "Koneksi sumber",
  "memory:可在沉淀记忆后查看视图": "Buat memori untuk melihat graf",
  "memory:正在读取记忆地图…": "Memuat peta memori…",
  "memory:查看选中来源原文": "Lihat teks sumber yang dipilih",
  "memory:查看选中记忆原文": "Lihat teks memori yang dipilih",
  "memory:查看原文": "Lihat percakapan asli",
  "memory:整理已有关系": "Bangun koneksi yang ada",
  "memory:正在读取原文…": "Memuat percakapan asli…",
  "memory:选择一条原始对话": "Pilih percakapan asli",
  "memory:记忆暂时无法读取": "Memori untuk sementara tidak tersedia",
  "memory:当前筛选没有可显示的记忆":
    "Tidak ada memori untuk ditampilkan dengan filter ini",
  "memory:正在读取记忆…": "Memuat memori…",
  "memory:已保存的对话还未生成记忆":
    "Percakapan tersimpan belum menjadi memori",
  "memory:还没有可显示的记忆": "Belum ada memori untuk ditampilkan",
  "memory:连线表示来源归属": "Garis menunjukkan koneksi sumber",
  "memory:记忆详情": "Detail memori",
  "memory:来源原文": "Teks asli sumber",
  "memory:关联节点": "Node terkait",
  "memory:关闭记忆详情": "Tutup detail memori",
  "memory:关闭详情": "Tutup detail",
  "memory:正在读取这条记忆…": "Memuat memori ini…",
  "memory:这条记忆的原文暂时无法读取，选择已保留。":
    "Tidak dapat memuat teks asli memori ini. Pilihan tetap disimpan.",
  "memory:重试读取原文": "Coba muat percakapan lagi",
  "memory:已沉淀的记忆": "Memori yang dibuat",
  "memory:正在读取记忆详情…": "Memuat detail memori…",
  "memory:沉淀记忆后查看详情": "Buat memori untuk melihat detail",
  "memory:时间未知": "Waktu tidak diketahui",
  "memory:来源原文已不可读取": "Teks asli sumber tidak tersedia",
  "memory:关联记忆": "Memori terkait",
  "memory:正在读取完整对话…": "Memuat percakapan lengkap…",
  "memory:重试读取完整对话": "Coba muat percakapan lengkap lagi",
  "memory:看完整对话": "Lihat percakapan lengkap",
  "memory:今天": "Hari ini",
  "memory:你": "Anda",
  "memory:助手": "Asisten",
  "memory:工具": "Alat",
  "memory:未知": "Tidak diketahui",
  "memory:作者待核对": "Penulis perlu ditinjau",
  "memory:你说": "Anda berkata",
  "memory:助手原话": "Pesan asli asisten",
  "memory:已沉淀": "Dibuat",
  "memory:已删除": "Dihapus",
  "memory:已被取代": "Digantikan",
  "memory:状态待核对": "Status perlu ditinjau",
  "memory:编辑": "Edit",
  "memory:原始依据": "Bukti asli",
  "memory:这段原文当前不可查看。": "Teks asli ini sedang tidak tersedia.",
  "memory:版本记录": "Riwayat versi",
  "memory:更多管理": "Tindakan lainnya",
  "memory:撤销上次修改": "Batalkan perubahan terakhir",
  "memory:删除记忆": "Hapus memori",
  "memory:编辑记忆": "Edit memori",
  "memory:返回记忆": "Kembali ke memori",
  "memory:修改这条记忆，原始对话保持不变。":
    "Edit memori ini; percakapan asli tidak berubah.",
  "memory:记忆内容": "Isi memori",
  "memory:使用范围保持不变": "Cakupan akses tidak berubah",
  "memory:已有新版本": "Ada versi yang lebih baru",
  "memory:你的草稿已保留，先查看新版本再决定如何修改。":
    "Draf Anda tetap disimpan. Tinjau versi baru sebelum membuat perubahan.",
  "memory:查看新版本": "Lihat versi baru",
  "memory:基于此版本继续编辑": "Lanjutkan mengedit dari versi ini",
  "memory:取消": "Batal",
  "memory:保存": "Simpan",
  "memory:重试": "Coba lagi",
  "memory:删除": "Hapus",
  "memory:对话": "Percakapan",
  "memory:工作": "Pekerjaan",
  "memory:编码": "Pengodean",
  "memory:保存修改": "Simpan perubahan",
  "memory:对应记忆": "Memori terkait",
  "memory:查看记忆": "Lihat memori",
  "memory:删除这条记忆？": "Hapus memori ini?",
  "memory:删除后不再用于新的召回，来源 App 中的原始对话不会删除。":
    "Memori ini tidak akan digunakan lagi untuk pencarian baru. Percakapan asli di aplikasi sumber tidak akan dihapus.",
  "memory:已经发送给应用的内容无法远程收回。":
    "Konten yang sudah dikirim ke aplikasi tidak dapat ditarik kembali dari jarak jauh.",
  "memory:放弃尚未保存的修改？": "Buang perubahan yang belum disimpan?",
  "memory:来源权限已变化或原文不可用。":
    "Izin sumber berubah atau teks asli tidak tersedia.",
  "memory:暂时无法读取新版本，草稿已保留。":
    "Tidak dapat memuat versi baru. Draf Anda tetap disimpan.",
  "memory:修改未提交；草稿已保留。请按提示修正后重试。":
    "Perubahan tidak dikirim; draf Anda tetap disimpan. Perbaiki masalahnya lalu coba lagi.",
  "memory:修改尚未确认，草稿已保留。请先刷新核对，避免重复提交。":
    "Perubahan belum terkonfirmasi; draf Anda tetap disimpan. Muat ulang dan periksa sebelum mencoba lagi agar tidak terkirim ganda.",
  "memory:删除未提交。请核对这条记忆的状态后重试。":
    "Penghapusan tidak dikirim. Periksa status memori ini sebelum mencoba lagi.",
  "memory:删除尚未确认。请重新核对记忆状态，不会自动扩大删除范围。":
    "Penghapusan belum terkonfirmasi. Periksa status memori; cakupan penghapusan tidak akan meluas secara otomatis.",
  "memory:撤销未提交。请按提示修正后重试。":
    "Pembatalan tidak dikirim. Perbaiki masalahnya lalu coba lagi.",
  "memory:撤销尚未确认，请先核对当前记忆版本，避免重复撤销。":
    "Pembatalan belum terkonfirmasi. Periksa versi memori saat ini sebelum mencoba lagi agar tidak dibatalkan dua kali.",
  "memory:添加记忆": "Tambah memori",
  "memory:关闭新增记忆": "Tutup dialog tambah memori",
  "memory:关闭": "Tutup",
  "memory:记下这件事": "Catat hal ini",
  "memory:例如：项目周会不排在周五。":
    "Misalnya: Jangan jadwalkan rapat proyek pada hari Jumat.",
  "memory:整理模型：": "Model pemrosesan: ",
  "memory:尚未配置，原话仍可保存":
    "Belum dikonfigurasi; teks asli tetap dapat disimpan",
  "memory:正在读取": "Memuat",
  "memory:配置模型": "Konfigurasi model",
  "memory:保存中…": "Menyimpan…",
  "memory:保存原话": "Simpan teks asli",
  "memory:最近添加": "Baru ditambahkan",
  "memory:原话已保存，等待可用模型":
    "Teks asli disimpan; menunggu model yang tersedia",
  "memory:原话已保存，等待整理": "Teks asli disimpan; menunggu pemrosesan",
  "memory:正在等待整理": "Menunggu pemrosesan",
  "memory:正在整理": "Memproses",
  "memory:原话已保存，整理未完成":
    "Teks asli disimpan; pemrosesan belum selesai",
  "memory:整理已完成": "Pemrosesan selesai",
  "memory:原话已保存，未提取出可用记忆":
    "Teks asli disimpan; tidak ada memori yang dapat diekstrak",
  "memory:最近添加的内容读取失败。":
    "Tidak dapat membaca memori yang baru ditambahkan.",
  "memory:整理状态暂时不可用，原话已保存。":
    "Status pemrosesan untuk sementara tidak tersedia; teks asli telah disimpan.",
  "memory:已保存，正在后台沉淀记忆":
    "Disimpan; membuat memori di latar belakang",
  "memory:未能确认保存结果，输入已保留。重试不会重复添加。":
    "Hasil penyimpanan tidak dapat dikonfirmasi. Teks Anda tetap disimpan; mencoba lagi tidak akan menambahkan duplikat.",
  "memory:模型服务要求检查余额或付款状态，原话仍已保存。":
    "Layanan model meminta pemeriksaan saldo atau pembayaran; teks asli tetap disimpan.",
  "memory:这台设备打不开 3D 视图，已显示关联列表。":
    "Tampilan 3D tidak tersedia di perangkat ini. Menampilkan koneksi sebagai daftar.",
  "memory:图谱工具": "Kontrol graf",
  "memory:放大": "Perbesar",
  "memory:放大图谱": "Perbesar graf",
  "memory:缩小": "Perkecil",
  "memory:缩小图谱": "Perkecil graf",
  "memory:重置视角": "Atur ulang tampilan",
  "memory:重置图谱视角": "Atur ulang tampilan graf",
  "memory:查看读取详情": "Lihat detail pembacaan",
  "memory:原文暂时无法读取": "Tidak dapat memuat teks asli",
  "memory:暂时无法读取这段对话，已保留当前记忆。":
    "Tidak dapat memuat percakapan ini. Memori saat ini tetap dipilih.",
  "memory:已清除筛选，定位到关联记忆。":
    "Filter dihapus untuk menemukan memori terkait.",
  "memory:所选记忆不在当前筛选内，已取消选择。":
    "Memori yang dipilih tidak termasuk dalam filter ini; pilihan dihapus.",
  "memory:列表分隔符": ", ",
  "memory:冒号": ":",
  "memory:手动添加": "Ditambahkan secara manual",
  "memory:导入的已有记忆": "Memori lama yang diimpor",
  "memory:记忆与原文": "Memori dan teks asli",
  "memory:第 {revision} 版": "Versi {revision}",
  "memory:对话时间 ": "Waktu percakapan",
  "memory:沉淀时间 ": "Dibuat pada",
  "memory:原对话 ": "Percakapan asli ",
  "memory:{count} 条记录没有原始时间，未用导入时间代替。":
    "{count} catatan tidak memiliki waktu asli; waktu impor tidak digunakan sebagai pengganti.",
  "memory:作者数量按当前记录分类统计，不代表记忆抽取已完成或语义已核实。":
    "Jumlah penulis mencerminkan catatan tersimpan, bukan ekstraksi memori yang selesai atau makna yang terverifikasi.",
  "memory:统计于 ": "Statistik per ",
  "memory: · 导入数量仍在更新，可刷新核对":
    " · Jumlah impor masih diperbarui; muat ulang untuk memeriksa",
  "memory:对话于 {day}": "Percakapan pada {day}",
  "memory:导入于 {day}": "Diimpor pada {day}",
  "memory:写入于 {day}": "Ditulis pada {day}",
  "memory:已开始处理所选对话": "Memproses percakapan yang dipilih",
  "memory:处理请求已受理": "Permintaan pemrosesan diterima",
  "memory:模型连接已通过。首批记忆生成后，会逐步出现在记忆列表里。":
    "Koneksi model terkonfirmasi. Memori akan muncul di daftar saat dibuat.",
  "memory:已导入 {count} 段对话，整理成记忆后会出现在这里。":
    "{count} percakapan diimpor. Memori akan muncul di sini setelah diproses.",
  "memory:历史已保存在本机，记忆就绪后会出现在这里。":
    "Riwayat disimpan di perangkat ini. Memori akan muncul di sini saat siap.",
  "memory:连接应用并导入历史后，可以开始沉淀记忆。":
    "Hubungkan aplikasi dan impor riwayat untuk mulai membuat memori.",
  "memory:当前已生成 0 条记忆。请求已受理不等于已经完成，原始对话、模型处理任务和记忆数量分别统计。":
    "Belum ada memori yang dibuat. Permintaan yang diterima belum berarti selesai; percakapan asli, pekerjaan pemrosesan, dan memori dihitung terpisah.",
  "memory:返回应用": "Kembali ke aplikasi",
  "memory:共 {count} 条 · 正在核对来源…": "Total {count} · Memeriksa sumber…",
  "memory:共 {count} 条 · 正在核对来源….one":
    "Total {count} · Memeriksa sumber…",
  "memory:共 {count} 条": "Total {count} item",
  "memory:共 {count} 条.one": "Total {count} item",
  "memory: · 正在核对…": " · Memeriksa…",
  "memory:新增 {count} 条记忆": "{count} memori baru",
  "memory:新增 {count} 条记忆.one": "{count} memori baru",
  "memory:沉淀于 ": "Dibuat pada ",
  "memory:共 {count} 条，第 {page} / {pages} 页":
    "Total {count} · Halaman {page} dari {pages}",
  "memory:共 {count} 条，第 {page} / {pages} 页.one":
    "Total {count} · Halaman {page} dari {pages}",
  "memory:已沉淀 {count} 条记忆": "{count} memori dibuat",
  "memory:已沉淀 {count} 条记忆.one": "{count} memori dibuat",
  "memory:关键词“{query}”": "Kata kunci “{query}”",
  "memory:已沉淀 {count} 条{filter} · 当前展示 {shown} 条":
    "{count} memori dibuat{filter} · Menampilkan {shown}",
  "memory:已沉淀 {count} 条{filter} · 当前展示 {shown} 条.one":
    "{count} memori dibuat{filter} · Menampilkan {shown}",
  "memory:（{filter}）": " ({filter})",
  "memory:第 {page} 页": "Halaman {page}",
  "memory:正在等待整理：": "Menunggu pemrosesan: ",
  "memory:记忆未在生成": "Memori tidak sedang dibuat",
  "memory:还没有开始生成记忆：{reason}（{count} 条待处理）":
    "Memori tidak sedang dibuat: {reason} ({count} tertunda)",
  "memory:去配置生成模型": "Konfigurasi model generatif",
  "memory:模型暂停处理": "Model dijeda",
  "memory:模型配置有问题（{reason}），记忆暂停处理，已有 {count} 条在排队":
    "Ada masalah konfigurasi model ({reason}). Pemrosesan memori dijeda; {count} item dalam antrean",
  "memory:模型配置有问题（{reason}），记忆暂停处理，已有 {count} 条在排队.one":
    "Ada masalah konfigurasi model ({reason}). Pemrosesan memori dijeda; {count} item dalam antrean",
  "memory:API Key 无效或没有权限":
    "kunci API tidak valid atau tidak punya izin",
  "memory:找不到模型或地址": "model atau alamat tidak ditemukan",
  "memory:请求过于频繁，被服务商限流":
    "terlalu banyak permintaan, dibatasi oleh penyedia",
  "memory:模型响应超时": "respons model melewati batas waktu",
  "memory:模型服务出错": "layanan model mengalami kesalahan",
  "memory:无法连接模型服务": "tidak dapat terhubung ke layanan model",
  "memory:连接模型服务失败": "koneksi ke layanan model gagal",
  "calls.directHost": "Panggilan langsung (diagnostik)",
  "calls.headline.timedOut": "{host} tidak dapat mencari memori tepat waktu",
  "calls.headline.unavailable": "{host} tidak dapat mencari memori saat ini",
  "calls.headline.denied": "{host} tidak diotorisasi untuk mencari memori",
  "calls.headline.empty": "{host} tidak menemukan memori yang cocok",
  "calls.headline.found": "{host} menemukan {count} item terkait",
  "calls.headline.found.one": "{host} menemukan {count} item terkait",
  "calls.headline.rawOnly": "{host} menemukan {count} percakapan asli",
  "calls.headline.rawOnly.one": "{host} menemukan {count} percakapan asli",
  "calls.headline.memoryOnly": "{host} menemukan {count} memori",
  "calls.headline.memoryOnly.one": "{host} menemukan {count} memori",
  "calls.headline.mixed":
    "{host} menemukan {memories} memori · {raw} percakapan asli",
  "calls.headline.mixed.one":
    "{host} menemukan {memories} memori · {raw} percakapan asli",
  "calls.headline.completed": "Panggilan {host} selesai",
  "calls.detail.timedOut":
    "Memori sedang diproses. Pencarian habis waktu, sehingga jawaban ini tidak menyertakan memori.",
  "calls.detail.unavailable":
    "MemHub untuk sementara tidak tersedia, sehingga jawaban ini tidak menyertakan memori.",
  "calls.detail.denied": "Izin saat ini tidak memperbolehkan pencarian ini.",
  "calls.detail.partialDelivered":
    "Diberikan ke {host} · sebagian konten masih diproses",
  "calls.detail.partial": "Sebagian konten masih diproses",
  "calls.detail.delivered": "Diberikan ke {host}",
  "calls.detail.notDelivered": "Tidak diberikan ke aplikasi",
  "calls.detail.empty": "Tidak ada memori yang cocok kali ini",
  "calls.detail.success": "Pencarian berhasil",
  "calls.title": "Riwayat penggunaan",
  "calls.query": "Pencarian memori ini",
  "calls.backApps": "Kembali ke aplikasi",
  "calls.backHistory": "Kembali ke riwayat",
  "calls.filter": "Filter menurut aplikasi",
  "calls.allApps": "Semua aplikasi",
  "calls.recent": "Riwayat terbaru",
  "calls.refresh": "Muat ulang riwayat",
  "calls.clearPage": "Hapus halaman ini",
  "calls.clearAll": "Hapus semua riwayat",
  "calls.clearAllShort": "Hapus semua",
  "calls.loading": "Memuat riwayat penggunaan",
  "calls.clearing": "Menghapus riwayat yang dipilih",
  "calls.readFailed": "Tidak dapat memuat riwayat. Muat ulang dan coba lagi.",
  "calls.unavailable": "Riwayat penggunaan tidak tersedia. Muat ulang nanti.",
  "calls.incomplete":
    "Sebagian panggilan tidak tercatat; daftar mungkin tidak lengkap.",
  "calls.callDetail": "Detail panggilan",
  "calls.source": " · Sumber: {source}",
  "calls.stats":
    "Ditemukan {raw} bagian asli · {memories} memori · {seconds} detik",
  "calls.metadata": "Detail catatan",
  "calls.privacy":
    "Demi privasi, pertanyaan asli dan jawaban Anda tidak disimpan di sini; hanya waktu, sumber, dan hasil yang disimpan.",
  "calls.exactTime": "Waktu tepat",
  "calls.app": "Aplikasi",
  "calls.type": "Jenis",
  "calls.result": "Status hasil",
  "calls.tool": "Cari memori",
  "calls.notRecorded": "Tidak dicatat",
  "calls.missingRow":
    "Catatan ini tidak lagi ada di halaman ini. Kembali ke riwayat untuk menemukannya.",
  "calls.list": "Riwayat panggilan",
  "calls.emptyPage": "Tidak ada catatan di halaman ini",
  "calls.empty": "Belum ada riwayat penggunaan",
  "calls.pagination": "Halaman riwayat",
  "calls.previous": "Halaman sebelumnya",
  "calls.next": "Halaman berikutnya",
  "calls.policy": "Disimpan selama {days} hari, maksimal {count} catatan",
  "calls.confirmAllTitle": "Hapus semua riwayat penggunaan?",
  "calls.confirmPageTitle": "Hapus {count} catatan di halaman ini?",
  "calls.confirmPageTitle.one": "Hapus {count} catatan di halaman ini?",
  "calls.confirmAllDescription":
    "Menghapus semua catatan panggilan yang disimpan, bukan hanya halaman atau filter ini. Percakapan asli dan memori, izin, serta aturan penyimpanan tidak berubah. Tindakan ini tidak dapat dibatalkan.",
  "calls.confirmPageDescription":
    "Hanya menghapus catatan panggilan ini. Percakapan asli dan memori, izin, serta aturan penyimpanan tidak berubah. Tindakan ini tidak dapat dibatalkan.",
  "calls.confirmAll": "Konfirmasi hapus semua",
  "calls.confirmPage": "Konfirmasi hapus",
  "calls.cancel": "Batal",
  "calls.clearedAll": "Semua riwayat telah dihapus.",
  "calls.clearedPage": "Riwayat yang dipilih telah dihapus.",
  "calls.clearAllFailed":
    "Tidak dapat menghapus riwayat penggunaan. Catatan yang ada tidak berubah; periksa lalu coba lagi.",
  "calls.clearPageFailed":
    "Tidak dapat menghapus riwayat yang dipilih. Catatan yang ada tidak berubah; periksa lalu coba lagi.",
  "calls.clearUnknown":
    "Hasil tidak dapat dikonfirmasi. Periksa riwayat yang dimuat ulang; penghapusan tidak dicoba ulang secara otomatis.",
  "calls.references": "Konten terkait",
  "calls.referencesLoading": "Memuat konten terkait",
  "calls.referencesFailed":
    "Tidak dapat memuat konten terkait. Muat ulang dan coba lagi.",
  "calls.raw": "Percakapan asli",
  "calls.memory": "Memori",
  "calls.changed": "Versi berubah",
  "calls.pending": "Perlu ditinjau",
  "calls.notAvailable": "Tidak tersedia",
  "calls.openRaw": "Lihat percakapan asli",
  "calls.openMemory": "Lihat memori",
  "calls.noReferences": "Tidak ditemukan konten terkait",
  "calls.rawBody": "Teks percakapan asli",
  "calls.rawLoading": "Memuat percakapan asli",
  "calls.rawFailed":
    "Percakapan asli tidak tersedia atau versinya telah berubah.",
  "calls.state.grounded": "Pencarian berhasil",
  "calls.state.empty": "Pencarian berhasil; tidak ada memori yang cocok",
  "calls.state.partial": "Pencarian berhasil; sebagian konten masih diproses",
  "calls.state.timed_out":
    "Pencarian habis waktu; tidak ada memori yang diberikan",
  "calls.state.unavailable": "MemHub untuk sementara tidak tersedia",
  "calls.state.denied": "Pencarian ini tidak diotorisasi",
  "calls.return.timed_out":
    "MemHub sedang mengimpor dan memproses memori. Pencarian tidak selesai tepat waktu, sehingga {host} menjawab tanpa memori. Pencarian akan berlanjut setelah pemrosesan selesai.",
  "calls.return.unavailable":
    "MemHub untuk sementara tidak tersedia; jawaban ini tidak menyertakan memori.",
  "calls.return.denied":
    "Izin saat ini tidak memperbolehkan pencarian ini; tidak ada memori yang diberikan ke aplikasi.",
  "calls.return.partial": "Sebagian konten masih diproses.",
  "clock.unknown": "Waktu tidak diketahui",
  "clock.today": "Hari ini {clock}",
  settings: "Pengaturan",
  languageTitle: "Bahasa antarmuka",
  languageLabel: "Bahasa",
  languageSystem: "Gunakan bahasa sistem",
  languageChinese: "Bahasa Tionghoa Sederhana",
  languageEnglish: "Bahasa Inggris",
  languageSpanish: "Bahasa Spanyol",
  languageDescription:
    "Pilih bahasa antarmuka. Pengaturan ini tidak mengubah percakapan atau memori Anda.",
  languageSaveError:
    "Tidak dapat menyimpan pengaturan bahasa. Silakan coba lagi.",
  languageLoadError: "Tidak dapat memuat pengaturan bahasa. Silakan coba lagi.",
  nativeErrorTitle: "MemHub tidak dapat dimulai",
  startupPreparing: "Memulai dan menyiapkan layanan memori…",
  startupUpgradeHint:
    "Peluncuran pertama atau peningkatan data memori dapat memerlukan beberapa menit. MemHub terbuka otomatis setelah selesai.",
  "migration.dialogMessage":
    "Migrasi data untuk pembaruan MemHub ini tidak selesai.",
  "migration.dialogDetail":
    "Untuk melindungi data Anda, MemHub tidak akan dijalankan. Lampirkan log migrasi ke issue GitHub dan kirimkan kepada kami. (Kode galat: {code})",
  "migration.dataRestored":
    "Data Anda telah dipulihkan ke keadaan sebelum pembaruan.",
  "migration.logPath": "Log: {path}",
  "migration.openLog": "Buka log",
  "migration.quit": "Keluar",
  "migration.issueHeading": "## Migrasi pembaruan gagal",
  "migration.issueUpgrade":
    "- Pembaruan MemHub dari {fromVersion} (build {fromBuild}) ke {toVersion} (build {toBuild}) gagal memigrasikan data.",
  "migration.issueCode": "- Kode galat: {code}",
  "migration.issueStep": "- Langkah yang gagal: {step}",
  "migration.issueRestored":
    "- Data dipulihkan ke keadaan sebelum pembaruan: {restored}",
  "migration.issueSystem": "- Sistem: {system}",
  "migration.issueArch": " ({arch})",
  "migration.issueLog": "- Log migrasi (lokal): {path}",
  "migration.issueAttach":
    "Seret file log di atas ke sini sebagai lampiran, lalu kirim.",
  "migration.unknown": "tidak diketahui",
  "migration.yes": "ya",
  "migration.no": "tidak",
};
