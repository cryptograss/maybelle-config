# The `branches` agent (Cryptograss:Moods/ramp/Plan, section 11): where branch code runs, and nothing else.
# No secrets, no jenkins_home; on branches-net, which reaches only Jenkins and the egress proxy.
# What a repository's ci/test may need: Python, Node, git, ssh-keygen (signatures in tests), a compiler.
#
# Two users (re, 9 Nov): `jenkins` runs the agent and holds its secret (/run/agent-secret, 0400); a branch's
# ci/test runs as `builder`, which can't read it, through run-ci, the one thing `jenkins` may sudo. Code
# running as the agent's own user could print the secret into its public console, and with it anyone could
# connect a `branches` agent of their own to Jenkins.
FROM jenkins/inbound-agent:latest-jdk17
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip python3-dev git openssh-client build-essential \
        nodejs npm ca-certificates curl sudo procps \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1500 -s /bin/sh builder
COPY run-ci /usr/local/bin/run-ci
# Exactly run-ci, with no arguments ("" in sudoers), nothing else.
RUN chmod 0755 /usr/local/bin/run-ci && chown root:root /usr/local/bin/run-ci \
    && printf '%s\n' 'Defaults:jenkins env_keep += "http_proxy https_proxy HTTP_PROXY HTTPS_PROXY no_proxy NO_PROXY"' \
                      'jenkins ALL=(root) NOPASSWD: /usr/local/bin/run-ci ""' > /etc/sudoers.d/run-ci \
    && chmod 0440 /etc/sudoers.d/run-ci && visudo -cf /etc/sudoers.d/run-ci
USER jenkins
