"""
Zerohook Bot - Web UI + Scheduler
Uses proper async handling for Telethon
"""
import os
import json
import asyncio
import threading
import time
import logging
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, url_for
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError
from dotenv import load_dotenv
import telebot
from tinydb import TinyDB, Query

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Credentials
API_ID = int(os.getenv('TG_API_ID', '32685245'))
API_HASH = os.getenv('TG_API_HASH', '6b0237418574e0b8287e8aafb32fd6ca')

# Paths
BASE_DIR = os.path.dirname(__file__)
SESSION_PATH = os.path.join(BASE_DIR, 'sessions')
RESOURCE_PATH = os.path.join(BASE_DIR, 'resources')
MEDIA_PATH = os.path.join(BASE_DIR, 'media', 'autopost')
CONFIG_PATH = os.path.join(RESOURCE_PATH, 'autopostConfig.json')
QUEUE_PATH = os.path.join(RESOURCE_PATH, 'post_queue.json')

os.makedirs(SESSION_PATH, exist_ok=True)
os.makedirs(RESOURCE_PATH, exist_ok=True)
os.makedirs(MEDIA_PATH, exist_ok=True)

# Global state
auth_data = {}
scheduler_running = False
last_post_time = None
posts_made = 0
bot_running = False

# Single event loop for Telethon operations
telethon_loop = None
telethon_thread = None

def get_telethon_loop():
    """Get or create the dedicated Telethon event loop"""
    global telethon_loop, telethon_thread
    if telethon_loop is None or not telethon_loop.is_running():
        telethon_loop = asyncio.new_event_loop()
        def run_loop():
            asyncio.set_event_loop(telethon_loop)
            telethon_loop.run_forever()
        telethon_thread = threading.Thread(target=run_loop, daemon=True)
        telethon_thread.start()
        time.sleep(0.5)  # Let loop start
    return telethon_loop

def run_async(coro):
    """Run async coroutine in the Telethon event loop"""
    loop = get_telethon_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=60)

def load_config():
    defaults = {
        'bot_token': os.getenv('TG_BOT_TOKEN', ''),
        'owner_username': os.getenv('TG_OWNER_USERNAME', ''),
        'channels': {},
        'active_channel_id': None,
        'posting_interval_minutes': 60,
        'enabled': True
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                defaults.update(json.load(f))
        except: pass
    return defaults

def save_config(config):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)

def get_sessions():
    """Get list of session files (just filenames without .session)"""
    if not os.path.exists(SESSION_PATH):
        return []
    sessions = []
    for f in os.listdir(SESSION_PATH):
        if f.endswith('.session'):
            # Check if file has actual content (not just empty/corrupted)
            fpath = os.path.join(SESSION_PATH, f)
            if os.path.getsize(fpath) > 1000:  # Valid sessions are > 1KB
                sessions.append(f.replace('.session', ''))
    return sessions

async def check_session_authorized(session_file):
    """Check if a session is actually authorized"""
    try:
        client = TelegramClient(session_file, API_ID, API_HASH)
        await client.connect()
        authorized = await client.is_user_authorized()
        await client.disconnect()
        return authorized
    except:
        return False

def get_queue():
    db = TinyDB(QUEUE_PATH)
    return db.all()

def add_to_queue(file_path, caption=''):
    db = TinyDB(QUEUE_PATH)
    db.insert({'file_path': file_path, 'caption': caption, 'added_at': datetime.now().isoformat(), 'posted': False})

def get_next_post():
    db = TinyDB(QUEUE_PATH)
    result = db.search(Query().posted == False)
    return result[0] if result else None

def mark_posted(file_path):
    db = TinyDB(QUEUE_PATH)
    db.update({'posted': True, 'posted_at': datetime.now().isoformat()}, Query().file_path == file_path)

# ============== TELETHON ASYNC FUNCTIONS ==============
async def async_send_code(phone, session_file):
    """Send verification code"""
    client = TelegramClient(session_file, API_ID, API_HASH)
    await client.connect()
    
    if await client.is_user_authorized():
        me = await client.get_me()
        await client.disconnect()
        return {'status': 'authorized', 'name': f"{me.first_name} {me.last_name or ''}"}
    
    result = await client.send_code_request(phone)
    # Keep client connected for verification
    auth_data['client'] = client
    auth_data['phone'] = phone
    auth_data['phone_code_hash'] = result.phone_code_hash
    return {'status': 'code_sent', 'phone_code_hash': result.phone_code_hash}

async def async_verify_code(phone, code, phone_code_hash):
    """Verify the code"""
    client = auth_data.get('client')
    if not client:
        return {'status': 'error', 'message': 'Session expired'}
    
    try:
        await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        me = await client.get_me()
        name = f"{me.first_name} {me.last_name or ''}"
        await client.disconnect()
        auth_data.clear()
        return {'status': 'success', 'name': name}
    except SessionPasswordNeededError:
        return {'status': '2fa'}
    except PhoneCodeInvalidError:
        return {'status': 'error', 'message': 'Invalid code'}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

async def async_verify_2fa(password):
    """Verify 2FA password"""
    client = auth_data.get('client')
    if not client:
        return {'status': 'error', 'message': 'Session expired'}
    
    try:
        await client.sign_in(password=password)
        me = await client.get_me()
        name = f"{me.first_name} {me.last_name or ''}"
        await client.disconnect()
        auth_data.clear()
        return {'status': 'success', 'name': name}
    except PasswordHashInvalidError:
        return {'status': 'error', 'message': 'Invalid password'}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

async def async_post_to_channel(session_file, channel_id, file_path, caption):
    """Post file to channel"""
    # Check if session file actually exists
    if not os.path.exists(session_file + '.session'):
        logger.warning(f"Session file not found: {session_file}")
        return False
    
    client = TelegramClient(session_file, API_ID, API_HASH)
    
    try:
        await client.connect()
        
        if not await client.is_user_authorized():
            logger.warning("Session not authorized - need to login first")
            await client.disconnect()
            return False
        
        try:
            channel = int(channel_id)
        except:
            channel = channel_id
        
        await client.send_file(channel, file_path, caption=caption)
        logger.info(f"📤 Sent to channel {channel}")
        await client.disconnect()
        return True
    except EOFError:
        logger.warning("Session invalid (EOF) - need to re-authenticate")
        return False
    except Exception as e:
        logger.error(f"Post error: {e}")
        try:
            await client.disconnect()
        except:
            pass
        return False

# ============== BOT RECEIVER ==============
def start_bot_receiver():
    """Start the bot receiver for accepting photos"""
    global bot_running
    
    while True:
        config = load_config()
        token = config.get('bot_token')
        
        if not token:
            logger.info("⏳ No bot token, waiting...")
            time.sleep(30)
            continue
        
        try:
            bot = telebot.TeleBot(token)
            owner = config.get('owner_username', '').replace('@', '').lower()
            
            @bot.message_handler(content_types=['photo'])
            def handle_photo(message):
                username = (message.from_user.username or '').lower()
                if owner and username != owner:
                    bot.reply_to(message, "❌ Not authorized")
                    return
                try:
                    file_info = bot.get_file(message.photo[-1].file_id)
                    downloaded = bot.download_file(file_info.file_path)
                    filename = f"photo_{int(time.time())}.jpg"
                    filepath = os.path.join(MEDIA_PATH, filename)
                    with open(filepath, 'wb') as f:
                        f.write(downloaded)
                    add_to_queue(filepath, message.caption or '')
                    pending = len([q for q in get_queue() if not q.get('posted')])
                    bot.reply_to(message, f"✅ Queued! ({pending} pending)")
                    logger.info(f"📸 Photo from {username}")
                except Exception as e:
                    bot.reply_to(message, f"❌ Error: {e}")
            
            @bot.message_handler(content_types=['video'])
            def handle_video(message):
                username = (message.from_user.username or '').lower()
                if owner and username != owner:
                    bot.reply_to(message, "❌ Not authorized")
                    return
                try:
                    file_info = bot.get_file(message.video.file_id)
                    downloaded = bot.download_file(file_info.file_path)
                    filename = f"video_{int(time.time())}.mp4"
                    filepath = os.path.join(MEDIA_PATH, filename)
                    with open(filepath, 'wb') as f:
                        f.write(downloaded)
                    add_to_queue(filepath, message.caption or '')
                    pending = len([q for q in get_queue() if not q.get('posted')])
                    bot.reply_to(message, f"✅ Queued! ({pending} pending)")
                    logger.info(f"🎥 Video from {username}")
                except Exception as e:
                    bot.reply_to(message, f"❌ Error: {e}")
            
            @bot.message_handler(commands=['status'])
            def status(message):
                cfg = load_config()
                q = get_queue()
                pending = len([x for x in q if not x.get('posted')])
                posted = len([x for x in q if x.get('posted')])
                ch = cfg.get('channels', {}).get(cfg.get('active_channel_id'), 'Not set')
                bot.reply_to(message, f"📊 Status\n⏱ Interval: {cfg.get('posting_interval_minutes')}m\n📢 Channel: {ch}\n📬 Pending: {pending}\n✅ Posted: {posted}")
            
            @bot.message_handler(commands=['start', 'help'])
            def help_cmd(message):
                bot.reply_to(message, "🤖 Send photos/videos to queue\n/status - Check status\n🌐 https://zerohookbot.onrender.com")
            
            @bot.message_handler(func=lambda m: True)
            def other(message):
                bot.reply_to(message, "📸 Send a photo or video!")
            
            bot_running = True
            logger.info("🤖 Bot receiver started!")
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e:
            logger.error(f"Bot error: {e}")
            bot_running = False
        
        time.sleep(30)  # Wait before retry

# ============== SCHEDULER ==============
def run_scheduler():
    """Post scheduler"""
    global scheduler_running, last_post_time, posts_made
    scheduler_running = True
    logger.info("📅 Scheduler started")
    
    while True:
        try:
            config = load_config()
            
            if not config.get('enabled', True):
                time.sleep(30)
                continue
            
            channel_id = config.get('active_channel_id')
            if not channel_id:
                time.sleep(30)
                continue
            
            sessions = get_sessions()
            if not sessions:
                logger.debug("No valid sessions found")
                time.sleep(30)
                continue
            
            # Check if we have an authorized session
            session_file = os.path.join(SESSION_PATH, sessions[0])
            try:
                is_authorized = run_async(check_session_authorized(session_file))
                if not is_authorized:
                    logger.warning("⚠️ Session not authorized - please login via /auth")
                    time.sleep(60)
                    continue
            except Exception as e:
                logger.warning(f"Session check failed: {e}")
                time.sleep(60)
                continue
            
            interval = config.get('posting_interval_minutes', 60)
            
            should_post = False
            if last_post_time is None:
                should_post = True
            else:
                elapsed = (datetime.now() - last_post_time).total_seconds() / 60
                if elapsed >= interval:
                    should_post = True
            
            if should_post:
                post = get_next_post()
                if post:
                    logger.info(f"📤 Posting: {os.path.basename(post['file_path'])}")
                    session_file = os.path.join(SESSION_PATH, sessions[0])
                    
                    try:
                        success = run_async(async_post_to_channel(
                            session_file, channel_id, 
                            post['file_path'], post.get('caption', '')
                        ))
                        
                        if success:
                            mark_posted(post['file_path'])
                            last_post_time = datetime.now()
                            posts_made += 1
                            logger.info(f"✅ Posted! Total: {posts_made}")
                        else:
                            logger.warning("❌ Post failed")
                    except Exception as e:
                        logger.error(f"Post error: {e}")
            
            time.sleep(30)
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            time.sleep(60)

# ============== WEB UI ==============
HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>Zerohook Bot</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="60">
    <style>
        *{box-sizing:border-box;margin:0;padding:0}
        body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:linear-gradient(135deg,#667eea,#764ba2);min-height:100vh;padding:20px}
        .container{max-width:800px;margin:0 auto}
        .card{background:#fff;padding:25px;border-radius:15px;box-shadow:0 10px 40px rgba(0,0,0,.2);margin-bottom:20px}
        h1{color:#333;margin-bottom:10px}
        h2{color:#333;margin-bottom:15px;padding-bottom:10px;border-bottom:2px solid #667eea}
        p{color:#666;margin-bottom:15px}
        .nav{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px}
        .nav a{padding:10px 20px;background:#f0f0f0;color:#333;text-decoration:none;border-radius:8px;font-weight:500}
        .nav a:hover,.nav a.active{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff}
        .form-group{margin-bottom:20px}
        label{display:block;margin-bottom:8px;color:#333;font-weight:500}
        input,select{width:100%;padding:12px;border:2px solid #e1e1e1;border-radius:8px;font-size:16px}
        input:focus,select:focus{outline:none;border-color:#667eea}
        button,.btn{display:inline-block;padding:12px 25px;background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;border:none;border-radius:8px;font-size:16px;font-weight:600;cursor:pointer;text-decoration:none}
        .btn-danger{background:#dc3545}
        .btn-success{background:#28a745}
        .error{background:#fee;color:#c00;padding:15px;border-radius:8px;margin-bottom:15px;border-left:4px solid #c00}
        .success{background:#efe;color:#060;padding:15px;border-radius:8px;margin-bottom:15px;border-left:4px solid #060}
        .info{background:#e8f4fd;color:#0066cc;padding:15px;border-radius:8px;margin-bottom:15px;border-left:4px solid #0066cc}
        .warning{background:#fff3cd;color:#856404;padding:15px;border-radius:8px;margin-bottom:15px;border-left:4px solid #856404}
        .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:10px;margin-bottom:15px}
        .stat{background:#f8f9fa;padding:15px;border-radius:8px;text-align:center}
        .stat h4{color:#667eea;font-size:24px;margin-bottom:5px}
        .stat p{color:#666;font-size:12px;margin:0}
        .list{list-style:none}
        .list li{padding:12px;background:#f8f9fa;border-radius:8px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center}
        .list li.active{background:#e8f4fd;border:2px solid #667eea}
        .badge{padding:4px 10px;border-radius:20px;font-size:11px;font-weight:600}
        .badge-ok{background:#d4edda;color:#155724}
        .badge-warn{background:#fff3cd;color:#856404}
        small{color:#666;display:block;margin-top:5px}
        .actions{display:flex;gap:8px;flex-wrap:wrap}
    </style>
</head>
<body>
<div class="container">
    <div class="card">
        <h1>🤖 Zerohook Bot</h1>
        <p>Auto-post to Telegram channels</p>
        <div class="nav">
            <a href="/" class="{{ 'active' if page=='home' }}">🏠 Home</a>
            <a href="/auth" class="{{ 'active' if page=='auth' }}">🔐 Auth</a>
            <a href="/channels" class="{{ 'active' if page=='channels' }}">📢 Channels</a>
            <a href="/queue" class="{{ 'active' if page=='queue' }}">📬 Queue</a>
            <a href="/settings" class="{{ 'active' if page=='settings' }}">⚙️ Settings</a>
        </div>
    </div>
    {% if error %}<div class="error">❌ {{ error }}</div>{% endif %}
    {% if success %}<div class="success">✅ {{ success }}</div>{% endif %}
    {{ content|safe }}
</div>
</body>
</html>
'''

def render(page, content, error=None, success=None):
    return render_template_string(HTML, page=page, content=content, error=error, success=success)

@app.route('/')
def home():
    config = load_config()
    sessions = get_sessions()
    queue = get_queue()
    pending = len([q for q in queue if not q.get('posted')])
    posted = len([q for q in queue if q.get('posted')])
    interval = config.get('posting_interval_minutes', 60)
    has_token = bool(config.get('bot_token'))
    
    content = f'''
    <div class="card">
        <h2>📊 Dashboard</h2>
        <div class="grid">
            <div class="stat"><h4>{len(sessions)}</h4><p>Sessions</p></div>
            <div class="stat"><h4>{len(config.get('channels', {}))}</h4><p>Channels</p></div>
            <div class="stat"><h4>{pending}</h4><p>Pending</p></div>
            <div class="stat"><h4>{posted}</h4><p>Posted</p></div>
            <div class="stat"><h4>{interval}m</h4><p>Interval</p></div>
            <div class="stat"><h4>{'🟢' if scheduler_running else '🔴'}</h4><p>Sched</p></div>
            <div class="stat"><h4>{'🟢' if bot_running else '🔴'}</h4><p>Bot</p></div>
        </div>
        {'<div class="success">✅ Session: '+sessions[0]+'</div>' if sessions else '<div class="warning">⚠️ <a href="/auth">Login required</a></div>'}
        {'<div class="success">✅ Bot token set</div>' if has_token else '<div class="warning">⚠️ <a href="/settings">Add bot token</a></div>'}
        {f'<div class="info">📬 {pending} pending, posting every {interval}m</div>' if pending else '<div class="info">📭 Queue empty</div>'}
        <div class="info">🕐 Last: {last_post_time.strftime('%H:%M:%S') if last_post_time else 'Never'} | Posts: {posts_made}</div>
    </div>
    <div class="card">
        <h2>📱 Quick Start</h2>
        <div class="info">
            1. Settings → Add bot token<br>
            2. Auth → Login with phone<br>
            3. Channels → Add channel<br>
            4. Send photos to your bot<br>
            5. Auto-posts every {interval} minutes!
        </div>
    </div>
    '''
    return render('home', content)

@app.route('/auth')
def auth():
    sessions = get_sessions()
    shtml = ''.join([f'<li><strong>{s}</strong> <span class="badge badge-ok">Active</span></li>' for s in sessions])
    
    content = f'''
    <div class="card">
        <h2>🔐 Login</h2>
        <form method="POST" action="/auth/send">
            <div class="form-group">
                <label>Phone (with country code)</label>
                <input type="text" name="phone" placeholder="+233597832202" required>
            </div>
            <button type="submit">Send Code →</button>
        </form>
    </div>
    {f'<div class="card"><h2>📱 Sessions</h2><ul class="list">{shtml}</ul></div>' if sessions else ''}
    '''
    return render('auth', content, request.args.get('error'), request.args.get('success'))

@app.route('/auth/send', methods=['POST'])
def auth_send():
    phone = request.form.get('phone', '').strip()
    if not phone:
        return redirect(url_for('auth', error='Phone required'))
    if not phone.startswith('+'):
        phone = '+' + phone
    
    session_file = os.path.join(SESSION_PATH, f'session_{phone.replace("+", "")}')
    
    try:
        result = run_async(async_send_code(phone, session_file))
        
        if result['status'] == 'authorized':
            return redirect(url_for('auth', success=f"Already logged in as {result['name']}"))
        
        content = f'''
        <div class="card">
            <h2>🔐 Enter Code</h2>
            <div class="info">📱 Code sent to {phone}</div>
            <form method="POST" action="/auth/verify">
                <input type="hidden" name="phone" value="{phone}">
                <div class="form-group">
                    <label>Verification Code</label>
                    <input type="text" name="code" placeholder="12345" required autofocus>
                </div>
                <button type="submit">Verify →</button>
            </form>
        </div>
        '''
        return render('auth', content)
    except Exception as e:
        logger.error(f"Send code error: {e}")
        return redirect(url_for('auth', error=str(e)))

@app.route('/auth/verify', methods=['POST'])
def auth_verify():
    phone = request.form.get('phone', '')
    code = request.form.get('code', '').strip()
    
    try:
        result = run_async(async_verify_code(phone, code, auth_data.get('phone_code_hash')))
        
        if result['status'] == 'success':
            return render('auth', f'''
            <div class="card">
                <h2>✅ Success!</h2>
                <div class="success">Logged in as {result['name']}</div>
                <div class="actions">
                    <a href="/" class="btn">🏠 Home</a>
                    <a href="/channels" class="btn">📢 Channels</a>
                </div>
            </div>
            ''', success='Authenticated!')
        elif result['status'] == '2fa':
            content = f'''
            <div class="card">
                <h2>🔐 2FA Password</h2>
                <form method="POST" action="/auth/2fa">
                    <input type="hidden" name="phone" value="{phone}">
                    <div class="form-group">
                        <label>Cloud Password</label>
                        <input type="password" name="password" required autofocus>
                    </div>
                    <button type="submit">Login →</button>
                </form>
            </div>
            '''
            return render('auth', content)
        else:
            return redirect(url_for('auth', error=result.get('message', 'Error')))
    except Exception as e:
        logger.error(f"Verify error: {e}")
        return redirect(url_for('auth', error=str(e)))

@app.route('/auth/2fa', methods=['POST'])
def auth_2fa():
    password = request.form.get('password', '')
    
    try:
        result = run_async(async_verify_2fa(password))
        if result['status'] == 'success':
            return redirect(url_for('home'))
        return redirect(url_for('auth', error=result.get('message', 'Error')))
    except Exception as e:
        return redirect(url_for('auth', error=str(e)))

@app.route('/channels')
def channels():
    config = load_config()
    chs = config.get('channels', {})
    active = config.get('active_channel_id')
    
    html = ''
    for cid, name in chs.items():
        is_active = str(cid) == str(active)
        html += f'''
        <li class="{'active' if is_active else ''}">
            <div><strong>{name}</strong><br><small>{cid}</small></div>
            <div class="actions">
                {f'<span class="badge badge-ok">Active</span>' if is_active else f'<form method="POST" action="/channels/activate" style="display:inline"><input type="hidden" name="id" value="{cid}"><button class="btn btn-success" style="padding:6px 12px;font-size:12px">Activate</button></form>'}
                <form method="POST" action="/channels/remove" style="display:inline">
                    <input type="hidden" name="id" value="{cid}">
                    <button class="btn btn-danger" style="padding:6px 12px;font-size:12px">Remove</button>
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
                <input type="text" name="id" placeholder="-1001234567890" required>
                <small>Forward msg from channel to @userinfobot</small>
            </div>
            <div class="form-group">
                <label>Name</label>
                <input type="text" name="name" placeholder="My Channel" required>
            </div>
            <button type="submit">➕ Add</button>
        </form>
    </div>
    <div class="card">
        <h2>📋 Channels</h2>
        {f'<ul class="list">{html}</ul>' if chs else '<div class="info">No channels yet</div>'}
    </div>
    '''
    return render('channels', content, request.args.get('error'), request.args.get('success'))

@app.route('/channels/add', methods=['POST'])
def channels_add():
    cid = request.form.get('id', '').strip()
    name = request.form.get('name', '').strip()
    if not cid or not name:
        return redirect(url_for('channels', error='Both fields required'))
    config = load_config()
    if 'channels' not in config:
        config['channels'] = {}
    config['channels'][cid] = name
    if not config.get('active_channel_id'):
        config['active_channel_id'] = cid
    save_config(config)
    return redirect(url_for('channels', success=f'"{name}" added!'))

@app.route('/channels/activate', methods=['POST'])
def channels_activate():
    cid = request.form.get('id', '').strip()
    config = load_config()
    if cid in config.get('channels', {}):
        config['active_channel_id'] = cid
        save_config(config)
        return redirect(url_for('channels', success='Activated!'))
    return redirect(url_for('channels', error='Not found'))

@app.route('/channels/remove', methods=['POST'])
def channels_remove():
    cid = request.form.get('id', '').strip()
    config = load_config()
    if cid in config.get('channels', {}):
        name = config['channels'].pop(cid)
        if config.get('active_channel_id') == cid:
            config['active_channel_id'] = list(config['channels'].keys())[0] if config['channels'] else None
        save_config(config)
        return redirect(url_for('channels', success=f'"{name}" removed'))
    return redirect(url_for('channels', error='Not found'))

@app.route('/queue')
def queue_page():
    queue = get_queue()
    pending = [q for q in queue if not q.get('posted')]
    posted = [q for q in queue if q.get('posted')]
    
    html = ''
    for item in pending[:20]:
        html += f'<li><strong>{os.path.basename(item["file_path"])}</strong></li>'
    
    content = f'''
    <div class="card">
        <h2>📬 Queue</h2>
        <div class="grid">
            <div class="stat"><h4>{len(pending)}</h4><p>Pending</p></div>
            <div class="stat"><h4>{len(posted)}</h4><p>Posted</p></div>
        </div>
    </div>
    <div class="card">
        <h2>📋 Pending</h2>
        {f'<ul class="list">{html}</ul>' if html else '<div class="info">Queue empty. Send photos to bot!</div>'}
    </div>
    '''
    return render('queue', content)

@app.route('/settings')
def settings():
    config = load_config()
    intervals = [1, 2, 3, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1440]
    opts = ''.join([f'<option value="{i}" {"selected" if config.get("posting_interval_minutes")==i else ""}>{i}m{f" ({i//60}h)" if i>=60 else ""}</option>' for i in intervals])
    
    content = f'''
    <div class="card">
        <h2>⚙️ Settings</h2>
        <form method="POST" action="/settings/save">
            <div class="form-group">
                <label>Bot Token (@BotFather)</label>
                <input type="text" name="bot_token" value="{config.get('bot_token', '')}" placeholder="123456:ABC...">
            </div>
            <div class="form-group">
                <label>Owner Username</label>
                <input type="text" name="owner" value="{config.get('owner_username', '')}" placeholder="yourusername">
            </div>
            <div class="form-group">
                <label>Posting Interval</label>
                <select name="interval">{opts}</select>
            </div>
            <div class="form-group">
                <label><input type="checkbox" name="enabled" {'checked' if config.get('enabled', True) else ''}> Enable Auto-Posting</label>
            </div>
            <button type="submit">💾 Save</button>
        </form>
    </div>
    <div class="card">
        <h2>📊 Status</h2>
        <div class="info">
            Scheduler: {'🟢 Running' if scheduler_running else '🔴 Stopped'}<br>
            Bot: {'🟢 Running' if bot_running else '🔴 Stopped'}<br>
            Posts: {posts_made} | Last: {last_post_time.strftime('%H:%M:%S') if last_post_time else 'Never'}
        </div>
    </div>
    '''
    return render('settings', content, request.args.get('error'), request.args.get('success'))

@app.route('/settings/save', methods=['POST'])
def settings_save():
    config = load_config()
    config['bot_token'] = request.form.get('bot_token', '').strip()
    config['owner_username'] = request.form.get('owner', '').strip().replace('@', '')
    config['posting_interval_minutes'] = int(request.form.get('interval', 60))
    config['enabled'] = 'enabled' in request.form
    save_config(config)
    return redirect(url_for('settings', success='Saved!'))

# ============== STARTUP ==============
def start_services():
    # Initialize Telethon event loop
    get_telethon_loop()
    
    # Scheduler thread
    threading.Thread(target=run_scheduler, daemon=True).start()
    logger.info("📅 Scheduler started")
    
    # Bot receiver thread
    threading.Thread(target=start_bot_receiver, daemon=True).start()
    logger.info("🤖 Bot thread started")

if __name__ == '__main__':
    start_services()
    port = int(os.getenv('PORT', 10000))
    logger.info(f"🌐 http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
