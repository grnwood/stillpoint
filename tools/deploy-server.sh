#!/bin/bash

SERVER_HOME="/opt/stillpoint/app"
SERVER_USER="stillpoint"
su $SERVER_USER 

echo "Deploying Stillpoint server..."
git checkout master
git pull
cp -r sp/ $SERVER_HOME/sp
