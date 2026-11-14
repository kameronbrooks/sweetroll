bl_info = {
    "name": "Sweetroll",
    "author": "Kameron Brooks",
    "version": (3, 0, 0),
    "blender": (3, 0, 0),
    "location": "View2D > UV > Sweet(Un)roll",
    "description": "Unroll your rolled up uv island",
    "warning": "",
    "doc_url": "",
    "category": "UV",
}
import bpy
import bmesh
import math
from bpy.props import BoolProperty, EnumProperty

# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
#                           Utilities
# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=

# Blender 5.0 moved UV selection from the UV layer data (loop[uv_layer].select)
# onto the loop itself (loop.uv_select_vert)
HAS_LOOP_UV_SELECT = hasattr(bmesh.types.BMLoop, "uv_select_vert")
# UV edge selection on the UV layer data (Blender 3.3 to 4.x)
HAS_UV_SELECT_EDGE = hasattr(bmesh.types.BMLoopUV, "select_edge")


class SweetrollError(Exception):
    """ A problem that should be shown to the user """


def face_uv_area(uvs, face):
    """ Signed area of a face in uv space (positive when counter-clockwise) """
    area = 0.0
    loops = list(face.loops)
    prev = uvs[loops[-1]]
    for loop in loops:
        cur = uvs[loop]
        area += prev[0] * cur[1] - cur[0] * prev[1]
        prev = cur
    return area * 0.5


def rotate_quarter_turns(p, turns):
    """ Rotate a 2d point counter-clockwise in steps of 90 degrees """
    if turns == 1:
        return (-p[1], p[0])
    if turns == 2:
        return (-p[0], -p[1])
    if turns == 3:
        return (p[1], -p[0])
    return p


# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
#                           UVIsland Class
# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=

class UVIsland:
    """ Represents a UV island. Contains convenience methods for working with islands """
    def __init__(self, uvbmesh, faces):
        self.uvbmesh = uvbmesh
        self.uv_layer = uvbmesh.uv_layer
        self.name = "island"
        self.faces = faces
        self.face_set = set(faces)
        self.loops = set()
        self.quads_only = True
        # the uv coordinates as they were before anything was moved
        self.uvs = {}
        for face in faces:
            if len(face.loops) != 4:
                self.quads_only = False
            for loop in face.loops:
                self.loops.add(loop)
                uv = loop[self.uv_layer].uv
                self.uvs[loop] = (uv.x, uv.y)
        self.selected_loops = [loop for loop in self.loops if uvbmesh.is_loop_selected(loop)]

    def __iter__(self):
        return self.loops.__iter__()

    def __contains__(self, loop):
        return loop in self.loops

    def get_edge_neighbors(self, loop, faces):
        """
        Get the loops on the other side of this loop's edge that are connected to it in uv space.
        Returns a list of (loop, same_direction) where same_direction is True if the other loop
        runs along the edge the same way this one does (happens when normals are inconsistent)
        """
        uvs = self.uvs
        a = uvs[loop]
        b = uvs[loop.link_loop_next]
        output = []
        for other in loop.edge.link_loops:
            if other == loop or other.face not in faces:
                continue
            if other.vert == loop.vert:
                if uvs[other] == a and uvs[other.link_loop_next] == b:
                    output.append((other, True))
            elif uvs[other] == b and uvs[other.link_loop_next] == a:
                output.append((other, False))
        return output

    @staticmethod
    def seed_coords(face):
        """ Grid coordinates for the 4 corners of the first face of a grid """
        loop = face.loops[0]
        return {
            loop: (0, 0),
            loop.link_loop_next: (1, 0),
            loop.link_loop_next.link_loop_next: (1, 1),
            loop.link_loop_prev: (0, 1),
        }

    @staticmethod
    def step_coords(coords, loop, other, same_direction):
        """ Grid coordinates for the 4 corners of the face across loop's edge """
        p = coords[loop]
        q = coords[loop.link_loop_next]
        inner = coords[loop.link_loop_prev]
        # the far side of the neighbor is one step away from this face
        p_out = (2 * p[0] - inner[0], 2 * p[1] - inner[1])
        q_out = (q[0] + p_out[0] - p[0], q[1] + p_out[1] - p[1])
        if same_direction:
            return {
                other: p,
                other.link_loop_next: q,
                other.link_loop_next.link_loop_next: q_out,
                other.link_loop_prev: p_out,
            }
        return {
            other: q,
            other.link_loop_next: p,
            other.link_loop_next.link_loop_next: p_out,
            other.link_loop_prev: q_out,
        }

    @staticmethod
    def get_cell(coords, face):
        """ The grid cell a face sits in (its lowest corner) """
        return (min(coords[loop][0] for loop in face.loops), min(coords[loop][1] for loop in face.loops))

    def map_grid_loose(self, seed_face, candidates):
        """
        Walk outward from seed_face giving every connected candidate face a place on a grid.
        Every face is placed once, then every connection is checked against it:
        periods collects the offsets found when a ring of faces closes on itself, and
        twisted is True if some faces can not be laid out as a grid at all (poles)
        """
        coords = self.seed_coords(seed_face)
        faces = [seed_face]
        seen = {seed_face}
        periods = set()
        twisted = False
        i = 0
        while i < len(faces):
            face = faces[i]
            i += 1
            for loop in face.loops:
                for other, same_direction in self.get_edge_neighbors(loop, candidates):
                    stepped = self.step_coords(coords, loop, other, same_direction)
                    other_face = other.face
                    if other_face not in seen:
                        seen.add(other_face)
                        faces.append(other_face)
                        coords.update(stepped)
                        continue
                    offsets = set()
                    for l in other_face.loops:
                        offsets.add((stepped[l][0] - coords[l][0], stepped[l][1] - coords[l][1]))
                    if len(offsets) > 1:
                        twisted = True
                        continue
                    offset = offsets.pop()
                    if offset != (0, 0):
                        periods.add(offset if offset > (0, 0) else (-offset[0], -offset[1]))
        return faces, coords, periods, twisted

    def map_grid_strict(self, seed_face, candidates):
        """
        Walk outward from seed_face, only taking faces that fit on the grid without
        overlapping another face or pulling a uv vertex to two places.
        Used when the quads do not form a clean grid, the rejected faces are left out
        """
        uvs = self.uvs
        coords = {}
        cells = set()
        corner_coords = {}
        faces = []
        seen = set()

        def try_add(face, face_coords):
            cell = self.get_cell(face_coords, face)
            if cell in cells:
                return False
            for l in face.loops:
                known = corner_coords.get((l.vert, uvs[l]))
                if known is not None and known != face_coords[l]:
                    return False
            cells.add(cell)
            seen.add(face)
            faces.append(face)
            for l in face.loops:
                coords[l] = face_coords[l]
                corner_coords[(l.vert, uvs[l])] = face_coords[l]
            return True

        try_add(seed_face, self.seed_coords(seed_face))
        i = 0
        while i < len(faces):
            face = faces[i]
            i += 1
            for loop in face.loops:
                for other, same_direction in self.get_edge_neighbors(loop, candidates):
                    if other.face not in seen:
                        try_add(other.face, self.step_coords(coords, loop, other, same_direction))
        return faces, coords

    def cut_cycle(self, faces, coords, period):
        """
        Open up a ring of faces that closes on itself by shifting faces a whole number
        of periods so they all sit on one side of a cut line.
        The cut follows a line of seams if there is one, otherwise it goes through a selected uv
        """
        axis = 0 if period[0] != 0 else 1
        length = period[axis]

        # look for a grid line that is made entirely of seams
        totals = {}
        seams = {}
        for face in faces:
            for loop in face.loops:
                c = coords[loop][axis]
                if coords[loop.link_loop_next][axis] != c:
                    continue
                key = c % length
                totals[key] = totals.get(key, 0) + 1
                if loop.edge.seam:
                    seams[key] = seams.get(key, 0) + 1

        cut_at = None
        for key in sorted(seams):
            if seams[key] == totals[key]:
                cut_at = key
                break
        if cut_at is None:
            for loop in self.selected_loops:
                if loop in coords:
                    cut_at = coords[loop][axis]
                    break
        if cut_at is None:
            cut_at = 0

        for face in faces:
            lowest = min(coords[loop][axis] for loop in face.loops)
            shift = (lowest - cut_at) // length
            if shift:
                for loop in face.loops:
                    c = coords[loop]
                    coords[loop] = (c[0] - shift * period[0], c[1] - shift * period[1])

    def find_grid(self, selected_only=False):
        """
        Find the part of this island that can be straightened into a grid.
        Returns (faces, coords) where coords maps every loop of those faces to a grid coordinate
        """
        if selected_only:
            selected = set(self.selected_loops)
            candidates = set()
            for face in self.faces:
                if len(face.loops) == 4 and all(loop in selected for loop in face.loops):
                    candidates.add(face)
            if len(candidates) < 1:
                raise SweetrollError("No fully selected quads on this island")
        else:
            candidates = set(face for face in self.faces if len(face.loops) == 4)
            if len(candidates) < 1:
                raise SweetrollError("This island has no quads")

        # try the faces the selection is on first
        seeds = []
        seeded = set()
        for loop in self.selected_loops:
            if loop.face in candidates and loop.face not in seeded:
                seeded.add(loop.face)
                seeds.append(loop.face)
        if len(seeds) < 1:
            seeds = [face for face in self.faces if face in candidates]

        # the quads may be in several separate groups, take the biggest one
        best = None
        placed = set()
        for seed in seeds:
            if seed in placed:
                continue
            group = self.map_grid_loose(seed, candidates)
            placed.update(group[0])
            if best is None or len(group[0]) > len(best[0]):
                best = (group[0], group[1], group[2], group[3], seed)
        faces, coords, periods, twisted, seed = best

        clean = not twisted and len(periods) < 2
        if clean and len(periods) == 1:
            self.cut_cycle(faces, coords, next(iter(periods)))
        if clean:
            cells = set(self.get_cell(coords, face) for face in faces)
            clean = len(cells) == len(faces)
        if not clean:
            faces, coords = self.map_grid_strict(seed, set(faces))
        return faces, coords

    def get_cut_edges(self, faces, coords):
        """ Get the edges where connected faces of the grid get pulled apart """
        face_set = set(faces)
        output = set()
        for face in faces:
            for loop in face.loops:
                for other, same_direction in self.get_edge_neighbors(loop, face_set):
                    stepped = self.step_coords(coords, loop, other, same_direction)
                    if stepped[other] != coords[other]:
                        output.add(loop.edge)
        return output

    def layout_grid(self, faces, coords, spacing='AVERAGE', orientation='VERTICAL', placement='KEEP'):
        """
        Work out where every loop of the grid goes in uv space.
        Returns a dict of loop -> (u, v)
        """
        uvs = self.uvs
        i0 = min(c[0] for c in coords.values())
        j0 = min(c[1] for c in coords.values())
        column_count = max(c[0] for c in coords.values()) - i0
        row_count = max(c[1] for c in coords.values()) - j0

        # for every column and row: [uv length, mesh length, edge count, border uv length, border edge count]
        columns = [[0.0, 0.0, 0, 0.0, 0] for _ in range(column_count)]
        rows = [[0.0, 0.0, 0, 0.0, 0] for _ in range(row_count)]
        for face in faces:
            for loop in face.loops:
                next_loop = loop.link_loop_next
                a = coords[loop]
                b = coords[next_loop]
                uv_a = uvs[loop]
                uv_b = uvs[next_loop]
                uv_length = math.hypot(uv_b[0] - uv_a[0], uv_b[1] - uv_a[1])
                if a[1] == b[1]:
                    totals = columns[min(a[0], b[0]) - i0]
                    on_border = a[1] == j0
                else:
                    totals = rows[min(a[1], b[1]) - j0]
                    on_border = a[0] == i0
                totals[0] += uv_length
                totals[1] += loop.edge.calc_length()
                totals[2] += 1
                if on_border:
                    totals[3] += uv_length
                    totals[4] += 1

        def get_sizes(lines):
            average = [t[0] / t[2] for t in lines]
            if spacing == 'BORDER':
                return [t[3] / t[4] if t[4] > 0 else average[k] for k, t in enumerate(lines)]
            if spacing == 'MESH':
                return [t[1] / t[2] for t in lines]
            if spacing == 'EVEN':
                return [sum(average) / len(average)] * len(average)
            return average

        widths = get_sizes(columns)
        heights = get_sizes(rows)

        if spacing == 'MESH':
            """ Scale the mesh lengths so the island covers the same area that it did before """
            uv_area = sum(abs(face_uv_area(uvs, face)) for face in faces)
            grid_area = 0.0
            for face in faces:
                cell = self.get_cell(coords, face)
                grid_area += widths[cell[0] - i0] * heights[cell[1] - j0]
            if uv_area > 0 and grid_area > 0:
                scale = math.sqrt(uv_area / grid_area)
            else:
                mesh_length = sum(t[1] for t in columns) + sum(t[1] for t in rows)
                uv_length = sum(t[0] for t in columns) + sum(t[0] for t in rows)
                scale = uv_length / mesh_length if mesh_length > 0 else 1.0
            widths = [w * scale for w in widths]
            heights = [h * scale for h in heights]

        column_xs = [0.0]
        for w in widths:
            column_xs.append(column_xs[-1] + w)
        row_ys = [0.0]
        for h in heights:
            row_ys.append(row_ys[-1] + h)

        """ Keep mirrored islands mirrored """
        mirror = -1.0 if sum(face_uv_area(uvs, face) for face in faces) < 0 else 1.0
        base = {}
        for loop, c in coords.items():
            base[loop] = (mirror * column_xs[c[0] - i0], row_ys[c[1] - j0])

        """ Of the rotations that are allowed, use the one closest to how the island sits now """
        straight = 0.0
        turned = 0.0
        for face in faces:
            for loop in face.loops:
                next_loop = loop.link_loop_next
                old_x = uvs[next_loop][0] - uvs[loop][0]
                old_y = uvs[next_loop][1] - uvs[loop][1]
                new_x = base[next_loop][0] - base[loop][0]
                new_y = base[next_loop][1] - base[loop][1]
                straight += old_x * new_x + old_y * new_y
                turned += old_y * new_x - old_x * new_y
        scores = [straight, turned, -straight, -turned]
        width = column_xs[-1]
        height = row_ys[-1]
        if orientation == 'VERTICAL':
            allowed = (0, 2) if height >= width else (1, 3)
        elif orientation == 'HORIZONTAL':
            allowed = (0, 2) if width >= height else (1, 3)
        else:
            allowed = (0, 1, 2, 3)
        turns = max(allowed, key=lambda t: scores[t])
        positions = {}
        for loop, p in base.items():
            positions[loop] = rotate_quarter_turns(p, turns)

        if placement == 'FIT':
            """ Scale the grid to fill the 0-1 uv square """
            min_x = min(p[0] for p in positions.values())
            max_x = max(p[0] for p in positions.values())
            min_y = min(p[1] for p in positions.values())
            max_y = max(p[1] for p in positions.values())
            size = max(max_x - min_x, max_y - min_y)
            scale = 1.0 / size if size > 0 else 1.0
            center_x = (min_x + max_x) * 0.5
            center_y = (min_y + max_y) * 0.5
            for loop, p in positions.items():
                positions[loop] = ((p[0] - center_x) * scale + 0.5, (p[1] - center_y) * scale + 0.5)
        else:
            """ Keep the selected uvs where they are (on average) """
            anchors = [loop for loop in self.selected_loops if loop in positions]
            if len(anchors) < 1:
                anchors = list(positions)
            offset_x = sum(uvs[loop][0] - positions[loop][0] for loop in anchors) / len(anchors)
            offset_y = sum(uvs[loop][1] - positions[loop][1] for loop in anchors) / len(anchors)
            for loop, p in positions.items():
                positions[loop] = (p[0] + offset_x, p[1] + offset_y)

        return positions

    def add_followers(self, faces, positions):
        """
        Loops of faces that are not part of the grid, but share a uv with a loop
        that is, have to move along with it
        """
        uvs = self.uvs
        face_set = set(faces)
        followers = {}
        touching = set()
        for face in faces:
            for loop in face.loops:
                for other in loop.vert.link_loops:
                    if other.face in face_set or other.face not in self.face_set:
                        continue
                    if uvs[other] != uvs[loop]:
                        continue
                    """ Prefer following a face that shares an edge (matters next to a cut) """
                    shares_edge = (loop.edge == other.link_loop_prev.edge
                                   or loop.link_loop_prev.edge == other.edge
                                   or loop.edge == other.edge
                                   or loop.link_loop_prev.edge == other.link_loop_prev.edge)
                    if other not in followers or (shares_edge and other not in touching):
                        followers[other] = positions[loop]
                    if shares_edge:
                        touching.add(other)
        positions.update(followers)

    def apply_positions(self, positions):
        """ Move the loops to their new uv coordinates """
        uv_layer = self.uv_layer
        for loop, p in positions.items():
            loop[uv_layer].uv = p

    def get_split_edges(self):
        """ Get the edges where this island is not connected to itself in uv space """
        uv_layer = self.uv_layer
        output = []
        seen = set()
        for face in self.faces:
            for loop in face.loops:
                edge = loop.edge
                if edge in seen:
                    continue
                seen.add(edge)
                a = loop[uv_layer].uv
                b = loop.link_loop_next[uv_layer].uv
                for other in edge.link_loops:
                    if other == loop or other.face not in self.face_set:
                        continue
                    other_a = other[uv_layer].uv
                    other_b = other.link_loop_next[uv_layer].uv
                    if other.vert == loop.vert:
                        connected = other_a == a and other_b == b
                    else:
                        connected = other_a == b and other_b == a
                    if not connected:
                        output.append(edge)
                        break
        return output

    def map_grid(self, spacing='AVERAGE', orientation='VERTICAL', placement='KEEP', selected_only=False):
        """
        Straighten this island (or the part of it that is a grid of quads).
        Returns (positions, cut_edges, complete) where complete is False if some of
        the faces of this island are not part of the grid and still need to be relaxed around it
        """
        faces, coords = self.find_grid(selected_only)
        positions = self.layout_grid(faces, coords, spacing, orientation, placement)
        cut_edges = self.get_cut_edges(faces, coords)
        complete = len(faces) == len(self.faces)
        if not complete:
            self.add_followers(faces, positions)
        return positions, cut_edges, complete


# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
#                           UVBmesh Class
# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=

class UVBmesh:
    """ A UVBmesh class is a class that works specifically with uv related aspects of a bmesh object"""
    def __init__(self, ob, sync_select=False):
        self.obj = ob
        self.bmesh = bmesh.from_edit_mesh(ob.data)
        self.uv_layer = self.bmesh.loops.layers.uv.verify()
        self.islands = []
        self.sync_select = sync_select
        # With sync selection on, the UV selection is the mesh selection, unless
        # the mesh is storing its own UV selection alongside it (Blender 5.0+)
        self.use_mesh_select = sync_select and not getattr(self.bmesh, "uv_select_sync_valid", False)

    def is_face_visible(self, face):
        """ Is this face shown in the UV editor """
        if face.hide:
            return False
        # without sync selection only selected faces are shown in the UV editor
        return self.sync_select or face.select

    def is_loop_selected(self, loop):
        """ Is this loop selected in the UV editor """
        if not self.is_face_visible(loop.face):
            return False
        if self.use_mesh_select:
            return loop.vert.select
        if HAS_LOOP_UV_SELECT:
            return loop.uv_select_vert
        return loop[self.uv_layer].select

    def set_loop_selected(self, loop, value):
        """ Set the UV editor selection state of this loop """
        if HAS_LOOP_UV_SELECT:
            loop.uv_select_vert = value
        else:
            loop[self.uv_layer].select = value

    def get_island_faces(self, face):
        """ Get all of the faces of the island that this face belongs to """
        uv_layer = self.uv_layer
        faces = [face]
        seen = {face}
        i = 0
        while i < len(faces):
            c_face = faces[i]
            i += 1
            for loop in c_face.loops:
                uv = loop[uv_layer].uv
                u = uv.x
                v = uv.y
                for shared_loop in loop.vert.link_loops:
                    shared_face = shared_loop.face
                    if shared_face in seen or not self.is_face_visible(shared_face):
                        continue
                    shared_uv = shared_loop[uv_layer].uv
                    if shared_uv.x == u and shared_uv.y == v:
                        seen.add(shared_face)
                        faces.append(shared_face)
        return faces

    def calculate_islands(self):
        """ Calculate the islands for this UVBMesh """
        self.islands = []
        claimed = set()
        for face in self.bmesh.faces:
            if face in claimed or not self.is_face_visible(face):
                continue
            faces = self.get_island_faces(face)
            claimed.update(faces)
            self.islands.append(UVIsland(self, faces))

    def get_selected_islands(self):
        """ Get the islands that have at least one uv selected """
        output = []
        claimed = set()
        for face in self.bmesh.faces:
            if face in claimed or not self.is_face_visible(face):
                continue
            for loop in face.loops:
                if self.is_loop_selected(loop):
                    faces = self.get_island_faces(face)
                    claimed.update(faces)
                    output.append(UVIsland(self, faces))
                    break
        return output

    def get_island_by_loop(self, loop):
        """ Find the island that contains the loop """
        if len(self.islands) < 1:
            self.calculate_islands()

        for island in self.islands:
            if loop in island:
                return island
        return None

    def get_uv_distance(self, loop1, loop2):
        """ Get distance between 2 uvs """
        return (loop2[self.uv_layer].uv - loop1[self.uv_layer].uv).magnitude

    def update_mesh(self):
        """ Save changes to the mesh """
        bmesh.update_edit_mesh(self.obj.data)

    def get_selected_uv_loops(self):
        output = []
        for face in self.bmesh.faces:
            for loop in face.loops:
                if self.is_loop_selected(loop):
                    output.append(loop)
        return output

    def has_selected_uv_loop(self):
        """ Returns true if any uv is selected on this mesh """
        for face in self.bmesh.faces:
            for loop in face.loops:
                if self.is_loop_selected(loop):
                    return True
        return False

    def get_loop_uv_coord(self, loop):
        """ Get the uv coord for a loop """
        return loop[self.uv_layer].uv

    def get_shared_loops(self, loop):
        """ Get a list of connected loops that share the same uv coord as the input loop """
        output = []
        for l in loop.vert.link_loops:
            if l != loop and l[self.uv_layer].uv == loop[self.uv_layer].uv:
                output.append(l)
        return output


# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
#                           Relaxing faces that are not part of a grid
# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=

class SelectionState:
    """ Remembers what is selected on a mesh (and in its uvs) so it can be put back """
    def __init__(self, uvbmesh):
        bm = uvbmesh.bmesh
        uv_layer = uvbmesh.uv_layer
        self.bmesh = bm
        self.uv_layer = uv_layer
        self.faces = [face.select for face in bm.faces]
        self.edges = [edge.select for edge in bm.edges]
        self.verts = [vert.select for vert in bm.verts]
        self.history = list(bm.select_history)
        self.active_face = bm.faces.active
        self.uv_faces = None
        self.sync_valid = None
        if HAS_LOOP_UV_SELECT:
            self.uv_faces = [face.uv_select for face in bm.faces]
            self.uv_loops = [(loop.uv_select_vert, loop.uv_select_edge) for face in bm.faces for loop in face.loops]
            self.sync_valid = bm.uv_select_sync_valid
        elif HAS_UV_SELECT_EDGE:
            self.uv_loops = [(loop[uv_layer].select, loop[uv_layer].select_edge) for face in bm.faces for loop in face.loops]
        else:
            self.uv_loops = [(loop[uv_layer].select, False) for face in bm.faces for loop in face.loops]

    def set_uv(self, face_values, loop_values):
        bm = self.bmesh
        uv_layer = self.uv_layer
        i = 0
        for k, face in enumerate(bm.faces):
            if HAS_LOOP_UV_SELECT:
                face.uv_select = face_values[k]
            for loop in face.loops:
                vert_value, edge_value = loop_values[i]
                i += 1
                if HAS_LOOP_UV_SELECT:
                    loop.uv_select_vert = vert_value
                    loop.uv_select_edge = edge_value
                else:
                    loop[uv_layer].select = vert_value
                    if HAS_UV_SELECT_EDGE:
                        loop[uv_layer].select_edge = edge_value

    def clear(self):
        """ Deselect everything """
        bm = self.bmesh
        for face in bm.faces:
            face.select = False
        for edge in bm.edges:
            edge.select = False
        for vert in bm.verts:
            vert.select = False
        self.set_uv([False] * len(self.faces), [(False, False)] * len(self.uv_loops))

    def restore(self):
        """ Put the selection back the way it was """
        bm = self.bmesh
        # faces first: selecting a face also selects its edges and verts
        for face, value in zip(bm.faces, self.faces):
            face.select = value
        for edge, value in zip(bm.edges, self.edges):
            edge.select = value
        for vert, value in zip(bm.verts, self.verts):
            vert.select = value
        self.set_uv(self.uv_faces, self.uv_loops)
        if HAS_LOOP_UV_SELECT and bm.uv_select_sync_valid != self.sync_valid:
            try:
                bm.uv_select_sync_valid = self.sync_valid
            except AttributeError:
                pass
        try:
            bm.select_history.clear()
            for element in self.history:
                bm.select_history.add(element)
            bm.faces.active = self.active_face
        except (ValueError, TypeError, ReferenceError):
            pass


def select_island(island):
    """ Select every face of the island in the mesh and in the uvs """
    uv_layer = island.uv_layer
    for face in island.faces:
        face.select = True
        if HAS_LOOP_UV_SELECT:
            face.uv_select = True
        for loop in face.loops:
            if HAS_LOOP_UV_SELECT:
                loop.uv_select_vert = True
                loop.uv_select_edge = True
            else:
                loop[uv_layer].select = True
                if HAS_UV_SELECT_EDGE:
                    loop[uv_layer].select_edge = True


def relax_islands(uvbmeshes, jobs, method):
    """
    Unwrap the faces that are not part of a straightened grid around the grid.
    uvbmeshes is every mesh that is in edit mode, jobs is a list of (island, positions)
    where positions holds the loops that have to stay where they are.
    This runs the unwrap operator with the grids pinned, so the selection,
    pins and seams are changed for it and put back afterwards
    """
    states = [SelectionState(uvbmesh) for uvbmesh in uvbmeshes]
    pins = []
    seams = []
    try:
        for state in states:
            state.clear()
        for island, positions in jobs:
            uv_layer = island.uv_layer
            select_island(island)
            for loop in positions:
                pins.append((loop, uv_layer, loop[uv_layer].pin_uv))
                loop[uv_layer].pin_uv = True
            """ unwrap goes by seams, not by what is connected in the uvs """
            for edge in island.get_split_edges():
                if not edge.seam:
                    seams.append(edge)
                    edge.seam = True
        try:
            bpy.ops.uv.unwrap(method=method)
        except TypeError:
            # this version of Blender does not have that method
            bpy.ops.uv.unwrap(method='ANGLE_BASED')
    finally:
        for edge in seams:
            edge.seam = False
        for loop, uv_layer, value in pins:
            loop[uv_layer].pin_uv = value
        for state in states:
            state.restore()
    """ make sure the grids are exactly where they were put """
    for island, positions in jobs:
        island.apply_positions(positions)


# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
#                           Plugin
# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=

class UV_OT_sweetroll(bpy.types.Operator):
    """Unroll Your Rolled UV Island"""
    bl_idname = "uv.sweetroll"
    bl_label = "Sweet (Un)roll"
    bl_options = {'REGISTER', 'UNDO'}

    spacing: EnumProperty(
        name="Spacing",
        description="How wide to make the rows and columns of the straightened island",
        items=[
            ('AVERAGE', "Average", "Use the average length of the uv edges in each row and column"),
            ('BORDER', "Border", "Use the length of the uv edges along the first row and column"),
            ('MESH', "Mesh", "Use the length of the edges on the mesh, so the texture is not stretched"),
            ('EVEN', "Even", "Make every row the same height and every column the same width"),
        ],
        default='AVERAGE',
    )
    orientation: EnumProperty(
        name="Orientation",
        description="Which way the straightened island points",
        items=[
            ('VERTICAL', "Vertical", "Long side goes up and down"),
            ('HORIZONTAL', "Horizontal", "Long side goes left to right"),
            ('CLOSEST', "Closest", "Whichever is closest to how the island sits now"),
        ],
        default='VERTICAL',
    )
    placement: EnumProperty(
        name="Placement",
        description="Where the straightened island ends up",
        items=[
            ('KEEP', "Keep in Place", "The selected uvs stay where they are and the island straightens around them"),
            ('FIT', "Fit to Bounds", "Scale the island to fill the 0-1 uv square"),
        ],
        default='KEEP',
    )
    relax_method: EnumProperty(
        name="Other Faces",
        description="What to do with faces that are not part of the grid of quads (triangles, ngons and poles)",
        items=[
            ('ANGLE_BASED', "Angle Based", "Unwrap them around the straightened grid with the Angle Based method"),
            ('CONFORMAL', "Conformal", "Unwrap them around the straightened grid with the Conformal method"),
            ('MINIMUM_STRETCH', "Minimum Stretch", "Unwrap them around the straightened grid with the Minimum Stretch method (Blender 4.3+)"),
            ('NONE', "Skip Island", "Leave islands that are not a clean grid of quads alone"),
        ],
        default='ANGLE_BASED',
    )
    selected_only: BoolProperty(
        name="Selected Faces Only",
        description="Only straighten the quads that are fully selected, and unwrap the rest of the island around them",
        default=False,
    )
    mark_seams: BoolProperty(
        name="Mark Seams at Cuts",
        description="Mark a seam where a closed loop had to be cut open",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "spacing")
        layout.prop(self, "orientation")
        layout.prop(self, "placement")
        layout.prop(self, "relax_method")
        layout.prop(self, "selected_only")
        layout.prop(self, "mark_seams")

    def execute(self, context):
        sync_select = context.scene.tool_settings.use_uv_select_sync
        uvbmeshes = []
        meshes = set()
        for ob in context.view_layer.objects:
            if ob.type != 'MESH' or ob.mode != 'EDIT' or ob.data.as_pointer() in meshes:
                continue
            meshes.add(ob.data.as_pointer())
            uvbmeshes.append(UVBmesh(ob, sync_select))

        unrolled = 0
        failed = 0
        relax_jobs = []
        for uvbm in uvbmeshes:
            for island in uvbm.get_selected_islands():
                try:
                    positions, cut_edges, complete = island.map_grid(
                        self.spacing, self.orientation, self.placement, self.selected_only)
                    if not complete and self.relax_method == 'NONE':
                        raise SweetrollError("This island is not a clean grid of quads")
                    island.apply_positions(positions)
                    if not complete:
                        relax_jobs.append((island, positions))
                    if self.mark_seams:
                        for edge in cut_edges:
                            edge.seam = True
                    unrolled += 1
                except Exception as e:
                    failed += 1
                    self.report({'WARNING'}, "%s: %s" % (uvbm.obj.name, e))

        if len(relax_jobs) > 0:
            try:
                relax_islands(uvbmeshes, relax_jobs, self.relax_method)
            except Exception as e:
                failed += 1
                self.report({'WARNING'}, "Could not unwrap the other faces: %s" % e)

        for uvbm in uvbmeshes:
            uvbm.update_mesh()

        if unrolled == 0 and failed == 0:
            self.report({'INFO'}, "Select a uv on the island you want to unroll")
        return {'FINISHED'}


def sweetrollMenuFunc(self, context):
    self.layout.operator("uv.sweetroll")


def register():
    bpy.utils.register_class(UV_OT_sweetroll)
    # add to menu
    bpy.types.IMAGE_MT_uvs.append(sweetrollMenuFunc)
    # add to context menu
    bpy.types.IMAGE_MT_uvs_context_menu.append(sweetrollMenuFunc)

def unregister():
    bpy.utils.unregister_class(UV_OT_sweetroll)

    bpy.types.IMAGE_MT_uvs.remove(sweetrollMenuFunc)
    bpy.types.IMAGE_MT_uvs_context_menu.remove(sweetrollMenuFunc)


if __name__=="__main__":
     register()
