FROM ubuntu:latest

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    curl \
    ca-certificates \
    bash \
    htop \
    build-essential \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.x86_64 -o /usr/local/bin/ttyd && \
    chmod +x /usr/local/bin/ttyd

WORKDIR /workspace

EXPOSE 7681

CMD ["ttyd", "-W", "-p", "7681", "bash"]