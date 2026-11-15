#include <membership.h>
#include <node_api.h>
#include <stdbool.h>
#include <stdint.h>
#include <sys/acl.h>
#include <sys/file.h>
#include <unistd.h>

static napi_value error(napi_env env) {
    napi_throw_error(env, NULL, "local identity or lock unavailable");
    return NULL;
}

static bool descriptor(napi_env env, napi_callback_info info, int32_t *fd) {
    size_t count = 1;
    napi_value arg;
    return napi_get_cb_info(env, info, &count, &arg, NULL, NULL) == napi_ok
        && count == 1 && napi_get_value_int32(env, arg, fd) == napi_ok && *fd >= 0;
}

static napi_value peer_uid(napi_env env, napi_callback_info info) {
    int32_t fd;
    uid_t uid;
    gid_t gid;
    napi_value result;
    if (!descriptor(env, info, &fd) || getpeereid(fd, &uid, &gid) != 0
        || napi_create_uint32(env, uid, &result) != napi_ok) {
        return error(env);
    }
    return result;
}

static napi_value claim_lock(napi_env env, napi_callback_info info) {
    int32_t fd;
    napi_value result;
    if (!descriptor(env, info, &fd) || flock(fd, LOCK_EX | LOCK_NB) != 0
        || napi_get_undefined(env, &result) != napi_ok) {
        return error(env);
    }
    return result;
}

static napi_value grant_socket(napi_env env, napi_callback_info info) {
    size_t count = 2, length;
    napi_value args[2], result;
    char path[104];
    uint32_t uid;
    uuid_t subject;
    acl_t acl;
    acl_entry_t entry;
    acl_permset_t permissions;
    if (napi_get_cb_info(env, info, &count, args, NULL, NULL) != napi_ok || count != 2
        || napi_get_value_string_utf8(env, args[0], NULL, 0, &length) != napi_ok
        || length == 0 || length >= sizeof(path)
        || napi_get_value_string_utf8(env, args[0], path, sizeof(path), NULL) != napi_ok
        || napi_get_value_uint32(env, args[1], &uid) != napi_ok || uid == 0
        || mbr_uid_to_uuid(uid, subject) != 0) {
        return error(env);
    }
    acl = acl_init(1);
    if (acl == NULL) {
        return error(env);
    }
    bool ok = acl_create_entry(&acl, &entry) == 0
        && acl_set_tag_type(entry, ACL_EXTENDED_ALLOW) == 0
        && acl_set_qualifier(entry, subject) == 0
        && acl_get_permset(entry, &permissions) == 0
        && acl_add_perm(permissions, ACL_WRITE_DATA) == 0
        && acl_set_file(path, ACL_TYPE_EXTENDED, acl) == 0;
    acl_free(acl);
    if (!ok || napi_get_undefined(env, &result) != napi_ok) {
        return error(env);
    }
    return result;
}

NAPI_MODULE_INIT() {
    const napi_property_descriptor entries[] = {
        { "peerUid", NULL, peer_uid, NULL, NULL, NULL, napi_default, NULL },
        { "claimLock", NULL, claim_lock, NULL, NULL, NULL, napi_default, NULL },
        { "grantSocket", NULL, grant_socket, NULL, NULL, NULL, napi_default, NULL },
    };
    if (napi_define_properties(env, exports, 3, entries) != napi_ok) {
        return error(env);
    }
    return exports;
}
