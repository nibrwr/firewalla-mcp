#!/bin/sh
set -eu
src="$(cd -- "$(dirname -- "$0")" && pwd)"
test "$#" -eq 1 || test "$#" -eq 2
/usr/bin/xcrun clang -std=c11 -Wall -Wextra -Werror -O2 -bundle -undefined dynamic_lookup \
  -I /opt/hermes-homelab/node/include/node "$src/peer.c" -o "$1"
if [ "$#" -eq 2 ]; then
  /usr/bin/xcrun clang -std=c11 -Wall -Wextra -Werror -O2 "$src/boot.c" -o "$2"
fi
