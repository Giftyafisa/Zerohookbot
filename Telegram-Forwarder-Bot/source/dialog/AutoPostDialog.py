from source.dialog.BaseDialog import BaseDialog
from source.model.AutoPostConfig import AutoPostConfig


class AutoPostDialog(BaseDialog):
    async def get_action(self):
        options = [
            {"name": "Configure AutoPost Settings", "value": "configure"},
            {"name": "Start AutoPost Bot Receiver", "value": "start_receiver"},
            {"name": "Stop AutoPost Bot Receiver", "value": "stop_receiver"},
            {"name": "Start Scheduler", "value": "start_scheduler"},
            {"name": "Stop Scheduler", "value": "stop_scheduler"},
            {"name": "Queue Post From File", "value": "queue_file"},
            {"name": "Show Queue Status", "value": "status"},
            {"name": "List Pending Posts", "value": "list_pending"},
            {"name": "Clear Posted Items", "value": "clear_posted"},
            {"name": "Back", "value": "back"}
        ]
        return await self.show_options("AutoPost Menu:", options)

    async def get_config(self, use_existing=True):
        self.clear()
        return await AutoPostConfig.get(use_existing)

    async def get_file_path(self):
        return await self._prompt_text("Enter photo file path:")

    async def get_caption(self):
        return await self._prompt_text("Enter caption (optional):", default="")

    async def _prompt_text(self, message, default=None):
        from InquirerPy import inquirer
        return await inquirer.text(message=message, default=default).execute_async()
