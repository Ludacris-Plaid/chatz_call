FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y asterisk asterisk-config sqlite3 && rm -rf /var/lib/apt/lists/*

# Create required directories (NOT astdb dir - that triggers old Berkeley DB migration)
RUN mkdir -p /var/run/asterisk \
    /var/spool/asterisk/outgoing \
    /var/spool/asterisk/voicemail \
    /var/spool/asterisk/fax \
    /var/spool/asterisk/dictate \
    /var/spool/asterisk/tmp \
    /var/spool/asterisk/meetme \
    /var/spool/asterisk/system \
    /var/spool/asterisk/recording \
    /var/spool/asterisk/monitor \
    /var/spool/asterisk/moh \
    /var/lib/asterisk/keys \
    /var/lib/asterisk/sounds \
    /var/lib/asterisk/images \
    /var/lib/asterisk/phoneprov \
    /var/lib/asterisk/agi-bin \
    /var/log/asterisk

# Remove any old Berkeley DB directory and create SQLite database
RUN rm -rf /var/lib/asterisk/astdb && \
    sqlite3 /var/lib/asterisk/astdb.sqlite3 "CREATE TABLE IF NOT EXISTS astdb(key VARCHAR(256) NOT NULL PRIMARY KEY, value VARCHAR(256));" && \
    chmod 666 /var/lib/asterisk/astdb.sqlite3 && \
    chown -R asterisk:asterisk /var/lib/asterisk

# Set ownership on all Asterisk directories
RUN chown -R asterisk:asterisk /etc/asterisk /var/run/asterisk /var/lib/asterisk /var/spool/asterisk /var/log/asterisk

COPY asterisk/ /etc/asterisk/

EXPOSE 5060/udp 5060/tcp 5080/udp 5080/tcp 8088 8089 5038 16384-32768/udp

# Run as root so Asterisk can init database, then it drops to asterisk user via -U flag
CMD ["asterisk", "-f", "-vvvg", "-U", "asterisk"]
