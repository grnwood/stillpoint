
# Example running on a server...

export SERVER_ADMIN_PASSWORD="your_admin_password_here"
export STILLPOINT_SERVER_PORT=8080
export STILLPOINT_VAULTS_ROOT=/opt/stillpoint/vaults

nohup ./run-server.sh > /opt/stillpoint/logs/api-server.log &
