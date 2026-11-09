#!/bin/sh
# Run inside the Jenkins container by the deploy, once Jenkins is up (maybelle.yml):
# Configuration as Code reloaded, then every job its `jobs:` list names checked to exist.
#
# Why (branch-tests, 9 Nov): JCasC seeds jobs at boot while Jenkins is still loading the jobs already on disk.
# A job new to a deploy was written to disk (jobs/branch-tests/config.xml) yet never loaded: "Loaded all
# jobs" came 65 ms after it was made, from a list read before it existed. It would have appeared only at
# the next restart. Reloading at runtime creates any such job now, and the check says if one is still missing.
set -eu
base=http://localhost:8080
auth="$JENKINS_ADMIN_ID:$JENKINS_ADMIN_PASSWORD"
casc=${CASC_JENKINS_CONFIG:-/var/jenkins_home/casc_configs/jenkins.yml}

for _ in $(seq 90); do curl -sf -o /dev/null "$base/login" && break; sleep 2; done

jar=$(mktemp)
trap 'rm -f "$jar"' EXIT
# Jenkins wants a crumb with a password, tied to the session in the cookie.
crumb=$(curl -sf -c "$jar" -u "$auth" "$base/crumbIssuer/api/json" | sed -n 's/.*"crumb":"\([^"]*\)".*/\1/p')
[ -n "$crumb" ] || { echo "jcasc-reload: no crumb from Jenkins"; exit 1; }
curl -sf -o /dev/null -b "$jar" -u "$auth" -H "Jenkins-Crumb: $crumb" -X POST "$base/configuration-as-code/reload"
echo "Configuration as Code reloaded"

missing=0
for file in $(sed -n 's/^ *- file: *//p' "$casc"); do
    for name in $(sed -n "s/^ *[A-Za-z]*[Jj]ob(['\"]\([^'\"]*\)['\"]).*/\1/p" "$file"); do
        if curl -sf -o /dev/null -u "$auth" "$base/job/$name/api/json"; then
            echo "job $name: there"
        else
            echo "job $name: MISSING (from $file)"; missing=1
        fi
    done
done
exit $missing
