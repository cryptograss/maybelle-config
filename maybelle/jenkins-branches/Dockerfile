# The `branches` agent (Cryptograss:Moods/ramp/Plan, section 11): where branch code runs, and nothing else.
# No secrets, no jenkins_home; on branches-net, which reaches only Jenkins and the egress proxy.
# What a repository's ci/test may need: Python, Node, PHP, git, ssh-keygen (signatures in tests), a compiler.
#
# Two users (re, 9 Nov): `jenkins` runs the agent and holds its secret (/run/agent-secret, 0400); a branch's
# ci/test runs as `builder`, which can't read it, through run-ci, the one thing `jenkins` may sudo. Code
# running as the agent's own user could print the secret into its public console, and with it anyone could
# connect a `branches` agent of their own to Jenkins.
#
# Node is 22, from the official image, not Debian's (20.19 on the agent, 9 Nov): pickipedia-mcp needs >=22.12.
# The bookworm build, whose glibc is older than any base this agent has had. PHP's CLI is for pickipedia's
# ci/test: its PHP lint and verification-gate checks (re, 9 Nov). Both come in when the image is built on
# maybelle, not through the agent's proxy.
FROM node:22-bookworm-slim AS node
FROM jenkins/inbound-agent:latest-jdk17
USER root
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -sf ../lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip python3-dev git openssh-client build-essential \
        php-cli ca-certificates curl sudo procps \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1500 -s /bin/sh builder
COPY run-ci /usr/local/bin/run-ci
# Exactly run-ci, with no arguments ("" in sudoers), nothing else.
RUN chmod 0755 /usr/local/bin/run-ci && chown root:root /usr/local/bin/run-ci \
    && printf '%s\n' 'Defaults:jenkins env_keep += "http_proxy https_proxy HTTP_PROXY HTTPS_PROXY no_proxy NO_PROXY"' \
                      'jenkins ALL=(root) NOPASSWD: /usr/local/bin/run-ci ""' > /etc/sudoers.d/run-ci \
    && chmod 0440 /etc/sudoers.d/run-ci && visudo -cf /etc/sudoers.d/run-ci \
    && node --version && npm --version && php --version | head -1
USER jenkins
