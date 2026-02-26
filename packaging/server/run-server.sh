#### Set These
# Required environment variables for the Stillpoint server.

export SERVER_ADMIN_PASSWORD="change-me-to-a-secure-password"
export STILLPOINT_SERVER_PORT=8080
export STILLPOINT_VAULTS_ROOT=./vaults

# commend this out on VPS servers, 
# as the server will be run in the background and logs will be written to stillpoint-server.log
#./_launch.sh

# Uncomment for VPS server usage. Logs will be written to stillpoint-server.log in the current directory.
nohup "./_launch.sh" > stillpoint-server.log 2>&1 &
echo "Stillpoint server started in the background. Logs are being written to stillpoint-server.log"

