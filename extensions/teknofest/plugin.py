from .runtime import CompetitionRuntime


class TeknofestExtension:
    def configure(self, registrar, context):
        self.context = context
        registrar.singleton(CompetitionRuntime, lambda: CompetitionRuntime(context))
        registrar.sidebar_view("servers", "TEKNOFEST", lambda parent: self.panel(parent))
        registrar.settings_ui(lambda anchor: self.settings(anchor))

    def start(self, context):
        context.services.resolve(CompetitionRuntime).start()

    def stop(self):
        self.context.services.resolve(CompetitionRuntime).stop()

    def panel(self, parent):
        from .ui import ServerPanel
        return ServerPanel(self.context, parent)

    def settings(self, anchor):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout
        dialog = QDialog(anchor)
        panel = self.panel(dialog)
        layout = QVBoxLayout(dialog)
        layout.addWidget(panel)
        dialog.resize(600, 500)
        try:
            dialog.exec()
        finally:
            panel.prepare_shutdown()
            dialog.deleteLater()


def create_extension():
    return TeknofestExtension()
