#!/bin/sh
set -eu
src="$(cd -- "$(dirname -- "$0")" && pwd)/peer.c"
test "$#" -eq 1
exec /usr/bin/xcrun clang -std=c11 -Wall -Wextra -Werror -O2 -bundle -undefined dynamic_lookup \
  -I /opt/hermes-homelab/node/include/node "$src" -o "$1"
