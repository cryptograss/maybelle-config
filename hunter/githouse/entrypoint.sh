#!/bin/sh
# Its host key kept with the repositories, so a container made again is the same host to everyone's known_hosts.
set -e
mkdir -p /srv/git/.host-keys
[ -f /srv/git/.host-keys/ssh_host_ed25519_key ] || ssh-keygen -q -t ed25519 -N '' -f /srv/git/.host-keys/ssh_host_ed25519_key
chown -R git:git /srv/git
# The deploy key, if the vault has one: fetch over ssh, and forward pushes on to GitHub (githouse.py reads it).
if [ -s /etc/githouse/deploy_key ]; then
    install -o git -g git -m 600 /etc/githouse/deploy_key /srv/git/.deploy_key
    export GITHOUSE_URL='git@github.com:{}.git' GITHOUSE_FORWARD=true
else
    rm -f /srv/git/.deploy_key
    echo "githouse: no deploy key; fetching over https, pushes kept here, not forwarded to GitHub" >&2
fi
/usr/sbin/sshd
umask 022
runuser -u git -- python3 /opt/githouse/githouse.py init $GITHOUSE_REPOS
exec runuser -u git -- python3 /opt/githouse/githouse.py loop
