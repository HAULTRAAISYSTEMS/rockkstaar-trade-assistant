# Two workers, not one. Health checks run every 5 seconds — about 720 requests
# an hour — so --max-requests 100 recycled the single worker roughly every 8
# minutes, and a restart takes ~12 seconds to start listening. With one worker
# that gap has nothing serving it: every recycle was a burst of 502s, and a
# slow fundamentals request could stall the health check into a failed
# instance on top of that. A second worker means a recycle or a busy request
# never leaves zero workers, and 500 requests between recycles makes them
# roughly five times rarer. Peak memory sits near 20% of the 2GB limit, so the
# second worker fits comfortably.
web: gunicorn --bind 0.0.0.0:$PORT --workers 2 --worker-class gthread --threads 4 --timeout 120 --graceful-timeout 30 --max-requests 500 --max-requests-jitter 50 web_app:app
