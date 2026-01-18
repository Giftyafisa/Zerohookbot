# Render.com Deployment Configuration

## Build Command:
```
pip install -r requirements.txt && pip install -r Telegram-Forwarder-Bot/requirements.txt && mkdir -p Telegram-Forwarder-Bot/resources Telegram-Forwarder-Bot/sessions Telegram-Forwarder-Bot/media
```

## Start Command:
```
cd Telegram-Forwarder-Bot && python3 run_bot.py
```

## How It Works:
1. First deploy shows a **web authentication page**
2. Open your Render URL (e.g., https://zerohookbot.onrender.com)
3. Enter your phone number → Get OTP on Telegram → Enter OTP
4. If you have 2FA, enter your cloud password
5. Session is saved, bot starts automatically on next restart

## Environment Variables (Add in Render Dashboard):
- `TG_BOT_TOKEN`: Your bot token from @BotFather
- `TG_OWNER_USERNAME`: Your Telegram username (without @)
- `TG_CHANNEL_ID`: Target channel ID (optional)
- `POSTING_TIME_HOUR`: 13
- `POSTING_TIME_MINUTE`: 0

## Python Version:
Use `.python-version` file with content: `python-3.13.4`
