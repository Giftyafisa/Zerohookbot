"""
Zerohook Bot - Complete Web UI + Bot Scheduler
Handles authentication, configuration, and automated posting
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
from telethon.sync import TelegramClient as SyncTelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError
from dotenv import load_dotenv
import telebot
from tinydb import TinyDB, Query

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Telegram credentials
API_ID = int(os.getenv('TG_API_ID', '32685245'))
API_HASH = os.getenv('TG_API_HASH', '6b0237418574e0b8287e8aafb32fd6ca')

# Paths
BASE_DIR = os.path.dirname(__file__)
SESSION_PATH = os.path.join(BASE_DIR, 'sessions')
RESOURCE_PATH = os.path.join(BASE_DIR, 'resources')
MEDIA_PATH = os.path.join(BASE_DIR, 'media', 'autopost')
AUTOPOST_CONFIG_PATH = os.path.join(RESOURCE_PATH, 'autopostConfig.json')
QUEUE_DB_PATH = os.path.join(RESOURCE_PATH, 'post_queue.json')

# Ensure directories exist
os.makedirs(SESSION_PATH, exist_ok=True)
os.makedirs(RESOURCE_PATH, exist_ok=True)
os.makedirs(MEDIA_PATH, exist_ok=True)

# Global state
auth_state = {'phone': None, 'phone_code_hash': None, 'session_file': None, 'client': None}
bot_receiver = None
bot_receiver_thread = None
scheduler_running = False
last_post_time = None
posts_made = 0
bot_receiver_running = False

def load_config():
    """Load configuration"""
    defaults = {
        'bot_token': os.getenv('TG_BOT_TOKEN', ''),
        'owner_username': os.getenv('TG_OWNER_USERNAME', ''),
        'channels': {},
        'active_channel_id': None,
        'posting_interval_minutes': 60,
        'posting_mode': 'interval',
        'posting_hour': 13,
        'posting_minute': 0,
        'enabled': True
    }
    if os.path.exists(AUTOPOST_CONFIG_PATH):
        try:
            with open(AUTOPOST_CONFIG_PATH, 'r') as f:
                saved = json.load(f)
                defaults.update(saved)
        except:
            pass
    return defaults

def save_config(config):
    """Save configuration"""
    with open(AUTOPOST_CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)

def get_sessions():
    """Get list of session files"""
    if not os.path.exists(SESSION_PATH):
        return []
    return [f.replace('.session', '') for f in os.listdir(SESSION_PATH) if f.endswith('.session')]

def get_queue():
    """Get post queue"""
    db = TinyDB(QUEUE_DB_PATH)
    return db.all()

def add_to_queue(file_path, caption=''):
    """Add item to queue"""
    db = TinyDB(QUEUE_DB_PATH)
    db.insert({
        'file_path': file_path,
        'caption': caption,
        'added_at': datetime.now().isoformat(),
        'posted': False
    })

def get_next_post():
    """Get next unposted item"""
    db = TinyDB(QUEUE_DB_PATH)
    Post = Query()
    result = db.search(Post.posted == False)
    return result[0] if result else None

def mark_as_posted(file_path):
    """Mark item as posted"""
    db = TinyDB(QUEUE_DB_PATH)
    Post = Query()
    db.update({'posted': True, 'posted_at': datetime.now().isoformat()}, Post.file_path == file_path)

# ============== BOT RECEIVER ==============
def create_bot_receiver():
    """Create and configure bot receiver"""
    config = load_config()
    
    if not config.get('bot_token'):
        return None
    
    try:
        bot = telebot.TeleBot(config['bot_token'])
        owner = config.get('owner_username', '').replace('@', '').lower()
        
        @bot.message_handler(content_types=['photo'])
        def handle_photo(message):
            username = (message.from_user.username or '').lower()
            if owner and username != owner:
                bot.reply_to(message, "❌ You're not authorized")
                return
            
            try:
                file_info = bot.get_file(message.photo[-1].file_id)
                downloaded = bot.download_file(file_info.file_path)
                
                filename = f"photo_{int(time.time())}.jpg"
                filepath = os.path.join(MEDIA_PATH, filename)
                
                with open(filepath, 'wb') as f:
                    f.write(downloaded)
                
                caption = message.caption or ''
                add_to_queue(filepath, caption)
                
                queue = get_queue()
                pending = len([q for q in queue if not q.get('posted')])
                bot.reply_to(message, f"✅ Photo queued!\n📊 Queue: {pending} pending posts")
                logger.info(f"📸 Photo queued from {username}")
            except Exception as e:
                logger.error(f"Photo error: {e}")
                bot.reply_to(message, f"❌ Error: {e}")
        
        @bot.message_handler(content_types=['video'])
        def handle_video(message):
            username = (message.from_user.username or '').lower()
            if owner and username != owner:
                bot.reply_to(message, "❌ You're not authorized")
                return
            
            try:
                file_info = bot.get_file(message.video.file_id)
                downloaded = bot.download_file(file_info.file_path)
                
                filename = f"video_{int(time.time())}.mp4"
                filepath = os.path.join(MEDIA_PATH, filename)
                
                with open(filepath, 'wb') as f:
                    f.write(downloaded)
                
                caption = message.caption or ''
                add_to_queue(filepath, caption)
                
                queue = get_queue()
                pending = len([q for q in queue if not q.get('posted')])
                bot.reply_to(message, f"✅ Video queued!\n📊 Queue: {pending} pending posts")
                logger.info(f"🎥 Video queued from {username}")
            except Exception as e:
                logger.error(f"Video error: {e}")
                bot.reply_to(message, f"❌ Error: {e}")
        
        @bot.message_handler(commands=['start'])
        def handle_start(message):
            bot.reply_to(message, "👋 Welcome to Zerohook Bot!\n\nSend me photos or videos to queue for posting.\n\n/status - Check bot status\n/queue - View pending posts\n/help - Show help")
        
        @bot.message_handler(commands=['status'])
        def handle_status(message):
            config = load_config()
            queue = get_queue()
            pending = len([q for q in queue if not q.get('posted')])
            posted = len([q for q in queue if q.get('posted')])
            
            channels = config.get('channels', {})
            active_id = config.get('active_channel_id')
            channel_name = channels.get(active_id, 'Not set') if active_id else 'Not set'
            interval = config.get('posting_interval_minutes', 60)
            
            status = f"""📊 Bot Status

🔄 Scheduler: {'Running' if scheduler_running else 'Stopped'}
📢 Channel: {channel_name}
⏱ Interval: Every {interval} minutes
📬 Pending: {pending} posts
✅ Posted: {posted} posts
🕐 Last post: {last_post_time.strftime('%H:%M') if last_post_time else 'Never'}

🌐 Panel: https://zerohookbot.onrender.com"""
            
            bot.reply_to(message, status)
        
        @bot.message_handler(commands=['queue'])
        def handle_queue(message):
            queue = get_queue()
            pending = [q for q in queue if not q.get('posted')]
            
            if not pending:
                bot.reply_to(message, "📭 Queue is empty!\n\nSend me photos or videos to post.")
                return
            
            text = "📋 Pending Posts:\n\n"
            for i, item in enumerate(pending[:10], 1):
                text += f"{i}. {os.path.basename(item['file_path'])}\n"
            
            if len(pending) > 10:
                text += f"\n... and {len(pending) - 10} more"
            
            bot.reply_to(message, text)
        
        @bot.message_handler(commands=['help'])
        def handle_help(message):
            help_text = """🤖 Zerohook Bot

📸 Send photo - Queue for posting
🎥 Send video - Queue for posting

Commands:
/status - Bot status
/queue - Pending posts
/help - This help

🌐 Web: https://zerohookbot.onrender.com"""
            bot.reply_to(message, help_text)
        
        @bot.message_handler(func=lambda m: True)
        def handle_other(message):
            bot.reply_to(message, "📸 Send me a photo or video to queue!\n\n/help for commands")
        
        return bot
    except Exception as e:
        logger.error(f"Bot creation error: {e}")
        return None

def start_bot_receiver():
    """Start Telegram bot receiver with auto-restart"""
    global bot_receiver, bot_receiver_running
    
    while True:
        config = load_config()
        token = config.get('bot_token')
        
        if not token:
            logger.info("⏳ Waiting for bot token in settings...")
            time.sleep(30)
            continue
        
        try:
            bot_receiver = create_bot_receiver()
            if bot_receiver:
                bot_receiver_running = True
                logger.info("🤖 Bot receiver started!")
                bot_receiver.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e:
            logger.error(f"Bot receiver error: {e}")
            bot_receiver_running = False
        
        logger.info("🔄 Bot receiver restarting in 10s...")
        time.sleep(10)

# ============== SCHEDULER ==============
def run_scheduler():
    """Run the posting scheduler"""
    global scheduler_running, last_post_time, posts_made
    scheduler_running = True
    logger.info("📅 Scheduler started")
    
    while scheduler_running:
        try:
            config = load_config()
            
            if not config.get('enabled', True):
                time.sleep(30)
                continue
            
            if not config.get('active_channel_id'):
                logger.debug("No active channel configured")
                time.sleep(30)
                continue
            
            sessions = get_sessions()
            if not sessions:
                logger.debug("No sessions available")
                time.sleep(30)
                continue
            
            interval = config.get('posting_interval_minutes', 60)
            
            # Check if it's time to post
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
                    logger.info(f"📤 Attempting to post: {os.path.basename(post['file_path'])}")
                    success = do_post(post, config, sessions[0])
                    if success:
                        mark_as_posted(post['file_path'])
                        last_post_time = datetime.now()
                        posts_made += 1
                        logger.info(f"✅ Posted successfully! Total: {posts_made}")
                    else:
                        logger.warning("❌ Post failed")
                else:
                    logger.debug("No posts in queue")
            
            time.sleep(30)
            
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            time.sleep(60)

def do_post(post, config, session_name):
    """Execute a post to the channel using sync client"""
    try:
        session_file = os.path.join(SESSION_PATH, session_name)
        channel_id = config.get('active_channel_id')
        
        if not channel_id:
            logger.warning("No active channel")
            return False
        
        # Convert channel ID
        try:
            channel = int(channel_id)
        except:
            channel = channel_id
        
        file_path = post['file_path']
        caption = post.get('caption', '')
        
        if not os.path.exists(file_path):
            logger.warning(f"File not found: {file_path}")
            return False
        
        # Create event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Use sync client for posting
        with SyncTelegramClient(session_file, API_ID, API_HASH) as client:
            if not client.is_user_authorized():
                logger.warning("Session not authorized")
                return False
            
            client.send_file(channel, file_path, caption=caption)
            logger.info(f"📤 Sent to channel: {channel}")
            return True
            
    except Exception as e:
        logger.error(f"Post error: {e}")
        return False

# ============== WEB UI ==============
HTML_BASE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Zerohook Bot Control Panel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="60">
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
        input:focus, select:focus { outline: none; border-color: #667eea; }
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
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .stat-box { background: #f8f9fa; padding: 20px; border-radius: 10px; text-align: center; }
        .stat-box h4 { color: #667eea; font-size: 24px; margin-bottom: 5px; }
        .stat-box p { color: #666; font-size: 12px; margin: 0; }
        .channel-list { list-style: none; }
        .channel-item {
            display: flex; justify-content: space-between; align-items: center;
            padding: 15px; background: #f8f9fa; border-radius: 10px; margin-bottom: 10px;
        }
        .channel-item.active { background: #e8f4fd; border: 2px solid #667eea; }
        .badge { display: inline-block; padding: 5px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; }
        .badge-success { background: #d4edda; color: #155724; }
        .badge-primary { background: #cce5ff; color: #004085; }
        .badge-warning { background: #fff3cd; color: #856404; }
        .actions { display: flex; gap: 10px; flex-wrap: wrap; }
        .status-card { display: flex; align-items: center; padding: 20px; background: #f8f9fa; border-radius: 10px; margin-bottom: 15px; }
        .status-icon { font-size: 40px; margin-right: 20px; }
        .status-info h3 { color: #333; margin-bottom: 5px; }
        .status-info p { color: #666; margin: 0; font-size: 14px; }
        small { color: #666; display: block; margin-top: 5px; }
        .queue-item { padding: 10px; background: #f0f0f0; border-radius: 8px; margin-bottom: 8px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h1>🤖 Zerohook Bot</h1>
            <p>Auto-post to Telegram channels</p>
            <div class="nav">
                <a href="/" class="{{ 'active' if page == 'home' else '' }}">🏠 Home</a>
                <a href="/auth" class="{{ 'active' if page == 'auth' else '' }}">🔐 Auth</a>
                <a href="/channels" class="{{ 'active' if page == 'channels' else '' }}">📢 Channels</a>
                <a href="/queue" class="{{ 'active' if page == 'queue' else '' }}">📬 Queue</a>
                <a href="/settings" class="{{ 'active' if page == 'settings' else '' }}">⚙️ Settings</a>
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
            <div class="stat-box"><h4>{len(sessions)}</h4><p>Sessions</p></div>
            <div class="stat-box"><h4>{len(config.get('channels', {}))}</h4><p>Channels</p></div>
            <div class="stat-box"><h4>{pending}</h4><p>Pending</p></div>
            <div class="stat-box"><h4>{posted}</h4><p>Posted</p></div>
            <div class="stat-box"><h4>{interval}m</h4><p>Interval</p></div>
            <div class="stat-box"><h4>{'🟢' if scheduler_running else '🔴'}</h4><p>Scheduler</p></div>
            <div class="stat-box"><h4>{'🟢' if bot_receiver_running else '🔴'}</h4><p>Bot</p></div>
        </div>
    </div>
    <div class="card">
        <h2>📋 Status</h2>
        {'<div class="success">✅ Session active: ' + sessions[0] + '</div>' if sessions else '<div class="warning">⚠️ No session! <a href="/auth">Authenticate</a></div>'}
        {'<div class="success">✅ Bot token configured</div>' if has_token else '<div class="warning">⚠️ No bot token! <a href="/settings">Add in Settings</a></div>'}
        {f'<div class="info">📬 {pending} posts in queue. Posting every {interval} minutes.</div>' if pending else '<div class="info">📭 Queue empty. Send photos to your bot!</div>'}
        {f'<div class="info">🕐 Last post: {last_post_time.strftime("%H:%M:%S") if last_post_time else "Never"}</div>'}
    </div>
    <div class="card">
        <h2>📱 How to Use</h2>
        <div class="info">
            <strong>1.</strong> Go to Settings → Add bot token<br>
            <strong>2.</strong> Go to Auth → Login with phone<br>
            <strong>3.</strong> Go to Channels → Add your channel<br>
            <strong>4.</strong> Send photos to your bot on Telegram<br>
            <strong>5.</strong> Bot posts automatically!
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
        <h2><span class="step">1</span>Login with Phone</h2>
        <form method="POST" action="/auth/send_code">
            <div class="form-group">
                <label>Phone Number (with country code)</label>
                <input type="text" name="phone" placeholder="+233597832202" required>
            </div>
            <button type="submit">Send Code →</button>
        </form>
    </div>
    {f'<div class="card"><h2>📱 Active Sessions</h2><ul class="channel-list">{sessions_html}</ul></div>' if sessions else ''}
    '''
    return render_page('auth', content, error=request.args.get('error'), success=request.args.get('success'))

@app.route('/auth/send_code', methods=['POST'])
def send_code():
    phone = request.form.get('phone', '').strip()
    if not phone:
        return redirect(url_for('auth', error='Phone number required'))
    
    if not phone.startswith('+'):
        phone = '+' + phone
    
    session_file = os.path.join(SESSION_PATH, f'session_{phone.replace("+", "")}')
    
    try:
        # Create event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Use sync client
        client = SyncTelegramClient(session_file, API_ID, API_HASH)
        client.connect()
        
        if client.is_user_authorized():
            me = client.get_me()
            client.disconnect()
            return redirect(url_for('auth', success=f'Already logged in as {me.first_name}'))
        
        result = client.send_code_request(phone)
        auth_state['phone'] = phone
        auth_state['phone_code_hash'] = result.phone_code_hash
        auth_state['session_file'] = session_file
        # Don't disconnect - keep connection for verification
        auth_state['client'] = client
        
        content = f'''
        <div class="card">
            <h2><span class="step">2</span>Enter Code</h2>
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
        return render_page('auth', content)
    except Exception as e:
        logger.error(f"Send code error: {e}")
        return redirect(url_for('auth', error=str(e)))

@app.route('/auth/verify_code', methods=['POST'])
def verify_code():
    phone = request.form.get('phone', '')
    code = request.form.get('code', '').strip()
    
    client = auth_state.get('client')
    if not client:
        return redirect(url_for('auth', error='Session expired. Start again.'))
    
    try:
        client.sign_in(phone, code, phone_code_hash=auth_state['phone_code_hash'])
        me = client.get_me()
        user_info = f"{me.first_name or ''} {me.last_name or ''}"
        client.disconnect()
        auth_state['client'] = None
        
        content = f'''
        <div class="card">
            <h2>✅ Success!</h2>
            <div class="success"><strong>Logged in as:</strong> {user_info}</div>
            <div class="actions">
                <a href="/" class="btn">🏠 Dashboard</a>
                <a href="/channels" class="btn btn-secondary">📢 Channels</a>
            </div>
        </div>
        '''
        return render_page('auth', content, success='Authenticated!')
        
    except SessionPasswordNeededError:
        content = f'''
        <div class="card">
            <h2><span class="step">3</span>2FA Password</h2>
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
        return render_page('auth', content)
    except PhoneCodeInvalidError:
        return redirect(url_for('auth', error='Invalid code. Try again.'))
    except Exception as e:
        logger.error(f"Verify error: {e}")
        if client:
            try:
                client.disconnect()
            except:
                pass
        auth_state['client'] = None
        return redirect(url_for('auth', error=str(e)))

@app.route('/auth/verify_2fa', methods=['POST'])
def verify_2fa():
    password = request.form.get('password', '')
    
    client = auth_state.get('client')
    if not client:
        return redirect(url_for('auth', error='Session expired'))
    
    try:
        client.sign_in(password=password)
        me = client.get_me()
        user_info = f"{me.first_name or ''} {me.last_name or ''}"
        client.disconnect()
        auth_state['client'] = None
        return redirect(url_for('home'))
    except PasswordHashInvalidError:
        return redirect(url_for('auth', error='Invalid password'))
    except Exception as e:
        if client:
            try:
                client.disconnect()
            except:
                pass
        auth_state['client'] = None
        return redirect(url_for('auth', error=str(e)))

@app.route('/channels')
def channels():
    config = load_config()
    channels_dict = config.get('channels', {})
    active = config.get('active_channel_id')
    
    channels_html = ''
    for cid, name in channels_dict.items():
        is_active = str(cid) == str(active)
        channels_html += f'''
        <li class="channel-item {'active' if is_active else ''}">
            <div><strong>{name}</strong><br><small>ID: {cid}</small></div>
            <div class="actions">
                {f'<span class="badge badge-primary">Active</span>' if is_active else f'<form method="POST" action="/channels/activate" style="display:inline;"><input type="hidden" name="channel_id" value="{cid}"><button class="btn btn-success" style="padding:8px 15px;font-size:14px;">Set Active</button></form>'}
                <form method="POST" action="/channels/remove" style="display:inline;">
                    <input type="hidden" name="channel_id" value="{cid}">
                    <button class="btn btn-danger" style="padding:8px 15px;font-size:14px;">Remove</button>
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
                <small>Forward message from channel to @userinfobot to get ID</small>
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
    
    config = load_config()
    if 'channels' not in config:
        config['channels'] = {}
    
    config['channels'][channel_id] = channel_name
    if not config.get('active_channel_id'):
        config['active_channel_id'] = channel_id
    
    save_config(config)
    return redirect(url_for('channels', success=f'"{channel_name}" added!'))

@app.route('/channels/activate', methods=['POST'])
def activate_channel():
    channel_id = request.form.get('channel_id', '').strip()
    config = load_config()
    
    if channel_id in config.get('channels', {}):
        config['active_channel_id'] = channel_id
        save_config(config)
        return redirect(url_for('channels', success='Channel activated!'))
    return redirect(url_for('channels', error='Not found'))

@app.route('/channels/remove', methods=['POST'])
def remove_channel():
    channel_id = request.form.get('channel_id', '').strip()
    config = load_config()
    
    if channel_id in config.get('channels', {}):
        name = config['channels'].pop(channel_id)
        if config.get('active_channel_id') == channel_id:
            config['active_channel_id'] = list(config['channels'].keys())[0] if config['channels'] else None
        save_config(config)
        return redirect(url_for('channels', success=f'"{name}" removed!'))
    return redirect(url_for('channels', error='Not found'))

@app.route('/queue')
def queue_page():
    queue = get_queue()
    pending = [q for q in queue if not q.get('posted')]
    posted = [q for q in queue if q.get('posted')]
    
    pending_html = ''
    for item in pending[:20]:
        filename = os.path.basename(item['file_path'])
        caption = (item.get('caption', '') or '')[:30]
        pending_html += f'<div class="queue-item"><strong>{filename}</strong> {caption}</div>'
    
    content = f'''
    <div class="card">
        <h2>📬 Post Queue</h2>
        <div class="grid">
            <div class="stat-box"><h4>{len(pending)}</h4><p>Pending</p></div>
            <div class="stat-box"><h4>{len(posted)}</h4><p>Posted</p></div>
        </div>
    </div>
    <div class="card">
        <h2>📋 Pending Posts</h2>
        {pending_html if pending_html else '<div class="info">Queue empty. Send photos to your bot!</div>'}
    </div>
    '''
    return render_page('queue', content)

@app.route('/settings')
def settings():
    config = load_config()
    intervals = [1, 2, 3, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1440]
    
    interval_options = ''.join([
        f'<option value="{i}" {"selected" if config.get("posting_interval_minutes") == i else ""}>'
        f'{i} min' + (f' ({i//60}h)' if i >= 60 else '') + '</option>'
        for i in intervals
    ])
    
    content = f'''
    <div class="card">
        <h2>⚙️ Settings</h2>
        <form method="POST" action="/settings/save">
            <div class="form-group">
                <label>Bot Token (from @BotFather)</label>
                <input type="text" name="bot_token" value="{config.get('bot_token', '')}" placeholder="123456:ABC-DEF...">
            </div>
            <div class="form-group">
                <label>Owner Username (who can send posts)</label>
                <input type="text" name="owner_username" value="{config.get('owner_username', '')}" placeholder="yourusername">
            </div>
            <div class="form-group">
                <label>Posting Interval</label>
                <select name="posting_interval_minutes">
                    {interval_options}
                </select>
            </div>
            <div class="form-group">
                <label>
                    <input type="checkbox" name="enabled" {'checked' if config.get('enabled', True) else ''}> 
                    Enable Auto-Posting
                </label>
            </div>
            <button type="submit">💾 Save</button>
        </form>
    </div>
    <div class="card">
        <h2>📊 Status</h2>
        <div class="status-card">
            <div class="status-icon">{'🟢' if scheduler_running else '🔴'}</div>
            <div class="status-info">
                <h3>Scheduler: {'Running' if scheduler_running else 'Stopped'}</h3>
            </div>
        </div>
        <div class="status-card">
            <div class="status-icon">{'🟢' if bot_receiver_running else '🔴'}</div>
            <div class="status-info">
                <h3>Bot Receiver: {'Running' if bot_receiver_running else 'Waiting for token'}</h3>
            </div>
        </div>
        <div class="status-card">
            <div class="status-icon">📤</div>
            <div class="status-info">
                <h3>Posts: {posts_made}</h3>
                <p>Last: {last_post_time.strftime('%H:%M:%S') if last_post_time else 'Never'}</p>
            </div>
        </div>
    </div>
    '''
    return render_page('settings', content, error=request.args.get('error'), success=request.args.get('success'))

@app.route('/settings/save', methods=['POST'])
def save_settings():
    config = load_config()
    config['bot_token'] = request.form.get('bot_token', '').strip()
    config['owner_username'] = request.form.get('owner_username', '').strip().replace('@', '')
    config['posting_interval_minutes'] = int(request.form.get('posting_interval_minutes', 60))
    config['enabled'] = 'enabled' in request.form
    
    save_config(config)
    
    # Restart bot receiver if token changed
    global bot_receiver
    if bot_receiver:
        try:
            bot_receiver.stop_polling()
        except:
            pass
    
    return redirect(url_for('settings', success='Settings saved! Bot will restart with new token.'))

# ============== STARTUP ==============
def start_services():
    """Start bot receiver and scheduler in background threads"""
    # Start scheduler
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    logger.info("📅 Scheduler thread started")
    
    # Start bot receiver
    bot_thread = threading.Thread(target=start_bot_receiver, daemon=True)
    bot_thread.start()
    logger.info("🤖 Bot receiver thread started")

if __name__ == '__main__':
    start_services()
    port = int(os.getenv('PORT', 10000))
    logger.info(f"🌐 Web UI: http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
