#!/usr/bin/env python3

import copy
import random
import rospy
import tf
import math

from std_msgs.msg import ColorRGBA
from visualization_msgs.msg import Marker
from geometry_msgs.msg import Point, Vector3, PointStamped
from std_msgs.msg import Header
from object_pose_msgs.msg import ObjectList, ObjectPose
from moveit_msgs.msg import CollisionObject, PlanningScene
from typing import List


def make_plane_marker_msg(ref_frame, plane):
    '''
    receives 4 points, makes 2 triangles to form a squared plane
    '''
    assert isinstance(plane, list)
    assert len(plane) == 4
    for i in range(4):
        assert isinstance(plane[i], Point)
    marker_msg = Marker()
    # marker_msg.lifetime = rospy.Duration(15.0)
    marker_msg.ns = 'support_plane'
    marker_msg.pose.orientation.x = 0.0
    marker_msg.pose.orientation.y = 0.0
    marker_msg.pose.orientation.z = 0.0
    marker_msg.pose.orientation.w = 1.0
    marker_msg.header.frame_id = ref_frame
    marker_msg.type = Marker.TRIANGLE_LIST
    # p0, p1, p2
    marker_msg.points.append(plane[0])
    marker_msg.points.append(plane[1])
    marker_msg.points.append(plane[2])
    # p2, p3, p0
    marker_msg.points.append(plane[2])
    marker_msg.points.append(plane[3])
    marker_msg.points.append(plane[0])
    marker_msg.scale = Vector3(1.0, 1.0, 1.0)
    marker_msg.color = ColorRGBA(1.0, 0.61, 0.16, 1.0) # orange
    return marker_msg

def compute_object_height_for_insertion(object_class_tbi, support_obj_class, gap_between_objects=0.02):
    # ohd : objects height dictionary, object_class_tbi : object class to be inserted
    ohd = {'power_drill_with_grip': 0.2205359935760498,
           'klt': 0.14699999809265138,
           'multimeter': 0.04206399992108345,
           'relay': 0.10436400026082993,
           'screwdriver': 0.034412000328302383,
           'insole': 0.21,
           'bag': 0.34}
    return (ohd[support_obj_class] / 2.0) + (ohd[object_class_tbi] / 2.0) + gap_between_objects

def gen_insert_poses_from_obj(object_class, support_object_pose, obj_height, frame_id='map', same_orientation_as_support_obj=False):
    '''
    if same_orientation_as_support_obj is True then the object is aligned with the support object (e.g. box)
    if same_orientation_as_support_obj is False then 360 degree orientations are used to place the object (can be used if first time fails)
    '''
    object_list_msg = ObjectList()
    object_list_msg.header.frame_id = frame_id
    insert_poses_id = 1
    # only one insert pose for now
    object_pose_msg = ObjectPose()
    object_pose_msg.class_id = object_class
    object_pose_msg.pose.position.x = support_object_pose.pose.position.x
    object_pose_msg.pose.position.y = support_object_pose.pose.position.y
    object_pose_msg.pose.position.z = support_object_pose.pose.position.z + obj_height
    roll = 0.0
    pitch = 0.0
    yaw = 0.0
    # HACK: object specific rotations
    if object_class == 'power_drill_with_grip':
        roll = - math.pi / 2.0
    elif object_class == 'insole':
        roll = -1.54
    
    if not same_orientation_as_support_obj:
        for i in range(7):
            angular_q = tf.transformations.quaternion_from_euler(roll, pitch, yaw)
            object_pose_msg.pose.orientation.x = angular_q[0]
            object_pose_msg.pose.orientation.y = angular_q[1]
            object_pose_msg.pose.orientation.z = angular_q[2]
            object_pose_msg.pose.orientation.w = angular_q[3]
            object_pose_msg.instance_id = insert_poses_id
            object_list_msg.objects.append(copy.deepcopy(object_pose_msg))
            insert_poses_id +=1
            if object_class == 'insole':
                yaw += 3.14159 # ~ 180 degree
            else:
                yaw += 0.5 # ~ 30 degree
    else:
        object_pose_msg.pose.orientation.x = support_object_pose.pose.orientation.x
        object_pose_msg.pose.orientation.y = support_object_pose.pose.orientation.y
        object_pose_msg.pose.orientation.z = support_object_pose.pose.orientation.z
        object_pose_msg.pose.orientation.w = support_object_pose.pose.orientation.w
        if object_class == 'insole':
            object_pose_msg.pose.orientation.x -= 1.54
        object_pose_msg.instance_id = insert_poses_id
        object_list_msg.objects.append(copy.deepcopy(object_pose_msg))
        insert_poses_id +=1
        q = [support_object_pose.pose.orientation.x, support_object_pose.pose.orientation.y,
             support_object_pose.pose.orientation.z, support_object_pose.pose.orientation.w]
        euler_rot = tf.transformations.euler_from_quaternion(q)
        if object_class == 'insole':
            q_new = tf.transformations.quaternion_from_euler(-1.54, euler_rot[1], euler_rot[2] + 3.14159) # roll + 90 degree for insole, yaw + 180 degree
        else:
            q_new = tf.transformations.quaternion_from_euler(euler_rot[0], euler_rot[1], euler_rot[2] + 3.14159) # yaw + 180 degree
        object_pose_msg.pose.orientation.x = q_new[0]
        object_pose_msg.pose.orientation.y = q_new[1]
        object_pose_msg.pose.orientation.z = q_new[2]
        object_pose_msg.pose.orientation.w = q_new[3]
        object_pose_msg.instance_id = insert_poses_id
        object_list_msg.objects.append(copy.deepcopy(object_pose_msg))
        insert_poses_id +=1
    return object_list_msg

def well_separated(x_y_list, candidate_x, candidate_y, min_dist=0.2):
    if len(x_y_list) == 0:
        return True
    true_count = 0
    for p in x_y_list:
        if math.dist([candidate_x, candidate_y], p) > min_dist:
            true_count += 1
    if true_count == len(x_y_list):
        return True
    return False

def gen_place_poses_from_plane(object_class: str, support_object:str, plane: List[str], planning_scene: PlanningScene, frame_id:str = "map",
                                number_of_poses: int = 10, min_dist: float = 0.2, ignore_min_dist_list: List[str] = []):
    '''
    random sample poses within a plane and populate object list msg with the result
    '''
    if number_of_poses > 100:
        min_dist = 0.03
        rospy.logwarn(f'number of poses is greater than 100, min_dist will be set to {0.03} instead of desired value of {min_dist}')
    object_list_msg = ObjectList()
    object_list_msg.header.frame_id = frame_id
    x_y_list = []
    place_poses_id = 1
    for _ in range(1, number_of_poses + 1):
        object_pose_msg = ObjectPose()
        object_pose_msg.class_id = object_class
        count = 0
        while 1:
            candidate_x = round(random.uniform(plane[0].x, plane[1].x), 4)
            candidate_y = round(random.uniform(plane[0].y, plane[3].y), 4)
            if support_object in ignore_min_dist_list:
                rospy.logwarn(f'ignoring min dist param for object: {object_class}')
                break
            if well_separated(x_y_list, candidate_x, candidate_y, min_dist=min_dist):
                break
            count += 1
            if count > 50000: # avoid an infinite loop, cap the max attempts
                rospy.logwarn(f'Could not generate poses too much separated from each other, min dist : {min_dist}')
                break
        x_y_list.append([candidate_x, candidate_y])

        object_pose_msg.pose.position.x = candidate_x
        object_pose_msg.pose.position.y = candidate_y

        object_pose_msg.pose.position.z = attached_obj_height(support_object, planning_scene)

        roll = 0.0
        pitch = 0.0
        yaw = round(random.uniform(0.0, math.pi), 4)
        # HACK: object specific rotations
        if object_class == 'power_drill_with_grip':
            roll = - math.pi / 2.0
        if number_of_poses > 20:
            rospy.loginfo('covering 360 angle for each pose')
            yaw = 0.0
            for _ in range(7):
                angular_q = tf.transformations.quaternion_from_euler(roll, pitch, yaw)
                object_pose_msg.pose.orientation.x = angular_q[0]
                object_pose_msg.pose.orientation.y = angular_q[1]
                object_pose_msg.pose.orientation.z = angular_q[2]
                object_pose_msg.pose.orientation.w = angular_q[3]
                object_pose_msg.instance_id = place_poses_id
                object_list_msg.objects.append(copy.deepcopy(object_pose_msg))
                place_poses_id +=1
                yaw += 0.5 # ~ 30 degree
        else:
            angular_q = tf.transformations.quaternion_from_euler(roll, pitch, yaw)
            object_pose_msg.pose.orientation.x = angular_q[0]
            object_pose_msg.pose.orientation.y = angular_q[1]
            object_pose_msg.pose.orientation.z = angular_q[2]
            object_pose_msg.pose.orientation.w = angular_q[3]
            object_pose_msg.instance_id = place_poses_id
            object_list_msg.objects.append(copy.deepcopy(object_pose_msg))
            place_poses_id +=1
    return object_list_msg

# TODO: consider using shape_msgs/Plane instead of 4 points
def adjust_plane(
        plane: List[Point],
        x_extend: float = 0.0, 
        y_extend: float = 0.0, 
        x_offset: float = 0.0, 
        y_offset: float = 0.0
    ) -> List[Point]:
    """
    Adjust the dimensions and position of a plane.

    Parameters
    ----------
    plane : List[Point]
        A list of four points representing a plane.
    x_extend : float, optional
        The amount to increase/decrease all points on the x-axis. Defaults to 0.0.
    y_extend : float, optional
        The amount to increase/decrease all points on the y-axis. Defaults to 0.0.
    x_offset : float, optional
        The amount to offset all points on the x-axis. Defaults to 0.0.
    y_offset : float, optional
        The amount to offset all points on the y-axis. Defaults to 0.0.

    Raises
    ------
    ValueError
        If the plane is not parallel to the XY plane.

    Returns
    -------
    List[Point]
        A list of four points representing the adjusted plane.
    """
    if not len(set([p.z for p in plane])) <= 1:
        raise ValueError("Plane is not parallel to the XY plane")

    plane = [Point(p.x + x_offset, p.y + y_offset, p.z) for p in plane]

    min_x, max_x = min(p.x for p in plane), max(p.x for p in plane)
    min_y, max_y = min(p.y for p in plane), max(p.y for p in plane)

    return [Point(min_x - x_extend, min_y - y_extend, plane[0].z),
            Point(max_x + x_extend, min_y - y_extend, plane[0].z),
            Point(max_x + x_extend, max_y + y_extend, plane[0].z),
            Point(min_x - x_extend, max_y + y_extend, plane[0].z)]

def visualize_points(points: List[Point], point_publisher: rospy.Publisher) -> None:
    """Publish points to view them in RViz"""
    for point in points:
        point_publisher.publish(PointStamped(header=Header(frame_id='map'), point=point))
        rospy.sleep(0.5)

def get_obj_from_planning_scene(obj_name: str, planning_scene: PlanningScene) -> CollisionObject:
    """
    Get a Object from a MoveIt PlanningScene by its name.

    Parameters
    ----------
    obj_name : str
        The name of the object to retrieve.
    planning_scene : PlanningScene
        The MoveIt PlanningScene to retrieve the object from.

    Returns
    -------
    CollisionObject
        The CollisionObject with the specified name.

    Raises
    ------
    ValueError
        If the object with the specified name is not in the planning scene.
    """
    if obj_name not in planning_scene.get_known_object_names():
        raise ValueError(f"Object '{obj_name}' not in planning scene")
    return planning_scene.get_objects([obj_name])[obj_name]

# TODO: consider shape_msgs/Plane instead of 4 points
def obj_to_plane(support_obj: str, planning_scene: PlanningScene, offset: float = 0.001) -> List[Point]:
    """
    Get a top surface plane from an object in the MoveIt planning scene.
 
    NOTE: Currently only works for boxes parallel to the XY plane.

    Parameters
    ----------
    support_obj : str
        The name of the box in the MoveIt planning scene.
    planning_scene : PlanningScene
        The MoveIt planning scene.
    offset : float, optional
        The offset from the MoveIt object and the created plane. Defaults to 0.001.

    Returns
    -------
    List[Point]
        A list of four points representing the corners of the top surface plane.
    """
    collision_object = get_obj_from_planning_scene(support_obj, planning_scene)

    if len(collision_object.primitives) != 1 or collision_object.primitives[0].type != 1:
        raise ValueError(f"Object '{support_obj}' is not a box")
     
    rotation_angle = tf.euler_from_quaternion(
        [collision_object.pose.orientation.x, collision_object.pose.orientation.y,
        collision_object.pose.orientation.z, collision_object.pose.orientation.w]
    )

    # TODO: this should be checked in a central place and not in each function
    if rotation_angle[0] != 0 or rotation_angle[1] != 0:
        raise ValueError(f"Object '{support_obj}' is not aligned with the XY plane")

    half_width = collision_object.primitives[0].dimensions[0] / 2
    half_depth = collision_object.primitives[0].dimensions[1] / 2
    
    center_point = [collision_object.pose.position.x, collision_object.pose.position.y, collision_object.pose.position.z * 2]

    corner_offsets = [(half_width, half_depth), (-half_width, half_depth), (-half_width, -half_depth), (half_width, -half_depth)]

    return [Point(center_point[0] + dx * math.cos(rotation_angle[2]) - dy * math.sin(rotation_angle[2]),
             center_point[1] + dx * math.sin(rotation_angle[2]) + dy * math.cos(rotation_angle[2]),
             center_point[2] + offset) for dx, dy in corner_offsets]

def attached_obj_height(attached_obj: str, planning_scene: PlanningScene, offset: float = 0.001) -> float:
    """
    Get the height of an object attached to the gripper

    Parameters
    ----------
    attached_obj : str
        The name of the attached object.
    planning_scene : PlanningScene
        The planning scene object.
    offset : float, optional
        An offset for the height. by default 0.001.

    Returns
    -------
    float
        The height of the attached object.
    """
    collision_object = get_obj_from_planning_scene(attached_obj, planning_scene)
    half_height_att_obj = list(planning_scene.get_attached_objects().values())[0].object.primitives[0].dimensions[2]  
    return half_height_att_obj + collision_object.pose.position.z * 2 + offset

# Example usage
if __name__ == '__main__':
    rospy.init_node('plane_visualiser', anonymous=False)
    support_plane_marker_pub = rospy.Publisher('support_plane_as_marker', Marker, queue_size=1, latch=True)
    point_pub = rospy.Publisher('plane_points', PointStamped, queue_size=1, latch=False)
    place_poses_pub = rospy.Publisher('~place_poses', ObjectList, queue_size=1, latch=True)
    rospy.loginfo('test started')
    rospy.sleep(0.2)

    support_object = 'table_3' # the object from which a surface will be generated and later on an object needs to be placed
    object_tbp = 'power_drill_with_grip' # the obj class to be place on a surface
    plane_1 = obj_to_plane(support_object)
    # currently the points need to be specified in a specific order (this is a workaround)
    # the animation helps to make sure the order is correct so that the functions can work correctly
    animate_plane_points(plane_1, point_pub)
    plane_1 = reduce_plane_area(plane_1, -0.2)
    # visualise plane as marker
    marker_msg = make_plane_marker_msg('map', plane_1)
    support_plane_marker_pub.publish(marker_msg)
    object_list_msg = gen_place_poses_from_plane(object_tbp, support_object, plane_1)
    place_poses_pub.publish(object_list_msg)

    rospy.loginfo('test finished')
    rospy.sleep(1.0)
