# Gunicorn reads this file automatically, whatever start command Render uses.
# Command-line flags (the Procfile, or a Start Command set in the Render
# dashboard) override anything here — so this only sets what a flag does not.
#
# Worker recycling. Live prices poll every 3 s from every open page, so the
# old --max-requests 500 restarted each worker every few minutes, and each
# restart is ~12 s with no answer. Recycling is a guard against slow memory
# growth, not a timer: 20000 keeps it at roughly hours. If the Render
# dashboard Start Command still says --max-requests 500, that flag wins —
# change it there to match the Procfile.
max_requests = 20000
max_requests_jitter = 2000
