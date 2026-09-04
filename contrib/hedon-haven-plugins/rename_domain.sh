#!/usr/bin/env bash
#
# Mechanical domain swap for the Hedon Haven Pornhub plugin.
#
# Usage:
#   ./rename_domain.sh path/to/pornhub.dart            # only the domain string
#   ./rename_domain.sh path/to/pornhub.dart --full     # domain + class/codeName/prettyName
#
# A .bak backup is written next to the original file.
#
set -euo pipefail

FILE="${1:?usage: rename_domain.sh <file.dart> [--full]}"
MODE="${2:-}"

cp "$FILE" "$FILE.bak"

# --- what the user asked for: only the "pornhub.com" inscription -------------
# Covers: iconUrl, serviceUrl, description, handleUrls[], _videoEndpoint,
#         _searchEndpoint, _channelEndpoint, _modelEndpoint, every
#         "Referer": "https://www.pornhub.com/" header, init(), getHomePage(),
#         getComments() (/comment/show + view-replies button), getAuthorPage().
sed -i 's/pornhub\.com/porntrex.tv/g' "$FILE"

if [[ "$MODE" == "--full" ]]; then
  sed -i \
    -e 's/Pornhub\.com/Porntrex.tv/g' \
    -e 's/com\.hedon_haven\.pornhub/com.hedon_haven.porntrex/g' \
    -e 's/PornhubPlugin/PorntrexPlugin/g' \
    -e 's/\bPornhub\b/Porntrex/g' \
    "$FILE"
fi

echo "Done. Occurrences left of 'pornhub':"
grep -in "pornhub" "$FILE" || echo "  (none)"
echo
echo "NOTE: cookie keys such as accessAgeDisclaimerPH and the testingMap video"
echo "IDs are Pornhub-specific and were intentionally left untouched."
