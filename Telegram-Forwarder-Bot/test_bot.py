"""Quick test to verify bot token works"""
import telebot
import sys

if len(sys.argv) < 2:
    print("Usage: python test_bot.py <YOUR_BOT_TOKEN>")
    sys.exit(1)

token = sys.argv[1]
bot = telebot.TeleBot(token)

try:
    print("Testing bot connection...")
    me = bot.get_me()
    print(f"✅ Bot connected successfully!")
    print(f"   Bot name: {me.first_name}")
    print(f"   Bot username: @{me.username}")
    print(f"\n✅ Your bot is working! You can now use it in the main app.")
    print(f"   Open Telegram and search for @{me.username}")
except Exception as e:
    print(f"❌ Connection failed: {e}")
    print("\nPossible issues:")
    print("  - Check your internet connection")
    print("  - Verify bot token is correct")
    print("  - Try using a VPN if Telegram is blocked")
