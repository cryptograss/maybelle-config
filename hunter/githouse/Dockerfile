# githouse: our canonical clones, at /srv/git (githouse.py). sshd for pushes,
# as the git user, each key's forced command naming who it is; the fetch loop
# beside it. Its tests run as it's built.
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-server openssh-client python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -u 2999 -d /srv/git -M -s /bin/sh git && mkdir -p /run/sshd /opt/githouse
COPY githouse.py test_githouse.py entrypoint.sh /opt/githouse/
RUN cd /opt/githouse && python3 -m unittest -q test_githouse && rm -rf __pycache__ /tmp/tmp*
COPY sshd_config /etc/ssh/sshd_config
ENTRYPOINT ["/bin/sh", "/opt/githouse/entrypoint.sh"]
