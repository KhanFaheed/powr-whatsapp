#!/bin/bash
# POWR WhatsApp Bot - Development Runner
# This script starts all services needed for WhatsApp testing

echo "=========================================="
echo "POWR WhatsApp Bot - Dev Server"
echo "=========================================="

# Check if Redis is running
if ! pgrep -x "redis-server" > /dev/null; then
    echo "⚠️  Redis is not running. Starting Redis..."
    redis-server --daemonize yes
    sleep 1
fi

echo "✅ Redis is running"

# Activate virtual environment
source .venv/bin/activate

# Start the Tailwind CSS watcher (rebuilds static/dist/output.css on changes)
if [ -d "node_modules" ]; then
    echo "Starting Tailwind CSS watcher..."
    npm run css:dev &
    TAILWIND_PID=$!
    echo "✅ Tailwind watcher started (PID: $TAILWIND_PID)"
else
    echo "⚠️  node_modules missing — run 'npm install' to enable the Tailwind build"
    TAILWIND_PID=""
fi

# Start Celery worker in background
echo "Starting Celery worker..."
celery -A config worker -l info &
CELERY_PID=$!
echo "✅ Celery worker started (PID: $CELERY_PID)"

# Wait a moment for Celery to initialize
sleep 2

# Start Django server
echo ""
echo "Starting Django server on http://localhost:8000"
echo ""
echo "=========================================="
echo "NEXT STEPS:"
echo "1. Open a new terminal and run: ngrok http 8000"
echo "2. Copy the https URL (e.g., https://abc123.ngrok.io)"
echo "3. Go to Twilio Console > WhatsApp Sandbox"
echo "4. Set webhook URL to: https://YOUR-NGROK-URL/webhook/"
echo "5. Send 'join <sandbox-code>' to +1 415 523 8886"
echo "6. Then send your Arabic test messages!"
echo "=========================================="
echo ""

python manage.py runserver 0.0.0.0:8000

# Cleanup on exit
kill $CELERY_PID $TAILWIND_PID 2>/dev/null
