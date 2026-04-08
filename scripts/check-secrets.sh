#!/usr/bin/env bash
# scripts/check-secrets.sh
#
# Lightweight secret scanner. Run before pushing to avoid leaking credentials
# into the public repo. Exits non-zero if it finds anything suspicious.
#
# Usage:
#   ./scripts/check-secrets.sh                # scan tracked files in HEAD
#   ./scripts/check-secrets.sh --staged       # scan only staged files
#
# Optional: install as a git pre-push hook with
#   ln -sf ../../scripts/check-secrets.sh .git/hooks/pre-push

set -euo pipefail
cd "$(dirname "$0")/.."

RED=$(printf '\033[0;31m')
YEL=$(printf '\033[0;33m')
GRN=$(printf '\033[0;32m')
NC=$(printf '\033[0m')

errors=0

note() { echo "  ${YEL}!${NC} $*"; errors=$((errors+1)); }
fail() { echo "  ${RED}✗${NC} $*"; errors=$((errors+1)); }
ok()   { echo "  ${GRN}✓${NC} $*"; }

# Choose the file list to scan: HEAD tree by default, --staged if asked
if [ "${1:-}" = "--staged" ]; then
    files=$(git diff --cached --name-only --diff-filter=ACMR)
    [ -z "$files" ] && { echo "No staged files."; exit 0; }
else
    files=$(git ls-files)
fi

echo "==> Scanning $(echo "$files" | wc -l | tr -d ' ') tracked file(s)..."

# 1) .env must NEVER be tracked
if echo "$files" | grep -qx '.env'; then
    fail ".env is tracked! Remove with: git rm --cached .env"
fi

# 2) The rendered Caddyfile must NEVER be tracked (only the template)
if echo "$files" | grep -qx 'caddy/Caddyfile'; then
    fail "caddy/Caddyfile is tracked but should be in .gitignore (it's generated)"
fi

# 3) Look for high-confidence secret patterns inside the tracked files.
#    We deliberately skip .env.example because it's expected to contain placeholders.
scan_files=$(echo "$files" | grep -v -E '(^|/)\.env\.example$|(^|/)check-secrets\.sh$' || true)
[ -z "$scan_files" ] && scan_files=$(echo "$files")

# Mistral API keys are 32-char alphanumeric. Match the assignment, then verify
# the value is not the documented placeholder.
while IFS= read -r f; do
    [ -f "$f" ] || continue
    while IFS=: read -r line val; do
        case "$val" in
            *your_mistral_api_key_here*|*placeholder*|*REPLACE*|*replace_me*|*change_me*) ;;
            *)
                if [ -n "$val" ] && echo "$val" | grep -Eq '^[A-Za-z0-9]{20,}$'; then
                    fail "$f:${line%%:*} → looks like a real Mistral API key"
                fi
                ;;
        esac
    done < <(grep -nE '^[[:space:]]*MISTRAL_API_KEY[[:space:]]*=[[:space:]]*([^[:space:]"]+)' "$f" 2>/dev/null \
                | sed -E 's/^([0-9]+):[[:space:]]*MISTRAL_API_KEY[[:space:]]*=[[:space:]]*//')
done <<< "$scan_files"

# JWT secret: 64-hex strings outside .env.example
while IFS= read -r f; do
    [ -f "$f" ] || continue
    matches=$(grep -nE '^[[:space:]]*(JWT_SECRET|AUTHELIA_JWT_SECRET|AUTHELIA_SESSION_SECRET|AUTHELIA_STORAGE_KEY)[[:space:]]*=[[:space:]]*[a-f0-9]{32,}' "$f" 2>/dev/null || true)
    if [ -n "$matches" ]; then
        while IFS= read -r m; do
            fail "$f → embedded high-entropy secret: ${m%% *}"
        done <<< "$matches"
    fi
done <<< "$scan_files"

# Generic high-confidence patterns
patterns=(
    'AKIA[0-9A-Z]{16}'                 # AWS Access Key ID
    'aws_secret_access_key[[:space:]]*=[[:space:]]*[A-Za-z0-9/+=]{40}'
    'ghp_[A-Za-z0-9]{36}'              # GitHub PAT (classic)
    'github_pat_[A-Za-z0-9_]{82}'      # GitHub PAT (fine-grained)
    'xox[abprs]-[A-Za-z0-9-]{10,}'     # Slack tokens
    '-----BEGIN (RSA|OPENSSH|EC|DSA|PGP) PRIVATE KEY-----'
)
for pat in "${patterns[@]}"; do
    while IFS= read -r f; do
        [ -f "$f" ] || continue
        if grep -nIE "$pat" "$f" >/dev/null 2>&1; then
            hit=$(grep -nIE "$pat" "$f")
            fail "$f → matches forbidden pattern: $pat"
            echo "        $hit"
        fi
    done <<< "$scan_files"
done

# Demo password leaking outside .env.example
while IFS= read -r f; do
    [ -f "$f" ] || continue
    if grep -qE '!demo123\$' "$f" 2>/dev/null; then
        note "$f → contains the dev demo password '!demo123\$' — rotate before public release"
    fi
done <<< "$scan_files"

echo
if [ "$errors" -gt 0 ]; then
    echo "${RED}✗ Found $errors issue(s). Refusing to push.${NC}"
    echo "  Fix the items above (rotate secrets, remove tracked files, etc.)"
    exit 1
fi
ok "No tracked secrets found."
exit 0
