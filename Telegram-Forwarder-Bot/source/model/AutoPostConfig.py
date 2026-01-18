import json
import os
from InquirerPy import inquirer
from source.utils.Constants import AUTOPOST_CONFIG_FILE_PATH, AUTOPOST_MEDIA_PATH


class AutoPostConfig:
    def __init__(self, bot_token=None, owner_username=None, channel_id=None, posting_hour=13, posting_minute=0, channels=None):
        self.bot_token = bot_token
        self.owner_username = owner_username
        self.channel_id = channel_id  # Active channel
        self.posting_hour = posting_hour
        self.posting_minute = posting_minute
        self.channels = channels or {}  # Dict of {channel_id: channel_name}

    @staticmethod
    def read():
        with open(AUTOPOST_CONFIG_FILE_PATH, "r") as file:
            data = json.load(file)
            return AutoPostConfig(**data)

    @staticmethod
    def write(config):
        os.makedirs(os.path.dirname(AUTOPOST_CONFIG_FILE_PATH), exist_ok=True)
        with open(AUTOPOST_CONFIG_FILE_PATH, "w") as file:
            json.dump(config.__dict__, file, indent=2)

    @staticmethod
    async def get(use_existing=True):
        if use_existing and os.path.exists(AUTOPOST_CONFIG_FILE_PATH):
            return AutoPostConfig.read()

        env_config = AutoPostConfig._get_from_env()
        if env_config:
            AutoPostConfig.write(env_config)
            return env_config

        return await AutoPostConfig._get_from_user()

    @staticmethod
    def _get_from_env():
        bot_token = os.getenv("TG_BOT_TOKEN")
        owner_username = os.getenv("TG_OWNER_USERNAME")
        channel_id = os.getenv("TG_CHANNEL_ID")
        posting_hour = os.getenv("POSTING_TIME_HOUR")
        posting_minute = os.getenv("POSTING_TIME_MINUTE")

        if not bot_token or not owner_username:
            return None

        ch_id = int(channel_id) if channel_id else None
        channels = {ch_id: "Default Channel"} if ch_id else {}

        return AutoPostConfig(
            bot_token=bot_token,
            owner_username=owner_username,
            channel_id=ch_id,
            posting_hour=int(posting_hour or 13),
            posting_minute=int(posting_minute or 0),
            channels=channels
        )

    @staticmethod
    async def _get_from_user():
        bot_token = await inquirer.text(message="Enter Bot Token (TG_BOT_TOKEN):").execute_async()
        owner_username = await inquirer.text(message="Enter Owner Username (without @):").execute_async()
        channel_id_str = await inquirer.text(message="Enter Target Channel ID (optional, can add via bot commands):", default="").execute_async()
        
        # Validate posting hour
        while True:
            posting_hour = await inquirer.text(message="Posting Hour (0-23):", default="13").execute_async()
            try:
                hour = int(posting_hour)
                if 0 <= hour <= 23:
                    break
                print("Hour must be between 0-23")
            except ValueError:
                print("Please enter a valid number")
        
        # Validate posting minute
        while True:
            posting_minute = await inquirer.text(message="Posting Minute (0-59):", default="0").execute_async()
            try:
                minute = int(posting_minute)
                if 0 <= minute <= 59:
                    break
                print("Minute must be between 0-59")
            except ValueError:
                print("Please enter a valid number")

        channel_id = int(channel_id_str) if channel_id_str else None
        channels = {channel_id: "Default Channel"} if channel_id else {}

        config = AutoPostConfig(
            bot_token=bot_token,
            owner_username=owner_username,
            channel_id=channel_id,
            posting_hour=int(posting_hour),
            posting_minute=int(posting_minute),
            channels=channels
        )
        AutoPostConfig.write(config)
        return config

    def to_service_config(self):
        return {
            "channel_id": self.channel_id,
            "posting_hour": self.posting_hour,
            "posting_minute": self.posting_minute,
            "media_path": AUTOPOST_MEDIA_PATH
        }

    def add_channel(self, channel_id, channel_name):
        """Add a channel to the config."""
        self.channels[channel_id] = channel_name
        if not self.channel_id:
            self.channel_id = channel_id
        AutoPostConfig.write(self)

    def set_active_channel(self, channel_id):
        """Set the active channel for posting."""
        if channel_id in self.channels:
            self.channel_id = channel_id
            AutoPostConfig.write(self)
            return True
        return False

    def remove_channel(self, channel_id):
        """Remove a channel from config."""
        if channel_id in self.channels:
            del self.channels[channel_id]
            if self.channel_id == channel_id:
                self.channel_id = next(iter(self.channels.keys()), None)
            AutoPostConfig.write(self)
            return True
        return False
