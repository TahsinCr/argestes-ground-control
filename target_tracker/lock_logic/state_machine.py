"""ACQUIRE -> TRACK -> LOCK_CONFIRM -> LOCKED -> HANDOVER -> LOST durum makinesi."""

from enum import Enum, auto


class LockState(Enum):
    ACQUIRE = auto()
    TRACK = auto()
    LOCK_CONFIRM = auto()
    LOCKED = auto()
    HANDOVER = auto()
    LOST = auto()


class LockStateMachine:
    def __init__(self):
        self.state = LockState.ACQUIRE

    def transition(self, locked, target_visible):
        if self.state == LockState.ACQUIRE:
            if target_visible:
                self.state = LockState.TRACK
        elif self.state == LockState.TRACK:
            if locked:
                self.state = LockState.LOCK_CONFIRM
            elif not target_visible:
                self.state = LockState.LOST
        elif self.state == LockState.LOCK_CONFIRM:
            if locked:
                self.state = LockState.LOCKED
            else:
                self.state = LockState.TRACK
        elif self.state == LockState.LOCKED:
            if not target_visible:
                self.state = LockState.HANDOVER
        elif self.state == LockState.HANDOVER:
            if not target_visible:
                self.state = LockState.LOST
        elif self.state == LockState.LOST:
            if target_visible:
                self.state = LockState.TRACK
        return self.state
