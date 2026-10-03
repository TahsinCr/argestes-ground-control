from .runtime import CompetitionRuntime


class TeknofestExtension:
    def configure(self, registrar, context):
        self.context = context
        registrar.singleton(CompetitionRuntime, lambda: CompetitionRuntime(context))
        registrar.status_widget(
            "connection",
            lambda parent, theme, language: self.status(parent, theme, language),
        )
        registrar.settings_page(self.panel)

    def start(self, context):
        context.services.resolve(CompetitionRuntime).start()

    def stop(self):
        self.context.services.resolve(CompetitionRuntime).stop()

    def panel(self, parent):
        from .ui import ServerPanel

        return ServerPanel(self.context, parent)

    def status(self, parent, theme, language):
        from .ui import ServerStatusWidget

        return ServerStatusWidget(self.context, parent, theme, language)


def create_extension():
    return TeknofestExtension()
