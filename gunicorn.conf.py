import os

# Bind to 0.0.0.0 on Render's assigned port.
# This is what makes Render's port scanner succeed.
bind    = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

workers = 1
threads = 4
timeout = 120

# Do NOT preload the app in the master process.
# preload_app=True would run app.py imports (including init_db) before
# the socket is bound, which is the exact pattern that causes Render to
# report "No open ports detected".
preload_app = False
