FROM ubuntu:24.04

# Harbor's agent tooling only. A fetch tool here would hand the agent a way around the haystack.
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tmux asciinema \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
