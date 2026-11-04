#!/bin/bash
# Clone the private storage repository into the ClusterFuzzLite filestore, or push what a run added to it.
# usage: cflite_storage.sh clone | push <commit subject>
set -euo pipefail
storage=$RUNNER_TEMP/cflite/storage
: "${STORAGE_REPO:?vars.CFLITE_STORAGE_REPO is not set}"
: "${STORAGE_TOKEN:?secrets.CFLITE_STORAGE_TOKEN is not set}"
# an extra header keeps the token out of the remote URL, which git prints in errors and stores in .git/config
header="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$STORAGE_TOKEN" | base64 -w0)"
case $1 in
clone)
    git -c "http.https://github.com/.extraheader=$header" clone --quiet "https://github.com/$STORAGE_REPO.git" "$storage"
    ;;
push)
    mkdir -p "$storage/logs/$GITHUB_RUN_ID"
    cp -R "$RUNNER_TEMP/cflite/logs/." "$storage/logs/$GITHUB_RUN_ID/"
    cd "$storage"
    git add --force --all -- . ':!build' ':!coverage'
    if git diff --cached --quiet; then
        exit 0
    fi
    git -c user.name="turbohtml ClusterFuzzLite" -c user.email="cflite@users.noreply.github.com" commit --quiet \
        -m "$2 (run $GITHUB_RUN_ID)"
    # GitHub answers a push with a 5xx when its backend hiccups; a concurrent run's push also rejects this one, and
    # rebasing onto it before the retry lets the commit land
    for _ in 1 2 3; do
        git -c "http.https://github.com/.extraheader=$header" push --quiet && exit 0
        sleep 30
        git -c user.name="turbohtml ClusterFuzzLite" -c user.email="cflite@users.noreply.github.com" \
            -c "http.https://github.com/.extraheader=$header" pull --quiet --rebase
    done
    exit 1
    ;;
*)
    echo 'usage: cflite_storage.sh clone | push <commit subject>' >&2
    exit 2
    ;;
esac
