#!/usr/bin/env bash
# Builds the bot image and saves it to a tarball for `docker load` on the host.
#
# The image is tagged twice: with a version derived from the current commit (the same string
# .github/workflows/tag.yml tags that commit with, e.g. v2026.10.09-c97a8ec) and with `latest`.
# Older versions stay loaded on the host under their own tag, so a stack can be rolled back by
# setting IMAGE_TAG to one of them.
set -euo pipefail
cd "$(dirname "$0")"

VERSION="v$(TZ=UTC git show -s --format=%cd --date=format-local:%Y.%m.%d HEAD)-$(git rev-parse --short=7 HEAD)"
# Uncommitted changes mean the image isn't what that commit describes; say so in the tag.
if [ -n "$(git status --porcelain)" ]; then
    VERSION="$VERSION-dirty"
fi

ENGINE="${ENGINE:-$(command -v podman || command -v docker)}"
TARBALL="gulp-bot-$VERSION.tar"

"$ENGINE" build -t "gulp-bot:$VERSION" -t gulp-bot:latest .

# podman needs --multi-image-archive to write more than one tag into a docker-format archive.
if [ "$(basename "$ENGINE")" = podman ]; then
    "$ENGINE" save --multi-image-archive -o "$TARBALL" "gulp-bot:$VERSION" gulp-bot:latest
else
    "$ENGINE" save -o "$TARBALL" "gulp-bot:$VERSION" gulp-bot:latest
fi

echo
echo "Built gulp-bot:$VERSION (also tagged latest)"
echo "Saved to $TARBALL; load it on the host with: docker load -i $TARBALL"
