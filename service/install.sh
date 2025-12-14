#!/bin/bash

# copy & chmod
cp service/lanbin_airtransfer.service /etc/systemd/system/lanbin_airtransfer.service
chmod 644 /etc/systemd/system/lanbin_airtransfer.service

# reload systemd
systemctl daemon-reload

# enable auto-start
systemctl enable lanbin_airtransfer

# run
systemctl start lanbin_airtransfer
systemctl status lanbin_airtransfer