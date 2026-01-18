from InquirerPy import inquirer
from source.utils.Console import Terminal
from source.model.Credentials import Credentials
from source.core.Telegram import Telegram
from source.dialog.ForwardDialog import ForwardDialog
from source.dialog.DeleteDialog import DeleteDialog
from source.dialog.FindUserDialog import FindUserDialog
from source.dialog.AutoPostDialog import AutoPostDialog
from source.menu.AccountSelector import AccountSelector
import os

class MainMenu:
    def __init__(self, telegram):
        self.console = Terminal.console
        self.telegram = telegram
        self.menu_options = self._init_menu_options()
        self.forward_dialog = ForwardDialog()
        self.delete_dialog = DeleteDialog()
        self.find_user_dialog = FindUserDialog()
        self.autopost_dialog = AutoPostDialog()

    def _init_menu_options(self):
        return [
            {"name": "Add/Update Credentials", "value": "1", "handler": self.update_credentials},
            {"name": "List Chats", "value": "2", "handler": self.list_chats},
            {"name": "Delete My Messages", "value": "3", "handler": self.delete_messages},
            {"name": "Find User Messages", "value": "4", "handler": self.find_user},
            {"name": "Live Forward Messages", "value": "5", "handler": self.live_forward},
            {"name": "Past Forward Messages", "value": "6", "handler": self.past_forward},
            {"name": "AutoPost Scheduler", "value": "7", "handler": self.autopost_menu},
            {"name": "Switch Account", "value": "8", "handler": self.switch_account},
            {"name": "Exit", "value": "0", "handler": None}
        ]

    async def _get_menu_choice(self):
        choices = [{"name": opt["name"], "value": opt["value"]} for opt in self.menu_options]
        return await inquirer.select(message="Menu:", choices=choices).execute_async()

    async def start(self):
        try:
            while True:
                choice = await self._get_menu_choice()
                if choice == "0":
                    self.console.print("[bold red]Exiting...[/bold red]")
                    break
                
                handler = next(opt["handler"] for opt in self.menu_options if opt["value"] == choice)
                if handler:
                    await handler()
                else:
                    self.console.print("[bold red]Invalid choice[/bold red]")
        finally:
            if self.telegram:
                await self._cleanup()

    async def _cleanup(self):
        if self.telegram:
            await self.telegram.disconnect()

    async def update_credentials(self):
        self.console.clear()
        await self.telegram.disconnect()
        credentials = await Credentials.get(False)
        self.telegram = await Telegram.create(credentials)

    async def list_chats(self):
        self.console.clear()
        await self.telegram.list_chats()

    async def live_forward(self):
        config = await self.forward_dialog.get_config()
        await self.telegram.start_forward_live(config)

    async def past_forward(self):
        config = await self.forward_dialog.get_config()
        await self.telegram.past_forward(config)

    async def delete_messages(self):
        ignore_chats = await self.delete_dialog.get_config()
        await self.telegram.delete(ignore_chats)

    async def find_user(self):
        config = await self.find_user_dialog.get_config()
        await self.telegram.find_user(config)

    async def switch_account(self):
        await self._cleanup()
        selector = AccountSelector()
        self.telegram = await selector.select_account()

    async def autopost_menu(self):
        self.console.clear()
        while True:
            action = await self.autopost_dialog.get_action()
            if action == "back":
                break

            if action == "configure":
                await self.autopost_dialog.get_config(use_existing=False)
                self.console.print("[bold green]AutoPost settings saved.[/bold green]")
                continue

            config = await self.autopost_dialog.get_config(use_existing=True)

            if action == "start_receiver":
                self.telegram.start_autopost_receiver(config)
                self.console.print("[bold green]AutoPost receiver started.[/bold green]")
            elif action == "stop_receiver":
                self.telegram.stop_autopost_receiver()
                self.console.print("[bold yellow]AutoPost receiver stopped.[/bold yellow]")
            elif action == "start_scheduler":
                self.telegram.start_autopost_scheduler(config)
                self.console.print("[bold green]AutoPost scheduler started.[/bold green]")
            elif action == "stop_scheduler":
                self.telegram.stop_autopost_scheduler()
                self.console.print("[bold yellow]AutoPost scheduler stopped.[/bold yellow]")
            elif action == "queue_file":
                file_path = await self.autopost_dialog.get_file_path()
                caption = await self.autopost_dialog.get_caption()
                if not os.path.exists(file_path):
                    self.console.print("[bold red]File does not exist.[/bold red]")
                    continue
                self.telegram.init_autopost(config)
                queued = self.telegram.queue_autopost_file(file_path, caption)
                self.console.print(f"[bold green]Queued for {queued['scheduled_for']}[/bold green]")
            elif action == "status":
                self.telegram.init_autopost(config)
                status = self.telegram.get_autopost_status()
                if status:
                    self.console.print(f"Total: {status['total']}, Pending: {status['pending']}, Posted: {status['posted']}")
                    self.console.print(f"Next Post Time: {status['next_post_time']}")
                    self.console.print(f"Channel ID: {status['channel_id']}")
            elif action == "list_pending":
                self.telegram.init_autopost(config)
                pending = self.telegram.list_autopost_pending()
                if not pending:
                    self.console.print("[bold yellow]No pending posts.[/bold yellow]")
                else:
                    for item in pending:
                        self.console.print(f"{item['scheduled_for']} - {item['photo_path']}")
            elif action == "clear_posted":
                self.telegram.clear_autopost_posted()
                self.console.print("[bold green]Cleared posted items.[/bold green]")