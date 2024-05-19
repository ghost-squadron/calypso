import math

import numpy
import numpy.typing
import pydantic
from .constants import DEFAULT_OM_RADIUS, LOCATIONS
from mathutils.geometry import intersect_point_line  # type: ignore


def location_to_str(location: dict) -> str:
    match location["Type"]:
        case "RestStop" | "Refinery Station" | "Naval Station":
            return f'{location["InternalName"].replace("Station", "").strip()} - {location["ObjectContainer"].strip()}'.strip()
        case "Moon" | "Planet":
            return str(location["ObjectContainer"].strip())
        case _:
            return f'{location["InternalName"].strip()} - {location["ObjectContainer"].strip()}'.strip()


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


# def closest_point(
#     line: tuple[numpy.typing.NDArray, numpy.typing.NDArray], point: numpy.typing.NDArray
# ) -> numpy.typing.NDArray:
#     p1, p2 = line
#     x1, y1, z1 = p1
#     x2, y2, z2 = p2
#     x3, y3, z3 = point
#     dx, dy, dz = x2 - x1, y2 - y1, z2 - z1
#     det = dx * dx + dy * dy + dz * dz
#     a = (dx * (x3 - x1) + dy * (y3 - y1) + dz * (z3 - z1)) / det
#     return numpy.array([x1 + a * dx, y1 + a * dy, z1 + a * dz])


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


class Route(pydantic.BaseModel):
    destination: dict
    destination_dist: float
    centerline_dist: float
    snare_cone_dist: float
    z_mag: float
    z_dir: str
    s_mag: float
    s_dir: str
    f_mag: float
    f_dir: str
    closest_edge: float
    location_score: float


class Snare:
    def __init__(self, source: str, destination: str):
        self.source = next(l for l in LOCATIONS if location_to_str(l) == source)
        self.destination = next(
            l for l in LOCATIONS if location_to_str(l) == destination
        )

        # The travel source represented as a 3D coordinate
        self.source_point = numpy.array(
            [
                self.source["XCoord"],
                self.source["YCoord"],
                self.source["ZCoord"],
            ]
        )

        # The travel distination represented as a 3D coordinate
        self.destination_point = numpy.array(
            [
                self.destination["XCoord"],
                self.destination["YCoord"],
                self.destination["ZCoord"],
            ]
        )

        # Represents the centerline as a vector
        # with origin in the source point
        centerline = self.source_point - self.destination_point

        # Calculate the point on the centerline
        # where you enter the physics grid of the destination
        self.point_of_physics = (
            self.destination_point
            + centerline
            / point_point_dist(self.destination_point, self.source_point)
            * self.destination["GRIDRadius"]
        )

        # Orbital Markers are generally the furthest away from the center
        # of a celestial body anybody traveling from said body will travel before
        # jumping towards a new target
        om_radius = self.source["OrbitalMarkerRadius"] or DEFAULT_OM_RADIUS

        # All OM points orbit at the same height - this variable represents
        # an imaginary abitrary OM point placed perpendicular on the centerline
        # i.e. the worst case in order to catch a potential traveller
        puv = perpendicular_unit_vector(self.destination_point - self.source_point)
        assert abs(1.0 - numpy.linalg.norm(puv)) < 0.00000000001
        arbitrary_om_point = self.source_point + puv * om_radius

        # A linalg representation of an arbitrary worst case travel line
        self.hyp = (arbitrary_om_point, self.destination_point)

        # Approximates the point (down to 0.01m) closest to the source point
        # on the centerline which is less than 20,000m (snare range) from
        # the worst case travel line
        # i.e. the earliest possible point to catch everyone
        sp = self.source_point
        dp = self.point_of_physics
        while point_point_dist(sp, dp) > 0.01:
            h = sp + (dp - sp) / 2
            hd = line_point_dist(self.hyp, h)
            if hd < 20_000:
                dp = h
            else:
                sp = h
        min_pullout = h
        self.min_pullout_dist = point_point_dist(min_pullout, self.destination_point)

        # Approximates the point (down to 0.01m) where a ship would have to travel
        # the furthest to escape the cone in which it would still catch everyone
        sp = min_pullout
        dp = self.point_of_physics
        while point_point_dist(sp, dp) > 0.01:
            h = sp + (dp - sp) / 2
            hd = 20_000 - line_point_dist(self.hyp, h)
            hpp = point_point_dist(h, self.point_of_physics)
            if hpp > hd:
                sp = h
            else:
                dp = h
        self.optimal_pullout = h
        self.optimal_pullout_dist = point_point_dist(
            self.optimal_pullout, self.destination_point
        )

        # Calculate the coverage
        # i.e. at the clostest point possible to the destination (just before the physics grid)
        # how much of the required area to catch everyone does a 20,000m radius cover
        point_of_physics_radius = line_point_dist(self.hyp, self.point_of_physics)
        point_of_physics_area = point_of_physics_radius**2 * math.pi
        snare_coverage = 20_000**2 * math.pi
        self.coverage = snare_coverage / point_of_physics_area

    def get_route(self, location: numpy.typing.NDArray) -> Route | None:
        destination_dist = point_point_dist(location, self.destination_point)
        if destination_dist < float(self.optimal_pullout_dist):
            return None

        closest_centerline_point = numpy.array(
            intersect_point_line(location, self.source_point, self.destination_point)[
                0
            ][:]
        )
        centerline_dist = line_point_dist(
            (self.source_point, self.destination_point), location
        )
        max_dist = 20_000 - line_point_dist(self.hyp, closest_centerline_point)

        snare_cone_dist = centerline_dist - max_dist

        z_mag = abs(closest_centerline_point[2] - location[2])
        z_dir = "up" if closest_centerline_point[2] > 0 else "down"

        s_mag = numpy.linalg.norm((closest_centerline_point - location)[:2])
        s_dir = (
            "right"
            if is_left_of((self.source_point, self.destination_point), location)
            else "left"
        )

        f_mag = (
            point_point_dist(closest_centerline_point, self.destination_point)
            - self.destination["GRIDRadius"]
        )
        f_dir = "forward" if f_mag > 0 else "backwards"

        closest_edge = min(
            20_000 - line_point_dist(self.hyp, location),
            destination_dist - self.destination["GRIDRadius"],
        )

        location_score = (
            closest_edge
            / (float(self.optimal_pullout_dist) - self.destination["GRIDRadius"])
            * 10
        )
        return Route(
            destination=self.destination,
            destination_dist=float(destination_dist),
            centerline_dist=float(centerline_dist),
            snare_cone_dist=float(snare_cone_dist),
            z_mag=z_mag,
            z_dir=z_dir,
            s_mag=float(s_mag),
            s_dir=s_dir,
            f_mag=f_mag,
            f_dir=f_dir,
            closest_edge=closest_edge,
            location_score=location_score,
        )
