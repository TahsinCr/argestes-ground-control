"""Keep protocol selection outside device sampling and configuration UI."""
from src.extension.api.protocol import ActiveProtocol
from src.services.abstracts.vehicle import VehicleService


def controller(context):
    selected = context.services.resolve(ActiveProtocol)
    # RC semantics are supplied by the vehicle adapter; add future protocol
    # adapters here without changing the device worker or configuration dialog.
    return selected.adapter({"mavlink": lambda: context.services.resolve(VehicleService)})
