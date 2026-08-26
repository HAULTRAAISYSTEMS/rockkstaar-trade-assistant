"""Application entry point that registers modular Phase 2 Live Research routes."""
import app as legacy
from live_research_routes import create_live_research_blueprint
app=legacy.app
app.register_blueprint(create_live_research_blueprint(require_admin=legacy.require_admin,current_user=legacy.current_user,tracked_tickers=legacy.get_user_tracked_tickers))
