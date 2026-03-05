#!/bin/sh

# Run the auto-login script
# wine python $HOME/auto_login.py

# Wait for 2 seconds
# sleep 2

# Start the server after the auto-login script completes
echo "Starting FastAPI Server..."
cd $HOME/api
wine python -m app


# CMD ["wine", "python server.py"]
# --host ${SERVER_HOST} -p ${SERVER_PORT}