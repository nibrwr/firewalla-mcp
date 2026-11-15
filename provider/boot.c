#define _DARWIN_C_SOURCE 1
#include <fcntl.h>
#include <membership.h>
#include <stdbool.h>
#include <sys/acl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <errno.h>

static bool sampler_search(int fd, uid_t sampler) {
    uuid_t subject;
    if (mbr_uid_to_uuid(sampler, subject) != 0) return false;
    acl_t acl = acl_init(1);
    if (acl == NULL) return false;
    acl_entry_t entry;
    acl_permset_t permissions;
    bool ok = acl_create_entry(&acl, &entry) == 0
        && acl_set_tag_type(entry, ACL_EXTENDED_ALLOW) == 0
        && acl_set_qualifier(entry, subject) == 0
        && acl_get_permset(entry, &permissions) == 0
        && acl_add_perm(permissions, ACL_SEARCH) == 0
        && acl_set_fd_np(fd, acl, ACL_TYPE_EXTENDED) == 0;
    acl_free(acl);
    return ok;
}

bool prepare_socket_directory(int parent, uid_t owner, gid_t group, uid_t sampler) {
    static const char entry[] = "firewalla-health";
    bool created = mkdirat(parent, entry, 0700) == 0;
    if (!created && errno != EEXIST) return false;

    int fd = openat(parent, entry, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return false;
    struct stat info;
    bool ok = fstat(fd, &info) == 0 && S_ISDIR(info.st_mode) && (info.st_mode & 07777) == 0700;
    if (ok && created && (info.st_uid != owner || info.st_gid != group)) {
        ok = info.st_uid == geteuid() && fchown(fd, owner, group) == 0 && fstat(fd, &info) == 0;
    }
    ok = ok && info.st_uid == owner && info.st_gid == group && sampler_search(fd, sampler);
    close(fd);
    return ok;
}

#ifndef FIREWALLA_BOOT_TEST
#include <grp.h>
#include <pwd.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static uid_t configured_sampler(void) {
    int fd = open("/opt/firewalla-health-provider/sampler-uid",
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) return 0;
    char value[13] = {0};
    struct stat info;
    bool trusted = fstat(fd, &info) == 0 && S_ISREG(info.st_mode) && info.st_uid == 0
        && info.st_nlink == 1 && (info.st_mode & 07777) == 0644
        && info.st_size >= 2 && info.st_size <= 11;
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

static bool prepare_runtime(void) {
    uid_t sampler = configured_sampler();
    struct passwd *account = getpwnam("hermes");
    if (sampler == 0 || account == NULL || account->pw_uid == 0 || account->pw_uid == sampler) {
        return false;
    }
    uid_t owner = account->pw_uid;
    gid_t group = account->pw_gid;
    int parent = open("/private/var/run", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (parent < 0) return false;
    struct stat info;
    struct group *daemon = getgrnam("daemon");
    bool ready = fstat(parent, &info) == 0 && info.st_uid == 0 && (info.st_mode & 0002) == 0
        && (!(info.st_mode & 0020) || (daemon != NULL && info.st_gid == daemon->gr_gid))
        && prepare_socket_directory(parent, owner, group, sampler);
    close(parent);
    return ready && setgroups(1, &group) == 0 && setgid(group) == 0 && setuid(owner) == 0
        && getuid() == owner && geteuid() == owner && getgid() == group && getegid() == group;
}

int main(int argc, char **argv) {
    (void)argv;
    umask(077);
    if (argc == 1 && getuid() == 0 && geteuid() == 0 && prepare_runtime()) {
        char *const command[] = { "/opt/hermes-homelab/node/bin/node", "--jitless",
            "--max-semi-space-size=1", "--max-old-space-size=24", "/opt/firewalla-health-provider/main.mjs", "serve", NULL };
        char *const clean_environment[] = { "HOME=/var/empty", "PATH=/usr/bin:/bin", NULL };
        execve(command[0], command, clean_environment);
    }
    fputs("provider_boot_unavailable\n", stderr);
    return 1;
}
#endif
