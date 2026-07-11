#!/bin/bash
set -euo pipefail

INSTALL_ROOT="$HOME/Library/Application Support/Samosa"
MODELS=""
ACCEPT_RESTRICTED=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-root) INSTALL_ROOT="$2"; shift 2 ;;
    --models) MODELS="$2"; shift 2 ;;
    --accept-restricted-models) ACCEPT_RESTRICTED=true; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

PYTHON="$INSTALL_ROOT/runtime/Sammie-Roto-2/.venv/bin/python"
DOWNLOADER="$INSTALL_ROOT/installer/download_models.py"
CONFIG="$INSTALL_ROOT/cep/config.json"
[[ -x "$PYTHON" && -f "$DOWNLOADER" ]] || { echo "Samosa is not installed for this user." >&2; exit 1; }

if [[ -z "$MODELS" ]]; then
  cat <<'EOF'
Samosa model packs
  1  SAM2 Base
  2  SAM2 Large
  3  EfficientTAM
  4  MatAnyone (noncommercial terms)
  5  MatAnyone2 (noncommercial terms)
  6  VideoMaMa (restricted and separate SVD VAE terms)
  7  MiniMax Remover (noncommercial terms)
  A  All model packs
EOF
  read -r -p "Select packs (comma-separated, for example 1,3): " choices
  IFS=',' read -r -a choice_array <<< "$choices"
  keys=()
  for choice in "${choice_array[@]}"; do
    choice="${choice//[[:space:]]/}"
    choice_upper="$(printf '%s' "$choice" | tr '[:lower:]' '[:upper:]')"
    case "$choice_upper" in
      1) keys+=(Base) ;;
      2) keys+=(Large) ;;
      3) keys+=(Efficient) ;;
      4) keys+=(matanyone) ;;
      5) keys+=(matanyone2) ;;
      6) keys+=(videomama svd_vae) ;;
      7) keys+=(minimax_transformer minimax_vae) ;;
      A) keys=(Large Base Efficient matanyone matanyone2 minimax_transformer minimax_vae videomama svd_vae) ;;
      *) echo "Unknown selection: $choice" >&2; exit 2 ;;
    esac
  done
  MODELS="$(IFS=','; echo "${keys[*]}")"
fi

if [[ ",$MODELS," =~ ,(matanyone|matanyone2|minimax_transformer|minimax_vae|videomama|svd_vae)(,|$) ]] && [[ "$ACCEPT_RESTRICTED" != true ]]; then
  echo
  echo "Selected packs have noncommercial or additional model terms."
  echo "Review /Applications/Samosa/Resources/THIRD_PARTY_NOTICES.md before continuing."
  read -r -p "Type ACCEPT to download these model packs: " acceptance
  [[ "$acceptance" == "ACCEPT" ]] || { echo "Model terms were not accepted." >&2; exit 3; }
  ACCEPT_RESTRICTED=true
fi

"$PYTHON" "$DOWNLOADER" --repo "$INSTALL_ROOT/runtime/Sammie-Roto-2" --models "$MODELS"
if [[ "$ACCEPT_RESTRICTED" == true && -f "$CONFIG" ]]; then
  "$PYTHON" - "$CONFIG" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    config = json.load(handle)
config["accepted_restricted_models"] = True
with open(path, "w", encoding="utf-8") as handle:
    json.dump(config, handle, indent=2)
PY
fi
echo "Selected model packs are ready."
