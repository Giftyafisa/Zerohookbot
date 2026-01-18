"""
Web-based Telegram Bot Management UI
Complete control panel for authentication and bot management
"""
import os
import json
import asyncio
from flask import Flask, render_template_string, request, redirect, url_for
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Telegram credentials
API_ID = int(os.getenv('TG_API_ID', '32685245'))
API_HASH = os.getenv('TG_API_HASH', '6b0237418574e0b8287e8aafb32fd6ca')
BOT_TOKEN = os.getenv('TG_BOT_TOKEN', '')
OWNER_USERNAME = os.getenv('TG_OWNER_USERNAME', '')

# Paths
BASE_DIR = os.path.dirname(__file__)
SESSION_PATH = os.path.join(BASE_DIR, 'sessions')
RESOURCE_PATH = os.path.join(BASE_DIR, 'resources')
AUTOPOST_CONFIG_PATH = os.path.join(RESOURCE_PATH, 'autopostConfig.json')

# Ensure directories exist
os.makedirs(SESSION_PATH, exist_ok=True)
os.makedirs(RESOURCE_PATH, exist_ok=True)

# Global state for auth flow
auth_state = {
    'client': None,
    'phone': None,
    'phone_code_hash': None
}

def load_autopost_config():
    """Load autopost configuration"""
    if os.path.exists(AUTOPOST_CONFIG_PATH):
        try:
            with open(AUTOPOST_CONFIG_PATH, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        'bot_token': BOT_TOKEN,
        'owner_username': OWNER_USERNAME,
        'channels': {},
        'active_channel_id': None,
        'posting_hour': 13,
        'posting_minute': 0
    }

def save_autopost_config(config):
    """Save autopost configuration"""
    with open(AUTOPOST_CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)

def get_sessions():
    """Get list of session files"""
    if not os.path.exists(SESSION_PATH):
        return []
    return [f.replace('.session', '') for f in os.listdir(SESSION_PATH) if f.endswith('.session')]

# HTML Template
HTML_BASE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Zerohook Bot Control Panel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 800px; margin: 0 auto; }
        .card {
            background: white;
            padding: 30px;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            margin-bottom: 20px;
        }
        h1 { color: #333; margin-bottom: 10px; font-size: 28px; }
        h2 { color: #333; margin-bottom: 15px; font-size: 20px; border-bottom: 2px solid #667eea; padding-bottom: 10px; }
        p { color: #666; margin-bottom: 15px; line-height: 1.6; }
        .nav { display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }
        .nav a {
            padding: 10px 20px;
            background: #f0f0f0;
            color: #333;
            text-decoration: none;
            border-radius: 10px;
            font-weight: 500;
            transition: all 0.2s;
        }
        .nav a:hover, .nav a.active {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        .form-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; color: #333; font-weight: 500; }
        input[type="text"], input[type="password"], input[type="number"], select {
            width: 100%;
            padding: 15px;
            border: 2px solid #e1e1e1;
            border-radius: 10px;
            font-size: 16px;
        }
        input:focus { outline: none; border-color: #667eea; }
        button, .btn {
            display: inline-block;
            padding: 15px 30px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            text-decoration: none;
        }
        button:hover, .btn:hover { transform: translateY(-2px); box-shadow: 0 5px 20px rgba(102, 126, 234, 0.4); }
        .btn-secondary { background: #6c757d; }
        .btn-danger { background: #dc3545; }
        .btn-success { background: #28a745; }
        .error { background: #fee; color: #c00; padding: 15px; border-radius: 10px; margin-bottom: 20px; border-left: 4px solid #c00; }
        .success { background: #efe; color: #060; padding: 15px; border-radius: 10px; margin-bottom: 20px; border-left: 4px solid #060; }
        .info { background: #e8f4fd; color: #0066cc; padding: 15px; border-radius: 10px; margin-bottom: 20px; border-left: 4px solid #0066cc; }
        .warning { background: #fff3cd; color: #856404; padding: 15px; border-radius: 10px; margin-bottom: 20px; border-left: 4px solid #856404; }
        .step {
            display: inline-flex; align-items: center; justify-content: center;
            background: #667eea; color: white; width: 35px; height: 35px;
            border-radius: 50%; margin-right: 15px; font-weight: bold;
        }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .stat-box { background: #f8f9fa; padding: 20px; border-radius: 10px; text-align: center; }
        .stat-box h4 { color: #667eea; font-size: 32px; margin-bottom: 5px; }
        .stat-box p { color: #666; font-size: 14px; margin: 0; }
        .channel-list { list-style: none; }
        .channel-item {
            display: flex; justify-content: space-between; align-items: center;
            padding: 15px; background: #f8f9fa; border-radius: 10px; margin-bottom: 10px;
        }
        .channel-item.active { background: #e8f4fd; border: 2px solid #667eea; }
        .badge { display: inline-block; padding: 5px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; }
        .badge-success { background: #d4edda; color: #155724; }
        .badge-primary { background: #cce5ff; color: #004085; }
        .actions { display: flex; gap: 10px; flex-wrap: wrap; }
        .status-card { display: flex; align-items: center; padding: 20px; background: #f8f9fa; border-radius: 10px; margin-bottom: 15px; }
        .status-icon { font-size: 40px; margin-right: 20px; }
        .status-info h3 { color: #333; margin-bottom: 5px; }
        .status-info p { color: #666; margin: 0; font-size: 14px; }
        small { color: #666; display: block; margin-top: 5px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h1>🤖 Zerohook Bot Control Panel</h1>
            <p>Manage your Telegram bot authentication, channels, and settings</p>
            <div class="nav">
                <a href="/" class="{{ 'active' if page == 'home' else '' }}">🏠 Home</a>
                <a href="/auth" class="{{ 'active' if page == 'auth' else '' }}">🔐 Auth</a>
                <a href="/channels" class="{{ 'active' if page == 'channels' else '' }}">📢 Channels</a>
                <a href="/settings" class="{{ 'active' if page == 'settings' else '' }}">⚙️ Settings</a>
                <a href="/status" class="{{ 'active' if page == 'status' else '' }}">📊 Status</a>
            </div>
        </div>
        {% if error %}<div class="error">❌ {{ error }}</div>{% endif %}
        {% if success %}<div class="success">✅ {{ success }}</div>{% endif %}
        {{ content|safe }}
    </div>
</body>
</html>
'''

def render_page(page, content, error=None, success=None):
    return render_template_string(HTML_BASE, page=page, content=content, error=error, success=success)

@app.route('/')
def home():
    config = load_autopost_config()
    sessions = get_sessions()
    
    content = f'''
    <div class="card">
        <h2>📊 Dashboard</h2>
        <div class="grid">
            <div class="stat-box"><h4>{len(sessions)}</h4><p>Active Sessions</p></div>
            <div class="stat-box"><h4>{len(config.get('channels', {}))}</h4><p>Channels</p></div>
            <div class="stat-box"><h4>{config.get('posting_hour', 13)}:{config.get('posting_minute', 0):02d}</h4><p>Post Time</p></div>
        </div>
        {'<div class="success"><strong>✅ Bot is ready!</strong><br>Session: ' + sessions[0] + '</div>' if sessions else '<div class="warning"><strong>⚠️ No Telegram session!</strong><br><a href="/auth">Click here to authenticate</a></div>'}
    </div>
    <div class="card">
        <h2>🚀 Quick Actions</h2>
        <div class="actions">
            <a href="/auth" class="btn">🔐 Add Account</a>
            <a href="/channels" class="btn btn-secondary">📢 Manage Channels</a>
            <a href="/settings" class="btn btn-secondary">⚙️ Settings</a>
        </div>
    </div>
    '''
    return render_page('home', content)

@app.route('/auth')
def auth():
    sessions = get_sessions()
    sessions_html = ''.join([f'<li class="channel-item"><div><strong>{s}</strong></div><span class="badge badge-success">Active</span></li>' for s in sessions])
    
    content = f'''
    <div class="card">
        <h2><span class="step">1</span>Enter Phone Number</h2>
        <p>Enter your Telegram phone number with country code</p>
        <form method="POST" action="/auth/send_code">
            <div class="form-group">
                <label>Phone Number</label>
                <input type="text" name="phone" placeholder="+1234567890" required autofocus>
            </div>
            <button type="submit">Send Code →</button>
        </form>
    </div>
    {f'<div class="card"><h2>📱 Existing Sessions</h2><ul class="channel-list">{sessions_html}</ul></div>' if sessions else ''}
    '''
    return render_page('auth', content, error=request.args.get('error'), success=request.args.get('success'))

@app.route('/auth/send_code', methods=['POST'])
def send_code():
    phone = request.form.get('phone', '').strip()
    if not phone:
        return redirect(url_for('auth', error='Phone number is required'))
    
    if not phone.startswith('+'):
        phone = '+' + phone
    
    session_file = os.path.join(SESSION_PATH, f'session_{phone.replace("+", "")}')
    
    async def do_send_code():
        client = TelegramClient(session_file, API_ID, API_HASH)
        await client.connect()
        
        if await client.is_user_authorized():
            me = await client.get_me()
            await client.disconnect()
            return ('already_auth', f"{me.first_name} {me.last_name or ''}")
        
        result = await client.send_code_request(phone)
        auth_state['client'] = client
        auth_state['phone'] = phone
        auth_state['phone_code_hash'] = result.phone_code_hash
        return ('code_sent', None)
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result, data = loop.run_until_complete(do_send_code())
        loop.close()
        
        if result == 'already_auth':
            return redirect(url_for('auth', success=f'Already logged in as {data}'))
        
        content = f'''
        <div class="card">
            <h2><span class="step">2</span>Enter OTP Code</h2>
            <p>Check your Telegram app for the login code</p>
            <div class="info">📱 Code sent to <strong>{phone}</strong></div>
            <form method="POST" action="/auth/verify_code">
                <input type="hidden" name="phone" value="{phone}">
                <div class="form-group">
                    <label>Verification Code</label>
                    <input type="text" name="code" placeholder="12345" required autofocus pattern="[0-9]*" inputmode="numeric">
                </div>
                <button type="submit">Verify →</button>
            </form>
            <p style="margin-top: 20px;"><a href="/auth">← Back</a></p>
        </div>
        '''
        return render_page('auth', content)
    except Exception as e:
        return redirect(url_for('auth', error=str(e)))

@app.route('/auth/verify_code', methods=['POST'])
def verify_code():
    phone = request.form.get('phone', '')
    code = request.form.get('code', '').strip()
    
    if not auth_state.get('client'):
        return redirect(url_for('auth', error='Session expired. Please start again.'))
    
    client = auth_state['client']
    phone_code_hash = auth_state['phone_code_hash']
    
    async def do_verify():
        try:
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            me = await client.get_me()
            user_info = f"{me.first_name or ''} {me.last_name or ''} (@{me.username or 'no username'})"
            await client.disconnect()
            auth_state['client'] = None
            return ('success', user_info)
        except SessionPasswordNeededError:
            return ('2fa', None)
        except PhoneCodeInvalidError:
            return ('error', 'Invalid code. Please try again.')
        except Exception as e:
            return ('error', str(e))
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result, data = loop.run_until_complete(do_verify())
        loop.close()
        
        if result == 'success':
            content = f'''
            <div class="card">
                <h2>✅ Authentication Successful!</h2>
                <div class="success"><strong>Logged in as:</strong> {data}<br><br>Your session has been saved!</div>
                <div class="actions">
                    <a href="/" class="btn">🏠 Go to Dashboard</a>
                    <a href="/channels" class="btn btn-secondary">📢 Add Channels</a>
                </div>
            </div>
            '''
            return render_page('auth', content, success='Successfully authenticated!')
        elif result == '2fa':
            content = f'''
            <div class="card">
                <h2><span class="step">3</span>Two-Factor Authentication</h2>
                <p>Enter your Telegram cloud password</p>
                <div class="info">🔒 This is the password from: <strong>Telegram Settings → Privacy → Two-Step Verification</strong></div>
                <form method="POST" action="/auth/verify_2fa">
                    <input type="hidden" name="phone" value="{phone}">
                    <div class="form-group">
                        <label>Cloud Password</label>
                        <input type="password" name="password" placeholder="Your 2FA password" required autofocus>
                    </div>
                    <button type="submit">Login →</button>
                </form>
            </div>
            '''
            return render_page('auth', content)
        else:
            content = f'''
            <div class="card">
                <h2><span class="step">2</span>Enter OTP Code</h2>
                <div class="info">📱 Code sent to <strong>{phone}</strong></div>
                <form method="POST" action="/auth/verify_code">
                    <input type="hidden" name="phone" value="{phone}">
                    <div class="form-group">
                        <label>Verification Code</label>
                        <input type="text" name="code" placeholder="12345" required autofocus>
                    </div>
                    <button type="submit">Verify →</button>
                </form>
            </div>
            '''
            return render_page('auth', content, error=data)
    except Exception as e:
        return redirect(url_for('auth', error=str(e)))

@app.route('/auth/verify_2fa', methods=['POST'])
def verify_2fa():
    phone = request.form.get('phone', '')
    password = request.form.get('password', '')
    
    if not auth_state.get('client'):
        return redirect(url_for('auth', error='Session expired. Please start again.'))
    
    client = auth_state['client']
    
    async def do_verify_2fa():
        try:
            await client.sign_in(password=password)
            me = await client.get_me()
            user_info = f"{me.first_name or ''} {me.last_name or ''} (@{me.username or 'no username'})"
            await client.disconnect()
            auth_state['client'] = None
            return ('success', user_info)
        except PasswordHashInvalidError:
            return ('error', 'Invalid password. Please try again.')
        except Exception as e:
            return ('error', str(e))
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result, data = loop.run_until_complete(do_verify_2fa())
        loop.close()
        
        if result == 'success':
            content = f'''
            <div class="card">
                <h2>✅ Authentication Successful!</h2>
                <div class="success"><strong>Logged in as:</strong> {data}<br><br>Your session has been saved!</div>
                <div class="actions">
                    <a href="/" class="btn">🏠 Go to Dashboard</a>
                    <a href="/channels" class="btn btn-secondary">📢 Add Channels</a>
                </div>
            </div>
            '''
            return render_page('auth', content, success='Successfully authenticated!')
        else:
            content = f'''
            <div class="card">
                <h2><span class="step">3</span>Two-Factor Authentication</h2>
                <div class="info">🔒 This is the password from: <strong>Telegram Settings → Privacy → Two-Step Verification</strong></div>
                <form method="POST" action="/auth/verify_2fa">
                    <input type="hidden" name="phone" value="{phone}">
                    <div class="form-group">
                        <label>Cloud Password</label>
                        <input type="password" name="password" required autofocus>
                    </div>
                    <button type="submit">Login →</button>
                </form>
            </div>
            '''
            return render_page('auth', content, error=data)
    except Exception as e:
        return redirect(url_for('auth', error=str(e)))

@app.route('/channels')
def channels():
    config = load_autopost_config()
    channels_dict = config.get('channels', {})
    active = config.get('active_channel_id')
    
    channels_html = ''
    for cid, name in channels_dict.items():
        is_active = cid == active
        channels_html += f'''
        <li class="channel-item {'active' if is_active else ''}">
            <div><strong>{name}</strong><br><small>ID: {cid}</small></div>
            <div class="actions">
                {f'<span class="badge badge-primary">Active</span>' if is_active else f'<form method="POST" action="/channels/activate" style="display:inline;"><input type="hidden" name="channel_id" value="{cid}"><button type="submit" class="btn btn-success" style="padding:8px 15px;font-size:14px;">Set Active</button></form>'}
                <form method="POST" action="/channels/remove" style="display:inline;">
                    <input type="hidden" name="channel_id" value="{cid}">
                    <button type="submit" class="btn btn-danger" style="padding:8px 15px;font-size:14px;">Remove</button>
                </form>
            </div>
        </li>
        '''
    
    content = f'''
    <div class="card">
        <h2>📢 Add Channel</h2>
        <form method="POST" action="/channels/add">
            <div class="form-group">
                <label>Channel ID</label>
                <input type="text" name="channel_id" placeholder="-1001234567890" required>
                <small>Forward a message from channel to @userinfobot to get ID</small>
            </div>
            <div class="form-group">
                <label>Channel Name</label>
                <input type="text" name="channel_name" placeholder="My Channel" required>
            </div>
            <button type="submit">➕ Add Channel</button>
        </form>
    </div>
    <div class="card">
        <h2>📋 Your Channels</h2>
        {f'<ul class="channel-list">{channels_html}</ul>' if channels_dict else '<div class="info">No channels yet. Add one above!</div>'}
    </div>
    '''
    return render_page('channels', content, error=request.args.get('error'), success=request.args.get('success'))

@app.route('/channels/add', methods=['POST'])
def add_channel():
    channel_id = request.form.get('channel_id', '').strip()
    channel_name = request.form.get('channel_name', '').strip()
    
    if not channel_id or not channel_name:
        return redirect(url_for('channels', error='Both fields required'))
    
    config = load_autopost_config()
    if 'channels' not in config:
        config['channels'] = {}
    
    config['channels'][channel_id] = channel_name
    if not config.get('active_channel_id'):
        config['active_channel_id'] = channel_id
    
    save_autopost_config(config)
    return redirect(url_for('channels', success=f'Channel "{channel_name}" added!'))

@app.route('/channels/activate', methods=['POST'])
def activate_channel():
    channel_id = request.form.get('channel_id', '').strip()
    config = load_autopost_config()
    
    if channel_id in config.get('channels', {}):
        config['active_channel_id'] = channel_id
        save_autopost_config(config)
        return redirect(url_for('channels', success='Channel activated!'))
    return redirect(url_for('channels', error='Channel not found'))

@app.route('/channels/remove', methods=['POST'])
def remove_channel():
    channel_id = request.form.get('channel_id', '').strip()
    config = load_autopost_config()
    
    if channel_id in config.get('channels', {}):
        name = config['channels'].pop(channel_id)
        if config.get('active_channel_id') == channel_id:
            config['active_channel_id'] = list(config['channels'].keys())[0] if config['channels'] else None
        save_autopost_config(config)
        return redirect(url_for('channels', success=f'"{name}" removed!'))
    return redirect(url_for('channels', error='Channel not found'))

@app.route('/settings')
def settings():
    config = load_autopost_config()
    
    content = f'''
    <div class="card">
        <h2>⚙️ Bot Settings</h2>
        <form method="POST" action="/settings/save">
            <div class="form-group">
                <label>Bot Token</label>
                <input type="text" name="bot_token" value="{config.get('bot_token', '')}" placeholder="123456:ABC-DEF...">
                <small>Get from @BotFather</small>
            </div>
            <div class="form-group">
                <label>Owner Username</label>
                <input type="text" name="owner_username" value="{config.get('owner_username', '')}" placeholder="yourusername">
                <small>Without @</small>
            </div>
            <div class="grid">
                <div class="form-group">
                    <label>Posting Hour (0-23)</label>
                    <input type="number" name="posting_hour" value="{config.get('posting_hour', 13)}" min="0" max="23">
                </div>
                <div class="form-group">
                    <label>Posting Minute (0-59)</label>
                    <input type="number" name="posting_minute" value="{config.get('posting_minute', 0)}" min="0" max="59">
                </div>
            </div>
            <button type="submit">💾 Save Settings</button>
        </form>
    </div>
    <div class="card">
        <h2>🔑 API Credentials</h2>
        <div class="info"><strong>API ID:</strong> {API_ID}<br><strong>API Hash:</strong> {API_HASH[:8]}...</div>
    </div>
    '''
    return render_page('settings', content, error=request.args.get('error'), success=request.args.get('success'))

@app.route('/settings/save', methods=['POST'])
def save_settings():
    config = load_autopost_config()
    config['bot_token'] = request.form.get('bot_token', '').strip()
    config['owner_username'] = request.form.get('owner_username', '').strip()
    
    try:
        hour = int(request.form.get('posting_hour', 13))
        minute = int(request.form.get('posting_minute', 0))
        if 0 <= hour <= 23:
            config['posting_hour'] = hour
        if 0 <= minute <= 59:
            config['posting_minute'] = minute
    except:
        pass
    
    save_autopost_config(config)
    return redirect(url_for('settings', success='Settings saved!'))

@app.route('/status')
def status():
    config = load_autopost_config()
    sessions = get_sessions()
    active_name = config.get('channels', {}).get(config.get('active_channel_id'), 'Not set')
    
    content = f'''
    <div class="card">
        <h2>📊 System Status</h2>
        <div class="status-card">
            <div class="status-icon">{'✅' if sessions else '❌'}</div>
            <div class="status-info"><h3>Telegram Session</h3><p>{sessions[0] if sessions else 'No session'}</p></div>
        </div>
        <div class="status-card">
            <div class="status-icon">{'✅' if config.get('bot_token') else '⚠️'}</div>
            <div class="status-info"><h3>Bot Token</h3><p>{'Configured' if config.get('bot_token') else 'Not set'}</p></div>
        </div>
        <div class="status-card">
            <div class="status-icon">{'✅' if config.get('active_channel_id') else '⚠️'}</div>
            <div class="status-info"><h3>Active Channel</h3><p>{active_name}</p></div>
        </div>
        <div class="status-card">
            <div class="status-icon">⏰</div>
            <div class="status-info"><h3>Post Schedule</h3><p>Daily at {config.get('posting_hour', 13)}:{config.get('posting_minute', 0):02d} UTC</p></div>
        </div>
    </div>
    <div class="card">
        <h2>🔄 Actions</h2>
        <div class="actions">
            <a href="/auth" class="btn">🔐 Re-authenticate</a>
            <a href="/start_bot" class="btn btn-success">▶️ Start Bot</a>
        </div>
    </div>
    '''
    return render_page('status', content)

@app.route('/start_bot')
def start_bot():
    sessions = get_sessions()
    if not sessions:
        return redirect(url_for('auth', error='Please authenticate first'))
    
    # Signal that we should start the main bot
    with open(os.path.join(BASE_DIR, '.start_bot'), 'w') as f:
        f.write('start')
    
    return redirect(url_for('status', success='Bot start requested! Refresh in a few seconds.'))

if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))
    print(f"🌐 Open browser: http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
