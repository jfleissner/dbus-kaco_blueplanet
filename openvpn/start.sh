#!/bin/bash
OPENVPN=/data/openvpn
INSTALLED=$(opkg list_installed | grep openvpn | wc -l)
CONFIG=[your OpenVPN client configuration file here]
PACKAGE=openvpn_2.4.7-r0_cortexa7hf-neon-vfpv4.ipk
	if [ $INSTALLED -eq 0 ]
then
	opkg update
	IN_REPO=$(opkg list openvpn | wc -l)
	if [ $IN_REPO -eq 0 ]
	then
		if [ -f $OPENVPN/$PACKAGE ]; then opkg install $OPENVPN/$PACKAGE; fi
	else
		opkg update
		opkg install openvpn
	fi
	fi
	# We may not have been able to install OpenVPN so let's see...
	INSTALLED=$(opkg list_installed | grep openvpn | wc -l)

	if [ $INSTALLED -eq 1 ]
	then
		if [ ! -d /etc/openvpn ]; then mkdir /etc/openvpn; fi
		if [ ! -h /etc/openvpn/$CONFIG ]; then ln -s $OPENVPN/$CONFIG	/etc/openvpn/$CONFIG; fi
		if [ ! -h /etc/default/openvpn ]; then ln -s $OPENVPN/openvpn	/etc/default/openvpn; fi

		/etc/init.d/openvpn start
fi
