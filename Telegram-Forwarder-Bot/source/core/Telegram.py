import os
import telethon
from telethon.sync import TelegramClient
from telethon.tl.types import InputPeerEmpty
from source.utils.Console import Terminal

from source.model.Chat import Chat
from source.service.Forward import Forward
from source.utils.Constants import SESSION_PREFIX_PATH, MEDIA_FOLDER_PATH
from source.service.ChatService import ChatService
from source.service.MessageService import MessageService
from source.service.AutoPostService import AutoPostService
from source.service.AutoPostReceiver import AutoPostReceiver


class Telegram:
    def __init__(self, credentials):
        """Initialize the Telegram client with credentials."""
        self.credentials = credentials
        self.client = TelegramClient(
            SESSION_PREFIX_PATH + credentials.phone_number,
            credentials.api_id,
            credentials.api_hash
        )
        self._is_connected = False
        self.console = Terminal.console

        # Initialize services
        self.chat_service = ChatService(self.console)
        self.message_service = MessageService(self.client, self.console)
        self.message_service.chat_service = self.chat_service
        self.autopost_service = None
        self.autopost_receiver = None

    @classmethod
    async def create(cls, credentials):
        """Factory method to create and connect a Telegram instance."""
        instance = cls(credentials)
        await instance.connect()
        return instance

    async def connect(self):
        """Connect to Telegram if not already connected."""
        if not self._is_connected:
            try:
                if not self.client.is_connected():
                    await self.client.connect()
                if not await self.client.is_user_authorized():
                    await self.client.start(self.credentials.phone_number)
                self._is_connected = True
            except ConnectionError as e:
                self._is_connected = False
                raise ConnectionError(f"Failed to connect to Telegram: {e}")

    async def disconnect(self):
        """Safely disconnects the client."""
        if self._is_connected and self.client:
            try:
                self.stop_autopost_scheduler()
                self.stop_autopost_receiver()
                await self.client.disconnect()
            finally:
                self._is_connected = False

    async def get_me(self):
        """Gets the current user's information."""
        return await self.client.get_me()

    async def list_chats(self):
        """Lists and saves all available chats."""
        chats = await self.client.get_dialogs()
        chat_list = Chat.write(chats)
        
        # Print chat information
        self.console.print("\n[bold blue]Available Chats:[/]")
        for chat_dict in chat_list:
            chat = Chat(**chat_dict)
            self.console.print(chat.get_display_name())


    async def delete(self, ignore_chats):
        """Deletes user's messages from all groups except ignored ones."""
        me = await self.get_me()
        ignored_ids = [chat.id for chat in ignore_chats]

        async for dialog in self.client.iter_dialogs():
            if not self._should_process_dialog(dialog, me.id, ignored_ids):
                continue

            await self.message_service.delete_messages_from_dialog(dialog, me.id)

    async def find_user(self, config):
        """Finds and downloads messages from a specific user.
        
        Args:
            config: tuple containing (wanted_user, message_limit)
        """
        wanted_user, message_limit = config
        if not wanted_user:
            return
        
        me = await self.get_me()
        
        # Create the user entity with the access hash
        wanted_user_entity = telethon.tl.types.User(
            id=wanted_user.id,
            access_hash=wanted_user.access_hash,
            username=wanted_user.username
        )

        async for dialog in self.client.iter_dialogs():
            chat = dialog.entity
            try:
                if chat.id == me.id and wanted_user.id != me.id:
                    continue

                if isinstance(chat, telethon.tl.types.User):
                    continue

                await self.message_service.process_user_messages(chat, wanted_user_entity, message_limit)

            except Exception as e:
                print(f"Error processing dialog: {e}")

    async def start_forward_live(self, forward_config):
        """Starts live message forwarding."""
        forward = Forward(self.client, forward_config)
        forward.add_events()
        await self.client.run_until_disconnected()

    async def past_forward(self, forward_config):
        """Forwards historical messages."""
        forward = Forward(self.client, forward_config)
        await forward.history_handler()

    async def download_media(self, message):
        """Downloads media from a message."""
        os.makedirs(MEDIA_FOLDER_PATH, exist_ok=True)
        return await self.client.download_media(message, file=MEDIA_FOLDER_PATH)

    def init_autopost(self, config):
        """Initialize AutoPost service with config."""
        if not self.autopost_service:
            self.autopost_service = AutoPostService(self.client, config.to_service_config())

    def start_autopost_scheduler(self, config):
        """Start AutoPost scheduler."""
        self.init_autopost(config)
        self.autopost_service.start_scheduler()

    def stop_autopost_scheduler(self):
        """Stop AutoPost scheduler."""
        if self.autopost_service:
            self.autopost_service.stop_scheduler()

    def start_autopost_receiver(self, config):
        """Start AutoPost bot receiver."""
        self.init_autopost(config)
        if not self.autopost_receiver:
            self.autopost_receiver = AutoPostReceiver(
                config.bot_token,
                config.owner_username,
                self.autopost_service,
                config
            )
        self.autopost_receiver.start()

    def stop_autopost_receiver(self):
        """Stop AutoPost bot receiver."""
        if self.autopost_receiver:
            self.autopost_receiver.stop()

    def queue_autopost_file(self, file_path, caption):
        """Queue a local file for AutoPost."""
        if not self.autopost_service:
            raise ValueError("AutoPost service is not initialized")
        return self.autopost_service.queue_post(file_path, caption)

    def get_autopost_status(self):
        if not self.autopost_service:
            return None
        return self.autopost_service.get_queue_status()

    def list_autopost_pending(self):
        if not self.autopost_service:
            return []
        return self.autopost_service.get_pending_posts()

    def clear_autopost_posted(self):
        if self.autopost_service:
            self.autopost_service.clear_posted()

    def _should_process_dialog(self, dialog, my_id, ignored_ids):
        """Determines if a dialog should be processed for deletion."""
        if not dialog.is_group:
            return False
        if dialog.id == my_id or dialog.id in ignored_ids:
            return False
        return True
