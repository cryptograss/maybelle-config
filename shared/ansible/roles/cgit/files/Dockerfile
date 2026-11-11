# cgit: githouse's repositories to read in a browser (Justin, #ramp, 11 Nov: "where can I simply browse the
# codebases?" ... "cgit with Pygments, go ahead and build it"). Read-only: each repository is mounted :ro.
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        cgit lighttpd python3-pygments python3-markdown \
    && rm -rf /var/lib/apt/lists/*
COPY lighttpd.conf /etc/lighttpd/lighttpd.conf
COPY cgitrc /etc/cgitrc
COPY robots.txt /usr/share/cgit/robots.txt
EXPOSE 8080
CMD ["lighttpd", "-D", "-f", "/etc/lighttpd/lighttpd.conf"]
