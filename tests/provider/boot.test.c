#define _DARWIN_C_SOURCE 1
#include <assert.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <unistd.h>

bool prepare_socket_directory(int parent, uid_t owner, gid_t group, uid_t sampler);

int main(void) {
    char path[] = "/private/tmp/firewalla-boot-XXXXXX";
    assert(mkdtemp(path) != NULL);
    int parent = open(path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
    assert(parent >= 0);
    uid_t owner = getuid();
    gid_t group = getgid();
    assert(owner != 0);
    assert(prepare_socket_directory(parent, owner, group, owner));
    struct stat info;
    assert(fstatat(parent, "firewalla-health", &info, AT_SYMLINK_NOFOLLOW) == 0);
    assert(info.st_uid == owner && info.st_gid == group);
    assert(S_ISDIR(info.st_mode) && (info.st_mode & 0777) == 0700);
    assert(prepare_socket_directory(parent, owner, group, owner));
    // The new provider directory can disappear at boot. Starting against a
    // clean second runtime root must recreate the same owner and ACL.
    assert(unlinkat(parent, "firewalla-health", AT_REMOVEDIR) == 0);
    assert(prepare_socket_directory(parent, owner, group, owner));
    assert(unlinkat(parent, "firewalla-health", AT_REMOVEDIR) == 0);
    assert(symlinkat(path, parent, "firewalla-health") == 0);
    assert(!prepare_socket_directory(parent, owner, group, owner));
    assert(unlinkat(parent, "firewalla-health", 0) == 0);
    assert(close(parent) == 0);
    assert(rmdir(path) == 0);
    return 0;
}
