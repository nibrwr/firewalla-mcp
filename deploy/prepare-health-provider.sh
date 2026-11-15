#!/bin/sh
# Materialize the reviewed provider release as the current user.
set -eu
test "$#" -eq 1
repo="$(cd -- "$(dirname -- "$0")/.." && pwd)"
bundle="$1"
test -d "$bundle"
test -z "$(ls -A "$bundle")"
umask 077

for file in attest.py firewalla.mjs main.mjs network.mjs sdk.mjs socket.mjs state.mjs; do
  /usr/bin/install -m 0600 "$repo/provider/$file" "$bundle/$file"
done
/bin/sh "$repo/provider/build-native.sh" "$bundle/peer.node"
/usr/bin/install -m 0600 "$repo/deploy/com.nibrwr.firewalla-health-provider.plist" "$bundle/provider.plist"
/usr/bin/plutil -lint -- "$bundle/provider.plist"
