# Render.com Deployment Configuration

## Build Command:
```
pip install -r requirements.txt && mkdir -p Telegram-Forwarder-Bot/resources Telegram-Forwarder-Bot/sessions Telegram-Forwarder-Bot/media
```

## Start Command:
```
python3 main.py
```

## Environment Variables (Add in Render Dashboard):
- `TG_BOT_TOKEN`: Your bot token from @BotFather
- `TG_OWNER_USERNAME`: Your Telegram username (without @)
- `TG_CHANNEL_ID`: Target channel ID (optional)
- `POSTING_TIME_HOUR`: 13
- `POSTING_TIME_MINUTE`: 0

## Python Version:
Use `.python-version` file with content: `python-3.13.4`
