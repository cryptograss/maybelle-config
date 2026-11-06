# githouse (githouse.py): on maybelle, our canonical copies and sshd for pushes;
# on hunter, the read-only mirror of them (GITHOUSE_MODE). Its tests run as
# it is built.
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-server openssh-client python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# useradd leaves the password field "!", which sshd reads as a locked account and refuses even by
# key ("User git not allowed because account is locked"); "*" is unusable but not locked.
RUN useradd -u 2999 -d /srv/git -M -s /bin/sh git && usermod -p '*' git && mkdir -p /run/sshd /opt/githouse
COPY githouse.py test_githouse.py entrypoint.sh /opt/githouse/
RUN cd /opt/githouse && python3 -m unittest -q test_githouse && rm -rf __pycache__ /tmp/tmp*
COPY sshd_config /etc/ssh/sshd_config
ENTRYPOINT ["/bin/sh", "/opt/githouse/entrypoint.sh"]
