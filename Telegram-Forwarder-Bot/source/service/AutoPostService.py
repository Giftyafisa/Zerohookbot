"""AutoPost Service for scheduled posting to Telegram channels.

This service allows queuing photos with captions and automatically posting
them to specified channels at scheduled times.
"""

import os
from datetime import date, datetime, time, timedelta
from typing import Optional
import schedule
import simplejson as json
from tinydb import TinyDB, where
from telethon import TelegramClient
from telethon.tl.types import InputPeerChannel
import asyncio
from threading import Thread
from time import sleep


class AutoPostService:
    """Service for scheduling and managing automatic posts to Telegram channels.
    
    Attributes:
        client (TelegramClient): The Telegram client instance
        db (TinyDB): Database for storing scheduled posts
        target_time (time): Time of day when posts should be sent
        channel_id (int): ID of the channel to post to
        running (bool): Flag indicating if scheduler is running
    """

    def __init__(self, client: TelegramClient, config: Optional[dict] = None):
        """Initialize AutoPost service.
        
        Args:
            client: Telegram client instance
            config: Optional configuration dict with channel_id, posting_hour, posting_minute
        """
        self.client = client
        self.config = config or {}
        
        # Database setup
        db_path = os.path.join("resources", "autopost_schedule.json")
        self.db = TinyDB(db_path)
        
        # Scheduler configuration
        posting_hour = self.config.get('posting_hour', 13)
        posting_minute = self.config.get('posting_minute', 0)
        self.target_time = time(int(posting_hour), int(posting_minute))
        
        # Channel configuration
        self.channel_id = self.config.get('channel_id')
        
        # Scheduler state
        self.running = False
        self.scheduler_thread = None

    def queue_post(self, photo_path: str, caption: str = "", scheduled_for: Optional[date] = None) -> dict:
        """Queue a photo for posting.
        
        Args:
            photo_path: Path to the photo file
            caption: Caption text for the photo
            scheduled_for: Optional specific date to post (defaults to next available slot)
            
        Returns:
            dict: The queued post data
        """
        if not self.channel_id:
            raise ValueError("No active channel set. Use /addchannel command to add a channel first.")

        # Calculate scheduled date
        if not scheduled_for:
            queue_length = self.db.count(where("posted") == 0)
            today = date.today()
            date_to_post = today + timedelta(days=queue_length)
            
            # If queue is empty and time has passed today, schedule for tomorrow
            now = datetime.now()
            if queue_length == 0 and now.time() > self.target_time:
                date_to_post += timedelta(days=1)
        else:
            date_to_post = scheduled_for
        
        # Create post data
        data = {
            "photo_path": photo_path,
            "channel_id": self.channel_id,
            "caption": caption,
            "scheduled_for": date_to_post.isoformat(),
            "added": datetime.now().isoformat(),
            "posted": 0,
        }
        
        # Insert into database
        self.db.insert(data)
        
        return data

    async def do_post(self, not_todays_post: bool = False) -> bool:
        """Post a scheduled image.
        
        Args:
            not_todays_post: If True, post the first queued item regardless of date
            
        Returns:
            bool: True if post was successful, False otherwise
        """
        # Check if there are any posts in queue
        if len(self.db.all()) == 0:
            print("No posts are scheduled")
            return False

        # Get scheduled post
        if not_todays_post:
            chosen_post = self.db.all()[0]
        else:
            is_not_posted = where("posted") == 0
            is_for_today = where("scheduled_for") == date.today().isoformat()
            chosen_post = self.db.search((is_not_posted) & (is_for_today))
            
            if len(chosen_post) == 0:
                print("No posts for today")
                return False
            else:
                chosen_post = chosen_post[0]

        try:
            # Get channel entity
            channel = await self.client.get_entity(self.channel_id)
            
            # Send photo with caption
            photo_path = chosen_post["photo_path"]
            caption = chosen_post.get("caption", "")
            
            await self.client.send_file(
                channel,
                photo_path,
                caption=caption
            )
            
            # Mark as posted
            post_query = where("photo_path") == photo_path
            update_value = {"posted": 1}
            self.db.update(update_value, post_query)
            
            print(f"Post with caption '{caption}' posted successfully")
            return True
            
        except Exception as e:
            print(f"Error posting: {e}")
            return False

    def _scheduler_loop(self):
        """Internal scheduler loop running in a separate thread."""
        print(f"Starting AutoPost scheduler - posts will be sent at {self.target_time}")
        
        # Schedule daily post
        scheduled_time = f"{self.target_time.hour:02d}:{self.target_time.minute:02d}"
        schedule.every().day.at(scheduled_time).do(self._run_async_post)
        
        while self.running:
            schedule.run_pending()
            sleep(1)

    def _run_async_post(self):
        """Helper to run async post in sync scheduler context."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.do_post())
        finally:
            loop.close()

    def start_scheduler(self):
        """Start the background scheduler."""
        if not self.running:
            self.running = True
            self.scheduler_thread = Thread(target=self._scheduler_loop, daemon=True)
            self.scheduler_thread.start()
            print("AutoPost scheduler started")

    def stop_scheduler(self):
        """Stop the background scheduler."""
        if self.running:
            self.running = False
            if self.scheduler_thread:
                self.scheduler_thread.join(timeout=2)
            print("AutoPost scheduler stopped")

    def get_queue_status(self) -> dict:
        """Get current queue status.
        
        Returns:
            dict: Queue statistics
        """
        all_posts = self.db.all()
        pending = self.db.count(where("posted") == 0)
        posted = self.db.count(where("posted") == 1)
        
        return {
            "total": len(all_posts),
            "pending": pending,
            "posted": posted,
            "next_post_time": f"{self.target_time}",
            "channel_id": self.channel_id
        }

    def get_pending_posts(self) -> list:
        """Get all pending posts.
        
        Returns:
            list: List of pending post dicts
        """
        return self.db.search(where("posted") == 0)

    def clear_posted(self):
        """Remove all posted items from queue."""
        self.db.remove(where("posted") == 1)
        print("Cleared all posted items from queue")

    def set_channel(self, channel_id: int):
        """Set the target channel ID.
        
        Args:
            channel_id: Telegram channel ID
        """
        self.channel_id = channel_id
        self.config['channel_id'] = channel_id
