"""
Combined Web Auth + Bot Runner for Render deployment
Starts web UI for authentication, then runs the bot after session exists
"""
import os
import sys
import time
import threading
import asyncio
from pathlib import Path

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

SESSION_PATH = os.path.join(os.path.dirname(__file__), 'sessions')
RESOURCE_PATH = os.path.join(os.path.dirname(__file__), 'resources')

def has_valid_session():
    """Check if there's already a session file"""
    if not os.path.exists(SESSION_PATH):
        return False
    sessions = [f for f in os.listdir(SESSION_PATH) if f.endswith('.session')]
    return len(sessions) > 0

def run_web_auth():
    """Run the web authentication server"""
    from web_auth import app
    port = int(os.getenv('PORT', 10000))
    print(f"🌐 No session found. Open browser to authenticate: https://zerohookbot.onrender.com")
    print(f"   Or locally: http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def run_main_bot():
    """Run the main Telegram bot"""
    print("✅ Session found! Starting main bot...")
    from source.core.Bot import Bot
    from source.utils.Console import Terminal
    from source.utils.Constants import SESSION_FOLDER_PATH, RESOURCE_FILE_PATH, MEDIA_FOLDER_PATH
    
    os.makedirs(RESOURCE_FILE_PATH, exist_ok=True)
    os.makedirs(MEDIA_FOLDER_PATH, exist_ok=True)
    os.makedirs(SESSION_FOLDER_PATH, exist_ok=True)
    
    async def main():
        bot = Bot()
        await bot.start()
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")

def run_combined():
    """Run web auth with session check - if session exists, run bot; otherwise run web UI"""
    # Ensure directories exist
    os.makedirs(SESSION_PATH, exist_ok=True)
    os.makedirs(RESOURCE_PATH, exist_ok=True)
    
    if has_valid_session():
        # Session exists, run the main bot
        run_main_bot()
    else:
        # No session, run web auth UI
        # Also periodically check if session was created
        def check_and_restart():
            while True:
                time.sleep(10)
                if has_valid_session():
                    print("\n✅ Session created! Please restart the service or wait for auto-restart.")
                    # On Render, the service will restart on next deploy
                    # For now, just notify
                    break
        
        checker_thread = threading.Thread(target=check_and_restart, daemon=True)
        checker_thread.start()
        
        run_web_auth()

if __name__ == '__main__':
    run_combined()
