#!/usr/bin/env bash
set -euo pipefail
source_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
nix build --no-update-lock-file --no-link "$source_root#default"
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
cd "$work"
export STAR_VECTOR_FLAKE="$source_root"
# Reuse the complete synthetic CLI fixture through the actual app output.
cat > run-vector <<'SH'
#!/usr/bin/env bash
exec nix run --no-update-lock-file "$STAR_VECTOR_FLAKE#default" -- "$@"
SH
chmod +x run-vector
STAR_VECTOR_CLI="$work/run-vector" bash "$source_root/tests/test_cli.sh"
