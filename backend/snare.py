import numpy
import numpy.typing


def point_point_dist(
    a: numpy.typing.NDArray, b: numpy.typing.NDArray
) -> numpy.floating:
    return numpy.linalg.norm(a - b)


def line_point_dist(
    line: tuple[numpy.typing.NDArray, numpy.typing.NDArray], point: numpy.typing.NDArray
) -> numpy.floating:
    p1, p2 = line
    return numpy.linalg.norm(numpy.cross(p2 - p1, p1 - point)) / numpy.linalg.norm(
        p2 - p1
    )


def closest_point(
    line: tuple[numpy.typing.NDArray, numpy.typing.NDArray], point: numpy.typing.NDArray
) -> numpy.typing.NDArray:
    p1, p2 = line
    x1, y1, z1 = p1
    x2, y2, z2 = p2
    x3, y3, z3 = point
    dx, dy, dz = x2 - x1, y2 - y1, z2 - z1
    det = dx * dx + dy * dy + dz * dz
    a = (dx * (x3 - x1) + dy * (y3 - y1) + dz * (z3 - z1)) / det
    return numpy.array([x1 + a * dx, y1 + a * dy, z1 + a * dz])


def perpendicular_unit_vector(v: numpy.typing.NDArray) -> numpy.typing.NDArray:
    if v[0] == 0 and v[1] == 0:
        if v[2] == 0:
            # v is Vector(0, 0, 0)
            raise ValueError("zero vector")

        # v is Vector(0, 0, v.z)
        return numpy.array([0, 1, 0])

    res_v = numpy.array([-v[1], v[0], 0])
    return numpy.array(res_v / numpy.linalg.norm(res_v))


def pretty_print_dist(number: float | numpy.floating) -> str:
    if number > 1_000:
        return f"{number/1000:,.1f} km"

    return f"{number:,.1f} m"


def is_left_of(
    line: tuple[numpy.typing.NDArray, numpy.typing.NDArray], point: numpy.typing.NDArray
) -> bool:
    aX = line[0][0]
    aY = line[0][1]
    bX = line[1][0]
    bY = line[1][1]
    cX = point[0]
    cY = point[1]

    val = (bX - aX) * (cY - aY) - (bY - aY) * (cX - aX)
    if val >= 0:
        return True
    else:
        return False
