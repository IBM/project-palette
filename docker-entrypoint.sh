#!/bin/sh
# Start Palette as the owner of the mounted data folder.
#
# /app/data is usually bind-mounted from the host (usage.db + archived decks).
# Under rootful Docker on Linux the container runs as root, so everything it
# wrote there would be root-owned on the host and need sudo to delete. If the
# folder belongs to someone else, drop to that uid/gid before starting.
#
# Rootless Podman/Docker (and Podman on macOS) map the host user to root, so
# the folder looks root-owned and Palette simply runs as root, as does a
# container with no mount at all (Code Engine).
set -e

uid=$(stat -c %u /app/data)
gid=$(stat -c %g /app/data)

if [ "$(id -u)" = 0 ] && [ "$uid" != 0 ]; then
  export HOME=/tmp   # LibreOffice + fontconfig want a writable home
  exec setpriv --reuid="$uid" --regid="$gid" --clear-groups python app.py "$@"
fi
exec python app.py "$@"
