#!/usr/bin/env bash
# Secret scan for the jev-change-monitor repository.
# Fails on anything that looks like a real credential, token, key or env file
# with a value. Static FIXTURE secrets are allowed only when clearly labeled.
set -u

cd "$(dirname "$0")/.."
repo="$(pwd)"
fail=0

EXCLUDES=(--exclude-dir=.git --exclude='secret_scan.sh' --exclude=test.sh)

echo "== secret scan: $repo =="

# 1. No real .env files with values
env_files=$(find . -name '.env' -o -name '.env.*' | grep -v '\.env\.example$' || true)
if [ -n "$env_files" ]; then
  echo "FAIL: unexpected env files present: $env_files"
  fail=1
fi

# 2. Credential patterns must not appear in committed text
patterns=(
  'sk-[A-Za-z0-9]\{20,\}'
  'gh[pousr]_[A-Za-z0-9]\{20,\}'
  'rn_live_[A-Za-z0-9]\{16,\}'
  'rn_test_[A-Za-z0-9]\{16,\}'
  'AKIA[0-9A-Z]\{16\}'
  'xox[baprs]-'
)
key_heads=('BEGIN OPENSSH PRIVATE KEY' 'BEGIN RSA PRIVATE KEY')
for pat in "${patterns[@]}" "${key_heads[@]}"; do
  hits=$(grep -RIn "${EXCLUDES[@]}" -e "$pat" . || true)
  if [ -n "$hits" ]; then
    echo "FAIL: secret-like pattern '$pat' found:"
    echo "$hits"
    fail=1
  fi
done

# 3. Long 64-hex blobs outside sha256-labeled fields
blobs=$(grep -RIn "${EXCLUDES[@]}" -e '[A-Fa-f0-9]{64}' . || true)
if [ -n "$blobs" ]; then
  leaks=$(echo "$blobs" | grep -v 'sha256' || true)
  if [ -n "$leaks" ]; then
    echo "FAIL: suspicious 64-hex blobs outside sha256-labeled fields:"
    echo "$leaks"
    fail=1
  fi
fi

# 4. The fixture secret must be visibly labeled as a fixture whenever used
fixture_secret=$(grep -RIn 'fixture:recipe-demo-change-me-2026' "${EXCLUDES[@]}" . || true)
if [ -z "$fixture_secret" ]; then
  echo "WARN: no labeled fixture secret reference found"
else
  echo "info: fixture secret references are labeled fixtures: $(echo "$fixture_secret" | wc -l) line(s)"
fi

if [ "$fail" -eq 0 ]; then
  echo "secret scan: PASS"
  exit 0
fi
echo "secret scan: FAIL"
exit 1