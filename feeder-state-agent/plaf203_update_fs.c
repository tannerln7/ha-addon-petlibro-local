#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#ifndef PATH_MAX
#define PATH_MAX 4096
#endif

#define MAX_BINARY_BYTES (64U * 1024U * 1024U)

static bool make_path(char *out, size_t out_len, const char *root,
                      const char *relative) {
  int written = snprintf(out, out_len, "%s/%s", root, relative);
  return written > 0 && (size_t)written < out_len;
}

static bool write_all(int fd, const uint8_t *data, size_t len) {
  size_t offset = 0;
  while (offset < len) {
    ssize_t written = write(fd, data + offset, len - offset);
    if (written > 0) {
      offset += (size_t)written;
      continue;
    }
    if (written < 0 && errno == EINTR)
      continue;
    return false;
  }
  return true;
}

static bool read_exact(int fd, uint8_t *data, size_t len) {
  size_t offset = 0;
  while (offset < len) {
    ssize_t got = read(fd, data + offset, len - offset);
    if (got > 0) {
      offset += (size_t)got;
      continue;
    }
    if (got < 0 && errno == EINTR)
      continue;
    return false;
  }
  return true;
}

static bool valid_arm_elf_fd(int fd) {
  struct stat stat_buf;
  if (fstat(fd, &stat_buf) != 0 || !S_ISREG(stat_buf.st_mode) ||
      stat_buf.st_size < 52 || (uint64_t)stat_buf.st_size > MAX_BINARY_BYTES)
    return false;

  uint8_t header[52];
  if (lseek(fd, 0, SEEK_SET) < 0 || !read_exact(fd, header, sizeof(header)))
    return false;
  if (lseek(fd, 0, SEEK_SET) < 0)
    return false;

  uint16_t type = (uint16_t)header[16] | ((uint16_t)header[17] << 8);
  uint16_t machine = (uint16_t)header[18] | ((uint16_t)header[19] << 8);
  return header[0] == 0x7f && header[1] == 'E' && header[2] == 'L' &&
         header[3] == 'F' && header[4] == 1 && header[5] == 1 && type == 2 &&
         machine == 40;
}

static bool fsync_directory(const char *path) {
  int fd = open(path, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  if (fd < 0)
    return false;
  bool ok = fsync(fd) == 0;
  close(fd);
  return ok;
}

static bool atomic_copy_elf(const char *source, const char *temporary,
                            const char *destination,
                            const char *destination_dir) {
  int source_fd = open(source, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
  if (source_fd < 0)
    return false;
  if (!valid_arm_elf_fd(source_fd)) {
    close(source_fd);
    errno = EINVAL;
    return false;
  }

  (void)unlink(temporary);
  int destination_fd = open(temporary, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC |
                                           O_NOFOLLOW,
                            0700);
  if (destination_fd < 0) {
    close(source_fd);
    return false;
  }

  bool ok = true;
  uint8_t buffer[8192];
  while (ok) {
    ssize_t got = read(source_fd, buffer, sizeof(buffer));
    if (got > 0) {
      ok = write_all(destination_fd, buffer, (size_t)got);
      continue;
    }
    if (got < 0 && errno == EINTR)
      continue;
    if (got < 0)
      ok = false;
    break;
  }
  if (ok)
    ok = fchmod(destination_fd, 0700) == 0;
  if (ok)
    ok = fsync(destination_fd) == 0;
  if (close(destination_fd) != 0)
    ok = false;
  close(source_fd);

  if (ok)
    ok = rename(temporary, destination) == 0;
  if (ok)
    ok = fsync_directory(destination_dir);
  if (!ok)
    (void)unlink(temporary);
  return ok;
}

static bool safe_json_token(const char *value, bool allow_empty) {
  if (!value || (!allow_empty && !value[0]) || strlen(value) > 80)
    return false;
  for (const unsigned char *cursor = (const unsigned char *)value; *cursor;
       cursor++) {
    if (!(('a' <= *cursor && *cursor <= 'z') ||
          ('A' <= *cursor && *cursor <= 'Z') ||
          ('0' <= *cursor && *cursor <= '9') || *cursor == '_' ||
          *cursor == '-' || *cursor == '.' || *cursor == '+'))
      return false;
  }
  return true;
}

static bool write_status(const char *update_dir, const char *status_path,
                         const char *status_tmp, const char *status,
                         const char *reason, const char *candidate,
                         const char *previous) {
  if (!safe_json_token(status, false) || !safe_json_token(reason, false) ||
      !safe_json_token(candidate, true) || !safe_json_token(previous, true)) {
    errno = EINVAL;
    return false;
  }

  char json[512];
  int length = snprintf(
      json, sizeof(json),
      "{\"status\":\"%s\",\"reason\":\"%s\",\"candidate_version\":"
      "\"%s\",\"previous_version\":\"%s\"}\n",
      status, reason, candidate, previous);
  if (length <= 0 || (size_t)length >= sizeof(json)) {
    errno = EOVERFLOW;
    return false;
  }

  (void)unlink(status_tmp);
  int fd = open(status_tmp, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW,
                0600);
  if (fd < 0)
    return false;
  bool ok = write_all(fd, (const uint8_t *)json, (size_t)length);
  if (ok)
    ok = fsync(fd) == 0;
  if (close(fd) != 0)
    ok = false;
  if (ok)
    ok = rename(status_tmp, status_path) == 0;
  if (ok)
    ok = fsync_directory(update_dir);
  if (!ok)
    (void)unlink(status_tmp);
  return ok;
}

static bool durable_cleanup(const char *update_dir, const char *pending,
                            const char *candidate) {
  bool ok = true;
  if (unlink(pending) != 0 && errno != ENOENT)
    ok = false;
  if (unlink(candidate) != 0 && errno != ENOENT)
    ok = false;
  return fsync_directory(update_dir) && ok;
}

static int fail(const char *operation) {
  fprintf(stderr, "plaf203-update-fs %s failed: %s\n", operation,
          strerror(errno));
  return 1;
}

int main(int argc, char **argv) {
  const char *root = "/user/data";
  int arg = 1;
  if (argc >= 3 && strcmp(argv[1], "--root") == 0) {
    root = argv[2];
    arg = 3;
  }
  if (arg >= argc) {
    fprintf(stderr, "usage: plaf203-update-fs [--root PATH] OPERATION ...\n");
    return 2;
  }

  char local_dir[PATH_MAX], update_dir[PATH_MAX], active[PATH_MAX];
  char candidate[PATH_MAX], backup[PATH_MAX], backup_tmp[PATH_MAX];
  char active_tmp[PATH_MAX], restore_tmp[PATH_MAX], pending[PATH_MAX];
  char status[PATH_MAX], status_tmp[PATH_MAX];
  if (!make_path(local_dir, sizeof(local_dir), root, "local-state-agent") ||
      !make_path(update_dir, sizeof(update_dir), root,
                 "local-state-agent/update") ||
      !make_path(active, sizeof(active), root,
                 "local-state-agent/plaf203-state-agent") ||
      !make_path(candidate, sizeof(candidate), root,
                 "local-state-agent/update/candidate.bin") ||
      !make_path(backup, sizeof(backup), root,
                 "local-state-agent/update/previous.bin") ||
      !make_path(backup_tmp, sizeof(backup_tmp), root,
                 "local-state-agent/update/previous.tmp") ||
      !make_path(active_tmp, sizeof(active_tmp), root,
                 "local-state-agent/active.tmp") ||
      !make_path(restore_tmp, sizeof(restore_tmp), root,
                 "local-state-agent/restore.tmp") ||
      !make_path(pending, sizeof(pending), root,
                 "local-state-agent/update/pending.json") ||
      !make_path(status, sizeof(status), root,
                 "local-state-agent/update/status.json") ||
      !make_path(status_tmp, sizeof(status_tmp), root,
                 "local-state-agent/update/status.tmp")) {
    errno = ENAMETOOLONG;
    return fail("path construction");
  }

  const char *operation = argv[arg++];
  if (strcmp(operation, "backup") == 0 && arg == argc)
    return atomic_copy_elf(active, backup_tmp, backup, update_dir) ? 0
                                                                  : fail(operation);
  if (strcmp(operation, "activate") == 0 && arg == argc)
    return atomic_copy_elf(candidate, active_tmp, active, local_dir) ? 0
                                                                    : fail(operation);
  if (strcmp(operation, "restore") == 0 && arg == argc)
    return atomic_copy_elf(backup, restore_tmp, active, local_dir) ? 0
                                                                  : fail(operation);
  if (strcmp(operation, "validate-backup") == 0 && arg == argc) {
    int fd = open(backup, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    bool ok = fd >= 0 && valid_arm_elf_fd(fd);
    if (fd >= 0)
      close(fd);
    return ok ? 0 : fail(operation);
  }
  if (strcmp(operation, "cleanup") == 0 && arg == argc)
    return durable_cleanup(update_dir, pending, candidate) ? 0 : fail(operation);
  if (strcmp(operation, "status") == 0 && argc - arg == 4)
    return write_status(update_dir, status, status_tmp, argv[arg], argv[arg + 1],
                        argv[arg + 2], argv[arg + 3])
               ? 0
               : fail(operation);

  fprintf(stderr, "invalid plaf203-update-fs operation or arguments\n");
  return 2;
}
