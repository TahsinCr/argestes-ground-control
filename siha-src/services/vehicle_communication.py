from abc import ABC, abstractmethod
from dataclasses import dataclass
from pymavlink.mavutil import mavlink_connection
from pymavlink.dialects.v20 import ardupilotmega as mavlink

from core.dependency import injection
from models.point import Point
from src.core.base import BaseService
from src.core.config import BaseConfig

@dataclass(slots=True)
class VehicleConfig(BaseConfig):
    protocol:str
    ip:str
    port:int

    @property
    def connection_str(self) -> str:
        return f"{self.protocol}:{self.ip}:{self.port}"


class BaseVehicleComunication(BaseService, ABC):
    def __init__(self, config:VehicleConfig):
        self.config = config

    @property
    def is_connected(self):
        return self.vehicle is not None

    @abstractmethod
    def connect(self): ...

    @abstractmethod
    def disconnect(self): ...

    @abstractmethod
    def goto(self, point:Point):...

@injection()
class ArduPilotMavlinkComunication(BaseVehicleComunication):
    def __init__(self, config:VehicleConfig):
        super().__init__(config)
        self.vehicle = None
    
    @property
    def is_connected(self):
        return self.vehicle is not None

    def connect(self):
        self.vehicle = mavlink_connection(self.config.connection_str)
        self.vehicle.wait_heartbeat()
        return self.vehicle

    def disconnect(self):
        self.vehicle.close()
        self.vehicle = None
    
    def goto(self, point:Point):
        self.vehicle



    
