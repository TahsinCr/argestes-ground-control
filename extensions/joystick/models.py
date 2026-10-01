from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.core.abstracts import Model


@dataclass(frozen=True, slots=True)
class JoystickChannelConfig(Model):
    channel: int
    control: str = ""
    pwm_min: int = 1000
    pwm_trim: int = 1500
    pwm_max: int = 2000
    deadzone: float = 0.03
    expo: int = 0
    reversed: bool = False

    def __post_init__(self) -> None:
        channel = int(self.channel)
        control = str(self.control).strip().lower()
        pwm_min = int(self.pwm_min)
        pwm_trim = int(self.pwm_trim)
        pwm_max = int(self.pwm_max)
        deadzone = float(self.deadzone)
        expo = int(self.expo)
        if not 1 <= channel <= 16:
            raise ValueError("RC kanalı 1..16 aralığında olmalı")
        if control and not (
            control.startswith("axis:")
            or control.startswith("button:")
            or control.startswith("toggle:")
        ):
            raise ValueError(f"Desteklenmeyen joystick denetimi: {control}")
        if not 800 <= pwm_min < pwm_trim < pwm_max <= 2200:
            raise ValueError("PWM değerleri 800 <= min < trim < max <= 2200 olmalı")
        if not 0.0 <= deadzone <= 0.5:
            raise ValueError("Joystick deadzone değeri 0..0.5 aralığında olmalı")
        if not 0 <= expo <= 100:
            raise ValueError("Joystick expo değeri 0..100 aralığında olmalı")
        object.__setattr__(self, "channel", channel)
        object.__setattr__(self, "control", control)
        object.__setattr__(self, "pwm_min", pwm_min)
        object.__setattr__(self, "pwm_trim", pwm_trim)
        object.__setattr__(self, "pwm_max", pwm_max)
        object.__setattr__(self, "deadzone", deadzone)
        object.__setattr__(self, "expo", expo)
        object.__setattr__(self, "reversed", bool(self.reversed))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class JoystickButtonConfig(Model):
    button: int
    action: str = ""
    mode: str = ""
    servo: int = 0
    pwm: int = 1500
    slot: int = 0

    def __post_init__(self) -> None:
        button = int(self.button)
        action = str(self.action).strip().upper()
        if not -1 <= button < 32:
            raise ValueError("Joystick buton numarası -1..31 aralığında olmalı")
        if action not in {"", "ARM", "DISARM", "SET_MODE", "SET_SERVO"}:
            raise ValueError(f"Desteklenmeyen joystick aksiyonu: {action}")
        mode = str(self.mode).strip().upper()
        servo = int(self.servo)
        pwm = int(self.pwm)
        slot = int(self.slot)
        if not 0 <= slot <= 10:
            raise ValueError("Joystick buton satırı 0..10 aralığında olmalı")
        if action == "SET_MODE" and button >= 0 and not mode:
            raise ValueError("Mod değiştirme aksiyonu için mod seçilmeli")
        if action == "SET_SERVO" and button >= 0 and not 1 <= servo <= 16:
            raise ValueError("Servo numarası 1..16 aralığında olmalı")
        if action == "SET_SERVO" and button >= 0 and not 800 <= pwm <= 2200:
            raise ValueError("Servo PWM değeri 800..2200 aralığında olmalı")
        object.__setattr__(self, "button", button)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "servo", servo)
        object.__setattr__(self, "pwm", pwm)
        object.__setattr__(self, "slot", slot)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_joystick_channels() -> list[JoystickChannelConfig]:
    return [JoystickChannelConfig(channel=index) for index in range(1, 17)]


@dataclass(slots=True)
class JoystickSettings:
    enabled: bool = False
    device_path: str = ""
    send_rate_hz: int = 25
    channels: list[JoystickChannelConfig] = field(default_factory=default_joystick_channels)
    buttons: list[JoystickButtonConfig] = field(default_factory=list)

    @classmethod
    def from_config(cls, values):
        from dataclasses import fields
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in values.items() if key in allowed})

    def __post_init__(self) -> None:
        self.enabled = bool(self.enabled)
        self.device_path = str(self.device_path).strip()
        self.send_rate_hz = int(self.send_rate_hz)
        if not 5 <= self.send_rate_hz <= 50:
            raise ValueError("Joystick gönderim hızı 5..50 Hz aralığında olmalı")
        parsed = [
            item if isinstance(item, JoystickChannelConfig) else JoystickChannelConfig(**dict(item))
            for item in self.channels
        ]
        by_channel = {item.channel: item for item in parsed}
        if len(by_channel) != len(parsed):
            raise ValueError("Joystick RC kanal numaraları benzersiz olmalı")
        self.channels = [
            by_channel.get(index, JoystickChannelConfig(channel=index))
            for index in range(1, 17)
        ]
        parsed_buttons = [
            item if isinstance(item, JoystickButtonConfig) else JoystickButtonConfig(**dict(item))
            for item in self.buttons
        ]
        assigned_buttons = [item.button for item in parsed_buttons if item.button >= 0]
        if len(set(assigned_buttons)) != len(assigned_buttons):
            raise ValueError("Joystick buton atamaları benzersiz olmalı")
        self.buttons = sorted(parsed_buttons, key=lambda item: item.slot or item.button + 1)


@dataclass(frozen=True, slots=True)
class JoystickDevice(Model):
    path: str
    name: str
    axis_count: int
    button_count: int


@dataclass(frozen=True, slots=True)
class JoystickState(Model):
    connected: bool = False
    transmitting: bool = False
    device_path: str = ""
    device_name: str = ""
    axes: tuple[float, ...] = ()
    buttons: tuple[bool, ...] = ()
    channel_pwm: tuple[int | None, ...] = (None,) * 16
    error: str = ""
