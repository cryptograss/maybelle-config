#!/bin/sh
# githouse's container, in one of two modes (GITHOUSE_MODE):
#   server  (maybelle): sshd for pushes, GitHub's branches fetched every minute, keys as relayed.
#   mirror  (hunter):   a read-only copy of maybelle's, fetched every minute; no sshd.
# GITHOUSE_REPOS: the repositories, owner/name, space-separated (shared/githouse/repos.txt).
set -e
umask 022
mkdir -p /srv/git
chown git:git /srv/git

if [ "$GITHOUSE_MODE" = "mirror" ]; then
    # Its key to maybelle (hunter's, made by ansible, fetch-only there): GITHOUSE_UPSTREAM is ssh://git@<maybelle>:<port>
    install -o git -g git -m 600 /etc/githouse/mirror_key /srv/git/.mirror_key
    export GIT_SSH_COMMAND="ssh -i /srv/git/.mirror_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/srv/git/.known_hosts"
    exec runuser -u git -- python3 /opt/githouse/githouse.py mirror "$GITHOUSE_UPSTREAM" $GITHOUSE_REPOS
fi

# The host key kept with the repositories, so a container made again is the same host to everyone's known_hosts.
mkdir -p /srv/git/.host-keys
[ -f /srv/git/.host-keys/ssh_host_ed25519_key ] || ssh-keygen -q -t ed25519 -N '' -f /srv/git/.host-keys/ssh_host_ed25519_key
chown -R git:git /srv/git/.host-keys
# The deploy key, if the vault has one: fetch over ssh, and forward pushes on to GitHub (githouse.py reads it).
if [ -s /etc/githouse/deploy_key.b64 ] && base64 -d /etc/githouse/deploy_key.b64 > /srv/git/.deploy_key.new 2>/dev/null && [ -s /srv/git/.deploy_key.new ]; then
    install -o git -g git -m 600 /srv/git/.deploy_key.new /srv/git/.deploy_key && rm -f /srv/git/.deploy_key.new
    export GITHOUSE_URL='git@github.com:{}.git' GITHOUSE_FORWARD=true
else
    rm -f /srv/git/.deploy_key /srv/git/.deploy_key.new
    echo "githouse: no deploy key; fetching over https, pushes kept here, not forwarded to GitHub" >&2
fi
runuser -u git -- python3 /opt/githouse/githouse.py init $GITHOUSE_REPOS
# sshd reads /srv/git/.authorized_keys, which the loop writes from the relayed keys; an empty one until then.
[ -f /srv/git/.authorized_keys ] || install -o git -g git -m 644 /dev/null /srv/git/.authorized_keys
/usr/sbin/sshd -D -e &   # -e: its log to stderr, so `docker logs githouse` says why a key was refused
exec runuser -u git -- python3 /opt/githouse/githouse.py loop
