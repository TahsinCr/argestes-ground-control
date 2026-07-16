from dataclasses import dataclass

@dataclass
class Point:
    x: int
    y: int
    z: int


def distance(self, point:Point): ...


A = Point(12,23,45)

B = Point(12,23,46)

C = A.distance(B)
