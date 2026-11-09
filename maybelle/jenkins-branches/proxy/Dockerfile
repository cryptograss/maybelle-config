# The branches agent's only way out (Plan, section 11): HTTP(S) to the package registries and GitHub, by
# name; anything else, including maybelle's private addresses, refused.
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends tinyproxy && rm -rf /var/lib/apt/lists/*
COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY allowed /etc/tinyproxy/allowed
USER nobody
CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
