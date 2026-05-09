FROM andreasterc/asterisk:latest

COPY asterisk/ /etc/asterisk/

RUN mkdir -p /etc/asterisk/tls && \
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/asterisk/tls/wss.pem \
    -out /etc/asterisk/tls/wss.pem \
    -subj "/CN=hushcircuits.online"

EXPOSE 5060/udp 5060/tcp 5080/udp 5080/tcp 7443 8088 16384-32768/udp
