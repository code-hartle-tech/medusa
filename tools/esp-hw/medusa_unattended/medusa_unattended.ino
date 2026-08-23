/*
 * medusa_unattended — passive, headless Wi-Fi inventory for an owned lab.
 *
 * Boots without a host, channel-hops across 2.4 GHz, deduplicates beaconing APs
 * by BSSID, and keeps two local LittleFS artifacts:
 *
 *   /medusa_inventory.bin  checksummed state used to survive power cycles
 *   /medusa_log.csv        human-readable snapshot for local USB retrieval
 *
 * This firmware never associates, transmits 802.11 frames, or uploads data.
 * It fails closed if LittleFS cannot be mounted and never formats flash on a
 * mount error. Commands on the 115200-baud USB serial console:
 *
 *   STATUS          print capture/storage status
 *   INIT CONFIRM    format an unavailable LittleFS partition, then mount it
 *   FLUSH           persist the current snapshot now
 *   DUMP            emit the selected stored inventory as CSV between markers
 *   DUMP STORED     emit the primary CSV artifact without modifying storage
 *   DUMP DB PRIMARY/TEMP/BACKUP
 *                   render one valid DB candidate read-only for recovery
 *   DUMP CSV TEMP/BACKUP
 *                   emit one preserved CSV candidate read-only for recovery
 *   CLEAR CONFIRM   explicitly erase the local inventory
 *
 * LAWFUL USE ONLY — capture only networks you own or are authorized to audit.
 */
#include <Arduino.h>
#include <WiFi.h>
#include <LittleFS.h>
#include <string.h>
#include "esp_wifi.h"

#define MAX_APS 120
#define FLUSH_INTERVAL_MS 15000UL
#define CHECKPOINT_INTERVAL_MS 300000UL

static const char* DB_PATH = "/medusa_inventory.bin";
static const char* DB_BACKUP_PATH = "/medusa_inventory.bak";
static const char* DB_TEMP_PATH = "/medusa_inventory.tmp";
static const char* CSV_PATH = "/medusa_log.csv";
static const char* CSV_BACKUP_PATH = "/medusa_log.bak";
static const char* CSV_TEMP_PATH = "/medusa_log.tmp";
static const uint32_t DB_COMPLETE_MARKER = 0x434F4D50UL;  // "COMP"
static const uint16_t DB_VERSION = 3;
static const char CSV_HEADER[] =
    "bssid,ssid,channel,rssi,seen,inventory_partial,capacity_drops\n";

struct __attribute__((packed)) Ap {
  uint8_t bssid[6];
  char ssid[33];
  uint8_t ssid_len;
  uint8_t channel;
  int8_t rssi;
  uint32_t seen;
};

// Version 2 was the first on-device format.  Its row CRC did not cover count,
// capacity_drops, or any transaction/completeness metadata, so it is treated
// as a legacy input and is never ordered against another legacy candidate.
struct __attribute__((packed)) LegacyDiskHeader {
  char magic[8];
  uint16_t version;
  uint16_t count;
  uint32_t crc32;
  uint32_t capacity_drops;
};

// Version 3 checksums the complete header (with crc32 set to zero) and all
// rows.  This binds the sequence, completeness marker, count, capacity-loss
// metadata, and the exact canonical CSV representation to one snapshot.
struct __attribute__((packed)) DiskHeader {
  char magic[8];
  uint16_t version;
  uint16_t header_size;
  uint16_t count;
  uint16_t flags;
  uint64_t generation;
  uint32_t capacity_drops;
  uint32_t rows_crc32;
  uint32_t csv_crc32;
  uint32_t csv_size;
  uint32_t complete_marker;
  uint32_t crc32;
};

struct DbCandidate {
  const char* path;
  bool exists;
  bool valid;
  bool current;
  bool unsupported_format;
  uint16_t detected_version;
  uint16_t count;
  uint32_t capacity_drops;
  uint64_t generation;
  uint32_t rows_crc32;
  uint32_t csv_crc32;
  uint32_t csv_size;
  uint32_t snapshot_crc32;
};

static_assert(sizeof(Ap) == 46, "unexpected inventory record layout");
static_assert(sizeof(LegacyDiskHeader) == 20, "unexpected legacy header layout");
static_assert(sizeof(DiskHeader) == 48, "unexpected v3 header layout");

static Ap aps[MAX_APS];
static Ap snapshot[MAX_APS];
static volatile uint16_t ap_count = 0;
static volatile bool inventory_dirty = false;  // new AP / revealed SSID / channel change
static volatile bool stats_dirty = false;      // RSSI and sighting-count updates
static volatile uint32_t inventory_generation = 0;
static volatile uint32_t stats_generation = 0;
static volatile uint32_t capacity_drops = 0;
static portMUX_TYPE inventory_mux = portMUX_INITIALIZER_UNLOCKED;
static bool storage_ready = false;
static bool capture_started = false;
static volatile bool capture_state_unknown = false;
// A CSV without any recoverable checksummed database may be the operator's
// only surviving copy. Keep the filesystem mounted for DUMP, but do not let a
// new capture session overwrite that file until CLEAR CONFIRM is explicit.
static volatile bool orphaned_csv_guard = false;
static bool recovery_requires_review = false;
static const char* recovery_state = "ok";
static bool database_loaded = false;
static bool dump_from_memory = false;
static bool dump_memory_is_volatile = false;
static uint64_t persisted_generation = 0;
static uint8_t active_channel = 1;

static void start_capture();

static uint32_t crc32_extend(uint32_t crc, const uint8_t* data, size_t length) {
  for (size_t i = 0; i < length; ++i) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; ++bit)
      crc = (crc >> 1) ^ (0xEDB88320UL & (uint32_t)-(int32_t)(crc & 1));
  }
  return crc;
}

static uint32_t crc32_bytes(const uint8_t* data, size_t length) {
  return ~crc32_extend(0xFFFFFFFFUL, data, length);
}

static uint16_t take_snapshot(uint32_t* generation = nullptr,
                              uint32_t* saved_stats_generation = nullptr,
                              uint32_t* saved_capacity_drops = nullptr) {
  portENTER_CRITICAL(&inventory_mux);
  uint16_t count = ap_count;
  memcpy(snapshot, aps, (size_t)count * sizeof(Ap));
  if (generation) *generation = inventory_generation;
  if (saved_stats_generation) *saved_stats_generation = stats_generation;
  if (saved_capacity_drops) *saved_capacity_drops = capacity_drops;
  portEXIT_CRITICAL(&inventory_mux);
  return count;
}

static bool append_char(char* output, size_t capacity, size_t* used, char value) {
  if (*used + 1 >= capacity) return false;
  output[(*used)++] = value;
  output[*used] = 0;
  return true;
}

static bool append_text(char* output, size_t capacity, size_t* used,
                        const char* text, size_t length) {
  if (*used + length >= capacity) return false;
  memcpy(output + *used, text, length);
  *used += length;
  output[*used] = 0;
  return true;
}

static bool format_csv_row(const Ap& ap, uint32_t drops, char* output,
                           size_t capacity, size_t* length) {
  int prefix = snprintf(output, capacity,
                        "%02X:%02X:%02X:%02X:%02X:%02X,\"",
                        ap.bssid[0], ap.bssid[1], ap.bssid[2],
                        ap.bssid[3], ap.bssid[4], ap.bssid[5]);
  if (prefix < 0 || (size_t)prefix >= capacity) return false;
  size_t used = (size_t)prefix;

  // Spreadsheet applications can interpret cells beginning with these bytes
  // as formulae. Prefix a literal apostrophe in the exported representation;
  // the checksummed database retains the exact SSID bytes.
  if (ap.ssid_len > 0 &&
      (ap.ssid[0] == '=' || ap.ssid[0] == '+' ||
       ap.ssid[0] == '-' || ap.ssid[0] == '@')) {
    if (!append_char(output, capacity, &used, '\'')) return false;
  }

  for (uint8_t i = 0; i < ap.ssid_len; ++i) {
    const uint8_t c = (uint8_t)ap.ssid[i];
    if (c == '"') {
      if (!append_text(output, capacity, &used, "\"\"", 2)) return false;
    } else if (c < 0x20 || c > 0x7E) {
      char escaped[5];
      const int escaped_length = snprintf(escaped, sizeof(escaped), "\\x%02X", c);
      if (escaped_length != 4 ||
          !append_text(output, capacity, &used, escaped, 4)) return false;
    } else if (!append_char(output, capacity, &used, (char)c)) {
      return false;
    }
  }

  const int suffix = snprintf(output + used, capacity - used,
                              "\",%u,%d,%lu,%s,%lu\n", ap.channel, ap.rssi,
                              (unsigned long)ap.seen, drops > 0 ? "yes" : "no",
                              (unsigned long)drops);
  if (suffix < 0 || (size_t)suffix >= capacity - used) return false;
  used += (size_t)suffix;
  *length = used;
  return true;
}

static bool valid_ap_record(const Ap& ap) {
  return ap.ssid_len <= 32 && ap.ssid[ap.ssid_len] == 0 &&
         ap.channel >= 1 && ap.channel <= 14;
}

static bool calculate_csv_metadata(const Ap* rows, uint16_t count, uint32_t drops,
                                   uint32_t* csv_crc32, uint32_t* csv_size) {
  uint32_t crc = crc32_extend(0xFFFFFFFFUL, (const uint8_t*)CSV_HEADER,
                              sizeof(CSV_HEADER) - 1);
  size_t total = sizeof(CSV_HEADER) - 1;
  for (uint16_t i = 0; i < count; ++i) {
    char row[224];
    size_t row_length = 0;
    if (!format_csv_row(rows[i], drops, row, sizeof(row), &row_length)) return false;
    if (total > UINT32_MAX - row_length) return false;
    total += row_length;
    crc = crc32_extend(crc, (const uint8_t*)row, row_length);
  }
  *csv_crc32 = ~crc;
  *csv_size = (uint32_t)total;
  return true;
}

// Probe one database into metadata only.  Candidate discovery must never
// mutate aps: an invalid or merely earlier file cannot become live state just
// because it happened to be inspected first.
static bool validate_database_candidate(DbCandidate* candidate) {
  candidate->exists = LittleFS.exists(candidate->path);
  candidate->valid = false;
  candidate->current = false;
  candidate->unsupported_format = false;
  candidate->detected_version = 0;
  candidate->count = 0;
  candidate->capacity_drops = 0;
  candidate->generation = 0;
  candidate->rows_crc32 = 0;
  candidate->csv_crc32 = 0;
  candidate->csv_size = 0;
  candidate->snapshot_crc32 = 0;
  if (!candidate->exists) return false;

  File file = LittleFS.open(candidate->path, FILE_READ);
  if (!file) return false;
  char magic[8] = {};
  uint16_t version = 0;
  bool ok = file.read((uint8_t*)magic, sizeof(magic)) == sizeof(magic) &&
            file.read((uint8_t*)&version, sizeof(version)) == sizeof(version) &&
            memcmp(magic, "MEDUSA01", 8) == 0;
  if (!ok || !file.seek(0)) {
    file.close();
    return false;
  }
  candidate->detected_version = version;

  uint16_t count = 0;
  uint32_t expected_rows_crc = 0;
  uint32_t complete_crc = 0xFFFFFFFFUL;
  if (version == DB_VERSION) {
    DiskHeader header = {};
    ok = file.read((uint8_t*)&header, sizeof(header)) == sizeof(header);
    ok = ok && header.header_size == sizeof(DiskHeader) && header.flags == 0;
    ok = ok && header.count <= MAX_APS && header.generation > 0;
    ok = ok && header.complete_marker == DB_COMPLETE_MARKER;
    ok = ok && header.csv_size >= sizeof(CSV_HEADER) - 1;
    ok = ok && file.size() == sizeof(header) + (size_t)header.count * sizeof(Ap);
    if (!ok) {
      file.close();
      return false;
    }
    DiskHeader checksum_header = header;
    checksum_header.crc32 = 0;
    complete_crc = crc32_extend(complete_crc, (const uint8_t*)&checksum_header,
                                sizeof(checksum_header));
    count = header.count;
    expected_rows_crc = header.rows_crc32;
    candidate->current = true;
    candidate->capacity_drops = header.capacity_drops;
    candidate->generation = header.generation;
    candidate->csv_crc32 = header.csv_crc32;
    candidate->csv_size = header.csv_size;
    candidate->snapshot_crc32 = header.crc32;
  } else if (version == 2) {
    LegacyDiskHeader header = {};
    ok = file.read((uint8_t*)&header, sizeof(header)) == sizeof(header);
    ok = ok && header.count <= MAX_APS;
    ok = ok && file.size() == sizeof(header) + (size_t)header.count * sizeof(Ap);
    if (!ok) {
      file.close();
      return false;
    }
    count = header.count;
    expected_rows_crc = header.crc32;
    candidate->capacity_drops = header.capacity_drops;
    candidate->snapshot_crc32 = header.crc32;
  } else {
    // Literal v1 and unknown future formats have no integrity semantics this
    // firmware can safely infer.  Preserve them for maintenance review.
    candidate->unsupported_format = true;
    file.close();
    return false;
  }

  uint32_t rows_crc = 0xFFFFFFFFUL;
  uint32_t canonical_csv_crc = crc32_extend(
      0xFFFFFFFFUL, (const uint8_t*)CSV_HEADER, sizeof(CSV_HEADER) - 1);
  size_t canonical_csv_size = sizeof(CSV_HEADER) - 1;
  for (uint16_t i = 0; ok && i < count; ++i) {
    Ap row = {};
    ok = file.read((uint8_t*)&row, sizeof(row)) == sizeof(row);
    ok = ok && valid_ap_record(row);
    if (ok) {
      rows_crc = crc32_extend(rows_crc, (const uint8_t*)&row, sizeof(row));
      if (candidate->current)
        complete_crc = crc32_extend(complete_crc, (const uint8_t*)&row, sizeof(row));
      char csv_row[224];
      size_t csv_row_length = 0;
      ok = format_csv_row(row, candidate->capacity_drops, csv_row,
                          sizeof(csv_row), &csv_row_length);
      ok = ok && canonical_csv_size <= UINT32_MAX - csv_row_length;
      if (ok) {
        canonical_csv_size += csv_row_length;
        canonical_csv_crc = crc32_extend(canonical_csv_crc,
                                         (const uint8_t*)csv_row,
                                         csv_row_length);
      }
    }
  }
  file.close();
  rows_crc = ~rows_crc;
  if (!ok || rows_crc != expected_rows_crc) return false;
  if (candidate->current && ~complete_crc != candidate->snapshot_crc32) return false;
  canonical_csv_crc = ~canonical_csv_crc;
  if (candidate->current &&
      (candidate->csv_crc32 != canonical_csv_crc ||
       candidate->csv_size != canonical_csv_size))
    return false;
  if (!candidate->current) {
    candidate->csv_crc32 = canonical_csv_crc;
    candidate->csv_size = (uint32_t)canonical_csv_size;
  }

  candidate->valid = true;
  candidate->count = count;
  candidate->rows_crc32 = rows_crc;
  return true;
}

static bool load_database_candidate(const DbCandidate& candidate) {
  if (!candidate.valid) return false;
  File file = LittleFS.open(candidate.path, FILE_READ);
  if (!file) return false;
  const size_t header_size = candidate.current ? sizeof(DiskHeader)
                                               : sizeof(LegacyDiskHeader);
  bool ok = file.seek(header_size);
  const size_t data_size = (size_t)candidate.count * sizeof(Ap);
  if (ok && data_size > 0)
    ok = file.read((uint8_t*)aps, data_size) == data_size;
  file.close();
  if (!ok || crc32_bytes((const uint8_t*)aps, data_size) != candidate.rows_crc32)
    return false;
  for (uint16_t i = 0; i < candidate.count; ++i)
    if (!valid_ap_record(aps[i])) return false;

  ap_count = candidate.count;
  capacity_drops = candidate.capacity_drops;
  persisted_generation = candidate.current ? candidate.generation : 0;
  inventory_dirty = false;
  stats_dirty = false;
  database_loaded = true;
  Serial.printf("UNATT_RESTORE\taps=%u\tfrom=%s\tgeneration=%llu\tformat=%s\n",
                ap_count, candidate.path,
                (unsigned long long)persisted_generation,
                candidate.current ? "v3" : "legacy-v2");
  return true;
}

static bool same_database_rows_exact(const DbCandidate& left,
                                     const DbCandidate& right) {
  if (!left.valid || !right.valid || left.count != right.count) return false;
  File left_file = LittleFS.open(left.path, FILE_READ);
  File right_file = LittleFS.open(right.path, FILE_READ);
  const size_t left_header = left.current ? sizeof(DiskHeader)
                                          : sizeof(LegacyDiskHeader);
  const size_t right_header = right.current ? sizeof(DiskHeader)
                                            : sizeof(LegacyDiskHeader);
  bool ok = left_file && right_file && left_file.seek(left_header) &&
            right_file.seek(right_header);
  uint8_t left_buffer[128];
  uint8_t right_buffer[128];
  size_t remaining = (size_t)left.count * sizeof(Ap);
  while (ok && remaining > 0) {
    const size_t wanted = min(sizeof(left_buffer), remaining);
    ok = left_file.read(left_buffer, wanted) == wanted &&
         right_file.read(right_buffer, wanted) == wanted &&
         memcmp(left_buffer, right_buffer, wanted) == 0;
    remaining -= wanted;
  }
  if (left_file) left_file.close();
  if (right_file) right_file.close();
  return ok;
}

static bool same_snapshot_state(const DbCandidate& left,
                                const DbCandidate& right) {
  return left.count == right.count &&
         left.capacity_drops == right.capacity_drops &&
         left.rows_crc32 == right.rows_crc32 &&
         same_database_rows_exact(left, right);
}

static bool same_current_snapshot(const DbCandidate& left,
                                  const DbCandidate& right) {
  return left.generation == right.generation &&
         left.snapshot_crc32 == right.snapshot_crc32 &&
         left.csv_crc32 == right.csv_crc32 && left.csv_size == right.csv_size &&
         same_snapshot_state(left, right);
}

static bool file_crc_and_size(const char* path, uint32_t* crc32, uint32_t* size) {
  File file = LittleFS.open(path, FILE_READ);
  if (!file) return false;
  uint32_t crc = 0xFFFFFFFFUL;
  uint32_t total = 0;
  uint8_t buffer[128];
  while (file.available()) {
    const size_t got = file.read(buffer, sizeof(buffer));
    if (got == 0 || total > UINT32_MAX - got) {
      file.close();
      return false;
    }
    total += (uint32_t)got;
    crc = crc32_extend(crc, buffer, got);
  }
  file.close();
  *crc32 = ~crc;
  *size = total;
  return true;
}

static bool csv_matches_metadata(const char* path, uint32_t expected_crc,
                                 uint32_t expected_size) {
  uint32_t actual_crc = 0;
  uint32_t actual_size = 0;
  return file_crc_and_size(path, &actual_crc, &actual_size) &&
         actual_crc == expected_crc && actual_size == expected_size;
}

static bool verify_database_file(const char* path, const Ap* rows, uint16_t count,
                                 uint32_t drops, uint64_t generation,
                                 uint32_t csv_crc32, uint32_t csv_size) {
  DbCandidate candidate = { path };
  if (!validate_database_candidate(&candidate) || !candidate.valid ||
      !candidate.current || candidate.count != count ||
      candidate.capacity_drops != drops || candidate.generation != generation ||
      candidate.csv_crc32 != csv_crc32 || candidate.csv_size != csv_size)
    return false;
  const size_t data_size = (size_t)count * sizeof(Ap);
  if (candidate.rows_crc32 != crc32_bytes((const uint8_t*)rows, data_size))
    return false;

  File file = LittleFS.open(path, FILE_READ);
  if (!file || !file.seek(sizeof(DiskHeader))) {
    if (file) file.close();
    return false;
  }
  uint8_t buffer[128];
  size_t offset = 0;
  bool ok = true;
  while (offset < data_size) {
    const size_t wanted = min(sizeof(buffer), data_size - offset);
    const size_t got = file.read(buffer, wanted);
    if (got != wanted || memcmp(buffer, ((const uint8_t*)rows) + offset, wanted) != 0) {
      ok = false;
      break;
    }
    offset += wanted;
  }
  file.close();
  return ok;
}

static bool write_database(const Ap* rows, uint16_t count, uint32_t drops,
                           uint64_t generation, uint32_t csv_crc32,
                           uint32_t csv_size) {
  LittleFS.remove(DB_TEMP_PATH);
  File file = LittleFS.open(DB_TEMP_PATH, FILE_WRITE);
  if (!file) return false;

  DiskHeader header = {};
  memcpy(header.magic, "MEDUSA01", 8);
  header.version = DB_VERSION;
  header.header_size = sizeof(DiskHeader);
  header.count = count;
  header.flags = 0;
  header.generation = generation;
  header.capacity_drops = drops;
  header.rows_crc32 = crc32_bytes((const uint8_t*)rows,
                                  (size_t)count * sizeof(Ap));
  header.csv_crc32 = csv_crc32;
  header.csv_size = csv_size;
  header.complete_marker = DB_COMPLETE_MARKER;
  header.crc32 = 0;
  uint32_t complete_crc = crc32_extend(0xFFFFFFFFUL,
                                       (const uint8_t*)&header, sizeof(header));
  complete_crc = crc32_extend(complete_crc, (const uint8_t*)rows,
                              (size_t)count * sizeof(Ap));
  header.crc32 = ~complete_crc;
  bool ok = file.write((const uint8_t*)&header, sizeof(header)) == sizeof(header);
  if (ok && count > 0)
    ok = file.write((const uint8_t*)rows, (size_t)count * sizeof(Ap)) == (size_t)count * sizeof(Ap);
  file.flush();
  const size_t expected_size = sizeof(header) + (size_t)count * sizeof(Ap);
  if (file.getWriteError() != 0 || file.size() != expected_size) ok = false;
  file.close();
  if (ok) ok = verify_database_file(DB_TEMP_PATH, rows, count, drops, generation,
                                    csv_crc32, csv_size);
  if (!ok) {
    LittleFS.remove(DB_TEMP_PATH);
    return false;
  }

  if (!LittleFS.exists(DB_PATH)) {
    // A valid backup may be the only durable copy after an interrupted prior
    // rotation. Keep it in place until the new database is installed.
    if (!LittleFS.rename(DB_TEMP_PATH, DB_PATH)) return false;
    return verify_database_file(DB_PATH, rows, count, drops, generation,
                                csv_crc32, csv_size);
  }

  if (LittleFS.exists(DB_BACKUP_PATH) && !LittleFS.remove(DB_BACKUP_PATH))
    return false;  // verified newer temp and primary both remain
  if (!LittleFS.rename(DB_PATH, DB_BACKUP_PATH)) {
    // Never discard the verified newer candidate merely because rotation
    // failed.  Boot selection can order primary/temp by generation.
    return false;
  }
  if (!LittleFS.rename(DB_TEMP_PATH, DB_PATH)) {
    if (LittleFS.exists(DB_BACKUP_PATH)) LittleFS.rename(DB_BACKUP_PATH, DB_PATH);
    return false;
  }
  return verify_database_file(DB_PATH, rows, count, drops, generation,
                              csv_crc32, csv_size);
}

static bool write_csv(const Ap* rows, uint16_t count, uint32_t drops,
                      uint32_t expected_crc32, uint32_t expected_size) {
  LittleFS.remove(CSV_TEMP_PATH);
  File file = LittleFS.open(CSV_TEMP_PATH, FILE_WRITE);
  if (!file) return false;
  size_t written_size = sizeof(CSV_HEADER) - 1;
  bool ok = file.write((const uint8_t*)CSV_HEADER, written_size) == written_size;
  for (uint16_t i = 0; i < count; ++i) {
    char row[224];
    size_t row_length = 0;
    if (!format_csv_row(rows[i], drops, row, sizeof(row), &row_length)) {
      ok = false;
      break;
    }
    written_size += row_length;
    if (file.write((const uint8_t*)row, row_length) != row_length) {
      ok = false;
      break;
    }
  }
  file.flush();
  if (file.getWriteError() != 0 || file.size() != written_size ||
      written_size != expected_size) ok = false;
  file.close();
  if (ok) ok = csv_matches_metadata(CSV_TEMP_PATH, expected_crc32, expected_size);
  if (!ok) {
    LittleFS.remove(CSV_TEMP_PATH);
    return false;
  }

  if (!LittleFS.exists(CSV_PATH)) {
    if (!LittleFS.rename(CSV_TEMP_PATH, CSV_PATH)) return false;
    const bool installed = csv_matches_metadata(CSV_PATH, expected_crc32,
                                                expected_size);
    bool cleanup_ok = true;
    if (installed && LittleFS.exists(CSV_BACKUP_PATH) &&
        !LittleFS.remove(CSV_BACKUP_PATH)) {
      Serial.println("UNATT_ERROR\tcsv-backup-cleanup-failed; paired backup retained");
      cleanup_ok = false;
    }
    return installed && cleanup_ok;
  }

  // Retain the prior primary until the complete, verified temp is ready.  The
  // old backup is rotated only after its equivalence to a database candidate
  // has been checked by boot recovery or the current transaction created it.
  if (LittleFS.exists(CSV_BACKUP_PATH) && !LittleFS.remove(CSV_BACKUP_PATH))
    return false;
  if (!LittleFS.rename(CSV_PATH, CSV_BACKUP_PATH)) return false;
  if (!LittleFS.rename(CSV_TEMP_PATH, CSV_PATH)) {
    if (LittleFS.exists(CSV_BACKUP_PATH)) LittleFS.rename(CSV_BACKUP_PATH, CSV_PATH);
    return false;
  }
  const bool installed = csv_matches_metadata(CSV_PATH, expected_crc32,
                                              expected_size);
  bool cleanup_ok = true;
  if (installed && LittleFS.exists(CSV_BACKUP_PATH) &&
      !LittleFS.remove(CSV_BACKUP_PATH)) {
    Serial.println("UNATT_ERROR\tcsv-backup-cleanup-failed; paired backup retained");
    cleanup_ok = false;
  }
  return installed && cleanup_ok;
}

static void stop_capture_for_recovery() {
  if (!capture_started && !capture_state_unknown) return;
  const esp_err_t disable_err = esp_wifi_set_promiscuous(false);
  if (disable_err == ESP_OK) {
    capture_started = false;
    capture_state_unknown = false;
    return;
  }
  const bool wifi_off = WiFi.mode(WIFI_OFF);
  if (wifi_off) {
    capture_started = false;
    capture_state_unknown = false;
    return;
  }
  const esp_err_t stop_err = esp_wifi_stop();
  if (stop_err == ESP_OK) {
    capture_started = false;
    capture_state_unknown = false;
    return;
  }
  capture_state_unknown = true;
  Serial.printf("UNATT_ERROR\twifi-stage=recovery-stop\tdisable_rc=0x%X\twifi_off=error\twifi_stop_rc=0x%X\tcapture=unknown\tcallback=quarantined\n",
                (unsigned int)disable_err, (unsigned int)stop_err);
}

static void enter_recovery(const char* state, bool requires_review,
                           bool prefer_memory_dump,
                           bool memory_is_volatile = false) {
  // Set the guard first; on_rx observes it and stops mutating the inventory
  // even if the SDK cannot prove that promiscuous reception was disabled.
  portENTER_CRITICAL(&inventory_mux);
  orphaned_csv_guard = true;
  portEXIT_CRITICAL(&inventory_mux);
  recovery_requires_review = requires_review;
  recovery_state = state;
  dump_from_memory = prefer_memory_dump;
  dump_memory_is_volatile = prefer_memory_dump && memory_is_volatile;
  stop_capture_for_recovery();
}

static bool persist_snapshot(bool force) {
  if (!storage_ready) {
    Serial.printf("UNATT_STORE\taps=0\tdb=error\tcsv=error\tcapacity_drops=%lu\treason=storage-unavailable\n",
                  (unsigned long)capacity_drops);
    return false;
  }
  if (orphaned_csv_guard) {
    Serial.printf("UNATT_STORE\taps=%u\tdb=error\tcsv=error\tcapacity_drops=%lu\treason=orphaned-csv-preserved\n",
                  ap_count, (unsigned long)capacity_drops);
    return false;
  }
  if (!force && !inventory_dirty && !stats_dirty) return true;
  uint32_t saved_generation = 0;
  uint32_t saved_stats_generation = 0;
  uint32_t saved_capacity_drops = 0;
  const uint16_t count = take_snapshot(&saved_generation, &saved_stats_generation,
                                       &saved_capacity_drops);
  uint32_t csv_crc32 = 0;
  uint32_t csv_size = 0;
  if (!calculate_csv_metadata(snapshot, count, saved_capacity_drops,
                              &csv_crc32, &csv_size)) {
    enter_recovery("csv-metadata-failed", true, true, true);
    Serial.printf("UNATT_STORE\taps=%u\tdb=error\tcsv=error\tcapacity_drops=%lu\treason=orphaned-csv-preserved\n",
                  count, (unsigned long)saved_capacity_drops);
    return false;
  }
  if (persisted_generation == UINT64_MAX) {
    enter_recovery("generation-exhausted", true, true, true);
    Serial.printf("UNATT_STORE\taps=%u\tdb=error\tcsv=error\tcapacity_drops=%lu\treason=orphaned-csv-preserved\n",
                  count, (unsigned long)saved_capacity_drops);
    return false;
  }
  const uint64_t next_generation = persisted_generation + 1;
  const bool db_ok = write_database(snapshot, count, saved_capacity_drops,
                                    next_generation, csv_crc32, csv_size);
  const bool csv_ok = db_ok && write_csv(snapshot, count, saved_capacity_drops,
                                         csv_crc32, csv_size);
  if (db_ok && csv_ok) {
    persisted_generation = next_generation;
    database_loaded = true;
    dump_from_memory = false;
    dump_memory_is_volatile = false;
    portENTER_CRITICAL(&inventory_mux);
    if (inventory_generation == saved_generation) inventory_dirty = false;
    if (stats_generation == saved_stats_generation) stats_dirty = false;
    portEXIT_CRITICAL(&inventory_mux);
  } else if (db_ok) {
    // The newest database is complete, but the prior CSV may be the only copy
    // of an earlier generation. Preserve every CSV artifact and stop capture.
    // Keep the now-stable live inventory intact: callbacks may have observed
    // additional beacons after take_snapshot() while the files were written.
    // Ordinary DUMP exposes that RAM read-only; explicit DUMP DB/CSV commands
    // expose each durable candidate for maintenance review.
    persisted_generation = next_generation;
    database_loaded = true;
    enter_recovery("db-csv-divergence", true, true, true);
    Serial.printf("UNATT_STORE\taps=%u\tdb=error\tcsv=error\tcapacity_drops=%lu\treason=orphaned-csv-preserved\tgeneration=%llu\n",
                  count, (unsigned long)saved_capacity_drops,
                  (unsigned long long)persisted_generation);
    return false;
  } else {
    enter_recovery("database-write-failed", true, true, true);
    Serial.printf("UNATT_STORE\taps=%u\tdb=error\tcsv=error\tcapacity_drops=%lu\treason=orphaned-csv-preserved\n",
                  count, (unsigned long)saved_capacity_drops);
    return false;
  }
  Serial.printf("UNATT_STORE\taps=%u\tdb=%s\tcsv=%s\tcapacity_drops=%lu\tpath=%s\tgeneration=%llu\n",
                count, db_ok ? "ok" : "error", csv_ok ? "ok" : "error",
                (unsigned long)saved_capacity_drops, CSV_PATH,
                (unsigned long long)persisted_generation);
  return db_ok && csv_ok;
}

static void dump_csv_file(const char* path, const char* label) {
  if (!storage_ready || !LittleFS.exists(path)) {
    Serial.println("UNATT_ERROR\tstored-csv-unavailable");
    return;
  }
  File file = LittleFS.open(path, FILE_READ);
  if (!file) {
    Serial.println("UNATT_ERROR\tstored-csv-open-failed");
    return;
  }
  const size_t maximum_csv_size = (sizeof(CSV_HEADER) - 1) +
                                  (size_t)MAX_APS * 223;
  if (file.size() < sizeof(CSV_HEADER) - 1 || file.size() > maximum_csv_size) {
    file.close();
    Serial.println("UNATT_ERROR\tstored-csv-size-out-of-range");
    return;
  }
  bool valid = true;
  for (size_t i = 0; i < sizeof(CSV_HEADER) - 1; ++i) {
    const int byte = file.read();
    if (byte < 0 || (uint8_t)byte != (uint8_t)CSV_HEADER[i]) {
      valid = false;
      break;
    }
  }
  uint16_t count = 0;
  size_t line_length = 0;
  while (valid && file.available()) {
    const int byte = file.read();
    if (byte < 0) {
      valid = false;
      break;
    }
    if (byte == '\n') {
      if (line_length == 0 || count >= MAX_APS) {
        valid = false;
        break;
      }
      count++;
      line_length = 0;
    } else {
      // Canonical rows contain printable ASCII only; arbitrary SSID bytes are
      // represented as bounded \\xNN escapes by format_csv_row.
      if (byte < 0x20 || byte > 0x7E || line_length >= 222) {
        valid = false;
        break;
      }
      line_length++;
    }
  }
  file.close();
  if (!valid || line_length != 0) {
    Serial.println("UNATT_ERROR\tstored-csv-invalid-or-unbounded-row");
    return;
  }
  file = LittleFS.open(path, FILE_READ);
  if (!file) {
    Serial.println("UNATT_ERROR\tstored-csv-reopen-failed");
    return;
  }
  Serial.printf("UNATT_DUMP_SOURCE\t%s\tpath=%s\n", label, path);
  Serial.printf("UNATT_CSV_BEGIN\t%u\n", count);
  uint8_t buffer[128];
  int last = '\n';
  while (file.available()) {
    const size_t got = file.read(buffer, sizeof(buffer));
    if (got == 0) break;
    Serial.write(buffer, got);
    last = buffer[got - 1];
  }
  file.close();
  if (last != '\n') Serial.println();
  Serial.println("UNATT_CSV_END");
}

static void dump_database_candidate(const char* path, const char* label) {
  DbCandidate candidate = { path };
  if (!validate_database_candidate(&candidate) || !candidate.valid) {
    Serial.println("UNATT_ERROR\tstored-database-candidate-invalid");
    return;
  }
  File file = LittleFS.open(path, FILE_READ);
  const size_t header_size = candidate.current ? sizeof(DiskHeader)
                                               : sizeof(LegacyDiskHeader);
  if (!file || !file.seek(header_size)) {
    if (file) file.close();
    Serial.println("UNATT_ERROR\tstored-database-candidate-open-failed");
    return;
  }
  const size_t data_size = (size_t)candidate.count * sizeof(Ap);
  bool readable = data_size == 0 ||
                  file.read((uint8_t*)snapshot, data_size) == data_size;
  file.close();
  readable = readable &&
             crc32_bytes((const uint8_t*)snapshot, data_size) ==
                 candidate.rows_crc32;
  for (uint16_t i = 0; readable && i < candidate.count; ++i) {
    char csv_row[224];
    size_t row_length = 0;
    readable = valid_ap_record(snapshot[i]) &&
               format_csv_row(snapshot[i], candidate.capacity_drops, csv_row,
                              sizeof(csv_row), &row_length);
  }
  if (!readable) {
    Serial.println("UNATT_ERROR\tstored-database-candidate-reread-failed");
    return;
  }
  Serial.printf("UNATT_DUMP_SOURCE\t%s\tpath=%s\tgeneration=%llu\tformat=%s\n",
                label, path, (unsigned long long)candidate.generation,
                candidate.current ? "v3" : "legacy-v2");
  Serial.printf("UNATT_CSV_BEGIN\t%u\n", candidate.count);
  Serial.write((const uint8_t*)CSV_HEADER, sizeof(CSV_HEADER) - 1);
  for (uint16_t i = 0; i < candidate.count; ++i) {
    char csv_row[224];
    size_t row_length = 0;
    if (!format_csv_row(snapshot[i], candidate.capacity_drops, csv_row,
                        sizeof(csv_row), &row_length)) {
      Serial.println("UNATT_ERROR\tstored-database-candidate-render-failed");
      return;
    }
    Serial.write((const uint8_t*)csv_row, row_length);
  }
  Serial.println("UNATT_CSV_END");
}

static void dump_guarded_memory_csv() {
  if (!storage_ready || (!dump_memory_is_volatile && !database_loaded)) {
    Serial.println("UNATT_ERROR\tstored-database-unavailable");
    return;
  }
  uint32_t ignored_crc = 0;
  uint32_t ignored_size = 0;
  const uint16_t count = take_snapshot();
  if (!calculate_csv_metadata(snapshot, count, capacity_drops,
                              &ignored_crc, &ignored_size)) {
    Serial.println("UNATT_ERROR\tstored-database-csv-render-failed");
    return;
  }
  if (dump_memory_is_volatile) {
    Serial.printf("UNATT_DUMP_SOURCE\tvolatile-ram-read-only\tcommitted_generation=%llu\trecovery=%s\n",
                  (unsigned long long)persisted_generation, recovery_state);
  } else {
    Serial.printf("UNATT_DUMP_SOURCE\tdatabase-read-only\tgeneration=%llu\trecovery=%s\n",
                  (unsigned long long)persisted_generation, recovery_state);
  }
  Serial.printf("UNATT_CSV_BEGIN\t%u\n", count);
  Serial.write((const uint8_t*)CSV_HEADER, sizeof(CSV_HEADER) - 1);
  for (uint16_t i = 0; i < count; ++i) {
    char row[224];
    size_t row_length = 0;
    if (!format_csv_row(snapshot[i], capacity_drops, row, sizeof(row), &row_length)) {
      Serial.println("UNATT_ERROR\tstored-database-csv-render-failed");
      return;
    }
    Serial.write((const uint8_t*)row, row_length);
  }
  Serial.println("UNATT_CSV_END");
}

static void dump_csv() {
  if (dump_from_memory) {
    dump_guarded_memory_csv();
  } else if (LittleFS.exists(CSV_PATH)) {
    dump_csv_file(CSV_PATH, "primary-csv");
  } else if (LittleFS.exists(CSV_TEMP_PATH)) {
    dump_csv_file(CSV_TEMP_PATH, "temp-csv-read-only-recovery");
  } else {
    dump_csv_file(CSV_BACKUP_PATH, "backup-csv-read-only-recovery");
  }
}

static bool find_ssid_and_channel(const uint8_t* frame, int length,
                                  char* ssid, uint8_t* ssid_len, uint8_t* channel) {
  if (length < 36) return false;
  bool found_ssid = false;
  int offset = 36;  // 24-byte management header + 12-byte beacon fixed fields
  while (offset + 2 <= length) {
    const uint8_t id = frame[offset];
    const uint8_t size = frame[offset + 1];
    offset += 2;
    if (offset + size > length) break;
    if (id == 0 && !found_ssid) {
      *ssid_len = size > 32 ? 32 : size;
      memcpy(ssid, frame + offset, *ssid_len);
      ssid[*ssid_len] = 0;
      found_ssid = true;
    } else if (id == 3 && size >= 1 && frame[offset] >= 1 && frame[offset] <= 14) {
      *channel = frame[offset];
    }
    offset += size;
  }
  return found_ssid;
}

static void on_rx(void* buffer, wifi_promiscuous_pkt_type_t type) {
  // If both receiver-disable paths fail, stop accepting observations even
  // though the SDK can no longer prove whether the radio callback is active.
  if (capture_state_unknown || orphaned_csv_guard) return;
  if (type != WIFI_PKT_MGMT) return;
  const wifi_promiscuous_pkt_t* packet = (const wifi_promiscuous_pkt_t*)buffer;
  const int length = packet->rx_ctrl.sig_len;
  const uint8_t* frame = packet->payload;
  if (length < 36 || (frame[0] & 0xFC) != 0x80) return;  // beacon only

  char ssid[33] = {};
  uint8_t ssid_len = 0;
  uint8_t channel = packet->rx_ctrl.channel;
  find_ssid_and_channel(frame, length, ssid, &ssid_len, &channel);
  const uint8_t* bssid = frame + 16;

  portENTER_CRITICAL(&inventory_mux);
  // A callback may have passed the fast-path guard immediately before recovery
  // began. Re-check under the same lock used to publish the recovery guard so
  // no observation can mutate the frozen inventory after enter_recovery().
  if (orphaned_csv_guard || capture_state_unknown) {
    portEXIT_CRITICAL(&inventory_mux);
    return;
  }
  for (uint16_t i = 0; i < ap_count; ++i) {
    if (memcmp(aps[i].bssid, bssid, 6) == 0) {
      bool structural_change = false;
      aps[i].rssi = packet->rx_ctrl.rssi;
      if (channel >= 1 && channel <= 14 && aps[i].channel != channel) {
        aps[i].channel = channel;
        structural_change = true;
      }
      if (aps[i].ssid_len == 0 && ssid_len > 0) {
        memcpy(aps[i].ssid, ssid, ssid_len + 1);
        aps[i].ssid_len = ssid_len;
        structural_change = true;
      }
      aps[i].seen++;
      stats_dirty = true;
      stats_generation++;
      if (structural_change) {
        inventory_dirty = true;
        inventory_generation++;
      }
      portEXIT_CRITICAL(&inventory_mux);
      return;
    }
  }
  if (ap_count < MAX_APS) {
    Ap* ap = &aps[ap_count++];
    memset(ap, 0, sizeof(*ap));
    memcpy(ap->bssid, bssid, 6);
    memcpy(ap->ssid, ssid, ssid_len + 1);
    ap->ssid_len = ssid_len;
    ap->channel = channel;
    ap->rssi = packet->rx_ctrl.rssi;
    ap->seen = 1;
    inventory_dirty = true;
    stats_dirty = true;
    inventory_generation++;
    stats_generation++;
  } else {
    capacity_drops++;
    stats_dirty = true;
    stats_generation++;
  }
  portEXIT_CRITICAL(&inventory_mux);
}

static void start_capture() {
  if (capture_started || capture_state_unknown || !storage_ready) return;
  if (!WiFi.mode(WIFI_STA)) {
    const esp_err_t disable_err = esp_wifi_set_promiscuous(false);
    const bool wifi_off = WiFi.mode(WIFI_OFF);
    capture_state_unknown = disable_err != ESP_OK && !wifi_off;
    Serial.printf("UNATT_ERROR\twifi-stage=station-mode\tdisable_rc=0x%X\twifi_off=%s\tcapture=%s%s\n",
                  (unsigned int)disable_err, wifi_off ? "ok" : "error",
                  capture_state_unknown ? "unknown" : "stopped",
                  capture_state_unknown ? "\tcallback=quarantined" : "");
    return;
  }
  esp_err_t err = esp_wifi_set_promiscuous_rx_cb(&on_rx);
  if (err == ESP_OK) err = esp_wifi_set_channel(active_channel, WIFI_SECOND_CHAN_NONE);
  if (err == ESP_OK) err = esp_wifi_set_promiscuous(true);
  if (err != ESP_OK) {
    const esp_err_t disable_err = esp_wifi_set_promiscuous(false);
    const bool wifi_off = WiFi.mode(WIFI_OFF);
    capture_state_unknown = disable_err != ESP_OK && !wifi_off;
    Serial.printf("UNATT_ERROR\twifi-stage=capture-init\trc=0x%X\tdisable_rc=0x%X\twifi_off=%s\tcapture=%s%s\n",
                  (unsigned int)err, (unsigned int)disable_err,
                  wifi_off ? "ok" : "error",
                  capture_state_unknown ? "unknown" : "stopped",
                  capture_state_unknown ? "\tcallback=quarantined" : "");
    return;
  }
  capture_started = true;
  capture_state_unknown = false;
  Serial.printf("UNATT_READY\taps=%u\tchannels=1-13\tstorage=%s\n", ap_count, CSV_PATH);
}

static void clear_inventory(bool recovery_override = false) {
  if (!storage_ready) {
    Serial.println("UNATT_CLEAR\terror=storage-unavailable");
    return;
  }
  if (recovery_requires_review && !recovery_override) {
    Serial.println("UNATT_CLEAR\tblocked=recovered-db-csv-divergence\taction=DUMP-and-maintenance-review; use CLEAR RECOVERY CONFIRM only after preserving both artifacts");
    return;
  }
  const char* paths[] = { DB_PATH, DB_BACKUP_PATH, DB_TEMP_PATH,
                          CSV_PATH, CSV_BACKUP_PATH, CSV_TEMP_PATH };
  bool removed = true;
  for (size_t i = 0; i < sizeof(paths) / sizeof(paths[0]); ++i)
    if (LittleFS.exists(paths[i]) && !LittleFS.remove(paths[i])) removed = false;
  if (!removed) {
    Serial.println("UNATT_CLEAR\terror=remove-failed; inventory retained in memory");
    inventory_dirty = true;
    return;
  }
  portENTER_CRITICAL(&inventory_mux);
  memset(aps, 0, sizeof(aps));
  ap_count = 0;
  inventory_dirty = false;
  stats_dirty = false;
  inventory_generation++;
  stats_generation++;
  capacity_drops = 0;
  portEXIT_CRITICAL(&inventory_mux);
  const bool resume_after_clear = orphaned_csv_guard;
  orphaned_csv_guard = false;
  recovery_requires_review = false;
  recovery_state = "ok";
  database_loaded = false;
  dump_from_memory = false;
  dump_memory_is_volatile = false;
  persisted_generation = 0;
  Serial.println("UNATT_CLEAR\tok");
  if (resume_after_clear) start_capture();
}

static void print_status() {
  Serial.printf("UNATT_STATUS\tch=%u\taps=%u\tdirty=%u\tstats_dirty=%u\tcapacity_drops=%lu\tstorage=%s\tcapture=%s\trecovery=%s\tgeneration=%llu\n",
                active_channel, ap_count, inventory_dirty ? 1 : 0, stats_dirty ? 1 : 0,
                (unsigned long)capacity_drops,
                storage_ready ? "ready" : "unavailable",
                capture_state_unknown ? "unknown" : (capture_started ? "running" : "stopped"),
                !storage_ready ? "storage-unavailable" : recovery_state,
                (unsigned long long)persisted_generation);
}

static void handle_serial() {
  static char command[48];
  static uint8_t used = 0;
  static bool overflow = false;
  while (Serial.available() > 0) {
    const char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c != '\n') {
      if (overflow) continue;
      if (used < sizeof(command) - 1) {
        command[used++] = c;
      } else {
        // Never process a prefix of an oversized command. Discard the whole
        // line so a trailing confirmation token cannot become a new command.
        overflow = true;
      }
      continue;
    }
    if (overflow) {
      overflow = false;
      used = 0;
      Serial.println("UNATT_ERROR\tcommand-too-long");
      continue;
    }
    command[used] = 0;
    if (strcmp(command, "STATUS") == 0) {
      print_status();
    } else if (strcmp(command, "INIT CONFIRM") == 0) {
      if (storage_ready) {
        if (orphaned_csv_guard) {
          Serial.printf("UNATT_INIT\tblocked=%s\taction=%s\n", recovery_state,
                        recovery_requires_review ? "DUMP-and-maintenance-review" : "DUMP-then-CLEAR-CONFIRM");
        } else {
          Serial.println("UNATT_INIT\talready-mounted");
        }
      } else {
        Serial.println("UNATT_INIT\tformatting explicitly confirmed partition");
        storage_ready = LittleFS.format() && LittleFS.begin(false);
        if (storage_ready) orphaned_csv_guard = false;
        Serial.printf("UNATT_INIT\t%s\n", storage_ready ? "ok" : "error");
        if (storage_ready) start_capture();
      }
    } else if (strcmp(command, "FLUSH") == 0) {
      persist_snapshot(true);
    } else if (strcmp(command, "DUMP") == 0) {
      dump_csv();
    } else if (strcmp(command, "DUMP STORED") == 0) {
      dump_csv_file(CSV_PATH, "primary-csv");
    } else if (strcmp(command, "DUMP CSV TEMP") == 0) {
      dump_csv_file(CSV_TEMP_PATH, "temp-csv");
    } else if (strcmp(command, "DUMP CSV BACKUP") == 0) {
      dump_csv_file(CSV_BACKUP_PATH, "backup-csv");
    } else if (strcmp(command, "DUMP DB PRIMARY") == 0) {
      dump_database_candidate(DB_PATH, "primary-db-read-only");
    } else if (strcmp(command, "DUMP DB TEMP") == 0) {
      dump_database_candidate(DB_TEMP_PATH, "temp-db-read-only");
    } else if (strcmp(command, "DUMP DB BACKUP") == 0) {
      dump_database_candidate(DB_BACKUP_PATH, "backup-db-read-only");
    } else if (strcmp(command, "CLEAR CONFIRM") == 0) {
      clear_inventory();
    } else if (strcmp(command, "CLEAR RECOVERY CONFIRM") == 0) {
      clear_inventory(true);
    } else if (used > 0) {
      Serial.println("UNATT_ERROR\tunknown command; use STATUS, FLUSH, DUMP, read-only DUMP DB/CSV variants, or an explicit CONFIRM command");
    }
    used = 0;
  }
}

static int choose_database_candidate(DbCandidate* candidates, size_t count,
                                     bool* ambiguous) {
  *ambiguous = false;
  int selected = -1;
  uint64_t highest_generation = 0;
  for (size_t i = 0; i < count; ++i) {
    if (!candidates[i].valid || !candidates[i].current) continue;
    if (selected < 0 || candidates[i].generation > highest_generation) {
      selected = (int)i;
      highest_generation = candidates[i].generation;
    }
  }
  for (size_t i = 0; selected >= 0 && i < count; ++i) {
    if ((int)i != selected && candidates[i].valid && candidates[i].current &&
        candidates[i].generation == highest_generation &&
        !same_current_snapshot(candidates[i], candidates[selected]))
      *ambiguous = true;
  }

  if (selected >= 0) {
    // An unsequenced legacy snapshot cannot be assumed older than v3.  It is
    // harmless only when it is provably the same state as the selected v3.
    for (size_t i = 0; i < count; ++i) {
      if (candidates[i].valid && !candidates[i].current &&
          !same_snapshot_state(candidates[i], candidates[selected]))
        *ambiguous = true;
      if (candidates[i].unsupported_format) *ambiguous = true;
    }
    return selected;
  }

  // Legacy v2 has no monotonic sequence.  It is selectable only when every
  // valid copy is byte-state-equivalent; filename priority is never used to
  // resolve divergent snapshots.
  for (size_t i = 0; i < count; ++i) {
    if (!candidates[i].valid || candidates[i].current) continue;
    if (selected < 0) selected = (int)i;
    else if (!same_snapshot_state(candidates[i], candidates[selected]))
      *ambiguous = true;
  }
  for (size_t i = 0; i < count; ++i)
    if (candidates[i].unsupported_format) *ambiguous = true;
  return selected;
}

static bool csv_matches_any_database(const char* path,
                                     const DbCandidate* candidates,
                                     size_t count) {
  if (!LittleFS.exists(path)) return false;
  for (size_t i = 0; i < count; ++i) {
    if (candidates[i].valid &&
        csv_matches_metadata(path, candidates[i].csv_crc32,
                             candidates[i].csv_size))
      return true;
  }
  return false;
}

static bool promote_temp_database(const DbCandidate& selected) {
  if (strcmp(selected.path, DB_TEMP_PATH) != 0 || !selected.valid) return false;
  if (LittleFS.exists(DB_PATH)) {
    if (LittleFS.exists(DB_BACKUP_PATH) && !LittleFS.remove(DB_BACKUP_PATH))
      return false;
    if (!LittleFS.rename(DB_PATH, DB_BACKUP_PATH)) return false;
  }
  if (!LittleFS.rename(DB_TEMP_PATH, DB_PATH)) {
    if (!LittleFS.exists(DB_PATH) && LittleFS.exists(DB_BACKUP_PATH))
      LittleFS.rename(DB_BACKUP_PATH, DB_PATH);
    return false;
  }
  DbCandidate installed = { DB_PATH };
  if (!validate_database_candidate(&installed) || !installed.valid) return false;
  if (selected.current)
    return installed.current && installed.generation == selected.generation &&
           installed.snapshot_crc32 == selected.snapshot_crc32 &&
           installed.count == selected.count &&
           installed.capacity_drops == selected.capacity_drops &&
           installed.rows_crc32 == selected.rows_crc32 &&
           installed.csv_crc32 == selected.csv_crc32 &&
           installed.csv_size == selected.csv_size;
  return !installed.current && installed.count == selected.count &&
         installed.capacity_drops == selected.capacity_drops &&
         installed.rows_crc32 == selected.rows_crc32;
}

static bool normalize_database(const DbCandidate& selected,
                               uint32_t csv_crc32, uint32_t csv_size) {
  const uint16_t count = take_snapshot();
  if (selected.current) {
    persisted_generation = selected.generation;
    if (strcmp(selected.path, DB_PATH) == 0) return true;
    if (strcmp(selected.path, DB_TEMP_PATH) == 0) {
      Serial.printf("UNATT_RECOVERY\taction=promote-db-temp\tgeneration=%llu\n",
                    (unsigned long long)selected.generation);
      return promote_temp_database(selected);
    }
    Serial.printf("UNATT_RECOVERY\taction=restore-db-backup\tgeneration=%llu\n",
                  (unsigned long long)selected.generation);
    return write_database(snapshot, count, capacity_drops, selected.generation,
                          csv_crc32, csv_size);
  }

  // A sole/equivalent legacy temp is first promoted without rewriting it, so
  // a power cut during v3 migration cannot destroy the only valid v2 source.
  if (strcmp(selected.path, DB_TEMP_PATH) == 0) {
    Serial.println("UNATT_RECOVERY\taction=promote-legacy-temp");
    if (!promote_temp_database(selected)) return false;
  }
  Serial.println("UNATT_RECOVERY\taction=migrate-legacy-v2-to-v3\tgeneration=1");
  if (!write_database(snapshot, count, capacity_drops, 1, csv_crc32, csv_size))
    return false;
  persisted_generation = 1;
  return true;
}

static bool normalize_csv(uint32_t csv_crc32, uint32_t csv_size) {
  if (csv_matches_metadata(CSV_PATH, csv_crc32, csv_size)) {
    if (LittleFS.exists(CSV_TEMP_PATH)) {
      Serial.println("UNATT_RECOVERY\taction=remove-proven-stale-csv-temp");
      if (!LittleFS.remove(CSV_TEMP_PATH)) return false;
    }
    if (LittleFS.exists(CSV_BACKUP_PATH)) {
      Serial.println("UNATT_RECOVERY\taction=remove-proven-stale-csv-backup");
      if (!LittleFS.remove(CSV_BACKUP_PATH)) return false;
    }
    return true;
  }
  const uint16_t count = take_snapshot();
  Serial.printf("UNATT_RECOVERY\taction=install-canonical-csv\tgeneration=%llu\n",
                (unsigned long long)persisted_generation);
  return write_csv(snapshot, count, capacity_drops, csv_crc32, csv_size);
}

void setup() {
  Serial.begin(115200);
  delay(600);
  Serial.println("\n=== medusa_unattended ===");
  Serial.println("PASSIVE local inventory. LAWFUL USE ONLY - owned/authorized networks.");

  storage_ready = LittleFS.begin(false);  // fail closed; never format captures on error
  if (!storage_ready) {
    Serial.println("UNATT_ERROR\tLittleFS mount failed; capture disabled to protect stored data");
    return;
  }
  DbCandidate candidates[] = {
      { DB_PATH }, { DB_TEMP_PATH }, { DB_BACKUP_PATH }
  };
  const size_t candidate_count = sizeof(candidates) / sizeof(candidates[0]);
  bool any_database_artifact = false;
  for (size_t i = 0; i < candidate_count; ++i) {
    validate_database_candidate(&candidates[i]);
    any_database_artifact = any_database_artifact || candidates[i].exists;
    if (candidates[i].exists) {
      Serial.printf("UNATT_DB_CANDIDATE\tpath=%s\tvalid=%s\tformat=%s\tgeneration=%llu\n",
                    candidates[i].path, candidates[i].valid ? "yes" : "no",
                    candidates[i].current ? "v3" :
                    (candidates[i].detected_version == 2 ? "legacy-v2" : "unsupported-or-corrupt"),
                    (unsigned long long)candidates[i].generation);
    }
  }

  bool ambiguous = false;
  const int selected_index = choose_database_candidate(
      candidates, candidate_count, &ambiguous);
  if (ambiguous) {
    enter_recovery("ambiguous-db-candidates", true, false);
    Serial.println("UNATT_ERROR\trecovery=blocked\treason=ambiguous-db-candidates\taction=DUMP-each-candidate-and-maintenance-review");
    return;
  }

  const char* csv_paths[] = { CSV_PATH, CSV_TEMP_PATH, CSV_BACKUP_PATH };
  const size_t csv_path_count = sizeof(csv_paths) / sizeof(csv_paths[0]);
  bool any_csv_artifact = false;
  for (size_t i = 0; i < csv_path_count; ++i)
    any_csv_artifact = any_csv_artifact || LittleFS.exists(csv_paths[i]);

  if (selected_index < 0) {
    if (any_database_artifact) {
      enter_recovery("unreadable-db-artifacts", true, false);
      Serial.println("UNATT_ERROR\trecovery=blocked\treason=unreadable-db-artifacts\taction=DUMP-available-CSV-and-maintenance-review");
    } else if (any_csv_artifact) {
      enter_recovery("orphaned-csv", false, false);
      Serial.println("UNATT_ERROR\trecovery=blocked\treason=orphaned-csv\taction=DUMP-then-CLEAR-CONFIRM");
    } else {
      start_capture();
    }
    return;
  }

  const DbCandidate selected = candidates[selected_index];
  if (!load_database_candidate(selected)) {
    enter_recovery("selected-db-reread-failed", true, false);
    Serial.println("UNATT_ERROR\trecovery=blocked\treason=selected-db-reread-failed\taction=maintenance-review");
    return;
  }

  uint32_t canonical_csv_crc = 0;
  uint32_t canonical_csv_size = 0;
  const uint16_t restored_count = take_snapshot();
  if (!calculate_csv_metadata(snapshot, restored_count, capacity_drops,
                              &canonical_csv_crc, &canonical_csv_size) ||
      canonical_csv_crc != selected.csv_crc32 ||
      canonical_csv_size != selected.csv_size) {
    enter_recovery("selected-db-metadata-mismatch", true, true);
    Serial.println("UNATT_ERROR\trecovery=blocked\treason=selected-db-metadata-mismatch\taction=DUMP-and-maintenance-review");
    return;
  }

  bool unpaired_csv = false;
  bool selected_csv_match = false;
  for (size_t i = 0; i < csv_path_count; ++i) {
    if (!LittleFS.exists(csv_paths[i])) continue;
    if (csv_matches_metadata(csv_paths[i], selected.csv_crc32,
                             selected.csv_size))
      selected_csv_match = true;
    if (!csv_matches_any_database(csv_paths[i], candidates, candidate_count)) {
      unpaired_csv = true;
      Serial.printf("UNATT_CSV_CANDIDATE\tpath=%s\tpaired=no\taction=preserve\n",
                    csv_paths[i]);
    }
  }
  if (unpaired_csv) {
    enter_recovery("unpaired-csv-artifact", true, true);
    Serial.println("UNATT_ERROR\trecovery=blocked\treason=unpaired-csv-artifact\taction=DUMP-and-preserve-each-CSV-for-maintenance-review");
    return;
  }
  if (!selected.current && !selected_csv_match &&
      (selected.count > 0 || selected.capacity_drops > 0)) {
    // Legacy capacity/count metadata was outside its row CRC.  A matching
    // canonical CSV is the only independent on-device corroboration, so do
    // not bless a lone data-bearing v2 file as v3 merely because it parses.
    enter_recovery("legacy-v2-metadata-unverified", true, true);
    Serial.println("UNATT_ERROR\trecovery=blocked\treason=legacy-v2-metadata-unverified\taction=DUMP-and-maintenance-review");
    return;
  }

  // Only after every DB and CSV artifact has validated (or been proven an
  // older paired snapshot) may boot finish an interrupted rotation/migration.
  if (!normalize_database(selected, canonical_csv_crc, canonical_csv_size)) {
    enter_recovery("database-promotion-failed", true, true);
    Serial.println("UNATT_ERROR\trecovery=blocked\treason=database-promotion-failed\taction=DUMP-and-maintenance-review");
    return;
  }
  if (!normalize_csv(canonical_csv_crc, canonical_csv_size)) {
    enter_recovery("csv-promotion-failed", true, true);
    Serial.println("UNATT_ERROR\trecovery=blocked\treason=csv-promotion-failed\taction=DUMP-and-maintenance-review");
    return;
  }
  recovery_state = selected.current ? "ok" : "legacy-v2-migrated";
  dump_from_memory = false;
  dump_memory_is_volatile = false;
  start_capture();
}

void loop() {
  if (!storage_ready) {
    handle_serial();
    delay(250);
    return;
  }

  static const uint8_t channels[] = { 1, 6, 11, 2, 7, 12, 3, 8, 13, 4, 9, 5, 10 };
  static uint8_t channel_index = 0;
  static uint32_t last_hop = 0;
  static uint32_t last_flush = 0;
  static uint32_t last_checkpoint = 0;
  static uint32_t last_status = 0;
  const uint32_t now = millis();

  handle_serial();
  if (capture_started && !capture_state_unknown && now - last_hop >= 400) {
    last_hop = now;
    channel_index = (channel_index + 1) % (sizeof(channels) / sizeof(channels[0]));
    const uint8_t next_channel = channels[channel_index];
    const esp_err_t err = esp_wifi_set_channel(next_channel, WIFI_SECOND_CHAN_NONE);
    if (err == ESP_OK) {
      active_channel = next_channel;
    } else {
      const esp_err_t disable_err = esp_wifi_set_promiscuous(false);
      if (disable_err == ESP_OK) {
        capture_started = false;
        Serial.printf("UNATT_ERROR\twifi-stage=channel-hop\trequested=%u\trc=0x%X\tdisable_rc=0x%X\tcapture=stopped\n",
                      next_channel, (unsigned int)err, (unsigned int)disable_err);
      } else {
        // Stopping the Wi-Fi driver is the fallback proof that the receiver is
        // no longer active. If that also fails, keep capture_started truthful,
        // quarantine the callback, and expose the state as unknown.
        const esp_err_t stop_err = esp_wifi_stop();
        if (stop_err == ESP_OK) {
          capture_started = false;
          Serial.printf("UNATT_ERROR\twifi-stage=channel-hop\trequested=%u\trc=0x%X\tdisable_rc=0x%X\twifi_stop_rc=0x%X\tcapture=stopped\n",
                        next_channel, (unsigned int)err, (unsigned int)disable_err,
                        (unsigned int)stop_err);
        } else {
          capture_state_unknown = true;
          Serial.printf("UNATT_ERROR\twifi-stage=channel-hop\trequested=%u\trc=0x%X\tdisable_rc=0x%X\twifi_stop_rc=0x%X\tcapture=unknown\tcallback=quarantined\n",
                        next_channel, (unsigned int)err, (unsigned int)disable_err,
                        (unsigned int)stop_err);
        }
      }
    }
  }
  if (inventory_dirty && now - last_flush >= FLUSH_INTERVAL_MS) {
    last_flush = now;
    persist_snapshot(false);
    last_checkpoint = now;
  } else if (stats_dirty && now - last_checkpoint >= CHECKPOINT_INTERVAL_MS) {
    last_checkpoint = now;
    persist_snapshot(false);
  }
  if (now - last_status >= 5000) {
    last_status = now;
    print_status();
  }
  delay(10);
}
