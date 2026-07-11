#!/bin/bash
set -euo pipefail

SAMMIE_COMMIT="129a0a54950d71b535cdcdbd06090c5583e293d9"
SAMMIE_ARCHIVE_URL="https://github.com/Zarxrax/Sammie-Roto-2/archive/${SAMMIE_COMMIT}.zip"
SAMMIE_ARCHIVE_SHA256="71CFC39AC389DA6C138E956881DEA1A811F473F63052FB026B8E24CFE28AF62B"
ALL_MODELS="Large,Base,Efficient,matanyone,matanyone2,minimax_transformer,minimax_vae,videomama,svd_vae"
RESTRICTED_PATTERN='(^|,)(matanyone|matanyone2|minimax_transformer|minimax_vae|videomama|svd_vae)(,|$)'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTALL_ROOT="$HOME/Library/Application Support/Samosa"
INSTALL_MODE="Standard"
MODELS="Base"
PORT=43831
ACCEPT_RESTRICTED=false
DRY_RUN=false
UNINSTALL=false

usage() {
  cat <<'EOF'
Usage: bootstrap.sh [options]
  --install-root PATH
  --mode Standard|Complete|Custom
  --models Base,Large,...|all
  --accept-restricted-models
  --port NUMBER
  --dry-run
  --uninstall
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-root) INSTALL_ROOT="$2"; shift 2 ;;
    --mode) INSTALL_MODE="$2"; shift 2 ;;
    --models) MODELS="$2"; shift 2 ;;
    --accept-restricted-models) ACCEPT_RESTRICTED=true; shift ;;
    --port) PORT="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    --uninstall) UNINSTALL=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$INSTALL_MODE" in Standard|Complete|Custom) ;; *) echo "Invalid install mode: $INSTALL_MODE" >&2; exit 2 ;; esac
MODELS_LOWER="$(printf '%s' "$MODELS" | tr '[:upper:]' '[:lower:]')"
if [[ "$INSTALL_MODE" == "Complete" || "$MODELS_LOWER" == "all" ]]; then MODELS="$ALL_MODELS"; fi
if [[ "$INSTALL_MODE" == "Standard" && -z "$MODELS" ]]; then MODELS="Base"; fi

IFS=',' read -r -a MODEL_ARRAY <<< "$MODELS"
declare -a CLEAN_MODELS=()
CLEAN_MODELS_CSV=""
for model in "${MODEL_ARRAY[@]}"; do
  model="${model//[[:space:]]/}"
  [[ -z "$model" ]] && continue
  case ",$ALL_MODELS," in
    *",$model,"*) ;;
    *) echo "Unknown model key: $model" >&2; exit 2 ;;
  esac
  case ",$CLEAN_MODELS_CSV," in
    *",$model,"*) ;;
    *)
      CLEAN_MODELS+=("$model")
      [[ -n "$CLEAN_MODELS_CSV" ]] && CLEAN_MODELS_CSV+=","
      CLEAN_MODELS_CSV+="$model"
      ;;
  esac
done
MODELS="$CLEAN_MODELS_CSV"
[[ -n "$MODELS" ]] || { echo "Select at least one model." >&2; exit 2; }

INCLUDES_RESTRICTED=false
if [[ ",$MODELS," =~ $RESTRICTED_PATTERN ]]; then INCLUDES_RESTRICTED=true; fi
if [[ "$INCLUDES_RESTRICTED" == true && "$ACCEPT_RESTRICTED" != true ]]; then
  echo "Restricted model packs require explicit acceptance of their noncommercial license terms." >&2
  exit 3
fi

if [[ "$DRY_RUN" == true ]]; then
  model_json=""
  for model in "${CLEAN_MODELS[@]}"; do
    [[ -n "$model_json" ]] && model_json+=","
    model_json+="\"$model\""
  done
  printf '{"install_root":"%s","platform":"macos","backend":"mps-or-cpu","install_mode":"%s","models":[%s],"includes_restricted_models":%s,"sammie_commit":"%s","sammie_archive_sha256":"%s","port":%s}\n' \
    "$INSTALL_ROOT" "$INSTALL_MODE" "$model_json" "$INCLUDES_RESTRICTED" "$SAMMIE_COMMIT" "$SAMMIE_ARCHIVE_SHA256" "$PORT"
  exit 0
fi

EXPECTED_ROOT="$HOME/Library/Application Support/Samosa"
if [[ "$INSTALL_ROOT" != "$EXPECTED_ROOT" ]]; then
  echo "For safety, the packaged macOS installer only supports $EXPECTED_ROOT" >&2
  exit 4
fi

CEP_TARGET="$HOME/Library/Application Support/Adobe/CEP/extensions/com.tenet.samosa.roto"
RUNTIME_ROOT="$INSTALL_ROOT/runtime/Sammie-Roto-2"
CEP_ROOT="$INSTALL_ROOT/cep"
LOG_DIR="$HOME/Library/Logs/Samosa"

remove_cep_registration() {
  if [[ -L "$CEP_TARGET" ]]; then
    target="$(readlink "$CEP_TARGET")"
    [[ "$target" == "$CEP_ROOT" ]] && rm "$CEP_TARGET"
  elif [[ -f "$CEP_TARGET/CSXS/manifest.xml" ]] && grep -q 'ExtensionBundleId="com.tenet.samosa.roto"' "$CEP_TARGET/CSXS/manifest.xml"; then
    rm -rf "$CEP_TARGET"
  fi
}

if [[ "$UNINSTALL" == true ]]; then
  remove_cep_registration
  rm -rf "$INSTALL_ROOT" "$LOG_DIR"
  echo "Removed Samosa user runtime and CEP registration."
  exit 0
fi

mkdir -p "$INSTALL_ROOT" "$LOG_DIR" "$INSTALL_ROOT/logs"
exec > >(tee -a "$INSTALL_ROOT/logs/installer.log") 2>&1
echo "Installing Samosa $INSTALL_MODE mode for macOS"

if health="$(curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" 2>/dev/null)"; then
  if [[ "$health" == *'"service": "samosa-ae"'* || "$health" == *'"service":"samosa-ae"'* ]]; then
    curl -fsS --max-time 2 -X POST -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:$PORT/shutdown" >/dev/null || true
    sleep 1
  else
    echo "Port $PORT is occupied by another service." >&2
    exit 5
  fi
fi

rm -rf "$CEP_ROOT" "$INSTALL_ROOT/backend" "$INSTALL_ROOT/installer"
mkdir -p "$CEP_ROOT" "$INSTALL_ROOT/backend" "$INSTALL_ROOT/installer/macos"
cp -R "$SOURCE_ROOT/panel/." "$CEP_ROOT/"
rm -f "$CEP_ROOT/config.json"
cp -R "$SOURCE_ROOT/backend/." "$INSTALL_ROOT/backend/"
cp "$SOURCE_ROOT/installer/download_models.py" "$INSTALL_ROOT/installer/download_models.py"
cp "$SOURCE_ROOT/installer/macos/bootstrap.sh" "$INSTALL_ROOT/installer/macos/bootstrap.sh"
cp "$SOURCE_ROOT/installer/macos/manage-models.sh" "$INSTALL_ROOT/installer/macos/manage-models.sh"
chmod +x "$INSTALL_ROOT/installer/macos/"*.sh

SOURCE_MARKER="$RUNTIME_ROOT/.samosa-upstream.json"
RUNTIME_CURRENT=false
if [[ -f "$RUNTIME_ROOT/pyproject.toml" && -f "$SOURCE_MARKER" ]] && grep -q "$SAMMIE_COMMIT" "$SOURCE_MARKER"; then
  RUNTIME_CURRENT=true
fi

if [[ "$RUNTIME_CURRENT" != true ]]; then
  DOWNLOAD_DIR="$INSTALL_ROOT/downloads"
  ARCHIVE="$DOWNLOAD_DIR/Sammie-Roto-2-$SAMMIE_COMMIT.zip"
  EXTRACT_ROOT="$DOWNLOAD_DIR/extract-$SAMMIE_COMMIT"
  mkdir -p "$DOWNLOAD_DIR"
  if [[ -f "$ARCHIVE" ]] && [[ "$(shasum -a 256 "$ARCHIVE" | awk '{print toupper($1)}')" != "$SAMMIE_ARCHIVE_SHA256" ]]; then rm "$ARCHIVE"; fi
  if [[ ! -f "$ARCHIVE" ]]; then
    echo "Downloading pinned Sammie-Roto-2 source..."
    curl -fL --retry 3 --output "$ARCHIVE" "$SAMMIE_ARCHIVE_URL"
  fi
  actual_hash="$(shasum -a 256 "$ARCHIVE" | awk '{print toupper($1)}')"
  [[ "$actual_hash" == "$SAMMIE_ARCHIVE_SHA256" ]] || { echo "Sammie-Roto-2 archive checksum mismatch." >&2; exit 6; }
  rm -rf "$EXTRACT_ROOT"
  mkdir -p "$EXTRACT_ROOT"
  ditto -x -k "$ARCHIVE" "$EXTRACT_ROOT"
  extracted="$(find "$EXTRACT_ROOT" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  [[ -n "$extracted" && -f "$extracted/pyproject.toml" ]] || { echo "Unexpected Sammie-Roto-2 archive structure." >&2; exit 7; }
  rm -rf "$RUNTIME_ROOT"
  mkdir -p "$(dirname "$RUNTIME_ROOT")"
  mv "$extracted" "$RUNTIME_ROOT"
  rm -rf "$EXTRACT_ROOT"
  printf '{"commit":"%s","source":"%s"}\n' "$SAMMIE_COMMIT" "$SAMMIE_ARCHIVE_URL" > "$SOURCE_MARKER"
fi

UV_DIR="$RUNTIME_ROOT/.uv"
UV_EXE="$UV_DIR/uv"
PYTHON="$RUNTIME_ROOT/.venv/bin/python"
export UV_PYTHON_INSTALL_DIR="$UV_DIR/python"
export UV_CACHE_DIR="$UV_DIR/uv_cache"
if [[ ! -x "$UV_EXE" ]]; then
  echo "Installing isolated uv runtime..."
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$UV_DIR" sh
fi
"$UV_EXE" python install --no-bin 3.12
"$UV_EXE" sync --extra cpu --directory "$RUNTIME_ROOT"
[[ -x "$PYTHON" ]] || { echo "Sammie-Roto-2 Python environment was not created." >&2; exit 8; }

if [[ -n "$MODELS" ]]; then
  "$PYTHON" "$INSTALL_ROOT/installer/download_models.py" --repo "$RUNTIME_ROOT" --models "$MODELS"
fi

rm -rf "$CEP_ROOT/backend"
cp -R "$INSTALL_ROOT/backend" "$CEP_ROOT/backend"
"$PYTHON" - "$CEP_ROOT/config.json" "$RUNTIME_ROOT" "$PYTHON" "$PORT" "$ACCEPT_RESTRICTED" <<'PY'
import json, sys
path, repo, python, port, accepted = sys.argv[1:]
with open(path, "w", encoding="utf-8") as handle:
    json.dump({
        "port": int(port), "repo": repo, "python": python,
        "accepted_restricted_models": accepted == "true", "platform": "macos",
        "environment": {"PYTORCH_ENABLE_MPS_FALLBACK": "1"},
    }, handle, indent=2)
PY
"$PYTHON" -m py_compile "$CEP_ROOT/backend/service.py"

for version in 11 12 13 14; do defaults write "com.adobe.CSXS.$version" PlayerDebugMode 1; done
remove_cep_registration
mkdir -p "$(dirname "$CEP_TARGET")"
ln -s "$CEP_ROOT" "$CEP_TARGET"

"$PYTHON" - "$INSTALL_ROOT/install-state.json" "$INSTALL_MODE" "$MODELS" "$PORT" <<'PY'
import datetime, json, sys
path, mode, models, port = sys.argv[1:]
with open(path, "w", encoding="utf-8") as handle:
    json.dump({
        "version": "1.2.0", "installed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "platform": "macos", "backend": "mps-or-cpu", "install_mode": mode,
        "models_requested": [item for item in models.split(",") if item],
        "sammie_commit": "129a0a54950d71b535cdcdbd06090c5583e293d9", "port": int(port),
    }, handle, indent=2)
PY

echo "Samosa installation completed. Restart After Effects and open Window > Extensions (Legacy) > Samosa."
