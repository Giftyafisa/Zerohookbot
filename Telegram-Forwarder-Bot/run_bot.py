"""
Zerohook Bot Runner
Simply runs the web_auth.py which handles everything
"""
from web_auth import app, start_services
import os

if __name__ == '__main__':
    # Start background services (scheduler + bot receiver)
    start_services()
    
    # Start Flask web UI
    port = int(os.getenv('PORT', 10000))
    print(f"🚀 Starting Zerohook Bot")
    print(f"🌐 Web UI: http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
