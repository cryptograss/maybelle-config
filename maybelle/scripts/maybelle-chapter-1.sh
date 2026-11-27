#!/bin/bash
# Chapter 1: Create symlink, clone repo, run ansible
# Run from your laptop (requires repo checkout)
#
# This script:
#   - Copies vault password from local env var to maybelle
#   - Connects via mosh/tmux
#   - Creates symlink from /mnt/persist to Hetzner volume
#   - Installs git and ansible
#   - Clones maybelle-config repo to the persistent volume
#   - Runs the ansible playbook
#
# Prerequisites:
#   - maybelle-chapter-0.sh has been run
#   - ANSIBLE_VAULT_PASSWORD_FILE env var set on your laptop
#
# Usage:
#   ./maybelle/scripts/maybelle-chapter-1.sh [--rebuild] [--ssh]
#
# Options:
#   --rebuild    Force rebuild of docker images (no cache)
#   --ssh        Attach via TCP ssh instead of mosh (mosh also falls back to ssh automatically if UDP is blocked)

set -e

# Parse command line arguments
REBUILD_IMAGES=""
USE_SSH=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --rebuild)
            REBUILD_IMAGES="true"
            shift
            ;;
        --ssh)
            USE_SSH="true"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--rebuild] [--ssh]"
            exit 1
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../config.yml"

# Parse config.yml
get_config() {
    grep "^$1:" "$CONFIG_FILE" | sed 's/^[^:]*: *"\?\([^"]*\)"\?/\1/'
}

VOLUME_ID=$(get_config hetzner_volume_id)
MOUNT_POINT=$(get_config mount_point)
HOST=$(get_config host)
USER=$(get_config user)
VAULT_PASSWORD_FILE=$(get_config vault_password_file)

# Who's deploying, for the Moods and the deploy log: DEPLOY_USER if set, else
# this laptop's login (USER above is the SSH user). Letters, digits, . _ - only.
DEPLOY_BY=$(printf '%s' "${DEPLOY_USER:-$(id -un)}" | tr -cd 'A-Za-z0-9._-' | cut -c1-40)

HETZNER_VOLUME_PATH="/mnt/HC_Volume_${VOLUME_ID}"
REPO_DIR="${MOUNT_POINT}/maybelle-config"
SESSION_NAME="chapter-1"

VOLUME_DEVICE="/dev/disk/by-id/scsi-0HC_Volume_${VOLUME_ID}"

echo "=== Maybelle Chapter 1 ==="
echo "Host: ${USER}@${HOST}"
echo "Volume: ${HETZNER_VOLUME_PATH} -> ${MOUNT_POINT}"
echo ""

# Check local vault password file
if [ -z "$ANSIBLE_VAULT_PASSWORD_FILE" ]; then
    echo "ERROR: ANSIBLE_VAULT_PASSWORD_FILE env var not set"
    exit 1
fi

if [ ! -f "$ANSIBLE_VAULT_PASSWORD_FILE" ]; then
    echo "ERROR: Vault password file not found at $ANSIBLE_VAULT_PASSWORD_FILE"
    exit 1
fi

# Mount volume if needed, and if a deploy is already running, reattach instead of overwriting its files.
echo "Ensuring volume is mounted..."
if ssh "${USER}@${HOST}" "pgrep -f 'ansible-playbook.*maybelle\\.yml' >/dev/null || ([ -e /tmp/chapter-1.running ] && pgrep -f '[/]tmp/chapter-1-script\\.sh' >/dev/null)"; then
    echo "A maybelle deploy is already running; attaching to tmux session '$SESSION_NAME'..."
    if [ -z "$USE_SSH" ] && command -v mosh >/dev/null 2>&1; then
        mosh "${USER}@${HOST}" -- tmux attach-session -t "$SESSION_NAME" || ssh -t "${USER}@${HOST}" "tmux attach-session -t '$SESSION_NAME'"
    else
        ssh -t "${USER}@${HOST}" "tmux attach-session -t '$SESSION_NAME'"
    fi
    exit 0
fi

# Kill any finished chapter-1 session (e.g. still sitting at "Press enter to exit") and wait for
# its EXIT trap to finish BEFORE uploading the new run's files; killing it after upload lets SIGHUP
# run the old trap's rm -f against the newly uploaded files (re's catch, #security, 19 Nov).
ssh "${USER}@${HOST}" "
    tmux kill-session -t '$SESSION_NAME' 2>/dev/null || true
    for _ in \$(seq 1 50); do
        pgrep -f '[/]tmp/chapter-1-script\\.sh' >/dev/null || break
        sleep 0.2
    done
    if pgrep -f '[/]tmp/chapter-1-script\\.sh' >/dev/null; then
        pkill -9 -f '[/]tmp/chapter-1-script\\.sh' 2>/dev/null || true
        while pgrep -f '[/]tmp/chapter-1-script\\.sh' >/dev/null; do sleep 0.1; done
    fi
    rm -f /tmp/chapter-1.running
    mkdir -p '${HETZNER_VOLUME_PATH}' && (mountpoint -q '${HETZNER_VOLUME_PATH}' || mount '${VOLUME_DEVICE}' '${HETZNER_VOLUME_PATH}')
"

# Until the detached tmux session starts on maybelle and arms its own EXIT trap, this laptop script
# owns removing the copied vault password and scrubber secrets if anything fails or is interrupted
# (19 Nov: mosh failed on venue Wi-Fi after scp, stranding .vault-password until the 30-min sweep).
REMOTE_ARMED=0
REMOTE_SCRIPT_PATH="/tmp/chapter-1-script.sh"
cleanup_unarmed() {
    if [ "$REMOTE_ARMED" -eq 0 ]; then
        echo "" >&2
        echo "Removing copied vault files from maybelle before exiting..." >&2
        ssh -o ConnectTimeout=10 "${USER}@${HOST}" \
            "rm -f '${HETZNER_VOLUME_PATH}/.vault-password' /tmp/scrubber-secrets.json '${REMOTE_SCRIPT_PATH}' /tmp/chapter-1.running" 2>/dev/null || true
    fi
}
trap cleanup_unarmed EXIT

echo "Copying vault password to maybelle..."
ssh "${USER}@${HOST}" "umask 077 && cat > '${HETZNER_VOLUME_PATH}/.vault-password'" < "$ANSIBLE_VAULT_PASSWORD_FILE"

# Extract secrets locally and copy only the JSON list to maybelle
# This keeps the vault password off maybelle for the scrubber
echo "Extracting secrets for scrubber..."
SECRETS_JSON=$(ansible-vault view "${SCRIPT_DIR}/../../secrets/vault.yml" --vault-password-file "$ANSIBLE_VAULT_PASSWORD_FILE" | python3 -c "
import sys, yaml, json
data = yaml.safe_load(sys.stdin)
secrets = [v for v in data.values() if isinstance(v, str) and len(v) > 4]
print(json.dumps(secrets))
")

# Copy secrets to temp file on maybelle with umask 077 so it is 0600 from creation
echo "$SECRETS_JSON" | ssh "${USER}@${HOST}" "umask 077 && cat > /tmp/scrubber-secrets.json"
echo "Extracted $(echo "$SECRETS_JSON" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))') secrets for scrubber"

echo ""

# Commands to run on maybelle
REMOTE_SCRIPT=$(cat <<'OUTER_EOF'
#!/usr/bin/env bash
set -e

HETZNER_VOLUME_PATH="__HETZNER_VOLUME_PATH__"
MOUNT_POINT="__MOUNT_POINT__"
REPO_DIR="__REPO_DIR__"
VAULT_PASSWORD_FILE="__VAULT_PASSWORD_FILE__"
VOLUME_DEVICE="__VOLUME_DEVICE__"
REBUILD_IMAGES="__REBUILD_IMAGES__"

: > /tmp/chapter-1.running

# The vault password and extracted scrubber secrets are copied here for this run
# only. Remove them on normal completion, on EXIT, and at the start of ERR before
# waiting on /dev/tty — otherwise a pre-ansible failure (apt, git fetch) in a
# detached session leaves them on disk until the 30-minute sweep.
cleanup_secrets() {
    rm -f "$VAULT_PASSWORD_FILE" /tmp/scrubber-secrets.json /tmp/chapter-1.running
}
trap 'cleanup_secrets; rm -f /tmp/chapter-1-script.sh' EXIT

# Pauses read from /dev/tty rather than inherited stdin. A bare `read` depends
# on whatever stdin tmux hands the script; when that isn't a terminal it returns
# non-zero at once, and with `set -e` the ERR trap below fires and announces a
# failed deploy after a run that succeeded completely. `|| true` stops a pause
# from ever being an error.
#
# `2>/dev/null` goes BEFORE `</dev/tty` on purpose. Redirections apply left to
# right, so with the usual ordering bash reports a failed /dev/tty open using
# the stderr it still has, and the message escapes anyway.
trap 'cleanup_secrets; echo ""; echo "ERROR: Script failed at line $LINENO. Press enter to exit."; read -r _ 2>/dev/null </dev/tty || true' ERR

# Mount volume if not already mounted
echo "Checking Hetzner volume..."
mkdir -p "$HETZNER_VOLUME_PATH"
if mountpoint -q "$HETZNER_VOLUME_PATH"; then
    echo "Volume already mounted at $HETZNER_VOLUME_PATH"
else
    echo "Mounting volume..."
    mount "$VOLUME_DEVICE" "$HETZNER_VOLUME_PATH"
fi

# Add to fstab for persistence across reboots
if ! grep -q "$HETZNER_VOLUME_PATH" /etc/fstab; then
    echo "Adding mount to fstab..."
    echo "$VOLUME_DEVICE $HETZNER_VOLUME_PATH ext4 defaults 0 2" >> /etc/fstab
fi

echo "Creating symlink..."
if [ -L "$MOUNT_POINT" ]; then
    echo "Symlink $MOUNT_POINT already exists"
elif [ -e "$MOUNT_POINT" ]; then
    echo "ERROR: $MOUNT_POINT exists but is not a symlink"
    exit 1
else
    ln -s "$HETZNER_VOLUME_PATH" "$MOUNT_POINT"
    echo "Created $MOUNT_POINT -> $HETZNER_VOLUME_PATH"
fi

echo ""
echo "Installing git and ansible..."
apt-get update -qq
apt-get install -y -qq git ansible

# Clone or update the repo
if [ -d "$REPO_DIR/.git" ]; then
    echo "Updating maybelle-config repository..."
    cd "$REPO_DIR"
    # From githouse's copy (the flip, 10 Nov 2026; Cryptograss:Moods/ramp/Plan, section 6): production
    # there moves only by a signed merge. GitHub only before githouse has a copy (a cold start).
    GITHOUSE_REPO="$MOUNT_POINT/githouse/git/maybelle-config.git"
    if [ -f "$GITHOUSE_REPO/HEAD" ]; then
        # githouse's git user owns it; root may read it (githouse's role says why)
        git config --system --get-all safe.directory | grep -qxF "$GITHOUSE_REPO" ||
            git config --system --add safe.directory "$GITHOUSE_REPO"
        git remote set-url origin "$GITHOUSE_REPO"
        echo "From githouse"
    else
        git remote set-url origin https://github.com/cryptograss/maybelle-config.git
        echo "githouse has no maybelle-config here: from GitHub (a cold start only)"
    fi
    git fetch origin production
    git reset --hard origin/production
else
    echo "Cloning maybelle-config repository..."
    git clone --depth 1 --branch production \
        https://github.com/cryptograss/maybelle-config.git "$REPO_DIR"
fi

echo ""

# Pass rebuild flag to ansible if set
EXTRA_VARS=""
if [ -n "$REBUILD_IMAGES" ]; then
    echo "Will rebuild docker images (--no-cache) during ansible run"
    EXTRA_VARS="-e rebuild_images=true"
fi

LOG_FILE="/tmp/ansible-deploy-$(date +%Y%m%d-%H%M%S).log"
install -m 600 /dev/null "$LOG_FILE"
echo "Running ansible playbook..."
echo "Log file: $LOG_FILE"
cd "$REPO_DIR/maybelle/ansible"
# Tell the Moods (memory-lane, which this deploy restarts) it's under way.
DEPLOY_BY="__DEPLOY_BY__"
"$REPO_DIR/maybelle/scripts/report-deploy.sh" maybelle started "$DEPLOY_BY"
# The log on PickiPedia, as DrivingThatTrain (post-deploy-log.py): the whole
# of it if the deploy failed, a summary if not, every vault value taken out.
# This playbook removes the vault password as it ends, so the poster starts
# now: it reads the vault while it can, keeps it in memory only, and posts
# once told below how the deploy went.
LOG_URL_FILE="/tmp/deploy_log_url_$$"
DEPLOY_DONE="/tmp/deploy_done_$$"
python3 "$REPO_DIR/maybelle/scripts/post-deploy-log.py" maybelle later "$LOG_FILE" \
    --vault-password-file "$VAULT_PASSWORD_FILE" --await "$DEPLOY_DONE" --url-file "$LOG_URL_FILE" --by "$DEPLOY_BY" &
POSTER=$!
START_TIME=$(date +%s)
ansible-playbook -i localhost, maybelle.yml --vault-password-file "$VAULT_PASSWORD_FILE" $EXTRA_VARS 2>&1 | tee "$LOG_FILE"
ANSIBLE_EXIT=${PIPESTATUS[0]}
STATE=$([ "$ANSIBLE_EXIT" -eq 0 ] && echo finished || echo failed)
printf '{"state": "%s", "took": %d}\n' "$STATE" "$(( $(date +%s) - START_TIME ))" > "$DEPLOY_DONE.tmp"
mv "$DEPLOY_DONE.tmp" "$DEPLOY_DONE"  # whole, or not at all: the poster is reading for it
wait "$POSTER" || true
# Nothing reads the vault or scrubber copy from here on; don't leave them for the pause below.
cleanup_secrets
rm -f "$DEPLOY_DONE"
# memory-lane may still be coming back up: keep trying for two minutes.
REPORT_TRIES=12 "$REPO_DIR/maybelle/scripts/report-deploy.sh" maybelle "$STATE" "$DEPLOY_BY" "$(cat "$LOG_URL_FILE" 2>/dev/null)"
rm -f "$LOG_URL_FILE"

echo ""
echo "=== Chapter 1 complete ==="
echo "Log saved to: $LOG_FILE"
echo "Press enter to exit"
read -r _ 2>/dev/null </dev/tty || true
OUTER_EOF
)

# Substitute variables
REMOTE_SCRIPT="${REMOTE_SCRIPT//__HETZNER_VOLUME_PATH__/$HETZNER_VOLUME_PATH}"
REMOTE_SCRIPT="${REMOTE_SCRIPT//__MOUNT_POINT__/$MOUNT_POINT}"
REMOTE_SCRIPT="${REMOTE_SCRIPT//__REPO_DIR__/$REPO_DIR}"
REMOTE_SCRIPT="${REMOTE_SCRIPT//__VAULT_PASSWORD_FILE__/$VAULT_PASSWORD_FILE}"
REMOTE_SCRIPT="${REMOTE_SCRIPT//__VOLUME_DEVICE__/$VOLUME_DEVICE}"
REMOTE_SCRIPT="${REMOTE_SCRIPT//__REBUILD_IMAGES__/$REBUILD_IMAGES}"
REMOTE_SCRIPT="${REMOTE_SCRIPT//__DEPLOY_BY__/$DEPLOY_BY}"

# Upload script and start the tmux session detached over TCP ssh first so its EXIT trap owns
# .vault-password and /tmp/scrubber-secrets.json before mosh even attempts UDP.
echo "$REMOTE_SCRIPT" | ssh "${USER}@${HOST}" "umask 077 && cat > '${REMOTE_SCRIPT_PATH}' && chmod 700 '${REMOTE_SCRIPT_PATH}'"
ssh "${USER}@${HOST}" "tmux new-session -d -s '$SESSION_NAME' '${REMOTE_SCRIPT_PATH}'"
REMOTE_ARMED=1

if [ -z "$USE_SSH" ] && command -v mosh >/dev/null 2>&1; then
    echo "Attaching via mosh (pass --ssh on networks that block UDP)..."
    mosh "${USER}@${HOST}" -- tmux attach-session -t "$SESSION_NAME" || {
        echo "mosh did not connect (UDP blocked?); attaching over ssh instead..."
        ssh -t "${USER}@${HOST}" "tmux attach-session -t '$SESSION_NAME'"
    }
else
    echo "Attaching via ssh..."
    ssh -t "${USER}@${HOST}" "tmux attach-session -t '$SESSION_NAME'"
fi
