"""Application entry point that registers modular Live Research features."""
import app as legacy
from live_research_routes import create_live_research_blueprint
from stock_research_integration import install_stock_research
from live_research_realtime import websocket_loop
from research_memory_routes import create_research_memory_blueprint
from sms_alert_routes import create_sms_alerts_blueprint
from browser_push_routes import create_browser_push_blueprint
from trade_tools_routes import create_trade_tools_blueprint
from news_desk_routes import create_news_desk_blueprint
from notifications_routes import create_notifications_blueprint
from wealth_routes import create_wealth_blueprint
from profile_photo import create_profile_photo_blueprint

from research_workbench import create_blueprint as create_research_lab

app=legacy.app
app.register_blueprint(create_research_lab(current_user=legacy.current_user))
app.register_blueprint(create_live_research_blueprint(require_admin=legacy.require_admin,current_user=legacy.current_user,tracked_tickers=legacy.get_user_tracked_tickers))
app.register_blueprint(create_research_memory_blueprint(current_user=legacy.current_user))
app.register_blueprint(create_sms_alerts_blueprint(current_user=legacy.current_user))
app.register_blueprint(create_browser_push_blueprint(current_user=legacy.current_user))
app.register_blueprint(create_trade_tools_blueprint(current_user=legacy.current_user))
app.register_blueprint(create_news_desk_blueprint(current_user=legacy.current_user,tracked_tickers=legacy.get_user_tracked_tickers))
app.register_blueprint(create_notifications_blueprint(current_user=legacy.current_user))
app.register_blueprint(create_profile_photo_blueprint(current_user=legacy.current_user))
def _wealth_zone(ticker, price):
    """The app's value zone for a ticker, restated at the given price."""
    z = legacy._value_zones_cached(ticker)
    if not z or not z.get("available"):
        return None
    if price:
        from thesis import restate_zone
        z = restate_zone(z, float(price))
    return z


app.register_blueprint(create_wealth_blueprint(current_user=legacy.current_user, zone_lookup=_wealth_zone))
install_stock_research(app)

@legacy.sock.route('/ws/live-research')
def live_research_socket(ws):
    """Authenticated realtime signal channel for explicitly published research."""
    user=legacy.current_user()
    if not user or not user.get('id'):
        try: ws.close(1008, 'authentication required')
        except Exception: pass
        return
    try:
        websocket_loop(ws,user_id=int(user['id']))
    except Exception:
        # Browser disconnects are normal; the shared WS manager reconnects/falls back.
        return
