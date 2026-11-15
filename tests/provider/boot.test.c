#define _DARWIN_C_SOURCE 1
#include <assert.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdlib.h>
#include <sys/acl.h>
#include <sys/stat.h>
#include <unistd.h>

bool prepare_socket_directory(int staging, int parent, uid_t owner, gid_t group, uid_t sampler);

int main(void) {
    char path[] = "/private/tmp/firewalla-boot-XXXXXX";
    char private_path[] = "/private/tmp/firewalla-stage-XXXXXX";
    assert(mkdtemp(path) != NULL);
    assert(mkdtemp(private_path) != NULL);
    int parent = open(path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
    int staging = open(private_path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
    assert(parent >= 0 && staging >= 0);
    uid_t owner = getuid();
    gid_t group = getgid();
    assert(owner != 0);
    assert(prepare_socket_directory(staging, parent, owner, group, owner));
    struct stat info;
    assert(fstatat(parent, "firewalla-health", &info, AT_SYMLINK_NOFOLLOW) == 0);
    assert(info.st_uid == owner && info.st_gid == group);
    assert(S_ISDIR(info.st_mode) && (info.st_mode & 0777) == 0700);
    assert(prepare_socket_directory(staging, parent, owner, group, owner));
    // The new provider directory can disappear at boot. Starting against a
    // clean second runtime root must recreate the same owner and ACL.
    assert(unlinkat(parent, "firewalla-health", AT_REMOVEDIR) == 0);
    // A failed start can leave its unpublished directory ready to complete.
    assert(mkdirat(staging, ".socket-dir.next", 0700) == 0);
    assert(prepare_socket_directory(staging, parent, owner, group, owner));
    assert(fstatat(staging, ".socket-dir.next", &info, AT_SYMLINK_NOFOLLOW) != 0);
    assert(unlinkat(parent, "firewalla-health", AT_REMOVEDIR) == 0);
    assert(mkdirat(parent, "firewalla-health", 0700) == 0);
    assert(fchownat(parent, "firewalla-health", owner, group, AT_SYMLINK_NOFOLLOW) == 0);
    assert(!prepare_socket_directory(staging, parent, owner, group, owner));
    int occupied = openat(parent, "firewalla-health", O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
    assert(occupied >= 0);
    acl_t original = acl_get_fd_np(occupied, ACL_TYPE_EXTENDED);
    if (original != NULL) {
        acl_entry_t entry;
        assert(acl_get_entry(original, ACL_FIRST_ENTRY, &entry) != 0);
        acl_free(original);
    }
    assert(close(occupied) == 0);
    assert(unlinkat(parent, "firewalla-health", AT_REMOVEDIR) == 0);
    assert(symlinkat(path, parent, "firewalla-health") == 0);
    assert(!prepare_socket_directory(staging, parent, owner, group, owner));
    assert(unlinkat(parent, "firewalla-health", 0) == 0);
    assert(close(parent) == 0);
    assert(close(staging) == 0);
    assert(rmdir(path) == 0);
    assert(rmdir(private_path) == 0);
    return 0;
}
