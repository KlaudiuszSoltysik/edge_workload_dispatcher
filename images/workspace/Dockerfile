FROM ubuntu:latest

RUN apt-get update && apt-get install -y openssh-server && \
    mkdir -p /var/run/sshd /run/sshd && \
    apt-get clean

RUN echo 'root:edge2026' | chpasswd
RUN sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config

EXPOSE 22
CMD ["/usr/sbin/sshd", "-D"]