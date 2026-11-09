# The `branches` agent (Cryptograss:Moods/ramp/Plan, section 11): where branch code runs, and nothing else.
# No secrets, no jenkins_home; on branches-net, which reaches only Jenkins and the egress proxy.
# What a repository's ci/test may need: Python, Node, git, ssh-keygen (signatures in tests), a compiler.
FROM jenkins/inbound-agent:latest-jdk17
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip python3-dev git openssh-client build-essential \
        nodejs npm ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
USER jenkins
