#!/usr/bin/env bash
set -euo pipefail
IP="${OPP_MAP_IP:-167.233.192.214}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -t oppmap.XXXXXX.tgz)"
trap 'rm -f "$TMP"' EXIT
export COPYFILE_DISABLE=1
tar -czf "$TMP" -C "$ROOT" \
  --exclude='pipelines' --exclude='docs' --exclude='.git' --exclude='deploy' \
  --exclude='__pycache__' --exclude='._*' \
  index.html results.html serve.py css js assets data lines sites README.md
scp -q "$TMP" "root@$IP:/tmp/oppmap.tgz"
ssh "root@$IP" 'bash -s' <<'EOS'
set -euo pipefail
tar -xzf /tmp/oppmap.tgz -C /var/www/opportunitymap
find /var/www/opportunitymap -name '._*' -delete
find /var/www/opportunitymap \( -name '*.json' -o -name '*.geojson' -o -name '*.js' -o -name '*.css' -o -name '*.html' -o -name '*.svg' \) -size +4k -print0 \
  | while IFS= read -r -d '' f; do gzip -9 -k -f "$f"; done
chown -R www-data:www-data /var/www/opportunitymap
curl -fsS http://127.0.0.1/healthz >/dev/null
EOS
echo "Hetzner synced → https://neuramap.167.233.192.214.sslip.io/"

# Mirror to GitHub Pages (karimcy/neuramap)
if command -v gh >/dev/null && [ -d /tmp/neuramap-gh/.git ]; then
  rsync -a --delete \
    --exclude='.git' \
    "$ROOT/index.html" "$ROOT/results.html" "$ROOT/README.md" \
    "$ROOT/css" "$ROOT/js" "$ROOT/assets" "$ROOT/data" "$ROOT/lines" "$ROOT/sites" \
    /tmp/neuramap-gh/
  touch /tmp/neuramap-gh/.nojekyll
  cd /tmp/neuramap-gh
  git add -A
  if ! git diff --cached --quiet; then
    git -c user.email='deploy@neura.energy' -c user.name='Neura Deploy' commit -m "Update OpportunityMap $(date -u +%Y-%m-%dT%H:%MZ)"
    git push origin main
    echo "GitHub Pages synced → https://karimcy.github.io/neuramap/"
  else
    echo "GitHub Pages already up to date"
  fi
fi
