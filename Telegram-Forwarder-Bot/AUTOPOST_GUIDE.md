# AutoPost Bot Setup & Usage Guide

## Initial Setup

1. **Run the bot**: `python main.py`
2. **Go to AutoPost menu** (option 7)
3. **Configure settings**:
   - Bot Token: Get from @BotFather on Telegram
   - Owner Username: Your Telegram username (without @)
   - Channel ID: **Leave blank** (you'll add via bot commands)

## Adding Channels via Telegram

### Method 1: Get Channel ID from Telegram
1. Forward any message from your channel to @userinfobot
2. It will show the channel ID (e.g., `-1001234567890`)

### Method 2: Add the bot to your channel as admin
1. Add your bot to the channel as administrator
2. Send a test message in the channel
3. The bot will detect the ID

### Add Channel Command
In your bot chat, send:
```
/addchannel -1001234567890 My Channel Name
```

## Bot Commands

- `/start` or `/help` - Show help message
- `/addchannel <id> <name>` - Add a new channel
- `/listchannels` - List all configured channels
- `/setchannel <id>` - Set active posting channel
- `/removechannel <id>` - Remove a channel
- `/status` - Show queue status

## Queue Posts

Just send photos to your bot with optional captions. They'll be queued for the active channel.

## Example Workflow

```
/addchannel -1001234567890 Marketing Channel
/addchannel -1009876543210 News Channel
/listchannels
/setchannel -1001234567890
[Send photo with caption]
✅ Queued for 2026-01-19
/status
```

## Multiple Channels

You can add multiple channels and switch between them:
- Add several channels with `/addchannel`
- Switch active channel with `/setchannel`
- Each photo goes to the currently active channel
