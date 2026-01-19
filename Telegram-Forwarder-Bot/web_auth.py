"""
Zerohook Bot - Content Groups System
Post different content to different channel groups with scheduling
Supports: Images, Videos, Text, URLs
"""
import os
import json
import asyncio
import threading
import time
import logging
import uuid
import requests
import base64
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, redirect, url_for, jsonify
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError
from dotenv import load_dotenv
import telebot
from tinydb import TinyDB, Query

load_dotenv()

# ============== DATABASE CONFIG ==============
# MongoDB connection
MONGO_URI = os.getenv('MONGO_URI', 'mongodb+srv://zerohookbot:11221122@zerohookbot.xtq3cj7.mongodb.net/?appName=zerohookbot')
USE_MONGO = True

mongo_client = None
mongo_db = None

def get_mongo_db():
    """Get MongoDB database connection"""
    global mongo_client, mongo_db
    if mongo_db is None:
        try:
            from pymongo import MongoClient
            mongo_client = MongoClient(MONGO_URI)
            mongo_db = mongo_client['zerohookbot']
            # Test connection
            mongo_client.admin.command('ping')
            logger.info("✅ MongoDB connected!")
        except Exception as e:
            logger.error(f"MongoDB connection error: {e}")
            return None
    return mongo_db

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.urandom(24)

# App URL for keep-alive ping
APP_URL = os.getenv('RENDER_EXTERNAL_URL', 'https://zerohookbot.onrender.com')

# Credentials
API_ID = int(os.getenv('TG_API_ID', '32685245'))
API_HASH = os.getenv('TG_API_HASH', '6b0237418574e0b8287e8aafb32fd6ca')

# Paths
BASE_DIR = os.path.dirname(__file__)
SESSION_PATH = os.path.join(BASE_DIR, 'sessions')
RESOURCE_PATH = os.path.join(BASE_DIR, 'resources')
MEDIA_PATH = os.path.join(BASE_DIR, 'media', 'autopost')
CONFIG_PATH = os.path.join(RESOURCE_PATH, 'autopostConfig.json')
GROUPS_PATH = os.path.join(RESOURCE_PATH, 'content_groups.json')

os.makedirs(SESSION_PATH, exist_ok=True)
os.makedirs(RESOURCE_PATH, exist_ok=True)
os.makedirs(MEDIA_PATH, exist_ok=True)

# Global state
auth_data = {}
scheduler_running = False
bot_running = False
group_post_counts = {}  # Track posts per group
group_last_post = {}    # Track last post time per group

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
        time.sleep(0.5)
    return telethon_loop

def run_async(coro):
    """Run async coroutine in the Telethon event loop"""
    loop = get_telethon_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=120)

# ============== DATABASE FUNCTIONS ==============
def init_database():
    """Initialize MongoDB collections"""
    db = get_mongo_db()
    if db is None:
        logger.warning("MongoDB not available, using local files")
        return
    
    # Create indexes for better performance
    try:
        db.config.create_index("key", unique=True)
        db.content_groups.create_index("id", unique=True)
        db.channels.create_index("id", unique=True)
        logger.info("✅ MongoDB indexes created")
    except Exception as e:
        logger.error(f"Index creation error: {e}")

# ============== CONFIG ==============
def load_config():
    defaults = {
        'bot_token': os.getenv('TG_BOT_TOKEN', ''),
        'owner_username': os.getenv('TG_OWNER_USERNAME', ''),
        'channels': {},  # id -> name mapping
        'enabled': True
    }
    
    db = get_mongo_db()
    if db:
        try:
            doc = db.config.find_one({'key': 'main'})
            if doc and 'value' in doc:
                defaults.update(doc['value'])
        except Exception as e:
            logger.error(f"DB load config error: {e}")
    elif os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                defaults.update(json.load(f))
        except: pass
    return defaults

def save_config(config):
    db = get_mongo_db()
    if db:
        try:
            db.config.update_one(
                {'key': 'main'},
                {'$set': {'key': 'main', 'value': config}},
                upsert=True
            )
        except Exception as e:
            logger.error(f"DB save config error: {e}")
    else:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(config, f, indent=2)

# ============== CONTENT GROUPS ==============
"""
Content Group Structure:
{
    "id": "uuid",
    "name": "Group Name",
    "content": [
        {"id": "uuid", "file_path": "path", "caption": "", "added_at": "iso"}
    ],
    "channels": ["-100xxx", "-100yyy"],  # channel IDs
    "interval_minutes": 1,
    "duration_type": "forever" | "hours" | "count",
    "duration_value": 24,  # hours or post count
    "enabled": true,
    "started_at": "iso",
    "total_posts": 0,
    "current_content_index": 0
}
"""

def load_groups():
    """Load content groups from database or file"""
    db = get_mongo_db()
    if db:
        try:
            docs = list(db.content_groups.find({}, {'_id': 0}))
            return [doc['data'] for doc in docs if 'data' in doc]
        except Exception as e:
            logger.error(f"DB load groups error: {e}")
            return []
    
    if os.path.exists(GROUPS_PATH):
        try:
            with open(GROUPS_PATH, 'r') as f:
                return json.load(f)
        except: pass
    return []

def save_groups(groups):
    """Save content groups to database or file"""
    db = get_mongo_db()
    if db:
        try:
            # Delete all and reinsert
            db.content_groups.delete_many({})
            for g in groups:
                db.content_groups.insert_one({'id': g['id'], 'data': g})
        except Exception as e:
            logger.error(f"DB save groups error: {e}")
    else:
        with open(GROUPS_PATH, 'w') as f:
            json.dump(groups, f, indent=2)

def get_group(group_id):
    """Get a specific group by ID"""
    groups = load_groups()
    for g in groups:
        if g['id'] == group_id:
            return g
    return None

def update_group(group_id, updates):
    """Update a specific group"""
    groups = load_groups()
    for i, g in enumerate(groups):
        if g['id'] == group_id:
            groups[i].update(updates)
            save_groups(groups)
            return True
    return False

def create_group(name):
    """Create a new content group"""
    groups = load_groups()
    group = {
        'id': str(uuid.uuid4())[:8],
        'name': name,
        'content': [],
        'channels': [],
        'interval_minutes': 5,
        'duration_type': 'forever',
        'duration_value': 0,
        'enabled': False,
        'started_at': None,
        'total_posts': 0,
        'current_content_index': 0
    }
    groups.append(group)
    save_groups(groups)
    return group

def delete_group(group_id):
    """Delete a content group"""
    groups = load_groups()
    groups = [g for g in groups if g['id'] != group_id]
    save_groups(groups)

def add_content_to_group(group_id, file_path=None, caption='', content_type='file', text_content=''):
    """Add content to a group. Supports: file (photo/video), text, url"""
    groups = load_groups()
    for g in groups:
        if g['id'] == group_id:
            content_item = {
                'id': str(uuid.uuid4())[:8],
                'type': content_type,  # 'file', 'text', 'url'
                'file_path': file_path,
                'text_content': text_content,
                'caption': caption,
                'added_at': datetime.now().isoformat()
            }
            g['content'].append(content_item)
            save_groups(groups)
            return True
    return False

def remove_content_from_group(group_id, content_id):
    """Remove content from a group"""
    groups = load_groups()
    for g in groups:
        if g['id'] == group_id:
            g['content'] = [c for c in g['content'] if c['id'] != content_id]
            save_groups(groups)
            return True
    return False

def get_sessions():
    """Get list of session files"""
    if not os.path.exists(SESSION_PATH):
        return []
    sessions = []
    for f in os.listdir(SESSION_PATH):
        if f.endswith('.session'):
            fpath = os.path.join(SESSION_PATH, f)
            if os.path.getsize(fpath) > 1000:
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

async def async_post_to_channels(session_file, channel_ids, content_item):
    """Post content to multiple channels. Supports file, text, URL"""
    if not os.path.exists(session_file + '.session'):
        logger.warning(f"Session file not found: {session_file}")
        return 0
    
    client = TelegramClient(session_file, API_ID, API_HASH)
    posted = 0
    
    content_type = content_item.get('type', 'file')
    file_path = content_item.get('file_path')
    text_content = content_item.get('text_content', '')
    caption = content_item.get('caption', '')
    
    try:
        await client.connect()
        
        if not await client.is_user_authorized():
            logger.warning("Session not authorized")
            await client.disconnect()
            return 0
        
        for channel_id in channel_ids:
            try:
                channel = int(channel_id)
            except:
                channel = channel_id
            
            try:
                if content_type == 'file' and file_path and os.path.exists(file_path):
                    await client.send_file(channel, file_path, caption=caption)
                elif content_type in ('text', 'url'):
                    # Send text message (can include URLs)
                    message_text = text_content
                    if caption:
                        message_text += f"\n\n{caption}"
                    await client.send_message(channel, message_text)
                else:
                    logger.warning(f"Unknown content type: {content_type}")
                    continue
                    
                logger.info(f"📤 Sent to {channel}")
                posted += 1
            except Exception as e:
                logger.error(f"Failed to post to {channel}: {e}")
        
        await client.disconnect()
        return posted
    except Exception as e:
        logger.error(f"Post error: {e}")
        try:
            await client.disconnect()
        except:
            pass
        return 0

# ============== BOT RECEIVER ==============
# Pending content - waiting to be assigned to a group
pending_content = {}  # user_id -> {'file_path': path, 'caption': str}

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
            
            def is_owner(message):
                username = (message.from_user.username or '').lower()
                return not owner or username == owner
            
            @bot.message_handler(commands=['start', 'help'])
            def help_cmd(message):
                if not is_owner(message):
                    bot.reply_to(message, "❌ Not authorized")
                    return
                groups = load_groups()
                glist = '\n'.join([f"• {g['name']} ({len(g['content'])} items)" for g in groups]) or 'No groups yet'
                bot.reply_to(message, f"""🤖 *Zerohook Bot*

📸 Send photos/videos/text to add content

📋 *Your Groups:*
{glist}

📝 *Commands:*
/groups - List all groups
/newgroup NAME - Create group
/addtext GROUP\\_ID Your text here - Add text content
/delete GROUP\\_ID CONTENT\\_ID - Delete content
/status - Check status

🌐 https://zerohookbot.onrender.com""", parse_mode='Markdown')
            
            @bot.message_handler(commands=['groups'])
            def list_groups(message):
                if not is_owner(message):
                    return
                groups = load_groups()
                if not groups:
                    bot.reply_to(message, "📭 No groups yet. Use /newgroup NAME to create one.")
                    return
                
                text = "📋 *Your Content Groups:*\n\n"
                for g in groups:
                    status = "🟢" if g['enabled'] else "🔴"
                    text += f"{status} *{g['name']}* (ID: `{g['id']}`)\n"
                    text += f"   📸 {len(g['content'])} items | 📢 {len(g['channels'])} channels\n"
                    text += f"   ⏱ Every {g['interval_minutes']}m | "
                    if g['duration_type'] == 'forever':
                        text += "♾ Forever\n\n"
                    elif g['duration_type'] == 'hours':
                        text += f"⏰ {g['duration_value']}h\n\n"
                    else:
                        text += f"🔢 {g['duration_value']} posts\n\n"
                
                bot.reply_to(message, text, parse_mode='Markdown')
            
            @bot.message_handler(commands=['newgroup'])
            def new_group(message):
                if not is_owner(message):
                    return
                parts = message.text.split(maxsplit=1)
                if len(parts) < 2:
                    bot.reply_to(message, "❌ Usage: /newgroup GROUP_NAME")
                    return
                name = parts[1].strip()
                group = create_group(name)
                bot.reply_to(message, f"✅ Created group *{name}* (ID: `{group['id']}`)\n\n📝 Now send photos to add to this group!", parse_mode='Markdown')
            
            @bot.message_handler(commands=['delete'])
            def delete_content(message):
                if not is_owner(message):
                    return
                parts = message.text.split()
                if len(parts) < 3:
                    bot.reply_to(message, "❌ Usage: /delete GROUP_ID CONTENT_ID")
                    return
                group_id = parts[1]
                content_id = parts[2]
                if remove_content_from_group(group_id, content_id):
                    bot.reply_to(message, f"✅ Content `{content_id}` deleted from group `{group_id}`", parse_mode='Markdown')
                else:
                    bot.reply_to(message, "❌ Not found")
            
            @bot.message_handler(commands=['status'])
            def status_cmd(message):
                if not is_owner(message):
                    return
                groups = load_groups()
                active = len([g for g in groups if g['enabled']])
                total_content = sum(len(g['content']) for g in groups)
                total_channels = len(set(ch for g in groups for ch in g['channels']))
                
                db_status = "🟢 MongoDB" if get_mongo_db() else "📁 JSON files"
                
                text = f"""📊 *Bot Status*

📦 Groups: {len(groups)} ({active} active)
📸 Total content: {total_content}
📢 Total channels: {total_channels}
⏰ Scheduler: {'🟢' if scheduler_running else '🔴'}
🤖 Bot: {'🟢' if bot_running else '🔴'}
💾 Storage: {db_status}

🌐 Manage at: https://zerohookbot.onrender.com"""
                bot.reply_to(message, text, parse_mode='Markdown')
            
            @bot.message_handler(commands=['addtext'])
            def add_text_cmd(message):
                """Add text content directly to a group: /addtext GROUP_ID Your text here"""
                if not is_owner(message):
                    return
                
                parts = message.text.split(maxsplit=2)
                if len(parts) < 3:
                    bot.reply_to(message, "❌ Usage: /addtext GROUP\\_ID Your text or URL here", parse_mode='Markdown')
                    return
                
                group_id = parts[1]
                text_content = parts[2]
                
                group = get_group(group_id)
                if not group:
                    bot.reply_to(message, f"❌ Group `{group_id}` not found", parse_mode='Markdown')
                    return
                
                # Detect if it's a URL
                content_type = 'url' if text_content.startswith(('http://', 'https://')) else 'text'
                
                if add_content_to_group(group_id, content_type=content_type, text_content=text_content):
                    emoji = "🔗" if content_type == 'url' else "📝"
                    bot.reply_to(message, f"✅ {emoji} Added to *{group['name']}*\n📦 {len(group['content'])+1} items in group", parse_mode='Markdown')
                else:
                    bot.reply_to(message, "❌ Error adding content")
            
            @bot.message_handler(content_types=['photo'])
            def handle_photo(message):
                if not is_owner(message):
                    bot.reply_to(message, "❌ Not authorized")
                    return
                
                try:
                    file_info = bot.get_file(message.photo[-1].file_id)
                    downloaded = bot.download_file(file_info.file_path)
                    filename = f"photo_{int(time.time())}.jpg"
                    filepath = os.path.join(MEDIA_PATH, filename)
                    with open(filepath, 'wb') as f:
                        f.write(downloaded)
                    
                    # Show group selection
                    groups = load_groups()
                    if not groups:
                        # Auto-create a default group
                        group = create_group("Default")
                        groups = [group]
                    
                    # Save pending content
                    user_id = message.from_user.id
                    pending_content[user_id] = {
                        'type': 'file',
                        'file_path': filepath,
                        'caption': message.caption or ''
                    }
                    
                    # Create keyboard with groups
                    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                    for g in groups:
                        btn = telebot.types.InlineKeyboardButton(
                            f"📁 {g['name']} ({len(g['content'])})",
                            callback_data=f"addto:{g['id']}"
                        )
                        markup.add(btn)
                    
                    bot.reply_to(message, "📸 Photo received! Select a group:", reply_markup=markup)
                    logger.info(f"📸 Photo from {message.from_user.username}")
                except Exception as e:
                    bot.reply_to(message, f"❌ Error: {e}")
                    logger.error(f"Photo error: {e}")
            
            @bot.message_handler(content_types=['video'])
            def handle_video(message):
                if not is_owner(message):
                    bot.reply_to(message, "❌ Not authorized")
                    return
                
                try:
                    file_info = bot.get_file(message.video.file_id)
                    downloaded = bot.download_file(file_info.file_path)
                    filename = f"video_{int(time.time())}.mp4"
                    filepath = os.path.join(MEDIA_PATH, filename)
                    with open(filepath, 'wb') as f:
                        f.write(downloaded)
                    
                    groups = load_groups()
                    if not groups:
                        group = create_group("Default")
                        groups = [group]
                    
                    user_id = message.from_user.id
                    pending_content[user_id] = {
                        'type': 'file',
                        'file_path': filepath,
                        'caption': message.caption or ''
                    }
                    
                    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                    for g in groups:
                        btn = telebot.types.InlineKeyboardButton(
                            f"📁 {g['name']} ({len(g['content'])})",
                            callback_data=f"addto:{g['id']}"
                        )
                        markup.add(btn)
                    
                    bot.reply_to(message, "🎥 Video received! Select a group:", reply_markup=markup)
                    logger.info(f"🎥 Video from {message.from_user.username}")
                except Exception as e:
                    bot.reply_to(message, f"❌ Error: {e}")
            
            @bot.callback_query_handler(func=lambda call: call.data.startswith('addto:'))
            def handle_add_to_group(call):
                user_id = call.from_user.id
                group_id = call.data.split(':')[1]
                
                content = pending_content.get(user_id)
                if not content:
                    bot.answer_callback_query(call.id, "❌ Content expired, send again")
                    return
                
                success = add_content_to_group(
                    group_id, 
                    file_path=content.get('file_path'),
                    caption=content.get('caption', ''),
                    content_type=content.get('type', 'file'),
                    text_content=content.get('text_content', '')
                )
                
                if success:
                    group = get_group(group_id)
                    del pending_content[user_id]
                    bot.answer_callback_query(call.id, f"✅ Added to {group['name']}")
                    
                    content_desc = "📸 Media" if content.get('type') == 'file' else "📝 Text"
                    bot.edit_message_text(
                        f"✅ Added to *{group['name']}*\n{content_desc} | {len(group['content'])} items in group",
                        call.message.chat.id,
                        call.message.message_id,
                        parse_mode='Markdown'
                    )
                else:
                    bot.answer_callback_query(call.id, "❌ Error adding")
            
            @bot.message_handler(content_types=['text'])
            def handle_text(message):
                """Handle plain text messages (not commands)"""
                if not is_owner(message):
                    return
                
                # Skip commands
                if message.text.startswith('/'):
                    return
                
                text = message.text.strip()
                if not text:
                    return
                
                # Show group selection for text content
                groups = load_groups()
                if not groups:
                    group = create_group("Default")
                    groups = [group]
                
                # Detect content type
                content_type = 'url' if text.startswith(('http://', 'https://')) else 'text'
                
                user_id = message.from_user.id
                pending_content[user_id] = {
                    'type': content_type,
                    'text_content': text,
                    'caption': ''
                }
                
                emoji = "🔗 URL" if content_type == 'url' else "📝 Text"
                markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                for g in groups:
                    btn = telebot.types.InlineKeyboardButton(
                        f"📁 {g['name']} ({len(g['content'])})",
                        callback_data=f"addto:{g['id']}"
                    )
                    markup.add(btn)
                
                preview = text[:50] + "..." if len(text) > 50 else text
                bot.reply_to(message, f"{emoji} received!\n`{preview}`\n\nSelect a group:", reply_markup=markup, parse_mode='Markdown')
                logger.info(f"📝 Text from {message.from_user.username}")
            
            @bot.message_handler(func=lambda m: True)
            def other(message):
                if is_owner(message):
                    bot.reply_to(message, "📸 Send photo, video, or text!\n/help for commands")
            
            bot_running = True
            logger.info("🤖 Bot receiver started!")
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e:
            logger.error(f"Bot error: {e}")
            bot_running = False
        
        time.sleep(30)

# ============== SCHEDULER ==============
def run_scheduler():
    """Post scheduler - handles multiple groups with different schedules"""
    global scheduler_running
    scheduler_running = True
    logger.info("📅 Scheduler started")
    
    while True:
        try:
            config = load_config()
            
            if not config.get('enabled', True):
                time.sleep(30)
                continue
            
            sessions = get_sessions()
            if not sessions:
                time.sleep(30)
                continue
            
            session_file = os.path.join(SESSION_PATH, sessions[0])
            
            # Check authorization
            try:
                is_authorized = run_async(check_session_authorized(session_file))
                if not is_authorized:
                    logger.debug("Session not authorized")
                    time.sleep(60)
                    continue
            except:
                time.sleep(60)
                continue
            
            # Process each enabled group
            groups = load_groups()
            for group in groups:
                if not group.get('enabled'):
                    continue
                
                if not group.get('content'):
                    continue
                
                if not group.get('channels'):
                    continue
                
                gid = group['id']
                
                # Check duration limits
                if group['duration_type'] == 'hours':
                    if group.get('started_at'):
                        started = datetime.fromisoformat(group['started_at'])
                        elapsed_hours = (datetime.now() - started).total_seconds() / 3600
                        if elapsed_hours >= group['duration_value']:
                            update_group(gid, {'enabled': False})
                            logger.info(f"⏰ Group {group['name']} duration ended")
                            continue
                
                elif group['duration_type'] == 'count':
                    if group.get('total_posts', 0) >= group['duration_value']:
                        update_group(gid, {'enabled': False})
                        logger.info(f"🔢 Group {group['name']} post count reached")
                        continue
                
                # Check interval
                interval = group.get('interval_minutes', 5)
                last_post = group_last_post.get(gid)
                
                should_post = False
                if last_post is None:
                    should_post = True
                else:
                    elapsed = (datetime.now() - last_post).total_seconds() / 60
                    if elapsed >= interval:
                        should_post = True
                
                if not should_post:
                    continue
                
                # Get next content (cycle through)
                idx = group.get('current_content_index', 0) % len(group['content'])
                content = group['content'][idx]
                
                # Log based on content type
                content_type = content.get('type', 'file')
                if content_type == 'file':
                    content_desc = os.path.basename(content.get('file_path', 'unknown'))
                else:
                    content_desc = content.get('text_content', '')[:30] + "..."
                
                logger.info(f"📤 Posting from {group['name']}: {content_desc}")
                
                try:
                    posted = run_async(async_post_to_channels(
                        session_file,
                        group['channels'],
                        content  # Pass the whole content item now
                    ))
                    
                    if posted > 0:
                        group_last_post[gid] = datetime.now()
                        new_total = group.get('total_posts', 0) + 1
                        new_idx = (idx + 1) % len(group['content'])
                        update_group(gid, {
                            'total_posts': new_total,
                            'current_content_index': new_idx
                        })
                        logger.info(f"✅ Posted to {posted} channels! Total: {new_total}")
                except Exception as e:
                    logger.error(f"Post error for {group['name']}: {e}")
            
            time.sleep(30)
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            time.sleep(60)

# ============== WEB UI ==============
HTML_BASE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Zerohook Bot</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        *{box-sizing:border-box;margin:0;padding:0}
        body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:linear-gradient(135deg,#667eea,#764ba2);min-height:100vh;padding:20px}
        .container{max-width:900px;margin:0 auto}
        .card{background:#fff;padding:25px;border-radius:15px;box-shadow:0 10px 40px rgba(0,0,0,.2);margin-bottom:20px}
        h1{color:#333;margin-bottom:10px}
        h2{color:#333;margin-bottom:15px;padding-bottom:10px;border-bottom:2px solid #667eea}
        h3{color:#555;margin:15px 0 10px}
        p{color:#666;margin-bottom:15px}
        .nav{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px}
        .nav a{padding:10px 20px;background:#f0f0f0;color:#333;text-decoration:none;border-radius:8px;font-weight:500}
        .nav a:hover,.nav a.active{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff}
        .form-group{margin-bottom:20px}
        label{display:block;margin-bottom:8px;color:#333;font-weight:500}
        input,select,textarea{width:100%;padding:12px;border:2px solid #e1e1e1;border-radius:8px;font-size:16px}
        input:focus,select:focus{outline:none;border-color:#667eea}
        button,.btn{display:inline-block;padding:12px 25px;background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;border:none;border-radius:8px;font-size:16px;font-weight:600;cursor:pointer;text-decoration:none;margin:2px}
        .btn-sm{padding:8px 15px;font-size:13px}
        .btn-danger{background:#dc3545}
        .btn-success{background:#28a745}
        .btn-warning{background:#ffc107;color:#333}
        .error{background:#fee;color:#c00;padding:15px;border-radius:8px;margin-bottom:15px;border-left:4px solid #c00}
        .success{background:#efe;color:#060;padding:15px;border-radius:8px;margin-bottom:15px;border-left:4px solid #060}
        .info{background:#e8f4fd;color:#0066cc;padding:15px;border-radius:8px;margin-bottom:15px;border-left:4px solid #0066cc}
        .warning{background:#fff3cd;color:#856404;padding:15px;border-radius:8px;margin-bottom:15px;border-left:4px solid #856404}
        .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:10px;margin-bottom:15px}
        .stat{background:#f8f9fa;padding:15px;border-radius:8px;text-align:center}
        .stat h4{color:#667eea;font-size:24px;margin-bottom:5px}
        .stat p{color:#666;font-size:12px;margin:0}
        .list{list-style:none}
        .list li{padding:12px;background:#f8f9fa;border-radius:8px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}
        .list li.active{background:#e8f4fd;border:2px solid #667eea}
        .badge{padding:4px 10px;border-radius:20px;font-size:11px;font-weight:600;display:inline-block;margin:2px}
        .badge-ok{background:#d4edda;color:#155724}
        .badge-warn{background:#fff3cd;color:#856404}
        .badge-info{background:#cce5ff;color:#004085}
        small{color:#666;display:block;margin-top:5px}
        .actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
        .group-card{border:2px solid #e1e1e1;padding:20px;border-radius:12px;margin-bottom:15px}
        .group-card.enabled{border-color:#28a745;background:#f8fff8}
        .group-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:15px;flex-wrap:wrap;gap:10px}
        .checkbox-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px}
        .checkbox-grid label{display:flex;align-items:center;gap:8px;padding:8px;background:#f8f9fa;border-radius:6px;cursor:pointer}
        .checkbox-grid input{width:auto;margin:0}
        .content-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:10px}
        .content-item{background:#f8f9fa;padding:10px;border-radius:8px;text-align:center;position:relative}
        .content-item img{max-width:100%;border-radius:4px;max-height:80px}
        .content-item .delete{position:absolute;top:5px;right:5px;background:#dc3545;color:#fff;border:none;border-radius:50%;width:24px;height:24px;cursor:pointer;font-size:14px}
        table{width:100%;border-collapse:collapse}
        th,td{padding:10px;text-align:left;border-bottom:1px solid #e1e1e1}
        th{background:#f8f9fa}
    </style>
</head>
<body>
<div class="container">
    <div class="card">
        <h1>🤖 Zerohook Bot</h1>
        <p>Post content to channel groups on schedule</p>
        <div class="nav">
            <a href="/" class="{{ 'active' if page=='home' }}">🏠 Home</a>
            <a href="/auth" class="{{ 'active' if page=='auth' }}">🔐 Auth</a>
            <a href="/channels" class="{{ 'active' if page=='channels' }}">📢 Channels</a>
            <a href="/groups" class="{{ 'active' if page=='groups' }}">📦 Groups</a>
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
    return render_template_string(HTML_BASE, page=page, content=content, 
                                 error=error or request.args.get('error'),
                                 success=success or request.args.get('success'))

@app.route('/')
def home():
    config = load_config()
    sessions = get_sessions()
    groups = load_groups()
    
    active_groups = len([g for g in groups if g['enabled']])
    total_content = sum(len(g['content']) for g in groups)
    total_channels = len(config.get('channels', {}))
    
    content = f'''
    <div class="card">
        <h2>📊 Dashboard</h2>
        <div class="grid">
            <div class="stat"><h4>{len(sessions)}</h4><p>Sessions</p></div>
            <div class="stat"><h4>{total_channels}</h4><p>Channels</p></div>
            <div class="stat"><h4>{len(groups)}</h4><p>Groups</p></div>
            <div class="stat"><h4>{active_groups}</h4><p>Active</p></div>
            <div class="stat"><h4>{total_content}</h4><p>Content</p></div>
            <div class="stat"><h4>{'🟢' if scheduler_running else '🔴'}</h4><p>Scheduler</p></div>
            <div class="stat"><h4>{'🟢' if bot_running else '🔴'}</h4><p>Bot</p></div>
        </div>
        {'<div class="success">✅ Logged in: '+sessions[0]+'</div>' if sessions else '<div class="warning">⚠️ <a href="/auth">Login required</a></div>'}
    </div>
    <div class="card">
        <h2>📦 Active Groups</h2>
    '''
    
    for g in groups:
        if g['enabled']:
            content += f'''
            <div class="info">
                <strong>{g['name']}</strong>: {len(g['content'])} items → {len(g['channels'])} channels
                | Every {g['interval_minutes']}m | Posts: {g.get('total_posts', 0)}
            </div>
            '''
    
    if not any(g['enabled'] for g in groups):
        content += '<div class="info">No active groups. <a href="/groups">Create one!</a></div>'
    
    content += '''
    </div>
    <div class="card">
        <h2>📱 How It Works</h2>
        <div class="info">
            1. <a href="/channels">Add channels</a> you want to post to<br>
            2. <a href="/groups">Create content groups</a> - map content to channels<br>
            3. Send photos/videos to your bot<br>
            4. Select which group to add them to<br>
            5. Enable the group - bot posts on schedule!<br><br>
            <strong>Example:</strong><br>
            Group "Promo1" → Channel A, Channel B (every 5 min)<br>
            Group "Promo2" → Channel C, Channel D (every 10 min)
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
    return render('auth', content)

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
            return redirect(url_for('home', success='Logged in!'))
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
        return redirect(url_for('auth', error=str(e)))

@app.route('/auth/2fa', methods=['POST'])
def auth_2fa():
    password = request.form.get('password', '')
    try:
        result = run_async(async_verify_2fa(password))
        if result['status'] == 'success':
            return redirect(url_for('home', success='Logged in!'))
        return redirect(url_for('auth', error=result.get('message', 'Error')))
    except Exception as e:
        return redirect(url_for('auth', error=str(e)))

@app.route('/channels')
def channels():
    config = load_config()
    chs = config.get('channels', {})
    
    html = ''
    for cid, name in chs.items():
        html += f'''
        <li>
            <div><strong>{name}</strong><br><small>ID: {cid}</small></div>
            <div class="actions">
                <form method="POST" action="/channels/remove" style="display:inline">
                    <input type="hidden" name="id" value="{cid}">
                    <button class="btn btn-danger btn-sm">Remove</button>
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
                <small>Forward a message from the channel to @userinfobot to get the ID</small>
            </div>
            <div class="form-group">
                <label>Channel Name</label>
                <input type="text" name="name" placeholder="My Channel" required>
            </div>
            <button type="submit">➕ Add Channel</button>
        </form>
    </div>
    <div class="card">
        <h2>📋 Your Channels ({len(chs)})</h2>
        {f'<ul class="list">{html}</ul>' if chs else '<div class="info">No channels yet. Add one above!</div>'}
    </div>
    '''
    return render('channels', content)

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
    save_config(config)
    return redirect(url_for('channels', success=f'"{name}" added!'))

@app.route('/channels/remove', methods=['POST'])
def channels_remove():
    cid = request.form.get('id', '').strip()
    config = load_config()
    if cid in config.get('channels', {}):
        name = config['channels'].pop(cid)
        save_config(config)
        return redirect(url_for('channels', success=f'"{name}" removed'))
    return redirect(url_for('channels', error='Not found'))

@app.route('/groups')
def groups_page():
    groups = load_groups()
    config = load_config()
    all_channels = config.get('channels', {})
    
    content = '''
    <div class="card">
        <h2>➕ Create New Group</h2>
        <form method="POST" action="/groups/create">
            <div class="form-group">
                <label>Group Name</label>
                <input type="text" name="name" placeholder="Promo Campaign 1" required>
            </div>
            <button type="submit">Create Group</button>
        </form>
    </div>
    '''
    
    if not groups:
        content += '''
        <div class="card">
            <h2>📦 Your Content Groups</h2>
            <div class="info">
                No groups yet! Create one above.<br><br>
                <strong>How groups work:</strong><br>
                • Each group has its own content and target channels<br>
                • Set different posting intervals per group<br>
                • Run multiple groups simultaneously!
            </div>
        </div>
        '''
    else:
        content += '<div class="card"><h2>📦 Your Content Groups</h2>'
        
        for g in groups:
            enabled_class = 'enabled' if g['enabled'] else ''
            status_badge = '<span class="badge badge-ok">Running</span>' if g['enabled'] else '<span class="badge badge-warn">Stopped</span>'
            
            # Duration display
            if g['duration_type'] == 'forever':
                duration_text = '♾ Forever'
            elif g['duration_type'] == 'hours':
                duration_text = f"⏰ {g['duration_value']}h"
            else:
                duration_text = f"🔢 {g['duration_value']} posts"
            
            content += f'''
            <div class="group-card {enabled_class}">
                <div class="group-header">
                    <div>
                        <h3>{g['name']} {status_badge}</h3>
                        <small>ID: {g['id']} | Posts: {g.get('total_posts', 0)}</small>
                    </div>
                    <div class="actions">
                        <a href="/groups/{g['id']}" class="btn btn-sm">✏️ Edit</a>
                        <form method="POST" action="/groups/{g['id']}/toggle" style="display:inline">
                            <button class="btn btn-sm {'btn-danger' if g['enabled'] else 'btn-success'}">
                                {'⏹ Stop' if g['enabled'] else '▶️ Start'}
                            </button>
                        </form>
                        <form method="POST" action="/groups/{g['id']}/delete" style="display:inline" onsubmit="return confirm('Delete this group?')">
                            <button class="btn btn-sm btn-danger">🗑</button>
                        </form>
                    </div>
                </div>
                <div class="grid" style="grid-template-columns:repeat(4,1fr)">
                    <div class="stat"><h4>{len(g['content'])}</h4><p>Content</p></div>
                    <div class="stat"><h4>{len(g['channels'])}</h4><p>Channels</p></div>
                    <div class="stat"><h4>{g['interval_minutes']}m</h4><p>Interval</p></div>
                    <div class="stat"><h4>{duration_text}</h4><p>Duration</p></div>
                </div>
            </div>
            '''
        
        content += '</div>'
    
    return render('groups', content)

@app.route('/groups/create', methods=['POST'])
def groups_create():
    name = request.form.get('name', '').strip()
    if not name:
        return redirect(url_for('groups_page', error='Name required'))
    group = create_group(name)
    return redirect(url_for('group_edit', group_id=group['id'], success='Group created!'))

@app.route('/groups/<group_id>')
def group_edit(group_id):
    group = get_group(group_id)
    if not group:
        return redirect(url_for('groups_page', error='Group not found'))
    
    config = load_config()
    all_channels = config.get('channels', {})
    
    # Channel checkboxes
    channel_html = ''
    for cid, cname in all_channels.items():
        checked = 'checked' if cid in group.get('channels', []) else ''
        channel_html += f'''
        <label>
            <input type="checkbox" name="channels" value="{cid}" {checked}>
            {cname}
        </label>
        '''
    
    # Content list
    content_html = ''
    for c in group.get('content', []):
        fname = os.path.basename(c['file_path'])
        content_html += f'''
        <div class="content-item">
            <small>{fname}</small><br>
            <small>ID: {c['id']}</small>
            <form method="POST" action="/groups/{group_id}/content/{c['id']}/delete" style="position:absolute;top:5px;right:5px">
                <button class="delete" title="Delete">×</button>
            </form>
        </div>
        '''
    
    # Interval options
    intervals = [1, 2, 3, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1440]
    interval_opts = ''.join([f'<option value="{i}" {"selected" if group.get("interval_minutes")==i else ""}>{i}m{f" ({i//60}h)" if i>=60 else ""}</option>' for i in intervals])
    
    # Duration options
    duration_html = f'''
    <label><input type="radio" name="duration_type" value="forever" {"checked" if group.get('duration_type')=='forever' else ""}> ♾ Forever</label>
    <label><input type="radio" name="duration_type" value="hours" {"checked" if group.get('duration_type')=='hours' else ""}> ⏰ For hours:</label>
    <input type="number" name="duration_hours" value="{group.get('duration_value') if group.get('duration_type')=='hours' else 1}" min="1" style="width:80px">
    <label><input type="radio" name="duration_type" value="count" {"checked" if group.get('duration_type')=='count' else ""}> 🔢 Number of posts:</label>
    <input type="number" name="duration_count" value="{group.get('duration_value') if group.get('duration_type')=='count' else 10}" min="1" style="width:80px">
    '''
    
    status = 'Running 🟢' if group['enabled'] else 'Stopped 🔴'
    
    content = f'''
    <div class="card">
        <div class="group-header">
            <h2>✏️ Edit: {group['name']}</h2>
            <span class="badge {'badge-ok' if group['enabled'] else 'badge-warn'}">{status}</span>
        </div>
        
        <form method="POST" action="/groups/{group_id}/update">
            <div class="form-group">
                <label>Group Name</label>
                <input type="text" name="name" value="{group['name']}" required>
            </div>
            
            <h3>📢 Target Channels</h3>
            <div class="checkbox-grid">
                {channel_html if channel_html else '<p>No channels yet. <a href="/channels">Add channels first!</a></p>'}
            </div>
            
            <h3>⏱ Posting Schedule</h3>
            <div class="form-group">
                <label>Post every:</label>
                <select name="interval">{interval_opts}</select>
            </div>
            
            <h3>⏰ Duration</h3>
            <div class="form-group" style="display:flex;flex-wrap:wrap;gap:15px;align-items:center">
                {duration_html}
            </div>
            
            <button type="submit">💾 Save Changes</button>
            <a href="/groups" class="btn btn-warning">← Back</a>
        </form>
    </div>
    
    <div class="card">
        <h2>📸 Content ({len(group.get('content', []))} items)</h2>
        <div class="info">Send photos/videos to your bot and select this group to add content.</div>
        <div class="content-grid">
            {content_html if content_html else '<p>No content yet. Send media to your bot!</p>'}
        </div>
    </div>
    
    <div class="card">
        <h2>🎮 Controls</h2>
        <div class="actions">
            <form method="POST" action="/groups/{group_id}/toggle">
                <button class="btn {'btn-danger' if group['enabled'] else 'btn-success'}">
                    {'⏹ Stop Posting' if group['enabled'] else '▶️ Start Posting'}
                </button>
            </form>
            <form method="POST" action="/groups/{group_id}/reset">
                <button class="btn btn-warning">🔄 Reset Counter</button>
            </form>
        </div>
        <div class="info" style="margin-top:15px">
            Total posts: {group.get('total_posts', 0)} | 
            Current index: {group.get('current_content_index', 0) + 1}/{len(group.get('content', [])) or 1}
        </div>
    </div>
    '''
    return render('groups', content)

@app.route('/groups/<group_id>/update', methods=['POST'])
def group_update(group_id):
    name = request.form.get('name', '').strip()
    channels = request.form.getlist('channels')
    interval = int(request.form.get('interval', 5))
    duration_type = request.form.get('duration_type', 'forever')
    
    if duration_type == 'hours':
        duration_value = int(request.form.get('duration_hours', 1))
    elif duration_type == 'count':
        duration_value = int(request.form.get('duration_count', 10))
    else:
        duration_value = 0
    
    update_group(group_id, {
        'name': name,
        'channels': channels,
        'interval_minutes': interval,
        'duration_type': duration_type,
        'duration_value': duration_value
    })
    
    return redirect(url_for('group_edit', group_id=group_id, success='Saved!'))

@app.route('/groups/<group_id>/toggle', methods=['POST'])
def group_toggle(group_id):
    group = get_group(group_id)
    if not group:
        return redirect(url_for('groups_page', error='Not found'))
    
    new_enabled = not group['enabled']
    updates = {'enabled': new_enabled}
    
    # Set started_at when enabling
    if new_enabled:
        updates['started_at'] = datetime.now().isoformat()
        updates['total_posts'] = 0  # Reset counter when starting
        group_last_post.pop(group_id, None)  # Clear last post time
    
    update_group(group_id, updates)
    
    status = 'started' if new_enabled else 'stopped'
    return redirect(url_for('groups_page', success=f'{group["name"]} {status}!'))

@app.route('/groups/<group_id>/reset', methods=['POST'])
def group_reset(group_id):
    update_group(group_id, {
        'total_posts': 0,
        'current_content_index': 0,
        'started_at': datetime.now().isoformat()
    })
    group_last_post.pop(group_id, None)
    return redirect(url_for('group_edit', group_id=group_id, success='Counter reset!'))

@app.route('/groups/<group_id>/delete', methods=['POST'])
def group_delete(group_id):
    delete_group(group_id)
    return redirect(url_for('groups_page', success='Group deleted'))

@app.route('/groups/<group_id>/content/<content_id>/delete', methods=['POST'])
def content_delete(group_id, content_id):
    remove_content_from_group(group_id, content_id)
    return redirect(url_for('group_edit', group_id=group_id, success='Content deleted'))

@app.route('/settings')
def settings():
    config = load_config()
    
    content = f'''
    <div class="card">
        <h2>⚙️ Bot Settings</h2>
        <form method="POST" action="/settings/save">
            <div class="form-group">
                <label>Bot Token (@BotFather)</label>
                <input type="text" name="bot_token" value="{config.get('bot_token', '')}" placeholder="123456:ABC...">
            </div>
            <div class="form-group">
                <label>Owner Username (only this user can send content)</label>
                <input type="text" name="owner" value="{config.get('owner_username', '')}" placeholder="yourusername">
            </div>
            <div class="form-group">
                <label><input type="checkbox" name="enabled" {'checked' if config.get('enabled', True) else ''}> Enable Scheduler</label>
            </div>
            <button type="submit">💾 Save</button>
        </form>
    </div>
    <div class="card">
        <h2>📊 System Status</h2>
        <div class="grid">
            <div class="stat"><h4>{'🟢' if scheduler_running else '🔴'}</h4><p>Scheduler</p></div>
            <div class="stat"><h4>{'🟢' if bot_running else '🔴'}</h4><p>Bot</p></div>
            <div class="stat"><h4>{'🟢 MongoDB' if get_mongo_db() else '📁 JSON'}</h4><p>Storage</p></div>
        </div>
    </div>
    '''
    return render('settings', content)

@app.route('/settings/save', methods=['POST'])
def settings_save():
    config = load_config()
    config['bot_token'] = request.form.get('bot_token', '').strip()
    config['owner_username'] = request.form.get('owner', '').strip().replace('@', '')
    config['enabled'] = 'enabled' in request.form
    save_config(config)
    return redirect(url_for('settings', success='Saved!'))

# ============== KEEP ALIVE (Anti-Sleep) ==============
def keep_alive():
    """Ping the server every 5 minutes to prevent Render free tier from sleeping"""
    while True:
        try:
            time.sleep(300)  # 5 minutes
            response = requests.get(APP_URL, timeout=30)
            logger.debug(f"🏓 Keep-alive ping: {response.status_code}")
        except Exception as e:
            logger.debug(f"Keep-alive error: {e}")

# ============== STARTUP ==============
def start_services():
    # Initialize MongoDB
    try:
        init_database()
    except Exception as e:
        logger.error(f"Database init error: {e}")
    
    get_telethon_loop()
    threading.Thread(target=run_scheduler, daemon=True).start()
    logger.info("📅 Scheduler started")
    threading.Thread(target=start_bot_receiver, daemon=True).start()
    logger.info("🤖 Bot thread started")
    threading.Thread(target=keep_alive, daemon=True).start()
    logger.info("🏓 Keep-alive thread started")

if __name__ == '__main__':
    start_services()
    port = int(os.getenv('PORT', 10000))
    logger.info(f"🌐 http://localhost:{port}")
    logger.info(f"💾 Storage: {'MongoDB' if get_mongo_db() else 'JSON files'}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
