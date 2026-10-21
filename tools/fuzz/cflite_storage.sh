#!/bin/bash
set -euo pipefail
storage=$RUNNER_TEMP/cflite/storage
case $1 in
clone)
    git clone --quiet --branch cflite-state --single-branch --depth 1 \
        "https://github.com/$GITHUB_REPOSITORY.git" "$storage"
    ;;
push)
    mkdir -p "$storage/logs/$GITHUB_RUN_ID"
    cp -R "$RUNNER_TEMP/cflite/logs/." "$storage/logs/$GITHUB_RUN_ID/"
    cd "$storage"
    git add --force --all -- . ':!build' ':!coverage'
    if git diff --cached --quiet; then
        exit 0
    fi
    snapshot=$(printf '%s\n' "$2 (run $GITHUB_RUN_ID)" | git -c user.name="turbohtml ClusterFuzzLite" \
        -c user.email="cflite@users.noreply.github.com" commit-tree "$(git write-tree)")
    header="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$STORAGE_TOKEN" | base64 -w0)"
    git -c "http.https://github.com/.extraheader=$header" push --quiet \
        --force-with-lease="refs/heads/cflite-state:$(git rev-parse HEAD)" origin "$snapshot:refs/heads/cflite-state"
    ;;
*)
    echo 'usage: cflite_storage.sh clone | push <commit subject>' >&2
    exit 2
    ;;
esac
