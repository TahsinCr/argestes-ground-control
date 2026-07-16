import os
import struct
import asyncio
from abc import ABC
from collections import deque
from typing import Callable, Generic, Hashable, Optional, Protocol, TypeVar, Union, overload
from pymavlink import mavutil

T = TypeVar("T")
K = TypeVar("K", bound=Hashable)

class MavHeader(Protocol):
    """MAVLink message header"""
    def __init__(self,
        msgId: int, incompat_flags: int = 0, 
        compat_flags: int = 0, mlen: int = 0, seq: int = 0, 
        srcSystem: int = 0, srcComponent: int = 0
    ) -> None:
        self.mlen = mlen
        self.seq = seq
        self.srcSystem = srcSystem
        self.srcComponent = srcComponent
        self.msgId = msgId
        self.incompat_flags = incompat_flags
        self.compat_flags = compat_flags

    def pack(self, force_mavlink1: bool = False) -> bytes: ...

class MavMessage(Protocol):
    """base MAVLink message class"""
    id = 0
    msgname = ""
    fieldnames: list[str] = []
    ordered_fieldnames: list[str] = []
    fieldtypes: list[str] = []
    fielddisplays_by_name: dict[str, str] = {}
    fieldenums_by_name: dict[str, str] = {}
    fieldunits_by_name: dict[str, str] = {}
    native_format = bytearray(b"")
    orders: list[int] = []
    lengths: list[int] = []
    array_lengths: list[int] = []
    crc_extra = 0
    unpacker = struct.Struct("")
    instance_field: Optional[str] = None
    instance_offset = -1

    def __init__(self, msgId: int, name: str) -> None:
        self._header = MavHeader(msgId)
        self._payload: Optional[bytes] = None
        self._msgbuf = bytearray(b"")
        self._crc: Optional[int] = None
        self._fieldnames: list[str] = []
        self._type = name
        self._signed = False
        self._link_id: Optional[int] = None
        self._instances: Optional[dict[str, str]] = None
        self._instance_field: Optional[str] = None

    def format_attr(self, field: str) -> Union[str, float, int]: ...

    def get_msgbuf(self) -> bytearray: ...

    def get_header(self) -> MavHeader: ...

    def get_payload(self) -> Optional[bytes]: ...

    def get_crc(self) -> Optional[int]: ...

    def get_fieldnames(self) -> list[str]: ...

    def get_type(self) -> str: ...

    def get_msgId(self) -> int: ...

    def get_srcSystem(self) -> int: ...

    def get_srcComponent(self) -> int: ...

    def get_seq(self) -> int: ...

    def get_signed(self) -> bool: ...

    def get_link_id(self) -> Optional[int]: ...

    def to_dict(self) -> dict[str, Union[str, float, int]]: ...

    def to_json(self) -> str: ...

    def sign_packet(self, mav: "MAVLink") -> None: ...

    def pack(self, mav: "MAVLink", force_mavlink1: bool = False) -> bytes: ...

class MavConnection:
    def __init__(self, connection_string:str, baud:int=115200):
        self.connection_string = connection_string
        self.baud = baud
        self.mavconn:mavutil.mavfile = None

    @property
    def is_connected(self):
        return self.mavconn is not None
    
    @property
    def mav(self):
        return self.mavconn.mav
    
    @property
    def fd(self):
        return self.mavconn.fd

    def connect(self, 
        system_id:int=255, component_id:int=0, 
        dialect:str='common', version:str='2.0', 
        auto_reconnect:bool = False
    ) -> bool:
        if self.is_connected:
            return False
        if version == "2.0":
            os.environ['MAVLINK20'] = '1'
        else:
            os.environ.pop('MAVLINK20', None)
        self.mavconn:mavutil.mavfile = mavutil.mavlink_connection(
            self.connection_string,
            baud=self.baud,
            dialect=dialect,
            autoreconnect=auto_reconnect,
            source_system=system_id,
            source_component=component_id
        )
        return True

    def wait_ready(self, timeout: float = 10.0):
        if not self.is_connected:
            raise Exception("Connection is not established.")
        msg = self.mavconn.wait_heartbeat(timeout=timeout)
        if not msg:
            raise Exception("Heartbeat timeout.")

    def get_message(self, blocking:bool=True, timeout:float|None=None):
        if not self.is_connected:
            raise Exception("Connection is not established.")
        return self.mavconn.recv_match(blocking = blocking, timeout = timeout)

    @overload
    def send_message(self, message:MavMessage):...
    @overload
    def send_message(self, type:str, **params): ...
    def send_message(self, message_or_type, **params):
        if not self.is_connected:
            raise Exception("Connection is not established.")
        if not isinstance(message_or_type, str):
            self.mav.send(message_or_type)
        else:
            method_name = f"{message_or_type.lower()}_send"
            if not hasattr(self.mav, method_name):
                raise ValueError(f"Mavlink does not recognize such a message: {method_name}")
            sender_method = getattr(self.mav, method_name)
            try:
                sender_method(**params)
            except TypeError as e:
                raise TypeError(f"Parameter error occurred while sending message ({method_name}): {e}")
    
    def close(self):
        if not self.is_connected:
            return False
        self.mavconn.close()
        self.mavconn = None
        return True

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @classmethod
    def tcp(cls, host:str, port:int, baud:int=115200):
        return cls(f"tcp:{host}:{port}", baud=baud)
    
    @classmethod
    def udp(cls, host:str, port:int, baud:int=115200):
        return cls(f"udp:{host}:{port}", baud=baud)
    
    @classmethod
    def serial(cls, port:str, baud:int=115200):
        return cls(f"serial://{port}", baud=baud)

class Cache(Generic[T, K]):
    def __init__(self, key_getter: Callable[[T], K], maxsize: int = 1000):
        if maxsize < 0:
            raise ValueError("maxsize must be zero or greater.")
        self._key_getter = key_getter
        self.maxsize = maxsize
        self._items_by_key: dict[K, deque[T]] = {}
        self._order: deque[tuple[K, T]] = deque()

    def __call__(self, item: T):
        self.add(item)

    def add(self, item: T):
        key = self.key_for(item)
        self._items_by_key.setdefault(key, deque()).append(item)
        self._order.append((key, item))
        self._trim(key)

    def key_for(self, item: T) -> K:
        return self._key_getter(item)

    def get_all(self, key: Optional[K] = None) -> list[T]:
        if key is not None:
            return self.get_all_by_key(key)
        return [item for _, item in self._order]

    def get_all_by_key(self, key: K) -> list[T]:
        return list(self._items_by_key.get(key, ()))

    def latest(self, key: Optional[K] = None) -> Optional[T]:
        if key is not None:
            return self.latest_by_key(key)
        if not self._order:
            return None
        return self._order[-1][1]

    def latest_by_key(self, key: K) -> Optional[T]:
        items = self._items_by_key.get(key)
        if not items:
            return None
        return items[-1]

    def oldest(self, key: Optional[K] = None) -> Optional[T]:
        if key is not None:
            return self.oldest_by_key(key)
        if not self._order:
            return None
        return self._order[0][1]

    def oldest_by_key(self, key: K) -> Optional[T]:
        items = self._items_by_key.get(key)
        if not items:
            return None
        return items[0]

    def discard(self, item: T, key: Optional[K] = None) -> bool:
        key = key if key is not None else self.key_for(item)
        removed_by_key = self._remove_from_key(item, key)
        removed_by_order = self._remove_from_order(item, key)
        return removed_by_key or removed_by_order

    def clear(self):
        self._items_by_key.clear()
        self._order.clear()

    def count(self, key: Optional[K] = None) -> int:
        if key is not None:
            return self.count_by_key(key)
        return len(self._order)

    def count_by_key(self, key: K) -> int:
        return len(self._items_by_key.get(key, ()))

    def keys(self) -> list[K]:
        return list(self._items_by_key.keys())

    def counts(self) -> dict[K, int]:
        return {key: len(items) for key, items in self._items_by_key.items()}

    def _trim(self, key: K):
        while self.maxsize > 0 and len(self._order) > self.maxsize:
            old_key, old_item = self._order.popleft()
            self._remove_from_key(old_item, old_key)

    def _remove_from_key(self, item: T, key: K) -> bool:
        items = self._items_by_key.get(key)
        if items is None:
            return False
        for index, current in enumerate(items):
            if current is item:
                del items[index]
                if not items:
                    del self._items_by_key[key]
                return True
        return False

    def _remove_from_order(self, item: T, key: K) -> bool:
        for index, (current_key, current_item) in enumerate(self._order):
            if current_key == key and current_item is item:
                del self._order[index]
                return True
        return False

    def __len__(self) -> int:
        return len(self._order)

class PerKeyLimitCache(Cache[T, K]):
    def __init__(self, key_getter: Callable[[T], K], maxsize_per_key: int = 1000):
        if maxsize_per_key < 0:
            raise ValueError("maxsize_per_key must be zero or greater.")
        super().__init__(key_getter=key_getter, maxsize=0)
        self.maxsize_per_key = maxsize_per_key

    def _trim(self, key: K):
        items = self._items_by_key.get(key)
        if items is None:
            return
        while self.maxsize_per_key > 0 and len(items) > self.maxsize_per_key:
            dropped = items.popleft()
            self._remove_from_order(dropped, key)
        if not items:
            del self._items_by_key[key]

class MavChannelBase(ABC):
    def __init__(
        self,
        connection: MavConnection, 
        buffer: Optional[deque[MavMessage]] = None
    ):
        self.connection = connection
        self.buffer = buffer if buffer is not None else deque[MavMessage]()
        self._filters: set[Callable[[MavMessage], bool]] = set()
        self._triggers: set[Callable[[MavMessage], None]] = set()
    
    def add_filter(self, filter: Callable[[MavMessage], bool]):
        self._filters.add(filter)
    
    def delete_filter(self, filter: Callable[[MavMessage], bool]):
        self._filters.remove(filter)
    
    def add_trigger(self, trigger: Callable[[MavMessage], None]):
        self._triggers.add(trigger)

    def delete_trigger(self, trigger: Callable[[MavMessage], None]):
        self._triggers.remove(trigger)

class MavAsyncReceiverChannel(MavChannelBase):
    def __init__(
        self, 
        connection: MavConnection,
        buffer: asyncio.Queue[MavMessage],
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ):
        self.connection = connection
        self.buffer = buffer if buffer is not None else asyncio.Queue[MavMessage]()
        self._filters: set[Callable[[MavMessage], bool]] = set()
        self._triggers: set[Callable[[MavMessage], None]] = set()
        self.loop = loop if loop is not None else asyncio.get_event_loop()
        self._message_event = asyncio.Event()

    def start(self):
        self.loop.add_reader(self.connection.fd, self._reader_callback)

    def stop(self):
        self.loop.remove_reader(self.connection.fd)

    def _reader_callback(self):
        while True:
            message = self.connection.get_message(blocking=False)
            if message is None:
                return
            if not all(filter(message) for filter in self._filters):
                continue
            self._put_message(message)
            self._message_event.set()
            for trigger in self._triggers:
                trigger(message)

    def _put_message(self, message: MavMessage):
        try:
            self.buffer.put_nowait(message)
        except asyncio.QueueFull:
            try:
                self.buffer.get_nowait()
                self.buffer.task_done()
            except asyncio.QueueEmpty:
                pass
            self.buffer.put_nowait(message)

    async def get_message(
        self,
        blocking: bool = True,
        timeout: Optional[float] = None
    ) -> Optional[MavMessage]:
        if not blocking:
            return self.buffer.get_nowait()
        if timeout is None:
            return await self.buffer.get()
        return await asyncio.wait_for(self.buffer.get(), timeout=timeout)

    async def get_next_message(self, timeout: Optional[float] = None):
        if timeout is None:
            await self._message_event.wait()
        else:
            await asyncio.wait_for(self._message_event.wait(), timeout=timeout)
        result = await self.get_message(blocking=False)
        self._message_event.clear()
        return result        
