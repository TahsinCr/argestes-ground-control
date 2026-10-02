from .runtime import CompetitionRuntime


class TeknofestExtension:
    def configure(self, registrar, context):
        self.context = context
        registrar.singleton(CompetitionRuntime, lambda: CompetitionRuntime(context))
        registrar.visual_view(
            "servers",
            lambda parent, theme, language: self.panel(parent),
            title="TEKNOFEST",
        )
        registrar.settings_page(self.panel)

    def start(self, context):
        context.services.resolve(CompetitionRuntime).start()

    def stop(self):
        self.context.services.resolve(CompetitionRuntime).stop()

    def panel(self, parent):
        from .ui import ServerPanel

        return ServerPanel(self.context, parent)


def create_extension():
    return TeknofestExtension()
