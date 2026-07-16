from models.point import Point

from ...utils.dependency_manager import injection
from ..vehicle_communication import BaseVehicleComunication, ArduPilotMavlinkComunication


@injection()
class KamikazeMisson(Misson):
    def __init__(self, 
        mavlink:BaseVehicleComunication, 
        image_proccess:BaseImageProccess,
        yki_connector:BaseGCSCommunication
    ):
        self.mavlink = mavlink
        self.image_proccess = image_proccess
        self.yki_connetor = yki_connector
        self.qr_data:str|None = None

    def start(self, command):
        if not self.mavlink.is_connected():
            command.kill_missison(HSSCheckMission)
        self.mavlink.goto(Point(12,2134,21))

    def stop(self, command): ...

    def proccess(self, command): ...
