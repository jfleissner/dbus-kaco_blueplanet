# TWIMC
# Script zum Testen der OpenVPN connectivity.
# Es wird ein Ping mit 5 Paketen zum Server ausgeführt und die Anzahl der Interfaces überprüft.
# Danach werden verschieden Fälle behandelt.
# Version des Scripts vom 08.11.2023


#!/bin/bash

server=10.8.0.1
echo "Pings zu $server werden gesendet"
pingcount=$( ping -w 5 -c 5 $server | grep ttl=* | wc -l )
echo "Antworten auf 5 Pings: $pingcount"

echo "Anzahl der Interfaces feststellen"
ifcount=$( ifconfig | grep -c "^tun" )
echo "Anzahl Interfaces: $ifcount"

#Anzahl Antworten > 2 => Alles ist gut
if [ $pingcount -gt 2 ]
	then
		echo "Alles ist gut."

#Anzahl Antworten < 3 => weiter gucken

##Anzahl Interfaces = 0
elif [ $ifcount -eq 0 ]
	then
		echo "Anzahl der Interfaces ist 0 => start.sh"
		/etc/init.d/openvpn stop
		sleep 5
		/data/openvpn/start.sh

##Anzahl Interfaces > 2
elif [ $ifcount -gt 1 ]
	then
		echo "Da sind zu viele Interfaces! => killall openvpn und Neustart"
		killall openvpn
		sleep 2
		/etc/init.d/openvpn stop
		sleep 2
		/data/openvpn/start.sh
		
##Anzahl Interfaces ist 1 aber nicht genug Antworten von Server.
###Wenn genau 1 Interface da ist
elif [ $ifcount -eq 1 ]
	then
		ifname=$( ifconfig | grep "^tun" | awk '{print $1;}' )
		echo "Ein Interface gefunden aber keine Antwort vom Server"
		echo $ifname
		if [ $ifname != 'tun0' ]
			then
				echo "Der Name des Interfaces ist nicht tun0. Da haengen wohl noch Interfaces rum. => killall openvpn"
				killall openvpn
				sleep 2
				/etc/init.d/openvpn stop
				sleep 2
				/data/openvpn/start.sh
			else
				echo "Der Name des Interfaces ist tun0. Ein Restart des Services reicht. Vorher aber das Interface down nehmen"
				ifconfig $ifname down
				/etc/init.d/openvpn stop
				sleep 5
				/data/openvpn/start.sh
		fi
fi
