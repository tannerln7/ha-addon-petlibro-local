/*
 * plaf203-state-agent
 * Read-only local state API for Petlibro PLAF203-style feeder firmware.
 *
 * Build target: static Linux ARM/musl preferred.
 * This program intentionally reads only allowlisted files under /user/data.
 * It never writes feeder state files and never executes shell commands.
 */
#define _GNU_SOURCE

#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#ifndef PATH_MAX
#define PATH_MAX 4096
#endif

#define AGENT_NAME "plaf203-state-agent"
#define AGENT_VERSION "0.2.0"
#define MAX_FILE_BYTES 8192
#define MAX_REQUEST 4096
#define MAX_TOKEN 256
#define STATE_BIN_SIZE 236
#define PLAN_RECORD_SIZE 47
#define FEED_RECORD_SIZE 31
#define FEED_PHASE_COUNT 3
#define FEED_SLOT_SIZE (FEED_RECORD_SIZE * FEED_PHASE_COUNT)
#define FEED_SLOT_COUNT 51
#define FEED_FILE_SIZE (FEED_SLOT_SIZE * FEED_SLOT_COUNT)

static volatile sig_atomic_t g_stop = 0;
static uint64_t g_start_ms = 0;

typedef struct {
    char *buf;
    size_t len;
    size_t cap;
} Str;

typedef struct {
    char root[PATH_MAX];
    char listen_ip[64];
    int listen_port;
    char token[MAX_TOKEN];
    bool require_token;
    char allow_ip[64];
    int poll_feed_ms;
    char pid_file[PATH_MAX];
    char log_file[PATH_MAX];
} Config;

typedef struct {
    uint8_t *data;
    size_t len;
    bool ok;
    char error[128];
} Bytes;

static Config g_cfg;

static uint64_t now_ms(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (uint64_t)tv.tv_sec * 1000ULL + (uint64_t)(tv.tv_usec / 1000);
}

static void on_signal(int sig) {
    (void)sig;
    g_stop = 1;
}

static void log_msg(const char *fmt, ...) {
    if (!g_cfg.log_file[0]) return;
    FILE *f = fopen(g_cfg.log_file, "a");
    if (!f) return;
    time_t t = time(NULL);
    struct tm tmv;
    gmtime_r(&t, &tmv);
    char ts[64];
    strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%SZ", &tmv);
    fprintf(f, "%s ", ts);
    va_list ap;
    va_start(ap, fmt);
    vfprintf(f, fmt, ap);
    va_end(ap);
    fprintf(f, "\n");
    fclose(f);
}

static void str_init(Str *s) {
    s->cap = 4096;
    s->len = 0;
    s->buf = (char *)malloc(s->cap);
    if (!s->buf) exit(2);
    s->buf[0] = '\0';
}

static void str_free(Str *s) {
    free(s->buf);
    s->buf = NULL;
    s->len = s->cap = 0;
}

static void str_reserve(Str *s, size_t add) {
    size_t need = s->len + add + 1;
    if (need <= s->cap) return;
    while (s->cap < need) s->cap *= 2;
    char *nb = (char *)realloc(s->buf, s->cap);
    if (!nb) exit(2);
    s->buf = nb;
}

static void str_append(Str *s, const char *text) {
    size_t n = strlen(text);
    str_reserve(s, n);
    memcpy(s->buf + s->len, text, n + 1);
    s->len += n;
}

static void str_appendf(Str *s, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    va_list ap2;
    va_copy(ap2, ap);
    int n = vsnprintf(NULL, 0, fmt, ap);
    va_end(ap);
    if (n < 0) {
        va_end(ap2);
        return;
    }
    str_reserve(s, (size_t)n);
    vsnprintf(s->buf + s->len, s->cap - s->len, fmt, ap2);
    va_end(ap2);
    s->len += (size_t)n;
}

static void json_string(Str *s, const char *v) {
    str_append(s, "\"");
    for (const unsigned char *p = (const unsigned char *)v; *p; p++) {
        unsigned char c = *p;
        switch (c) {
            case '\\': str_append(s, "\\\\"); break;
            case '"': str_append(s, "\\\""); break;
            case '\b': str_append(s, "\\b"); break;
            case '\f': str_append(s, "\\f"); break;
            case '\n': str_append(s, "\\n"); break;
            case '\r': str_append(s, "\\r"); break;
            case '\t': str_append(s, "\\t"); break;
            default:
                if (c < 0x20) str_appendf(s, "\\u%04x", c);
                else {
                    char tmp[2] = {(char)c, 0};
                    str_append(s, tmp);
                }
        }
    }
    str_append(s, "\"");
}

static const char *map_bool01(uint8_t v) {
    if (v == 0) return "disabled";
    if (v == 1) return "enabled";
    return "unknown";
}

static const char *map_always_scheduled(uint32_t v) {
    if (v == 1) return "always_active";
    if (v == 2) return "scheduled";
    return "unknown";
}

static const char *map_bowl_mode(uint8_t v) {
    if (v == 0) return "single_bowl";
    if (v == 1) return "dual_bowl";
    return "unknown";
}

static const char *map_feeding_audio_type(uint8_t v) {
    if (v == 0) return "disabled";
    if (v == 1) return "builtin";
    if (v == 2) return "custom";
    return "unknown";
}

static const char *map_resolution(uint32_t v) {
    if (v == 1) return "1080p";
    if (v == 2) return "720p";
    return "unknown";
}

static const char *map_night_vision(uint32_t v) {
    if (v == 1) return "auto";
    if (v == 2) return "on";
    if (v == 3) return "off";
    return "unknown";
}

static const char *map_recording_type(uint32_t v) {
    if (v == 1) return "continuous";
    if (v == 2) return "motion_detection";
    return "unknown";
}

static const char *map_motion_sensitivity(uint32_t v) {
    if (v == 5) return "high";
    if (v == 7) return "medium";
    if (v == 10) return "low";
    return "unknown";
}

static const char *map_motion_range(uint32_t v) {
    if (v == 1) return "large";
    if (v == 2) return "medium";
    if (v == 3) return "small";
    return "unknown";
}

static const char *map_sound_sensitivity(uint32_t v) {
    if (v == 70) return "high";
    if (v == 80) return "medium";
    if (v == 90) return "low";
    return "unknown";
}

static uint8_t byte_at(const Bytes *b, size_t off) {
    if (!b || !b->ok || off >= b->len) return 0;
    return b->data[off];
}

static uint16_t u16le_at(const Bytes *b, size_t off) {
    if (!b || !b->ok || off + 2 > b->len) return 0;
    return (uint16_t)b->data[off] | ((uint16_t)b->data[off + 1] << 8);
}

static uint32_t u32le_at(const Bytes *b, size_t off) {
    if (!b || !b->ok || off + 4 > b->len) return 0;
    return (uint32_t)b->data[off]
        | ((uint32_t)b->data[off + 1] << 8)
        | ((uint32_t)b->data[off + 2] << 16)
        | ((uint32_t)b->data[off + 3] << 24);
}

static uint64_t u64le_ptr(const uint8_t *p) {
    uint64_t value = 0;
    for (unsigned i = 0; i < 8; i++) value |= ((uint64_t)p[i]) << (8U * i);
    return value;
}

static void append_hex_string(Str *s, const uint8_t *data, size_t len) {
    str_append(s, "\"");
    static const char *hex = "0123456789abcdef";
    for (size_t i = 0; i < len; i++) {
        char tmp[3] = {hex[(data[i] >> 4) & 0xf], hex[data[i] & 0xf], 0};
        str_append(s, tmp);
        if (i + 1 < len) str_append(s, " ");
    }
    str_append(s, "\"");
}

static void bytes_free(Bytes *b) {
    if (!b) return;
    free(b->data);
    b->data = NULL;
    b->len = 0;
    b->ok = false;
}

static bool safe_join_path(char *out, size_t out_len, const char *root, const char *rel) {
    if (!rel || rel[0] == '/' || strstr(rel, "..")) return false;
    int n = snprintf(out, out_len, "%s/%s", root, rel);
    return n > 0 && (size_t)n < out_len;
}

static Bytes read_file_rel(const char *rel, size_t max_bytes) {
    Bytes b = {0};
    char path[PATH_MAX];
    if (!safe_join_path(path, sizeof(path), g_cfg.root, rel)) {
        snprintf(b.error, sizeof(b.error), "bad path");
        return b;
    }
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        snprintf(b.error, sizeof(b.error), "open failed: %s", strerror(errno));
        return b;
    }
    b.data = (uint8_t *)malloc(max_bytes + 1);
    if (!b.data) {
        close(fd);
        snprintf(b.error, sizeof(b.error), "malloc failed");
        return b;
    }
    size_t used = 0;
    while (used < max_bytes + 1) {
        ssize_t n = read(fd, b.data + used, max_bytes + 1 - used);
        if (n > 0) {
            used += (size_t)n;
            continue;
        }
        if (n == 0) break;
        if (errno == EINTR) continue;
        snprintf(b.error, sizeof(b.error), "read failed: %s", strerror(errno));
        free(b.data);
        b.data = NULL;
        close(fd);
        return b;
    }
    close(fd);
    if (used > max_bytes) {
        snprintf(b.error, sizeof(b.error), "file too large");
        free(b.data);
        b.data = NULL;
        return b;
    }
    b.len = used;
    b.ok = true;
    return b;
}

static uint64_t fnv64_init(void) { return 14695981039346656037ULL; }
static uint64_t fnv64_update(uint64_t h, const void *data, size_t len) {
    const uint8_t *p = (const uint8_t *)data;
    for (size_t i = 0; i < len; i++) {
        h ^= (uint64_t)p[i];
        h *= 1099511628211ULL;
    }
    return h;
}
static uint64_t fnv64_u8(uint64_t h, uint8_t v) { return fnv64_update(h, &v, 1); }
static uint64_t fnv64_u64(uint64_t h, uint64_t v) { return fnv64_update(h, &v, sizeof(v)); }

static void append_rev(Str *s, const char *key, uint64_t rev, bool comma) {
    str_appendf(s, "\"%s\":\"fnv64:%016llx\"%s", key, (unsigned long long)rev, comma ? "," : "");
}

static uint64_t settings_rev_from_state(const Bytes *state) {
    struct Range { size_t off; size_t len; } ranges[] = {
        {0x09, 1}, {0x10, 4},
        {0x15, 8}, {0x21, 8}, {0x2c, 100},
        {0x91, 17}, {0xa3, 20}, {0xb8, 17}, {0xca, 13}, {0xe7, 1},
    };
    uint64_t h = fnv64_init();
    if (!state || !state->ok || state->len != STATE_BIN_SIZE) return h;
    for (size_t i = 0; i < sizeof(ranges)/sizeof(ranges[0]); i++) {
        h = fnv64_u8(h, (uint8_t)ranges[i].off);
        h = fnv64_u8(h, (uint8_t)ranges[i].len);
        h = fnv64_update(h, state->data + ranges[i].off, ranges[i].len);
    }
    return h;
}

static uint64_t plans_rev_from_files(const Bytes *idx, const Bytes *plan) {
    uint64_t h = fnv64_init();
    if (!idx || !idx->ok || idx->len < 1 || !plan || !plan->ok) return h;
    uint8_t count = idx->data[0];
    h = fnv64_u8(h, count);
    if (plan->len != (size_t)count * PLAN_RECORD_SIZE) {
        h = fnv64_update(h, plan->data, plan->len);
        return h;
    }
    for (uint8_t i = 0; i < count; i++) {
        const uint8_t *rec = plan->data + (size_t)i * PLAN_RECORD_SIZE;
        /* Exclude execution_state (0x11..0x14) and sync_time (0x15..0x1c).
         * Both are firmware/protocol metadata, not schedule equality. */
        h = fnv64_update(h, rec, 0x11);
        h = fnv64_update(h, rec + 0x1d, PLAN_RECORD_SIZE - 0x1d);
    }
    return h;
}

static uint64_t queue_rev_from_files(const Bytes *head, const Bytes *tail) {
    uint64_t h = fnv64_init();
    h = fnv64_u8(h, byte_at(head, 0));
    h = fnv64_u8(h, byte_at(tail, 0));
    return h;
}

static uint64_t core_rev(uint64_t settings_rev, uint64_t plans_rev, uint64_t queue_rev) {
    uint64_t h = fnv64_init();
    h = fnv64_u64(h, settings_rev);
    h = fnv64_u64(h, plans_rev);
    h = fnv64_u64(h, queue_rev);
    return h;
}

static void append_raw_file(Str *s, const char *name, const Bytes *b, bool comma) {
    json_string(s, name);
    str_append(s, ":{");
    str_appendf(s, "\"ok\":%s,", b->ok ? "true" : "false");
    str_appendf(s, "\"size\":%zu", b->ok ? b->len : 0);
    if (b->ok) {
        str_append(s, ",\"hex\":");
        append_hex_string(s, b->data, b->len);
    } else {
        str_append(s, ",\"error\":");
        json_string(s, b->error);
    }
    str_append(s, comma ? "}," : "}");
}

static void append_setting_str(Str *s, const char *key, const char *value, bool comma) {
    json_string(s, key);
    str_append(s, ":");
    json_string(s, value);
    if (comma) str_append(s, ",");
}

static void append_setting_int(Str *s, const char *key, int value, bool comma) {
    json_string(s, key);
    str_appendf(s, ":%d%s", value, comma ? "," : "");
}

static void append_setting_u32(Str *s, const char *key, uint32_t value, bool comma) {
    json_string(s, key);
    str_appendf(s, ":%u%s", (unsigned)value, comma ? "," : "");
}

static void append_setting_bool(Str *s, const char *key, bool value, bool comma) {
    json_string(s, key);
    str_appendf(s, ":%s%s", value ? "true" : "false", comma ? "," : "");
}

static void append_bounded_string(Str *s, const Bytes *state, size_t off, size_t width) {
    char value[101];
    size_t len = 0;
    if (width >= sizeof(value)) width = sizeof(value) - 1;
    while (state && state->ok && off + len < state->len && len < width && state->data[off + len] != 0) {
        value[len] = (char)state->data[off + len];
        len++;
    }
    value[len] = '\0';
    json_string(s, value);
}

static void append_settings_summary(Str *s, const Bytes *state) {
    str_append(s, "{");
    append_setting_int(s, "motor_dir_raw", byte_at(state,0x08), true);
    append_setting_str(s, "bowl_mode", map_bowl_mode(byte_at(state,0x09)), true);
    append_setting_int(s, "power_type_raw", byte_at(state,0x0a), true);
    append_setting_int(s, "power_mode_raw", byte_at(state,0x0b), true);
    append_setting_int(s, "electric_quantity", byte_at(state,0x0c), true);
    append_setting_bool(s, "surplus_grain", byte_at(state,0x0d) != 0, true);
    append_setting_int(s, "motor_state_raw", byte_at(state,0x0e), true);
    append_setting_bool(s, "feeding_active", byte_at(state,0x0e) == 1 || byte_at(state,0x0e) == 3, true);
    append_setting_bool(s, "motor_monitor_flag", byte_at(state,0x0e) == 0 || byte_at(state,0x0e) == 3, true);
    append_setting_bool(s, "grain_outlet_state", byte_at(state,0x0f) != 0, true);
    append_setting_int(s, "volume", byte_at(state,0x10), true);
    append_setting_int(s, "auto_change_mode", byte_at(state,0x11), true);
    append_setting_int(s, "auto_threshold", byte_at(state,0x12), true);
    append_setting_str(s, "feeding_audio_type", map_feeding_audio_type(byte_at(state,0x13)), true);
    append_setting_str(s, "feeding_audio_enabled", byte_at(state,0x13) == 0 ? "disabled" : "enabled", true);
    append_setting_str(s, "light_effective_cached", map_bool01(byte_at(state,0x14)), true);
    append_setting_str(s, "light_switch", map_bool01(byte_at(state,0x15)), true);
    append_setting_str(s, "button_lights_mode", map_always_scheduled(byte_at(state,0x16)), true);
    append_setting_int(s, "light_start_hour_utc", byte_at(state,0x17), true);
    append_setting_int(s, "light_start_minute_utc", byte_at(state,0x18), true);
    append_setting_int(s, "light_end_hour_utc", byte_at(state,0x19), true);
    append_setting_int(s, "light_end_minute_utc", byte_at(state,0x1a), true);
    append_setting_int(s, "lighting_times_raw", u16le_at(state,0x1b), true);
    append_setting_str(s, "sound_effective_cached", map_bool01(byte_at(state,0x20)), true);
    append_setting_str(s, "sound_switch", map_bool01(byte_at(state,0x21)), true);
    append_setting_str(s, "sound_mode", map_always_scheduled(byte_at(state,0x22)), true);
    append_setting_int(s, "sound_start_hour_utc", byte_at(state,0x23), true);
    append_setting_int(s, "sound_start_minute_utc", byte_at(state,0x24), true);
    append_setting_int(s, "sound_end_hour_utc", byte_at(state,0x25), true);
    append_setting_int(s, "sound_end_minute_utc", byte_at(state,0x26), true);
    append_setting_int(s, "sound_times_raw", u16le_at(state,0x27), true);
    json_string(s, "audio_url"); str_append(s, ":"); append_bounded_string(s, state, 0x2c, 100); str_append(s, ",");
    append_setting_str(s, "camera_effective_cached", map_bool01(byte_at(state,0x90)), true);
    append_setting_str(s, "camera_switch", map_bool01(byte_at(state,0x91)), true);
    append_setting_str(s, "camera_mode", map_always_scheduled(u32le_at(state,0x92)), true);
    append_setting_int(s, "camera_start_hour_utc", byte_at(state,0x96), true);
    append_setting_int(s, "camera_start_minute_utc", byte_at(state,0x97), true);
    append_setting_int(s, "camera_end_hour_utc", byte_at(state,0x98), true);
    append_setting_int(s, "camera_end_minute_utc", byte_at(state,0x99), true);
    append_setting_str(s, "camera_resolution", map_resolution(u32le_at(state,0x9a)), true);
    append_setting_str(s, "night_vision_mode", map_night_vision(u32le_at(state,0x9e)), true);
    append_setting_str(s, "video_record_effective_cached", map_bool01(byte_at(state,0xa2)), true);
    append_setting_str(s, "video_record_switch", map_bool01(byte_at(state,0xa3)), true);
    append_setting_str(s, "local_camera_recording_type", map_recording_type(u32le_at(state,0xa4)), true);
    append_setting_str(s, "local_recording_mode", map_always_scheduled(u32le_at(state,0xa8)), true);
    append_setting_int(s, "video_record_start_hour_utc", byte_at(state,0xac), true);
    append_setting_int(s, "video_record_start_minute_utc", byte_at(state,0xad), true);
    append_setting_int(s, "video_record_end_hour_utc", byte_at(state,0xae), true);
    append_setting_int(s, "video_record_end_minute_utc", byte_at(state,0xaf), true);
    append_setting_str(s, "feeding_video_recording_enable", map_bool01(byte_at(state,0xb0)), true);
    append_setting_str(s, "record_scheduled_feedings", map_bool01(byte_at(state,0xb1)), true);
    append_setting_str(s, "record_manual_feedings", map_bool01(byte_at(state,0xb2)), true);
    append_setting_int(s, "before_feeding_plan_minutes", byte_at(state,0xb3), true);
    append_setting_int(s, "automatic_recording_minutes", byte_at(state,0xb4), true);
    append_setting_int(s, "after_manual_feeding_minutes", byte_at(state,0xb5), true);
    append_setting_str(s, "video_watermark_enable", map_bool01(byte_at(state,0xb6)), true);
    append_setting_str(s, "motion_detection_effective_cached", map_bool01(byte_at(state,0xb7)), true);
    append_setting_str(s, "motion_detection_switch", map_bool01(byte_at(state,0xb8)), true);
    append_setting_str(s, "motion_detection_mode", map_always_scheduled(u32le_at(state,0xb9)), true);
    append_setting_int(s, "motion_start_hour_utc", byte_at(state,0xbd), true);
    append_setting_int(s, "motion_start_minute_utc", byte_at(state,0xbe), true);
    append_setting_int(s, "motion_end_hour_utc", byte_at(state,0xbf), true);
    append_setting_int(s, "motion_end_minute_utc", byte_at(state,0xc0), true);
    append_setting_str(s, "motion_detection_sensitivity", map_motion_sensitivity(u32le_at(state,0xc1)), true);
    append_setting_u32(s, "motion_detection_sensitivity_raw", u32le_at(state,0xc1), true);
    append_setting_str(s, "motion_detection_range", map_motion_range(u32le_at(state,0xc5)), true);
    append_setting_u32(s, "motion_detection_range_raw", u32le_at(state,0xc5), true);
    append_setting_str(s, "sound_detection_effective_cached", map_bool01(byte_at(state,0xc9)), true);
    append_setting_str(s, "sound_detection_switch", map_bool01(byte_at(state,0xca)), true);
    append_setting_str(s, "sound_detection_mode", map_always_scheduled(u32le_at(state,0xcb)), true);
    append_setting_int(s, "sound_detection_start_hour_utc", byte_at(state,0xcf), true);
    append_setting_int(s, "sound_detection_start_minute_utc", byte_at(state,0xd0), true);
    append_setting_int(s, "sound_detection_end_hour_utc", byte_at(state,0xd1), true);
    append_setting_int(s, "sound_detection_end_minute_utc", byte_at(state,0xd2), true);
    append_setting_str(s, "sound_detection_sensitivity", map_sound_sensitivity(u32le_at(state,0xd3)), true);
    append_setting_u32(s, "sound_detection_sensitivity_raw", u32le_at(state,0xd3), true);
    append_setting_u32(s, "sd_card_state_raw", u32le_at(state,0xd7), true);
    append_setting_u32(s, "sd_card_filesystem_raw", u32le_at(state,0xdb), true);
    append_setting_u32(s, "sd_card_total_capacity", u32le_at(state,0xdf), true);
    append_setting_u32(s, "sd_card_used_capacity", u32le_at(state,0xe3), true);
    append_setting_str(s, "cloud_video_record_switch", map_bool01(byte_at(state,0xe7)), false);
    str_append(s, "}");
}

static void append_settings_raw(Str *s, const Bytes *state) {
    struct Field { const char *name; size_t off; unsigned width; } fields[] = {
        {"motor_dir_u8_0x08",0x08,1},{"bowl_mode_u8_0x09",0x09,1},
        {"power_type_u8_0x0a",0x0a,1},{"power_mode_u8_0x0b",0x0b,1},
        {"electric_quantity_u8_0x0c",0x0c,1},{"surplus_grain_u8_0x0d",0x0d,1},
        {"motor_state_u8_0x0e",0x0e,1},{"grain_outlet_state_u8_0x0f",0x0f,1},
        {"volume_u8_0x10",0x10,1},{"auto_change_mode_u8_0x11",0x11,1},
        {"auto_threshold_u8_0x12",0x12,1},{"feeding_audio_type_u8_0x13",0x13,1},
        {"light_effective_cached_u8_0x14",0x14,1},{"light_switch_u8_0x15",0x15,1},
        {"light_mode_u8_0x16",0x16,1},{"lighting_times_u16le_0x1b",0x1b,2},
        {"sound_effective_cached_u8_0x20",0x20,1},{"sound_switch_u8_0x21",0x21,1},
        {"sound_mode_u8_0x22",0x22,1},{"sound_times_u16le_0x27",0x27,2},
        {"camera_effective_cached_u8_0x90",0x90,1},{"camera_switch_u8_0x91",0x91,1},
        {"camera_mode_u32le_0x92",0x92,4},{"resolution_u32le_0x9a",0x9a,4},
        {"night_vision_u32le_0x9e",0x9e,4},{"video_record_effective_cached_u8_0xa2",0xa2,1},
        {"video_record_switch_u8_0xa3",0xa3,1},{"video_record_mode_u32le_0xa4",0xa4,4},
        {"video_record_schedule_mode_u32le_0xa8",0xa8,4},{"feeding_video_switch_u8_0xb0",0xb0,1},
        {"record_scheduled_feedings_u8_0xb1",0xb1,1},{"record_manual_feedings_u8_0xb2",0xb2,1},
        {"before_feeding_plan_minutes_u8_0xb3",0xb3,1},{"automatic_recording_minutes_u8_0xb4",0xb4,1},
        {"after_manual_feeding_minutes_u8_0xb5",0xb5,1},{"video_watermark_switch_u8_0xb6",0xb6,1},
        {"motion_detection_effective_cached_u8_0xb7",0xb7,1},{"motion_detection_switch_u8_0xb8",0xb8,1},
        {"motion_detection_mode_u32le_0xb9",0xb9,4},{"motion_sensitivity_u32le_0xc1",0xc1,4},
        {"motion_range_u32le_0xc5",0xc5,4},{"sound_detection_effective_cached_u8_0xc9",0xc9,1},
        {"sound_detection_switch_u8_0xca",0xca,1},{"sound_detection_mode_u32le_0xcb",0xcb,4},
        {"sound_detection_sensitivity_u32le_0xd3",0xd3,4},{"sd_card_state_u32le_0xd7",0xd7,4},
        {"sd_card_filesystem_u32le_0xdb",0xdb,4},{"sd_card_total_capacity_u32le_0xdf",0xdf,4},
        {"sd_card_used_capacity_u32le_0xe3",0xe3,4},{"cloud_video_record_switch_u8_0xe7",0xe7,1},
    };
    str_append(s, "{");
    for (size_t i = 0; i < sizeof(fields)/sizeof(fields[0]); i++) {
        json_string(s, fields[i].name);
        uint32_t value = fields[i].width == 4 ? u32le_at(state, fields[i].off)
            : fields[i].width == 2 ? u16le_at(state, fields[i].off)
            : byte_at(state, fields[i].off);
        str_appendf(s, ":%u%s", (unsigned)value, i + 1 < sizeof(fields)/sizeof(fields[0]) ? "," : "");
    }
    str_append(s, "}");
}

static void append_setting_classes(Str *s) {
    str_append(s,
        "{\"persistent\":["
        "\"bowl_mode\",\"volume\",\"auto_change_mode\",\"auto_threshold\","
        "\"feeding_audio_type\",\"feeding_audio_enabled\",\"audio_url\","
        "\"light_switch\",\"button_lights_mode\",\"light_start_hour_utc\",\"light_start_minute_utc\","
        "\"light_end_hour_utc\",\"light_end_minute_utc\",\"lighting_times_raw\","
        "\"sound_switch\",\"sound_mode\",\"sound_start_hour_utc\",\"sound_start_minute_utc\","
        "\"sound_end_hour_utc\",\"sound_end_minute_utc\",\"sound_times_raw\","
        "\"camera_switch\",\"camera_mode\",\"camera_start_hour_utc\",\"camera_start_minute_utc\","
        "\"camera_end_hour_utc\",\"camera_end_minute_utc\",\"camera_resolution\",\"night_vision_mode\","
        "\"video_record_switch\",\"local_camera_recording_type\",\"local_recording_mode\","
        "\"feeding_video_recording_enable\",\"record_scheduled_feedings\",\"record_manual_feedings\","
        "\"before_feeding_plan_minutes\",\"automatic_recording_minutes\",\"after_manual_feeding_minutes\","
        "\"video_watermark_enable\",\"motion_detection_switch\",\"motion_detection_mode\","
        "\"motion_detection_sensitivity\",\"motion_detection_range\",\"sound_detection_switch\","
        "\"sound_detection_mode\",\"sound_detection_sensitivity\",\"cloud_video_record_switch\"],"
        "\"effective_cached\":[\"light_effective_cached\",\"sound_effective_cached\","
        "\"camera_effective_cached\",\"video_record_effective_cached\","
        "\"motion_detection_effective_cached\",\"sound_detection_effective_cached\"],"
        "\"runtime\":[\"motor_dir_raw\",\"power_type_raw\",\"power_mode_raw\","
        "\"electric_quantity\",\"surplus_grain\",\"motor_state_raw\",\"feeding_active\","
        "\"motor_monitor_flag\",\"grain_outlet_state\",\"sd_card_state_raw\","
        "\"sd_card_filesystem_raw\",\"sd_card_total_capacity\",\"sd_card_used_capacity\"]}"
    );
}

static const char *weekday_name(uint8_t v) {
    switch (v) {
        case 1: return "monday";
        case 2: return "tuesday";
        case 3: return "wednesday";
        case 4: return "thursday";
        case 5: return "friday";
        case 6: return "saturday";
        case 7: return "sunday";
        default: return NULL;
    }
}

static void append_time_hhmm(Str *s, int hour, int minute) {
    if (hour < 0 || hour > 23 || minute < 0 || minute > 59) str_append(s, "null");
    else {
        char tmp[16];
        snprintf(tmp, sizeof(tmp), "\"%02d:%02d\"", hour, minute);
        str_append(s, tmp);
    }
}

static void append_plans(Str *s, const Bytes *idx, const Bytes *plan, int local_offset_hours, bool raw_records) {
    str_append(s, "{");
    if (!idx->ok || idx->len < 1 || !plan->ok) {
        str_append(s, "\"ok\":false,\"error\":\"missing plan files\"}");
        return;
    }
    uint8_t count = idx->data[0];
    bool valid = plan->len == (size_t)count * PLAN_RECORD_SIZE;
    str_appendf(s, "\"ok\":%s,", valid ? "true" : "false");
    str_appendf(s, "\"count\":%u,", (unsigned)count);
    str_appendf(s, "\"plan_bin_size\":%zu,", plan->len);
    str_appendf(s, "\"record_size\":%d,", PLAN_RECORD_SIZE);
    str_appendf(s, "\"even_split\":%s,", valid ? "true" : "false");
    if (!valid) str_append(s, "\"error\":\"plan.bin size does not match 47-byte records\",");
    str_append(s, "\"semantic_records\":[");
    if (valid) {
        for (uint8_t i = 0; i < count; i++) {
            const uint8_t *rec = plan->data + (size_t)i * PLAN_RECORD_SIZE;
            uint32_t id = (uint32_t)rec[0] | ((uint32_t)rec[1] << 8)
                | ((uint32_t)rec[2] << 16) | ((uint32_t)rec[3] << 24);
            uint8_t minute = rec[0x04];
            uint8_t hour_utc = rec[0x05];
            int hour_local = ((int)hour_utc + local_offset_hours) % 24;
            if (hour_local < 0) hour_local += 24;
            if (i) str_append(s, ",");
            str_append(s, "{");
            str_appendf(s, "\"id\":%u,", (unsigned)id);
            str_appendf(s, "\"minute\":%u,", minute);
            str_appendf(s, "\"hour_utc\":%u,", hour_utc);
            str_appendf(s, "\"one_shot\":%s,", rec[0x06] != 0 ? "true" : "false");
            str_appendf(s, "\"one_shot_raw\":%u,", rec[0x06]);
            str_append(s, "\"time_utc\":"); append_time_hhmm(s, hour_utc, minute); str_append(s, ",");
            str_append(s, "\"time_local_candidate\":"); append_time_hhmm(s, hour_local, minute); str_append(s, ",");
            str_append(s, "\"days_raw\":[");
            bool first = true;
            for (int j = 0x07; j <= 0x0d; j++) {
                if (rec[j] == 0) continue;
                if (!first) str_append(s, ",");
                str_appendf(s, "%u", rec[j]);
                first = false;
            }
            str_append(s, "],\"days\":[");
            first = true;
            for (int j = 0x07; j <= 0x0d; j++) {
                const char *nm = weekday_name(rec[j]);
                if (!nm) continue;
                if (!first) str_append(s, ",");
                json_string(s, nm);
                first = false;
            }
            str_append(s, "],");
            str_appendf(s, "\"portions\":%u,", rec[0x10]);
            str_appendf(s, "\"enable_audio\":%s,", rec[0x0e] != 0 ? "true" : "false");
            str_appendf(s, "\"enable_audio_raw\":%u,", rec[0x0e]);
            str_appendf(s, "\"audio_times\":%u,", rec[0x0f]);
            str_appendf(s, "\"execution_state\":%u,",
                (unsigned)((uint32_t)rec[0x11] | ((uint32_t)rec[0x12] << 8)
                    | ((uint32_t)rec[0x13] << 16) | ((uint32_t)rec[0x14] << 24)));
            str_appendf(s, "\"sync_time\":%llu,", (unsigned long long)u64le_ptr(rec + 0x15));
            str_appendf(s, "\"skip_end_time\":%llu,", (unsigned long long)u64le_ptr(rec + 0x1d));
            str_append(s, "\"opaque_hex\":"); append_hex_string(s, rec + 0x25, 10);
            if (raw_records) {
                str_append(s, ",\"raw\":{");
                str_append(s, "\"hex\":"); append_hex_string(s, rec, PLAN_RECORD_SIZE);
                str_append(s, "}");
            }
            str_append(s, "}");
        }
    }
    str_append(s, "]}");
}

static void iso_from_ms(uint64_t ms, char *out, size_t out_len) {
    if (ms == 0) {
        if (out_len > 0) out[0] = '\0';
        return;
    }
    time_t sec = (time_t)(ms / 1000ULL);
    struct tm tmv;
    gmtime_r(&sec, &tmv);
    strftime(out, out_len, "%Y-%m-%dT%H:%M:%SZ", &tmv);
}

static bool record_nonzero(const uint8_t *rec) {
    for (int i = 0; i < FEED_RECORD_SIZE; i++) if (rec[i] != 0) return true;
    return false;
}

static void append_queue(Str *s, const Bytes *head, const Bytes *tail) {
    uint8_t h = byte_at(head, 0);
    uint8_t t = byte_at(tail, 0);
    str_appendf(s, "{\"head\":%u,\"tail\":%u,\"pending\":%s}", h, t, h != t ? "true" : "false");
}

static const char *feed_type_name(uint8_t type) {
    if (type == 1) return "scheduled";
    if (type == 2) return "remote_manual";
    if (type == 3) return "local_button_manual";
    return "unknown";
}

static const char *feed_phase_name(int phase) {
    if (phase == 0) return "GRAIN_START";
    if (phase == 1) return "GRAIN_END";
    return "GRAIN_BLOCKING";
}

static void append_feed_phase(Str *s, const uint8_t *rec, int phase, bool raw) {
    uint32_t plan_id = (uint32_t)rec[0] | ((uint32_t)rec[1] << 8)
        | ((uint32_t)rec[2] << 16) | ((uint32_t)rec[3] << 24);
    uint32_t error_code = (uint32_t)rec[0x1a] | ((uint32_t)rec[0x1b] << 8)
        | ((uint32_t)rec[0x1c] << 16) | ((uint32_t)rec[0x1d] << 24);
    uint64_t exec_time = u64le_ptr(rec + 0x0e);
    char timestamp[64] = "";
    iso_from_ms(exec_time, timestamp, sizeof(timestamp));
    str_append(s, "{");
    str_append(s, "\"phase\":"); json_string(s, feed_phase_name(phase)); str_append(s, ",");
    str_appendf(s, "\"present\":%s,", record_nonzero(rec) ? "true" : "false");
    str_appendf(s, "\"plan_id\":%u,", (unsigned)plan_id);
    str_appendf(s, "\"finished\":%s,\"finished_raw\":%u,", rec[0x08] != 0 ? "true" : "false", rec[0x08]);
    str_appendf(s, "\"retried\":%s,\"retried_raw\":%u,", rec[0x09] != 0 ? "true" : "false", rec[0x09]);
    str_appendf(s, "\"phase_status_raw\":%u,", rec[0x0a]);
    str_append(s, "\"type\":"); json_string(s, feed_type_name(rec[0x0b])); str_append(s, ",");
    str_appendf(s, "\"type_raw\":%u,", rec[0x0b]);
    str_appendf(s, "\"actual_grain_num\":%u,\"expected_grain_num\":%u,", rec[0x0c], rec[0x0d]);
    str_appendf(s, "\"exec_time\":%llu,", (unsigned long long)exec_time);
    str_append(s, "\"exec_time_utc\":"); if (timestamp[0]) json_string(s, timestamp); else str_append(s, "null"); str_append(s, ",");
    str_appendf(s, "\"error_code\":%u,\"unknown_0x1e\":%u", (unsigned)error_code, rec[0x1e]);
    if (raw) { str_append(s, ",\"raw_hex\":"); append_hex_string(s, rec, FEED_RECORD_SIZE); }
    str_append(s, "}");
}

static void append_pending_feed_events(
    Str *s, const Bytes *feed, const Bytes *head, const Bytes *tail, bool raw
) {
    str_append(s, "[");
    if (!feed->ok || feed->len != FEED_FILE_SIZE) { str_append(s, "]"); return; }
    uint8_t index = byte_at(head, 0);
    uint8_t stop = byte_at(tail, 0);
    bool first_slot = true;
    for (int visited = 0; visited < FEED_SLOT_COUNT && index != stop; visited++) {
        if (index >= FEED_SLOT_COUNT) break;
        const uint8_t *slot = feed->data + (size_t)index * FEED_SLOT_SIZE;
        if (!first_slot) str_append(s, ",");
        first_slot = false;
        str_appendf(s, "{\"slot_index\":%u,\"queue_order\":%d,\"phases\":[", index, visited);
        for (int phase = 0; phase < FEED_PHASE_COUNT; phase++) {
            if (phase) str_append(s, ",");
            append_feed_phase(s, slot + (size_t)phase * FEED_RECORD_SIZE, phase, raw);
        }
        str_append(s, "]}");
        index = (uint8_t)((index + 1) % FEED_SLOT_COUNT);
    }
    str_append(s, "]");
}

typedef struct {
    Bytes state;
    Bytes idx;
    Bytes plan;
    Bytes head;
    Bytes tail;
    Bytes rtc;
} CoreFiles;

static CoreFiles read_core_files(void) {
    CoreFiles cf;
    memset(&cf, 0, sizeof(cf));
    cf.state = read_file_rel("attr/state.bin", MAX_FILE_BYTES);
    cf.idx = read_file_rel("feed_plan/index.bin", 64);
    cf.plan = read_file_rel("feed_plan/plan.bin", MAX_FILE_BYTES);
    cf.head = read_file_rel("feed_plan/rec_index_head.bin", 64);
    cf.tail = read_file_rel("feed_plan/rec_index_tail.bin", 64);
    cf.rtc = read_file_rel("rtc/rtc_time.bin", 64);
    return cf;
}

static void free_core_files(CoreFiles *cf) {
    bytes_free(&cf->state); bytes_free(&cf->idx); bytes_free(&cf->plan);
    bytes_free(&cf->head); bytes_free(&cf->tail); bytes_free(&cf->rtc);
}

static void append_revisions(Str *s, uint64_t sr, uint64_t pr, uint64_t qr) {
    uint64_t cr = core_rev(sr, pr, qr);
    str_append(s, "{");
    append_rev(s, "core_rev", cr, true);
    append_rev(s, "settings_rev", sr, true);
    append_rev(s, "plans_rev", pr, true);
    append_rev(s, "queue_index_rev", qr, false);
    str_append(s, "}");
}

static void build_health_response(Str *s) {
    Bytes state = read_file_rel("attr/state.bin", MAX_FILE_BYTES);
    bool state_ok = state.ok && state.len == STATE_BIN_SIZE;
    str_appendf(s, "{\"ok\":%s,", state_ok ? "true" : "false");
    str_append(s, "\"agent\":{");
    str_append(s, "\"name\":"); json_string(s, AGENT_NAME); str_append(s, ",");
    str_append(s, "\"version\":"); json_string(s, AGENT_VERSION); str_append(s, ",");
    str_appendf(s, "\"uptime_ms\":%llu", (unsigned long long)(now_ms() - g_start_ms));
    str_append(s, "},\"device\":{");
    str_append(s, "\"root\":"); json_string(s, g_cfg.root);
    str_appendf(s, "},\"state_decode\":{\"ok\":%s,\"expected_size\":%d,\"actual_size\":%zu}}",
        state_ok ? "true" : "false", STATE_BIN_SIZE, state.ok ? state.len : 0);
    bytes_free(&state);
}

static void build_rev_response(Str *s) {
    uint64_t t0 = now_ms();
    CoreFiles cf = read_core_files();
    if (!cf.state.ok || cf.state.len != STATE_BIN_SIZE) {
        str_appendf(s, "{\"ok\":false,\"error\":\"invalid attr/state.bin length\",\"expected_size\":%d,\"actual_size\":%zu}",
            STATE_BIN_SIZE, cf.state.ok ? cf.state.len : 0);
        free_core_files(&cf);
        return;
    }
    uint64_t sr = settings_rev_from_state(&cf.state);
    uint64_t pr = plans_rev_from_files(&cf.idx, &cf.plan);
    uint64_t qr = queue_rev_from_files(&cf.head, &cf.tail);
    str_append(s, "{\"ok\":true,");
    str_appendf(s, "\"read_ms\":%llu,", (unsigned long long)(now_ms() - t0));
    str_append(s, "\"revisions\":"); append_revisions(s, sr, pr, qr); str_append(s, ",");
    str_append(s, "\"queue\":"); append_queue(s, &cf.head, &cf.tail);
    str_append(s, "}");
    free_core_files(&cf);
}

static void build_core_response(Str *s, bool raw) {
    uint64_t t0 = now_ms();
    CoreFiles cf = read_core_files();
    if (!cf.state.ok || cf.state.len != STATE_BIN_SIZE) {
        str_appendf(s, "{\"ok\":false,\"error\":\"invalid attr/state.bin length\",\"expected_size\":%d,\"actual_size\":%zu}",
            STATE_BIN_SIZE, cf.state.ok ? cf.state.len : 0);
        free_core_files(&cf);
        return;
    }
    uint64_t sr = settings_rev_from_state(&cf.state);
    uint64_t pr = plans_rev_from_files(&cf.idx, &cf.plan);
    uint64_t qr = queue_rev_from_files(&cf.head, &cf.tail);
    str_append(s, "{\"ok\":true,");
    str_appendf(s, "\"read_ms\":%llu,", (unsigned long long)(now_ms() - t0));
    str_append(s, "\"revisions\":"); append_revisions(s, sr, pr, qr); str_append(s, ",");
    str_append(s, "\"settings\":"); append_settings_summary(s, &cf.state); str_append(s, ",");
    str_append(s, "\"setting_classes\":"); append_setting_classes(s); str_append(s, ",");
    str_append(s, "\"settings_raw\":"); append_settings_raw(s, &cf.state); str_append(s, ",");
    str_append(s, "\"plans\":"); append_plans(s, &cf.idx, &cf.plan, -4, raw); str_append(s, ",");
    str_append(s, "\"queue\":"); append_queue(s, &cf.head, &cf.tail);
    if (raw) {
        str_append(s, ",\"raw\":{");
        append_raw_file(s, "attr/state.bin", &cf.state, true);
        append_raw_file(s, "feed_plan/index.bin", &cf.idx, true);
        append_raw_file(s, "feed_plan/plan.bin", &cf.plan, true);
        append_raw_file(s, "feed_plan/rec_index_head.bin", &cf.head, true);
        append_raw_file(s, "feed_plan/rec_index_tail.bin", &cf.tail, true);
        append_raw_file(s, "rtc/rtc_time.bin", &cf.rtc, false);
        str_append(s, "}");
    }
    str_append(s, "}");
    free_core_files(&cf);
}

static void build_feed_events_response(Str *s, bool raw) {
    Bytes feed = read_file_rel("feed_plan/feed_rec.bin", MAX_FILE_BYTES);
    Bytes head = read_file_rel("feed_plan/rec_index_head.bin", 64);
    Bytes tail = read_file_rel("feed_plan/rec_index_tail.bin", 64);
    Bytes err_head = read_file_rel("feed_plan/err_rec_index_head.bin", 64);
    Bytes err_tail = read_file_rel("feed_plan/err_rec_index_tail.bin", 64);
    bool valid = feed.ok && feed.len == FEED_FILE_SIZE;
    str_appendf(s, "{\"ok\":%s,", valid ? "true" : "false");
    if (!valid) str_appendf(s, "\"error\":\"invalid feed_rec.bin length\",\"expected_size\":%d,\"actual_size\":%zu,", FEED_FILE_SIZE, feed.ok ? feed.len : 0);
    str_append(s, "\"semantics\":\"pending_outbound_events_not_history\",");
    str_append(s, "\"queue\":"); append_queue(s, &head, &tail); str_append(s, ",");
    str_append(s, "\"err_queue\":"); append_queue(s, &err_head, &err_tail); str_append(s, ",");
    str_append(s, "\"events\":"); append_pending_feed_events(s, &feed, &head, &tail, raw);
    str_append(s, "}");
    bytes_free(&feed); bytes_free(&head); bytes_free(&tail); bytes_free(&err_head); bytes_free(&err_tail);
}

static char *strcasestr_local(const char *haystack, const char *needle) {
    if (!needle[0]) return (char *)haystack;
    size_t nlen = strlen(needle);
    for (const char *p = haystack; *p; p++) {
        if (strncasecmp(p, needle, nlen) == 0) return (char *)p;
    }
    return NULL;
}

static bool request_authorized(const char *req, const char *client_ip) {
    if (g_cfg.allow_ip[0] && strcmp(g_cfg.allow_ip, client_ip) != 0) return false;
    if (!g_cfg.require_token) return true;
    char expected[MAX_TOKEN + 32];
    snprintf(expected, sizeof(expected), "Authorization: Bearer %s", g_cfg.token);
    return strcasestr_local(req, expected) != NULL;
}

static bool write_all(int fd, const char *buf, size_t len) {
    size_t off = 0;

    while (off < len) {
        ssize_t n = write(fd, buf + off, len - off);

        if (n > 0) {
            off += (size_t)n;
            continue;
        }

        if (n < 0 && errno == EINTR) {
            continue;
        }

        return false;
    }

    return true;
}

static void send_response(int fd, int status, const char *status_text, const char *body) {
    char header[512];
    size_t blen = strlen(body);
    int n = snprintf(header, sizeof(header),
        "HTTP/1.1 %d %s\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: %zu\r\n"
        "Connection: close\r\n"
        "Cache-Control: no-store\r\n"
        "\r\n",
        status, status_text, blen);

    if (n <= 0) {
        return;
    }

    if (!write_all(fd, header, (size_t)n)) {
        return;
    }

    (void)write_all(fd, body, blen);
}

static void build_error(Str *s, const char *error) {
    str_append(s, "{\"ok\":false,\"error\":"); json_string(s, error); str_append(s, "}");
}

static void handle_client(int cfd, struct sockaddr_in *peer) {
    char client_ip[64];
    inet_ntop(AF_INET, &peer->sin_addr, client_ip, sizeof(client_ip));
    char req[MAX_REQUEST + 1];
    ssize_t n = read(cfd, req, MAX_REQUEST);
    if (n <= 0) return;
    req[n] = '\0';

    char method[16] = {0}, target[256] = {0};
    if (sscanf(req, "%15s %255s", method, target) != 2) {
        Str b; str_init(&b); build_error(&b, "bad request"); send_response(cfd, 400, "Bad Request", b.buf); str_free(&b); return;
    }
    if (strcmp(method, "GET") != 0) {
        Str b; str_init(&b); build_error(&b, "method not allowed"); send_response(cfd, 405, "Method Not Allowed", b.buf); str_free(&b); return;
    }
    if (!request_authorized(req, client_ip)) {
        Str b; str_init(&b); build_error(&b, "unauthorized"); send_response(cfd, 401, "Unauthorized", b.buf); str_free(&b); return;
    }

    bool raw = strstr(target, "raw=1") != NULL;
    Str body; str_init(&body);
    int status = 200;
    const char *text = "OK";
    if (strncmp(target, "/health", 7) == 0) build_health_response(&body);
    else if (strncmp(target, "/v1/rev", 7) == 0) build_rev_response(&body);
    else if (strncmp(target, "/v1/core", 8) == 0) build_core_response(&body, raw);
    else if (strncmp(target, "/v1/feed-events", 15) == 0) build_feed_events_response(&body, raw);
    else { status = 404; text = "Not Found"; build_error(&body, "not found"); }
    send_response(cfd, status, text, body.buf);
    str_free(&body);
}

static bool parse_listen(const char *arg, char *ip, size_t ip_len, int *port) {
    const char *colon = strrchr(arg, ':');
    if (!colon) return false;
    size_t n = (size_t)(colon - arg);
    if (n == 0 || n >= ip_len) return false;
    memcpy(ip, arg, n); ip[n] = '\0';
    *port = atoi(colon + 1);
    return *port > 0 && *port < 65536;
}

static bool read_token_file(const char *path, char *out, size_t out_len) {
    FILE *f = fopen(path, "r");
    if (!f) return false;
    if (!fgets(out, (int)out_len, f)) { fclose(f); return false; }
    fclose(f);
    size_t n = strlen(out);
    while (n > 0 && (out[n-1] == '\n' || out[n-1] == '\r' || isspace((unsigned char)out[n-1]))) out[--n] = '\0';
    return n > 0;
}

static void usage(FILE *f) {
    fprintf(f,
        "Usage: plaf203-state-agent [options]\n"
        "  --root PATH              State root, default /user/data\n"
        "  --listen IP:PORT         Listen address, default 127.0.0.1:8765\n"
        "  --token TOKEN            Bearer token\n"
        "  --token-file PATH        Read bearer token from file\n"
        "  --allow-ip IP            Only allow one source IP\n"
        "  --poll-feed-ms N         Deprecated compatibility option (ignored)\n"
        "  --pid-file PATH          Write pid file\n"
        "  --log-file PATH          Append log file\n"
        "  --help\n");
}

static void config_defaults(void) {
    memset(&g_cfg, 0, sizeof(g_cfg));
    snprintf(g_cfg.root, sizeof(g_cfg.root), "/user/data");
    snprintf(g_cfg.listen_ip, sizeof(g_cfg.listen_ip), "127.0.0.1");
    g_cfg.listen_port = 8765;
    g_cfg.poll_feed_ms = 500;
}

static bool parse_args(int argc, char **argv) {
    config_defaults();
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--help") == 0) { usage(stdout); exit(0); }
        else if (strcmp(argv[i], "--root") == 0 && i + 1 < argc) snprintf(g_cfg.root, sizeof(g_cfg.root), "%s", argv[++i]);
        else if (strcmp(argv[i], "--listen") == 0 && i + 1 < argc) {
            if (!parse_listen(argv[++i], g_cfg.listen_ip, sizeof(g_cfg.listen_ip), &g_cfg.listen_port)) return false;
        }
        else if (strcmp(argv[i], "--token") == 0 && i + 1 < argc) {
            snprintf(g_cfg.token, sizeof(g_cfg.token), "%s", argv[++i]);
            g_cfg.require_token = true;
        }
        else if (strcmp(argv[i], "--token-file") == 0 && i + 1 < argc) {
            if (!read_token_file(argv[++i], g_cfg.token, sizeof(g_cfg.token))) {
                fprintf(stderr, "failed to read token file\n"); return false;
            }
            g_cfg.require_token = true;
        }
        else if (strcmp(argv[i], "--allow-ip") == 0 && i + 1 < argc) snprintf(g_cfg.allow_ip, sizeof(g_cfg.allow_ip), "%s", argv[++i]);
        else if (strcmp(argv[i], "--poll-feed-ms") == 0 && i + 1 < argc) g_cfg.poll_feed_ms = atoi(argv[++i]);
        else if (strcmp(argv[i], "--pid-file") == 0 && i + 1 < argc) snprintf(g_cfg.pid_file, sizeof(g_cfg.pid_file), "%s", argv[++i]);
        else if (strcmp(argv[i], "--log-file") == 0 && i + 1 < argc) snprintf(g_cfg.log_file, sizeof(g_cfg.log_file), "%s", argv[++i]);
        else { fprintf(stderr, "unknown/incomplete arg: %s\n", argv[i]); return false; }
    }
    return true;
}

static int run_server(void) {
    int sfd = socket(AF_INET, SOCK_STREAM, 0);
    if (sfd < 0) { perror("socket"); return 1; }
    int one = 1;
    setsockopt(sfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons((uint16_t)g_cfg.listen_port);
    if (inet_pton(AF_INET, g_cfg.listen_ip, &addr.sin_addr) != 1) {
        fprintf(stderr, "bad listen ip\n"); close(sfd); return 1;
    }
    if (bind(sfd, (struct sockaddr *)&addr, sizeof(addr)) < 0) { perror("bind"); close(sfd); return 1; }
    if (listen(sfd, 8) < 0) { perror("listen"); close(sfd); return 1; }
    log_msg("listening on %s:%d root=%s", g_cfg.listen_ip, g_cfg.listen_port, g_cfg.root);
    while (!g_stop) {
        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(sfd, &rfds);
        struct timeval tv;
        tv.tv_sec = 1;
        tv.tv_usec = 0;
        int sr = select(sfd + 1, &rfds, NULL, NULL, &tv);
        if (sr < 0) {
            if (errno == EINTR) continue;
            perror("select");
            break;
        }
        if (sr == 0) continue;

        struct sockaddr_in peer;
        socklen_t plen = sizeof(peer);
        int cfd = accept(sfd, (struct sockaddr *)&peer, &plen);
        if (cfd < 0) {
            if (errno == EINTR) continue;
            perror("accept"); break;
        }
        handle_client(cfd, &peer);
        close(cfd);
    }
    close(sfd);
    return 0;
}

int main(int argc, char **argv) {
    if (!parse_args(argc, argv)) { usage(stderr); return 2; }
    g_start_ms = now_ms();
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    signal(SIGPIPE, SIG_IGN);
    if (g_cfg.pid_file[0]) {
        FILE *f = fopen(g_cfg.pid_file, "w");
        if (f) { fprintf(f, "%ld\n", (long)getpid()); fclose(f); }
    }
    int rc = run_server();
    g_stop = 1;
    if (g_cfg.pid_file[0]) unlink(g_cfg.pid_file);
    return rc;
}
