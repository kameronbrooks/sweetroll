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
        output = []
        for loop in self.loops:
            uv_coord = loop[self.uv_layer].uv
            is_corner = True
            print(uv_coord)
            for v_loop in loop.vert.link_loops:
                if v_loop != loop and v_loop[self.uv_layer].uv == uv_coord:
                    is_corner = False
                    break
            if is_corner:
                output.append(loop)
        return output
                    
    
    def map_grid(self):
        pass

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
        self.calculate_islands()
    
    
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
        for island in self.islands:
            if loop in island:
                return island
        return None
    
    
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
                print(loop[self.uv_layer].select)
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
    
    
    def move_loop_uv(self,loop, pos):
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
        #radial_next = loop.link_loop_next.link_loop_radial_next.link_loop_next
        
        #next[self.uv_layer].select = True
        #radial_next[self.uv_layer].select = True
        
        self.update_mesh()
        
        return loop.link_loop_prev
    
    

        
    

# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
#                           Plugin
# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=




def register():
    pass

def unregister():
    pass

def test_func():
    obs = bpy.context.selected_editable_objects
    for ob in obs:
        uvbm = UVBmesh(ob)
        
        corners = uvbm.islands[4].get_corners()
        
        #selected_loops = uvbm.get_selected_uv_loops();
        
        
        print(corners)
        uvbm.select(corners)


if __name__=="__main__":
     #register()
     os.system('cls')
     test_func()
     