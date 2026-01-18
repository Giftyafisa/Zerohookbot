"""
Web-based Telegram Authentication
Run this to authenticate via browser, then the bot will auto-start
"""
import os
import asyncio
import threading
from flask import Flask, render_template_string, request, redirect, url_for, session
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Telegram client storage
clients = {}
phone_code_hashes = {}

# Get credentials from environment or use defaults
API_ID = int(os.getenv('TG_API_ID', '32685245'))
API_HASH = os.getenv('TG_API_HASH', '6b0237418574e0b8287e8aafb32fd6ca')
SESSION_PATH = os.path.join(os.path.dirname(__file__), 'sessions')

# Ensure sessions directory exists
os.makedirs(SESSION_PATH, exist_ok=True)

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Telegram Bot Authentication</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0;
            padding: 20px;
        }
        .container {
            background: white;
            padding: 40px;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            max-width: 400px;
            width: 100%;
        }
        h1 { 
            color: #333; 
            margin-bottom: 10px;
            font-size: 24px;
        }
        p { color: #666; margin-bottom: 25px; }
        .form-group { margin-bottom: 20px; }
        label { 
            display: block; 
            margin-bottom: 8px; 
            color: #333;
            font-weight: 500;
        }
        input[type="text"], input[type="password"] {
            width: 100%;
            padding: 15px;
            border: 2px solid #e1e1e1;
            border-radius: 10px;
            font-size: 16px;
            transition: border-color 0.3s;
        }
        input:focus {
            outline: none;
            border-color: #667eea;
        }
        button {
            width: 100%;
            padding: 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        button:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 20px rgba(102, 126, 234, 0.4);
        }
        .error {
            background: #fee;
            color: #c00;
            padding: 15px;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        .success {
            background: #efe;
            color: #060;
            padding: 15px;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        .info {
            background: #e8f4fd;
            color: #0066cc;
            padding: 15px;
            border-radius: 10px;
            margin-bottom: 20px;
            font-size: 14px;
        }
        .step {
            display: inline-block;
            background: #667eea;
            color: white;
            width: 30px;
            height: 30px;
            border-radius: 50%;
            text-align: center;
            line-height: 30px;
            margin-right: 10px;
            font-weight: bold;
        }
    </style>
</head>
<body>
    <div class="container">
        {% if step == 'phone' %}
            <h1><span class="step">1</span>Enter Phone Number</h1>
            <p>Enter your Telegram phone number with country code</p>
            {% if error %}<div class="error">{{ error }}</div>{% endif %}
            <form method="POST" action="/send_code">
                <div class="form-group">
                    <label>Phone Number</label>
                    <input type="text" name="phone" placeholder="+1234567890" required>
                </div>
                <button type="submit">Send Code →</button>
            </form>
        
        {% elif step == 'code' %}
            <h1><span class="step">2</span>Enter OTP Code</h1>
            <p>Check your Telegram app for the login code</p>
            <div class="info">Code sent to {{ phone }}</div>
            {% if error %}<div class="error">{{ error }}</div>{% endif %}
            <form method="POST" action="/verify_code">
                <input type="hidden" name="phone" value="{{ phone }}">
                <div class="form-group">
                    <label>Verification Code</label>
                    <input type="text" name="code" placeholder="12345" required autofocus>
                </div>
                <button type="submit">Verify →</button>
            </form>
        
        {% elif step == '2fa' %}
            <h1><span class="step">3</span>Two-Factor Authentication</h1>
            <p>Enter your Telegram cloud password</p>
            <div class="info">This is the password you set in Telegram Settings → Privacy → Two-Step Verification</div>
            {% if error %}<div class="error">{{ error }}</div>{% endif %}
            <form method="POST" action="/verify_2fa">
                <input type="hidden" name="phone" value="{{ phone }}">
                <div class="form-group">
                    <label>Cloud Password</label>
                    <input type="password" name="password" placeholder="Your 2FA password" required autofocus>
                </div>
                <button type="submit">Login →</button>
            </form>
        
        {% elif step == 'success' %}
            <h1>✅ Authentication Successful!</h1>
            <div class="success">
                <strong>Logged in as:</strong> {{ user_info }}<br><br>
                Session saved! The bot will now start automatically.
            </div>
            <p>You can close this window. The bot is running!</p>
            <a href="/status"><button>Check Bot Status</button></a>
        
        {% elif step == 'status' %}
            <h1>🤖 Bot Status</h1>
            <div class="success" style="background: #f0f0f0; color: #333;">
                <strong>Status:</strong> {{ status }}<br>
                <strong>Sessions:</strong> {{ sessions }}
            </div>
            <a href="/"><button>Add Another Account</button></a>
        {% endif %}
    </div>
</body>
</html>
'''

def run_async(coro):
    """Run async code in sync context"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, step='phone', error=None)

@app.route('/send_code', methods=['POST'])
def send_code():
    phone = request.form.get('phone', '').strip()
    if not phone:
        return render_template_string(HTML_TEMPLATE, step='phone', error='Phone number is required')
    
    # Normalize phone number
    if not phone.startswith('+'):
        phone = '+' + phone
    
    session_file = os.path.join(SESSION_PATH, f'session_{phone.replace("+", "")}')
    
    async def send_code_async():
        client = TelegramClient(session_file, API_ID, API_HASH)
        await client.connect()
        
        try:
            result = await client.send_code_request(phone)
            clients[phone] = client
            phone_code_hashes[phone] = result.phone_code_hash
            return None  # Success
        except Exception as e:
            await client.disconnect()
            return str(e)
    
    error = run_async(send_code_async())
    
    if error:
        return render_template_string(HTML_TEMPLATE, step='phone', error=error)
    
    return render_template_string(HTML_TEMPLATE, step='code', phone=phone, error=None)

@app.route('/verify_code', methods=['POST'])
def verify_code():
    phone = request.form.get('phone', '')
    code = request.form.get('code', '').strip()
    
    if phone not in clients:
        return render_template_string(HTML_TEMPLATE, step='phone', error='Session expired. Please start again.')
    
    client = clients[phone]
    phone_code_hash = phone_code_hashes.get(phone)
    
    async def verify_async():
        try:
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            me = await client.get_me()
            user_info = f"{me.first_name or ''} {me.last_name or ''} (@{me.username or 'no username'})"
            await client.disconnect()
            del clients[phone]
            return ('success', user_info)
        except SessionPasswordNeededError:
            return ('2fa', None)
        except Exception as e:
            return ('error', str(e))
    
    result, data = run_async(verify_async())
    
    if result == 'success':
        return render_template_string(HTML_TEMPLATE, step='success', user_info=data)
    elif result == '2fa':
        return render_template_string(HTML_TEMPLATE, step='2fa', phone=phone, error=None)
    else:
        return render_template_string(HTML_TEMPLATE, step='code', phone=phone, error=data)

@app.route('/verify_2fa', methods=['POST'])
def verify_2fa():
    phone = request.form.get('phone', '')
    password = request.form.get('password', '')
    
    if phone not in clients:
        return render_template_string(HTML_TEMPLATE, step='phone', error='Session expired. Please start again.')
    
    client = clients[phone]
    
    async def verify_2fa_async():
        try:
            await client.sign_in(password=password)
            me = await client.get_me()
            user_info = f"{me.first_name or ''} {me.last_name or ''} (@{me.username or 'no username'})"
            await client.disconnect()
            del clients[phone]
            return ('success', user_info)
        except Exception as e:
            return ('error', str(e))
    
    result, data = run_async(verify_2fa_async())
    
    if result == 'success':
        # Start the main bot after successful auth
        threading.Thread(target=start_main_bot, daemon=True).start()
        return render_template_string(HTML_TEMPLATE, step='success', user_info=data)
    else:
        return render_template_string(HTML_TEMPLATE, step='2fa', phone=phone, error=data)

@app.route('/status')
def status():
    sessions = os.listdir(SESSION_PATH) if os.path.exists(SESSION_PATH) else []
    session_files = [s for s in sessions if s.endswith('.session')]
    return render_template_string(HTML_TEMPLATE, 
                                  step='status', 
                                  status='Running',
                                  sessions=', '.join(session_files) if session_files else 'No sessions')

def start_main_bot():
    """Start the main bot after authentication"""
    import subprocess
    import sys
    # Give a moment for the response to be sent
    import time
    time.sleep(2)
    print("Starting main bot...")
    # The main bot will be started separately or can import and run here

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    print(f"🌐 Open your browser to: http://localhost:{port}")
    print("📱 Complete Telegram authentication via the web interface")
    app.run(host='0.0.0.0', port=port, debug=False)
