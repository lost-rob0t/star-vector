#!/usr/bin/env bash
set -euo pipefail

client=${STAR_VECTOR_CLI:-./build/star-vector}
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

store=$work/vectors.json
document=$work/document.json
printf '%s\n' '{"id":"doc:cli","dataset":"tests","dtype":"document","schemaVersion":"0.10.1"}' >"$document"

"$client" init "$store" 3
"$client" upsert "$store" 3 "$document" 1,0,0
"$client" get "$store" 3 doc:cli | python3 -c 'import json,sys; assert json.load(sys.stdin)["id"] == "doc:cli"'
"$client" search "$store" 3 cosine 5 1,0,0 | python3 -c 'import json,sys; value=json.load(sys.stdin); assert value[0]["id"] == "doc:cli" and value[0]["metric"] == "cosine"'
"$client" delete "$store" 3 doc:cli | python3 -c 'import json,sys; assert json.load(sys.stdin)["deleted"] is True'

if "$client" search "$store" 3 unsupported 5 1,0,0 >/dev/null 2>&1; then
  echo "unsupported metric unexpectedly succeeded" >&2
  exit 1
fi
