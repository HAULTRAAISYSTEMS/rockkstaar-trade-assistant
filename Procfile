# Two workers, not one. Health checks run every 5 seconds — about 720 requests
# an hour — so --max-requests 100 recycled the single worker roughly every 8
# minutes, and a restart takes ~12 seconds to start listening. With one worker
# that gap has nothing serving it: every recycle was a burst of 502s, and a
# slow fundamentals request could stall the health check into a failed
# instance on top of that. A second worker means a recycle or a busy request
# never leaves zero workers, and 500 requests between recycles makes them
# roughly five times rarer. Peak memory sits near 20% of the 2GB limit, so the
# second worker fits comfortably.
#
# 500 was set when traffic was page loads and a 45 s tape poll. Live prices
# now poll every 3 s from every open page, which reached 500 requests in a
# few minutes, so each worker restarted (≈12 s of no answer) every few
# minutes — the "crashes". Recycling exists to contain slow memory growth,
# not to restart on a timer; 20000 keeps that safety net at roughly hours.
# 12 threads per worker (was 4): opening the Terminal fires ~10 requests at
# once (candles, quotes, live, context, insiders, events, depth…), several of
# them slow outside lookups. With 8 slots in total the chart's candle request
# queued behind them — about 30 s before the chart appeared. Threads wait on
# the network, not the CPU, and cost little memory.
web: gunicorn --bind 0.0.0.0:$PORT --workers 2 --worker-class gthread --threads 12 --timeout 120 --graceful-timeout 30 --max-requests 20000 --max-requests-jitter 2000 web_app:app
