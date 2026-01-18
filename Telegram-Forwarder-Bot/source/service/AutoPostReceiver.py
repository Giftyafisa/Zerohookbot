import os
from time import sleep
from threading import Thread
import telebot
import logging

from source.utils.Constants import AUTOPOST_MEDIA_PATH

# Suppress verbose telebot logging
logging.getLogger('TeleBot').setLevel(logging.WARNING)


class AutoPostReceiver:
    def __init__(self, bot_token, owner_username, autopost_service, config):
        self.bot_token = bot_token
        self.owner_username = owner_username.lstrip("@") if owner_username else owner_username
        self.autopost_service = autopost_service
        self.config = config
        self.bot = telebot.TeleBot(bot_token, parse_mode=None)
        self.thread = None
        self.running = False

        os.makedirs(AUTOPOST_MEDIA_PATH, exist_ok=True)

        # Command handlers
        @self.bot.message_handler(commands=['start', 'help'])
        def send_welcome(message):
            if message.from_user.username != self.owner_username:
                return
            help_text = """
📸 AutoPost Bot Commands:

/addchannel <id> <name> - Add a channel (forward message from channel to get ID)
/listchannels - List all configured channels
/setchannel <id> - Set active posting channel
/removechannel <id> - Remove a channel
/status - Show queue status

Just send photos to queue them for posting!
            """
            self.bot.reply_to(message, help_text)

        @self.bot.message_handler(commands=['addchannel'])
        def add_channel(message):
            if message.from_user.username != self.owner_username:
                return
            try:
                parts = message.text.split(maxsplit=2)
                if len(parts) < 3:
                    self.bot.reply_to(message, "Usage: /addchannel <channel_id> <channel_name>")
                    return
                channel_id = int(parts[1])
                channel_name = parts[2]
                self.config.add_channel(channel_id, channel_name)
                self.autopost_service.set_channel(channel_id)
                self.bot.reply_to(message, f"✅ Added channel: {channel_name} ({channel_id})")
            except Exception as e:
                self.bot.reply_to(message, f"❌ Error: {e}")

        @self.bot.message_handler(commands=['listchannels'])
        def list_channels(message):
            if message.from_user.username != self.owner_username:
                return
            if not self.config.channels:
                self.bot.reply_to(message, "No channels configured. Use /addchannel to add one.")
                return
            text = "📋 Configured Channels:\n\n"
            for ch_id, ch_name in self.config.channels.items():
                active = "✅" if ch_id == self.config.channel_id else ""
                text += f"{active} {ch_name}: {ch_id}\n"
            self.bot.reply_to(message, text)

        @self.bot.message_handler(commands=['setchannel'])
        def set_channel(message):
            if message.from_user.username != self.owner_username:
                return
            try:
                channel_id = int(message.text.split()[1])
                if self.config.set_active_channel(channel_id):
                    self.autopost_service.set_channel(channel_id)
                    self.bot.reply_to(message, f"✅ Active channel set to: {self.config.channels[channel_id]}")
                else:
                    self.bot.reply_to(message, "❌ Channel not found. Use /listchannels to see available channels.")
            except (IndexError, ValueError):
                self.bot.reply_to(message, "Usage: /setchannel <channel_id>")

        @self.bot.message_handler(commands=['removechannel'])
        def remove_channel(message):
            if message.from_user.username != self.owner_username:
                return
            try:
                channel_id = int(message.text.split()[1])
                if self.config.remove_channel(channel_id):
                    self.bot.reply_to(message, f"✅ Removed channel: {channel_id}")
                else:
                    self.bot.reply_to(message, "❌ Channel not found.")
            except (IndexError, ValueError):
                self.bot.reply_to(message, "Usage: /removechannel <channel_id>")

        @self.bot.message_handler(commands=['status'])
        def show_status(message):
            if message.from_user.username != self.owner_username:
                return
            status = self.autopost_service.get_queue_status()
            active_ch = self.config.channels.get(self.config.channel_id, "None")
            text = f"""
📊 AutoPost Status:

Active Channel: {active_ch} ({self.config.channel_id})
Total Posts: {status['total']}
Pending: {status['pending']}
Posted: {status['posted']}
Next Post Time: {status['next_post_time']}
            """
            self.bot.reply_to(message, text)

        @self.bot.message_handler(content_types=["photo"])
        def handle_photo(message):
            if message.from_user.username != self.owner_username:
                return

            if not self.config.channel_id:
                self.bot.reply_to(message, "❌ No active channel set. Use /addchannel first.")
                return

            # Get the largest photo
            photo = message.photo[-1]
            file_info = self.bot.get_file(photo.file_id)
            downloaded = self.bot.download_file(file_info.file_path)

            # Save to disk
            ext = os.path.splitext(file_info.file_path)[1] or ".jpg"
            file_name = f"{photo.file_id}{ext}"
            file_path = os.path.join(AUTOPOST_MEDIA_PATH, file_name)
            with open(file_path, "wb") as f:
                f.write(downloaded)

            queued = self.autopost_service.queue_post(file_path, message.caption or "")
            self.bot.reply_to(message, f"✅ Queued for {queued['scheduled_for']}")

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = Thread(target=self._poll, daemon=True)
        self.thread.start()
        print("AutoPost bot receiver started. Send /start to your bot to see commands.")

    def _poll(self):
        print("Bot polling started. Send /start to your bot on Telegram.")
        retry_count = 0
        while self.running:
            try:
                self.bot.infinity_polling(timeout=30, long_polling_timeout=30, skip_pending=True)
            except Exception as e:
                if self.running:
                    retry_count += 1
                    if retry_count == 1:
                        print(f"⚠️ Connection issue (retrying in background...)")
                    # Only show error every 10 retries to avoid spam
                    if retry_count % 10 == 0:
                        print(f"Still retrying connection... (attempt {retry_count})")
                    sleep(5)
                else:
                    break

    def stop(self):
        if not self.running:
            return
        self.running = False
        try:
            self.bot.stop_polling()
        except:
            pass
        if self.thread:
            self.thread.join(timeout=2)
        print("AutoPost bot receiver stopped.")
