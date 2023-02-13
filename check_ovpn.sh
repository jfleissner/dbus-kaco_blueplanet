#!/bin/bash

target=10.8.0.1
count=$( ping -w 5 -c 1 $target | grep ttl=* | wc -l )

if [ $count -eq 0 ]
then
        echo "Restarting VPN"
        /etc/init.d/openvpn stop
        /data/openvpn/start.sh

else
        echo "VPN is Running"
fi
