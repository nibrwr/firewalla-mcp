#define _DARWIN_C_SOURCE 1
#include <fcntl.h>
#include <membership.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <sys/acl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include <errno.h>

static acl_t sampler_search(uid_t sampler) {
    uuid_t subject;
    if (mbr_uid_to_uuid(sampler, subject) != 0) return NULL;
    acl_t acl = acl_init(1);
    if (acl == NULL) return NULL;
    acl_entry_t entry;
    acl_permset_t permissions;
    bool ok = acl_create_entry(&acl, &entry) == 0
        && acl_set_tag_type(entry, ACL_EXTENDED_ALLOW) == 0
        && acl_set_qualifier(entry, subject) == 0
        && acl_get_permset(entry, &permissions) == 0
        && acl_add_perm(permissions, ACL_SEARCH) == 0;
    if (ok) return acl;
    acl_free(acl);
    return NULL;
}

static bool has_exact_acl(int fd, acl_t expected) {
    acl_t actual = acl_get_fd_np(fd, ACL_TYPE_EXTENDED);
    if (actual == NULL) return false;
    char *found = acl_to_text(actual, NULL);
    char *wanted = acl_to_text(expected, NULL);
    bool matches = found != NULL && wanted != NULL && strcmp(found, wanted) == 0;
    if (found != NULL) acl_free(found);
    if (wanted != NULL) acl_free(wanted);
    acl_free(actual);
    return matches;
}

static bool ready_directory(int fd, uid_t owner, gid_t group, struct stat *info) {
    return fstat(fd, info) == 0 && S_ISDIR(info->st_mode) && (info->st_mode & 07777) == 0700
        && info->st_uid == owner && info->st_gid == group;
}

static bool prepare_candidate(int staging, const char *entry, uid_t owner, gid_t group, acl_t search) {
    if (mkdirat(staging, entry, 0700) != 0 && errno != EEXIST) return false;
    int fd = openat(staging, entry, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return false;
    struct stat info;
    bool ready = fstat(fd, &info) == 0 && S_ISDIR(info.st_mode) && (info.st_mode & 07777) == 0700
        && (info.st_uid == geteuid() || info.st_uid == owner);
    if (ready && (info.st_uid != owner || info.st_gid != group)) ready = fchown(fd, owner, group) == 0;
    ready = ready && ready_directory(fd, owner, group, &info)
        && acl_set_fd_np(fd, search, ACL_TYPE_EXTENDED) == 0;
    close(fd);
    return ready;
}

bool prepare_socket_directory(int staging, int parent, uid_t owner, gid_t group, uid_t sampler) {
    static const char published[] = "firewalla-health";
    static const char candidate[] = ".socket-dir.next";
    if (flock(staging, LOCK_EX | LOCK_NB) != 0) return false;
    acl_t search = sampler_search(sampler);
    bool ready = false;
    if (search != NULL) {
        int fd = openat(parent, published, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        if (fd >= 0) {
            struct stat info;
            ready = ready_directory(fd, owner, group, &info) && has_exact_acl(fd, search);
            close(fd);
        } else if (errno == ENOENT && prepare_candidate(staging, candidate, owner, group, search)) {
            // Apply ownership and the ACL inside the trusted install directory.
            // Publication into the shared runtime parent is one exclusive rename.
            ready = renameatx_np(staging, candidate, parent, published, RENAME_EXCL) == 0;
        }
        acl_free(search);
    }
    flock(staging, LOCK_UN);
    return ready;
}

#ifndef FIREWALLA_BOOT_TEST
#include <grp.h>
#include <pwd.h>
#include <stdint.h>
#include <stdlib.h>

static bool without_acl(int fd) {
    acl_t acl = acl_get_fd_np(fd, ACL_TYPE_EXTENDED);
    if (acl == NULL) return errno == ENOENT || errno == ENOATTR;
    acl_free(acl);
    return false;
}

static uid_t configured_sampler(int install) {
    int fd = openat(install, "sampler-uid", O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) return 0;
    char value[13] = {0};
    struct stat info;
    bool trusted = fstat(fd, &info) == 0 && S_ISREG(info.st_mode) && info.st_uid == 0
        && info.st_nlink == 1 && (info.st_mode & 07777) == 0644
        && info.st_size >= 2 && info.st_size <= 11 && without_acl(fd);
    ssize_t length = trusted ? read(fd, value, sizeof(value) - 1) : -1;
    close(fd);
    if (!trusted || length != info.st_size || value[length - 1] != '\n') return 0;
    for (ssize_t i = 0; i < length - 1; i++) {
        if (value[i] < '0' || value[i] > '9') return 0;
    }
    unsigned long parsed = strtoul(value, NULL, 10);
    if (parsed == 0 || parsed >= UINT32_MAX || getpwuid((uid_t)parsed) == NULL) return 0;
    return (uid_t)parsed;
}

static bool prepare_runtime(int install) {
    struct stat install_info;
    if (fstat(install, &install_info) != 0 || install_info.st_uid != 0
        || (install_info.st_mode & 07777) != 0755 || !without_acl(install)) return false;
    uid_t sampler = configured_sampler(install);
    struct passwd *account = getpwnam("hermes");
    if (sampler == 0 || account == NULL || account->pw_uid == 0 || account->pw_uid == sampler) {
        return false;
    }
    uid_t owner = account->pw_uid;
    gid_t group = account->pw_gid;
    static const char stage_entry[] = ".socket-stage";
    if (mkdirat(install, stage_entry, 0700) != 0 && errno != EEXIST) return false;
    int staging = openat(install, stage_entry, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (staging < 0) return false;
    int parent = open("/private/var/run", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (parent < 0) {
        close(staging);
        return false;
    }
    struct stat info;
    struct stat stage_info;
    struct group *daemon = getgrnam("daemon");
    bool ready = fstat(staging, &stage_info) == 0 && stage_info.st_uid == 0
        && (stage_info.st_mode & 07777) == 0700 && without_acl(staging)
        && fstat(parent, &info) == 0 && info.st_dev == stage_info.st_dev
        && info.st_uid == 0 && (info.st_mode & 0002) == 0
        && (!(info.st_mode & 0020) || (daemon != NULL && info.st_gid == daemon->gr_gid))
        && prepare_socket_directory(staging, parent, owner, group, sampler);
    close(staging);
    close(parent);
    return ready && setgroups(1, &group) == 0 && setgid(group) == 0 && setuid(owner) == 0
        && getuid() == owner && geteuid() == owner && getgid() == group && getegid() == group;
}

int main(int argc, char **argv) {
    (void)argv;
    umask(077);
    if (argc == 1 && getuid() == 0 && geteuid() == 0) {
        int install = open("/opt/firewalla-health-provider", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        if (install >= 0) {
            bool ready = prepare_runtime(install);
            close(install);
            if (ready) {
                char *const command[] = { "/opt/hermes-homelab/node/bin/node", "--jitless",
                    "--max-semi-space-size=1", "--max-old-space-size=24",
                    "/opt/firewalla-health-provider/main.mjs", "serve", NULL };
                char *const clean_environment[] = { "HOME=/var/empty", "PATH=/usr/bin:/bin", NULL };
                execve(command[0], command, clean_environment);
            }
        }
    }
    fputs("provider_boot_unavailable\n", stderr);
    return 1;
}
#endif
