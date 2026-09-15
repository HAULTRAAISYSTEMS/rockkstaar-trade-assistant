"""Application entry point that registers modular Live Research features."""
import app as legacy
from live_research_routes import create_live_research_blueprint
from stock_research_integration import install_stock_research
from live_research_realtime import websocket_loop
from research_memory_routes import create_research_memory_blueprint
from sms_alert_routes import create_sms_alerts_blueprint
from browser_push_routes import create_browser_push_blueprint

app=legacy.app
app.register_blueprint(create_live_research_blueprint(require_admin=legacy.require_admin,current_user=legacy.current_user,tracked_tickers=legacy.get_user_tracked_tickers))
app.register_blueprint(create_research_memory_blueprint(current_user=legacy.current_user))
app.register_blueprint(create_sms_alerts_blueprint(current_user=legacy.current_user))
app.register_blueprint(create_browser_push_blueprint(current_user=legacy.current_user))
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
