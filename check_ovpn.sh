#!/bin/bash

target=10.8.0.1
count=$( ping -w 5 -c 1 $target | grep ttl=* | wc -l )
countif=$( ifconfig | grep -c "^tun" )

echo "Anzahl Interfaces"
echo $countif

if [ $count -eq 0 ]
then
        if [ $countif > 1 ]
        then
                echo "Reboot tut gut"
				sudo reboot
        else
                echo "Restarting VPN"
                /etc/init.d/openvpn stop
                /data/openvpn/start.sh
        fi
else
        echo "VPN is Running"
fi
