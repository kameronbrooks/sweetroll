bl_info = {
    "name": "Sweetroll",
    "author": "Kameron Brooks",
    "version": (1, 1, 1),
    "blender": (3, 0, 0),
    "location": "View2D > UV > Sweet(Un)roll",
    "description": "Unroll your rolled up uv island",
    "warning": "",
    "doc_url": "",
    "category": "UV",
}
import os
import bpy
import bmesh
from mathutils import Vector
import math
from bpy.props import IntProperty, FloatProperty, BoolProperty, FloatVectorProperty
from collections import deque as deque

# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
#                           Utilities
# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=

def clamp(v, min_v, max_v):
    """ Clamp v between min_v and max_v """
    return min(max(v,min_v),max_v)

def get_perp_vector2_cw(v2):
    """ Get a clockwise perpendicuar vector to v2 """
    return Vector((v2.y, -v2.x))

def get_perp_vector2_ccw(v2):
    """ Get a counter-clockwise perpendicuar vector to v2 """
    return Vector((-v2.y, v2.x))

# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
#                           UVIsland Class
# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=

class UVIsland:
    """ Represents a UV island. Contains convenience methods for working with islands """
    def __init__(self, uvbmesh, loops):
        self.loops = loops
        self.uvbmesh = uvbmesh
        self.name = "island"
        self.quads_only = False
        self.uv_layer = uvbmesh.uv_layer
        self.initialize()
    
    def initialize(self):
        for loop in self.loops:
            if len(loop.face.loops) != 4:
                self.quads_only = False
                return
        self.quads_only = True
     
    def __iter__(self):
        return self.loops.__iter__()
    
    def get_corners(self):
        """ Get the corners of this island """
        output = []
        for loop in self.loops:
            uv_coord = loop[self.uv_layer].uv
            is_corner = True

            for v_loop in loop.vert.link_loops:
                if v_loop != loop and v_loop[self.uv_layer].uv == uv_coord:
                    is_corner = False
                    break
            if is_corner:
                output.append(loop)
        return output
    
    def get_corner_with_longest_edge(self, corners):
        """ Gets the corner with the longest edge """
        max_length = 0
        max_corner = None
        for corner in corners:
            c_loop = corner
            
            starting_face = c_loop.face
            """ Add a safety incase something crazy happens """
            i = 0
            max_iters = 1000000
            straight_length = 0
            
            """ First create all of the rows by traversing the edge nodes """
            while i < max_iters:
                i += 1
                
                next = c_loop.link_loop_prev
                shared_loops = self.uvbmesh.get_shared_loops(next)
                
                """ Calculate the distance between the current and next """
                segment_dist = self.uvbmesh.get_uv_distance(c_loop, next)
                straight_length += segment_dist
                
                if len(shared_loops) < 1:
                    break
                for shared_loop in shared_loops:
                    if next.link_loop_prev.vert == shared_loop.link_loop_next.vert:  
                        c_loop = shared_loop
                        break
            if straight_length > max_length:
                max_corner = corner
                max_length = straight_length
                
        print(max_corner)
        return max_corner
    
    
    def map_grid(self):
        if not self.quads_only:
            raise Exception("This island must be all quads")
        corners = self.get_corners()
        """ Get the longest edge """
        c_loop = self.get_corner_with_longest_edge(corners)

        grid = [[(c_loop.face,c_loop)]]
        starting_face = c_loop.face
        """ Add a safety incase something crazy happens """
        i = 0
        max_iters = 1000000
        straight_length = 0
        straight_width = 0
        row_ys = [0]
        column_xs = [0]
        """ First create all of the rows by traversing the edge nodes """
        while i < max_iters:
            i += 1
            
            next = c_loop.link_loop_prev
            shared_loops = self.uvbmesh.get_shared_loops(next)
            
            """ Calculate the distance between the current and next """
            segment_dist = self.uvbmesh.get_uv_distance(c_loop, next)
            straight_length += segment_dist
            row_ys.append(straight_length)
            
            if len(shared_loops) < 1:
                break
            for shared_loop in shared_loops:
                if next.link_loop_prev.vert == shared_loop.link_loop_next.vert:       
                    grid.append([(shared_loop.face, shared_loop)])
                    c_loop = shared_loop
                    break
        
        column_count = 1
        row_count = len(grid)
        
        """ Traverse each row to get each cell """
        for row in grid: 
            c_loop = row[0][1]
            
            i = 0
            while i < max_iters:
                i += 1
                next = c_loop.link_loop_next
                shared_loops = self.uvbmesh.get_shared_loops(next)
                
                """ Calculate the distance between the current and next """
                segment_dist = self.uvbmesh.get_uv_distance(c_loop, next)
                straight_width += segment_dist
                if i >= len(column_xs):
                    column_xs.append(straight_width)

                if len(shared_loops) < 1:
                    break
                found_neighbor_face = False
                for shared_loop in shared_loops:
                    if next.link_loop_next.vert == shared_loop.link_loop_prev.vert:       
                        row.append((shared_loop.face, shared_loop))
                        c_loop = shared_loop
                        found_neighbor_face = True
                        break
                if not found_neighbor_face:
                    break
            
            if len(row) > column_count:
                column_count = len(row)
        # This does not matter now since we are using the saved distances of the rows and columns
        segment_length = straight_length / row_count
        
        root_uv = row[0][1][self.uv_layer].uv + Vector((0,0))
        """ Move all loops to where they need to go based on a distance from the root_uv"""
        for y in range(row_count):
            row = grid[y]

            for x in range(len(row)):
                cell = row[x]
                
                cell[1][self.uv_layer].uv = root_uv + Vector((column_xs[x], row_ys[y]))
                cell[1].link_loop_prev[self.uv_layer].uv = root_uv + Vector((column_xs[x], row_ys[y + 1]))
                cell[1].link_loop_next[self.uv_layer].uv = root_uv + Vector((column_xs[x + 1], row_ys[y]))
                cell[1].link_loop_prev.link_loop_prev[self.uv_layer].uv = root_uv + Vector((column_xs[x + 1], row_ys[y + 1]))
                
        self.uvbmesh.update_mesh()           

                

# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
#                           UVBmesh Class
# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=       
        
class UVBmesh:
    """ A UVBmesh class is a class that works specifically with uv related aspects of a bmesh object"""
    def __init__(self, ob):
        self.obj = ob
        self.bmesh = bmesh.from_edit_mesh(ob.data)
        self.uv_layer = self.bmesh.loops.layers.uv.verify()
        self.islands = []
        
    
    
    def calculate_islands(self):
        """ Calculate the islands for this UVBMesh """
        self.islands = []
        for face in self.bmesh.faces:
            for loop in face.loops:
                found_island = False
                for island in self.islands:
                    if loop in island:
                        found_island = True
                        break
                
                if not found_island:
                    self.islands.append(UVIsland(self, self.get_island_loops(loop)))
    
    
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
    
    
    def is_vert_selected(self, vert):
        """ Is this vertex selected in the UV editor """
        for loop in vert.link_loops:
            if(loop[self.uv_layer].select):
                return True
        return False
    
    
    def get_selected_uv_loops(self):
        output = []
        for face in self.bmesh.faces:
            for loop in face.loops:
                if loop[self.uv_layer].select:
                    output.append(loop)
        return output
    
    def has_selected_uv_loop(self):
        """ Returns true if any uv is selected on this mesh """
        output = []
        for face in self.bmesh.faces:
            for loop in face.loops:
                if loop[self.uv_layer].select:
                    return True
        return False
    
    
    def count_selected_loops(self, vert):
        """ Count the number of selected loops for this vert """
        count = 0
        for loop in vert.link_loops:
            if(loop[self.uv_layer].select):
                count += 1
        return count
    
    
    def count_unique_vertex_uvs(self, vert, selected_only=False):
        """ Count the number of unique uv coordinates that represent a vertex"""
        unique_loops = set([])
        if selected_only:
            for loop in vert.link_loops:
                if loop[self.uv_layer].select:
                    uv = loop[self.uv_layer].uv
                    unique_loops.add((vert.index, uv[0], uv[1]))
        else:
            for loop in vert.link_loops:
                uv = loop[self.uv_layer].uv
                unique_loops.add((vert.index, uv[0], uv[1]))
        #print(unique_loops)
        return len(unique_loops)
    
    
    def get_island_loops(self, loop):
        """ Get the loops that belong to the selected island """
        
        output = set([])
        faces = []
        check_faces = deque([loop.face])
        checked_face_ids = set([])

        while len(check_faces) > 0:
            c_face = check_faces[0]

            check_faces.popleft()
            checked_face_ids.add(c_face.index)
            faces.append(c_face)
            
            for face_loop in c_face.loops:
                shared_loops = self.get_shared_loops(face_loop)
                for shared_loop in shared_loops:
                    if shared_loop == face_loop:
                        continue
                    if shared_loop.face.index not in checked_face_ids:
                        check_faces.append(shared_loop.face)
                        checked_face_ids.add(shared_loop.face.index)
        
        
        for face in faces:
            for fl in face.loops:
                output.add(fl)
        
        return output
    

    def select_all(self):
        """ Select all of the uvs in the mesh """
        for face in self.bmesh.faces:
            for loop in face.loops:
                loop[self.uv_layer].select = True
        self.update_mesh()
    
    
    def deselect_all(self):
        """ Deselect all of the uvs in the mesh """
        for face in self.bmesh.faces:
            for loop in face.loops:
                loop[self.uv_layer].select = False
        self.update_mesh()
    
    
    def select(self, loops, deselect_others = False):
        """ Select the specified uvs on the mesh """
        if deselect_others:
            for face in self.bmesh.faces:
                for loop in face.loops:
                    loop[self.uv_layer].select =  loop in loops
        else:
            for face in self.bmesh.faces:
                for loop in face.loops:
                    if loop in loops:
                        loop[self.uv_layer].select = True
                        
        self.update_mesh()
    
    
    def move_loop_uv_coord(self,loop, pos):
        if not isinstance(pos, Vector):
            pos = Vector(pos)
            
        for l in loop.vert.link_loops:
            if l[self.uv_layer].uv == loop[self.uv_layer].uv:
                l[self.uv_layer].uv = pos
    
         
    def get_loop_uv_coord(self, loop):
        """ Get the uv coord for a loop """
        return loop[self.uv_layer].uv
    
    
    def get_shared_loops(self,loop):
        """ Get a list of connected loops that share the same uv coord as the input loop """
        output = []
        for l in loop.vert.link_loops:
            if l != loop and l[self.uv_layer].uv == loop[self.uv_layer].uv:
                output.append(l)
        return output
    
    
    def get_corners(self):
        """ Find the corners of the UV island """
        output = []
        for vert in self.bmesh.verts:
            if self.is_vert_selected(vert):  
                uv_count = self.count_unique_vertex_uvs(vert, selected_only=True)
                selected_loops = self.count_selected_loops(vert)

                if uv_count >= selected_loops:
                    output.append(vert)
        return output
    
    def face_shared_verts(self, face0, face1):
        """ Which verts are shared by both faces """
        shared = []
        for vert_0 in face0.verts:
            for vert_1 in face1.verts:
                if vert_0.index == vert_1.index:
                    shared.append(vert_0)
        return shared
    
    def free(self):
        self.bmesh.free()
                
    
    
    def get_cw_path(self,loop):
        
        output = []
        i = 0
        max_search = 100000
        while i < max_search:
            next = loop.link_loop_prev
            shared_loops = self.get_shared_loops(next)
            
            if len(shared_loops) < 1:
                output.append(next)
                break
            elif len(shared_loops) == 1:
                output.append(shared_loops[0])
            else:
                for shared_loop in shared_loops:
                    if next.link_loop_prev.vert.index == shared_loop.link_loop_next.vert.index:
                        output.append(shared_loop)
                    
            i += 1
        self.update_mesh()
        
        return loop.link_loop_prev
    

# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
#                           Plugin
# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=

class UV_OT_sweetroll(bpy.types.Operator):
    """Unroll Your Rolled UV Island"""
    bl_idname = "uv.sweetroll"
    bl_label = "Sweet (Un)roll"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        all_objects_edit_mode = True
        for ob in context.selected_editable_objects:
            if ob.type != 'MESH' or ob.mode != 'EDIT':
                all_objects_edit_mode = False
                break
        return all_objects_edit_mode
    
    def execute(self, context):
        for ob in context.selected_editable_objects:
            try:
                uvbm = UVBmesh(ob)
                # if not selected we can skip it
                if not uvbm.has_selected_uv_loop():
                    continue
                
                selected_loops = uvbm.get_selected_uv_loops()
                
                selected_islands = set([])
                for selected_loop in selected_loops:
                    selected_islands.add(uvbm.get_island_by_loop(selected_loop))  
                for island in selected_islands:
                    island.map_grid()
            except:
                continue
            
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

def test_func():
    obs = bpy.context.selected_editable_objects
    for ob in obs:
        uvbm = UVBmesh(ob)
        
        selected_loops = uvbm.get_selected_uv_loops();
        grid = uvbm.get_island_by_loop(selected_loops[0]).map_grid()



if __name__=="__main__":
     register()
     