"""Application entry point that registers modular Live Research features."""
import app as legacy
from live_research_routes import create_live_research_blueprint
from stock_research_integration import install_stock_research

app=legacy.app
app.register_blueprint(create_live_research_blueprint(require_admin=legacy.require_admin,current_user=legacy.current_user,tracked_tickers=legacy.get_user_tracked_tickers))
install_stock_research(app)
