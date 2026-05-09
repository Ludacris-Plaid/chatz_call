FROM safarov/freeswitch:latest

COPY freeswitch/conf/vars.xml /usr/share/freeswitch/conf/vanilla/vars.xml
COPY freeswitch/conf/sip_profiles/ /usr/share/freeswitch/conf/vanilla/sip_profiles/
COPY freeswitch/conf/dialplan/ /usr/share/freeswitch/conf/vanilla/dialplan/
COPY freeswitch/conf/directory/ /usr/share/freeswitch/conf/vanilla/directory/
COPY freeswitch/conf/autoload_configs/acl.conf.xml /usr/share/freeswitch/conf/vanilla/autoload_configs/acl.conf.xml
COPY freeswitch/conf/tls/ /usr/share/freeswitch/conf/vanilla/tls/

EXPOSE 5080/udp 5080/tcp 7443 8021 16384-32768/udp
