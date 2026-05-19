#!/bin/bash
set -e

# Ensure directories exist and have proper permissions
mkdir -p /var/run/asterisk /var/log/asterisk /var/spool/asterisk/outgoing /var/lib/asterisk/astdb
chown -R asterisk:asterisk /var/run/asterisk /var/log/asterisk /var/spool/asterisk /var/lib/asterisk

# Ensure astdb.sqlite3 exists
if [ ! -f /var/lib/asterisk/astdb.sqlite3 ]; then
    sqlite3 /var/lib/asterisk/astdb.sqlite3 "CREATE TABLE IF NOT EXISTS astdb(key VARCHAR(256), value VARCHAR(256));"
    chown asterisk:asterisk /var/lib/asterisk/astdb.sqlite3
fi

# Run Asterisk as asterisk user
exec su asterisk -s /bin/bash -c "$*"
