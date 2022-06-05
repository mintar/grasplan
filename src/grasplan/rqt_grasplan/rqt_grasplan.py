#!/usr/bin/env python3

import os
import tf
import math
import copy
import rospy
import rospkg
import yaml
import numpy as np

from qt_gui.plugin import Plugin
from python_qt_binding import loadUi
from python_qt_binding.QtWidgets import QWidget, QFileDialog, QMessageBox

from grasplan.common_grasp_tools import remove_object_id
from grasplan.visualisation.grasp_visualiser import GraspVisualiser

from std_msgs.msg import Int8
from std_msgs.msg import String
from geometry_msgs.msg import Pose, PoseArray, PoseStamped, Quaternion

class OpenFileDialog(QWidget):
    '''
    allow the user to select a different yaml file with a button,
    this will open a dialog to select and open a yaml file
    '''
    def __init__(self):
        super().__init__()
        left, top, width, height = 10, 10, 640, 480
        self.setGeometry(left, top, width, height)

    def openFileNameDialog(self):
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        fileName, _ = QFileDialog.getOpenFileName(self, 'Select grasps yaml file',\
                      os.environ['HOME'],'Yaml Files (*.yaml)', options=options)
        if fileName:
            return fileName

class RqtGrasplan(Plugin):

    def __init__(self, context):
        super(RqtGrasplan, self).__init__(context)
        rospy.loginfo('Initializing grasplan rqt, have a happy grasp editing !')

        self.setObjectName('RqtGrasplan')
        # Create QWidget
        self._widget = QWidget()
        # Get path to UI file (xml description of the gui window created with qtcreator)
        ui_file = os.path.join(rospkg.RosPack().get_path('grasplan'), 'config/rqt_grasplan', 'rqtgrasplan.ui')
        # Extend the widget with all attributes and children from UI file
        loadUi(ui_file, self._widget)
        self._widget.setObjectName('rqtgrasplan.ui')
        # Show _widget.windowTitle on left-top of each plugin (when
        # it's set in _widget). This is useful when you open multiple
        # plugins at once. Also if you open multiple instances of your
        # plugin at once, these lines add number to make it easy to
        # tell from pane to pane.
        if context.serial_number() > 1:
            self._widget.setWindowTitle(self._widget.windowTitle() + (' (%d)' % context.serial_number()))

        # variables
        self.grasps_as_pose_array = None # stores all grasps
        self.grasps_yaml_path = None
        self.object_name = None
        self.tab = '    ' # used in save function
        # flag used to highlight a grasp in green color, set to -1 to not highlight any pose in particular
        self.selected_pose = -1
        self.global_reference_frame = 'object'

        # publications
        self.pose_highlight_pub = rospy.Publisher('/rviz_gripper_visualiser/highlight_pose', Int8, queue_size=1)
        self.object_mesh_pub = rospy.Publisher('/grasp_editor/update_object_mesh', String, queue_size=1)
        self.grasp_poses_pub = rospy.Publisher('/grasp_editor/grasp_poses', PoseArray, queue_size=1)
        self.test_pose_pub = rospy.Publisher('/test_pose', PoseStamped, queue_size=1)

        # parameters
        obj_pkg_name = rospy.get_param('obj_pkg_name', 'mobipick_gazebo')
        if rospy.has_param('~object_name'):
            self.object_name = rospy.get_param('~object_name')
            # set object name to textbox
            self._widget.txtFileObjectName.setText(self.object_name)
            if rospy.has_param('~grasps_yaml_path'):
                self.grasps_yaml_path = rospy.get_param('~grasps_yaml_path') + f'/handcoded_grasp_planner_{self.object_name}.yaml'
                self.grasps_as_pose_array = self.load_grasps_from_yaml(self.object_name, self.grasps_yaml_path)
            else:
                rospy.logwarn('object name parameter is set but grasps_yaml_path param is missing, is this correct?')

        # publish object mesh to rviz with texture
        grasp_visualiser = GraspVisualiser()
        mesh_path = f'package://{obj_pkg_name}/meshes/{self.object_name}.dae'
        marker_msg = grasp_visualiser.make_mesh_marker_msg(mesh_path)
        grasp_visualiser.object_mesh_publisher.publish(marker_msg)

        # visualise grasps at startup
        self.publish_grasps()

        ## make a connection between the qt objects and this class methods
        self._widget.cmdFilePrintG.clicked.connect(self.handle_file_print_grasps_button)
        self._widget.cmdFileLoadG.clicked.connect(self.handle_file_load_grasps_button)
        self._widget.cmdFileSaveG.clicked.connect(self.handle_file_save_grasps_button)
        self._widget.cmdGraspSSelect.clicked.connect(self.handle_grasp_s_select_button)
        self._widget.cmdGraspSDelete.clicked.connect(self.handle_grasp_s_delete_button)
        self._widget.cmdEditGApply.clicked.connect(self.handle_edit_g_apply_button)
        self._widget.cmdTransformApply.clicked.connect(self.handle_transform_apply_button)
        self._widget.cmdGraspSUnselect.clicked.connect(self.handle_grasp_s_unselect_button)
        self._widget.cmdTransformCreateGrasp.clicked.connect(self.handle_transform_create_grasp_button)
        self._widget.cmdTransformQ2RPY.clicked.connect(self.handle_transform_q_2_rpy_button)
        self._widget.cmdTransformRPY2Q.clicked.connect(self.handle_transform_rpy_2_q_button)
        self._widget.cmdFileSelectObjPath.clicked.connect(self.handle_select_obj_path_button)

        # inform the user how many grasps were loaded
        self.update_grasp_number()
        context.add_widget(self._widget)
        rospy.loginfo('init finished')
        # end of constructor

    # ::::::::::::::  class methods

    def log_error(self, error_msg):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setText('Error:')
        msg.setInformativeText(error_msg)
        msg.setWindowTitle('Grasp editor')
        msg.exec_()
        rospy.logerr(error_msg)

    def list_to_pose_msg(self, linear=[0,0,0], angular_q=[0,0,0,1]):
        '''
        build a pose msg from input lists
        return the pose msg
        '''
        pose_msg = Pose()
        pose_msg.position.x = linear[0]
        pose_msg.position.y = linear[1]
        pose_msg.position.z = linear[2]
        pose_msg.orientation.x = angular_q[0]
        pose_msg.orientation.y = angular_q[1]
        pose_msg.orientation.z = angular_q[2]
        pose_msg.orientation.w = angular_q[3]
        return pose_msg

    def list_to_pose_stamped_msg(self, linear=[0,0,0], angular_q=[0,0,0,1]):
        '''
        build a pose stamped msg from input lists
        return the pose stamped msg
        '''
        pose_stamped_msg = PoseStamped()
        pose_stamped_msg.header.frame_id = self.global_reference_frame
        pose_stamped_msg.pose.position.x = linear[0]
        pose_stamped_msg.pose.position.y = linear[1]
        pose_stamped_msg.pose.position.z = linear[2]
        pose_stamped_msg.pose.orientation.x = angular_q[0]
        pose_stamped_msg.pose.orientation.y = angular_q[1]
        pose_stamped_msg.pose.orientation.z = angular_q[2]
        pose_stamped_msg.pose.orientation.w = angular_q[3]
        return pose_stamped_msg

    def publish_test_pose(self, linear=[0,0,0], angular_q=[0,0,0,1]):
        '''
        build pose stamped msg from input lists
        publish to test topic to visualise in rviz
        '''
        pose_stamped_msg = self.list_to_pose_stamped_msg(linear, angular_q)
        self.test_pose_pub.publish(pose_stamped_msg)

    def write_grasps_to_yaml_file(self, grasps, object_name):
        grasp_stream_list = ['# this file was generated automatically by grasplan grasp editor']
        tab = '  '
        grasp_stream_list.append(f'{object_name}:')
        grasp_stream_list.append(f'{tab}grasp_poses:')
        for grasp in grasps:
            linear = [grasp.position.x, grasp.position.y, grasp.position.z]
            angular_q = [grasp.orientation.x, grasp.orientation.y, grasp.orientation.z, grasp.orientation.w]
            grasp_stream_list.append(f'{tab}{tab}-')
            # translation
            translation_str = f'{tab}{tab}{tab}translation: [{linear[0]:.6f}, {linear[1]:.6f}, {linear[2]:.6f}]'
            grasp_stream_list.append(translation_str)
            # rotation
            rotation_str = f'{tab}{tab}{tab}rotation: [{angular_q[0]:.6f}, {angular_q[1]:.6f}, {angular_q[2]:.6f}, {angular_q[3]:.6f}]'
            grasp_stream_list.append(rotation_str)
        rospy.loginfo(f'writing grasps to file: {self.grasps_yaml_path}')
        f = open(self.grasps_yaml_path,'w+')
        for string in grasp_stream_list:
            f.write(string + '\n')
        f.close()

    def handle_file_save_grasps_button(self):
        rospy.loginfo('save!')
        self.write_grasps_to_yaml_file(self.grasps_as_pose_array.poses, self._widget.txtFileObjectName.toPlainText())

    def fix_displayed_text(self):
        '''
        sometimes the text boxes show a -0.0 number, this functions converts it into 0.0
        '''
        if self._widget.txtTransformLinearX.toPlainText() == '-0.0':
            self._widget.txtTransformLinearX.setPlainText('0.0')
        if self._widget.txtTransformLinearY.toPlainText() == '-0.0':
            self._widget.txtTransformLinearY.setPlainText('0.0')
        if self._widget.txtTransformLinearZ.toPlainText() == '-0.0':
            self._widget.txtTransformLinearZ.setPlainText('0.0')
        if self._widget.txtTransformAngularR.toPlainText() == '-0.0':
            self._widget.txtTransformAngularR.setPlainText('0.0')
        if self._widget.txtTransformAngularP.toPlainText() == '-0.0':
            self._widget.txtTransformAngularP.setPlainText('0.0')
        if self._widget.txtTransformAngularY.toPlainText() == '-0.0':
            self._widget.txtTransformAngularY.setPlainText('0.0')

    def write_rpy_to_tf_textbox(self, angular_rpy):
        self._widget.txtTransformAngularR.setPlainText(str(round(angular_rpy[0], 2)))
        self._widget.txtTransformAngularP.setPlainText(str(round(angular_rpy[1], 2)))
        self._widget.txtTransformAngularY.setPlainText(str(round(angular_rpy[2], 2)))
        # avoid -0.0 being displayed as text
        self.fix_displayed_text()

    def handle_transform_q_2_rpy_button(self):
        '''
        button that allows to explicitely change a quaternion to rpy
        additionally it publishes the transform as pose stamped msg for visualisation purposes
        '''
        linear, angular_rpy, angular_q = self.read_transform(apply_rpy_to_q=False)
        angular_rpy = list(tf.transformations.euler_from_quaternion(angular_q))
        if self._widget.optTransformUnitsDeg.isChecked():
            # angular_rpy is required in degrees
            angular_rpy = self.convert_rpy_rad_to_deg(angular_rpy)
        self.write_rpy_to_tf_textbox(angular_rpy)
        self.publish_test_pose(angular_q=[*angular_q])

    def write_q_to_tf_textbox(self, quaternion):
        self._widget.txtTransformAngularQx.setPlainText(str(round(quaternion[0], 4)))
        self._widget.txtTransformAngularQy.setPlainText(str(round(quaternion[1], 4)))
        self._widget.txtTransformAngularQz.setPlainText(str(round(quaternion[2], 4)))
        self._widget.txtTransformAngularQw.setPlainText(str(round(quaternion[3], 4)))

    def handle_transform_rpy_2_q_button(self):
        '''
        button that allows to explicitely change a rpy to quaternion
        additionally it publishes the transform as pose stamped msg for visualisation purposes
        '''
        linear, angular_rpy, angular_q = self.read_transform(apply_rpy_to_q=True)
        self.write_q_to_tf_textbox(angular_q)
        # publish transform to rviz for visualisation purposes
        self.publish_test_pose(angular_q=angular_q)

    def publish_grasps(self):
        '''
        publish a string topic that indicates an integer containing which grasp needs to be drawn in different color
        publish grasps as pose array msg for visualisation purposes
        '''
        self.pose_highlight_pub.publish(self.selected_pose)
        self.grasp_poses_pub.publish(self.grasps_as_pose_array)

    def handle_file_print_grasps_button(self):
        '''
        print grasps to console in yaml format
        '''
        rospy.loginfo('print!')
        for grasp in self.grasps_as_pose_array.poses:
            print(f'{self.tab}-\n{self.tab}  translation: [{grasp.position.x},{grasp.position.y},{grasp.position.z}]\n' +\
                     f'{self.tab}  rotation: [{grasp.orientation.x},{grasp.orientation.y},\
                                              {grasp.orientation.z},{grasp.orientation.w}]')

    def load_grasps_from_yaml(self, object_name, grasps_yaml_path):
        rospy.loginfo(f'reloading grasps from file: {grasps_yaml_path}')
        grasps_as_pose_array = PoseArray()
        grasps_as_pose_array.header.frame_id = self.global_reference_frame
        grasps_dic = None
        with open(grasps_yaml_path) as f:
            try:
                grasps_dic = yaml.full_load(f)
            except yaml.YAMLError as e:
                self.log_error(e)
        if grasps_dic is None:
            return None
        # load grasps from param server
        object_class = remove_object_id(object_name)
        if not object_class in grasps_dic:
            self.log_error(f'Object "{object_class}" not found in dictionary, check input yaml file: {grasps_yaml_path}')
            return None
        else:
            rospy.loginfo(f'loading {object_name} grasps from yaml file: {grasps_yaml_path}')
            for transform in grasps_dic[object_class]['grasp_poses']:
                pose_msg = self.list_to_pose_msg(transform['translation'], transform['rotation'])
                grasps_as_pose_array.poses.append(pose_msg)
        # update object mesh in rviz
        self.object_mesh_pub.publish(object_name)
        return grasps_as_pose_array

    def handle_file_load_grasps_button(self):
        rospy.loginfo('reload!')
        self.object_name = self._widget.txtFileObjectName.toPlainText()
        grasps = self.load_grasps_from_yaml(self.object_name, self.grasps_yaml_path)
        if grasps is None:
            return
        self.grasps_as_pose_array = grasps
        self.publish_grasps()
        self.update_grasp_number()

    def update_selected_pose(self):
        grasp_number = None
        if self._widget.chkGraspSAllGrasps.isChecked():
            self.selected_pose = -10 # -10 means select all poses
            return True
        else:
            grasp_number = int(self._widget.txtGraspSGraspNumbers.toPlainText())
            if grasp_number >= len(self.grasps_as_pose_array.poses):
                self.log_error('Cannot select/highlight grasp, selected number is out of bounds')
                return False
            else:
                self.selected_pose = grasp_number
                return True

    def write_linear_to_tf_textbox(self, linear):
        self._widget.txtTransformLinearX.setPlainText(str(round(linear[0], 2)))
        self._widget.txtTransformLinearY.setPlainText(str(round(linear[1], 2)))
        self._widget.txtTransformLinearZ.setPlainText(str(round(linear[2], 2)))

    def convert_rpy_rad_to_deg(self, rpy_in_rad):
        rpy_in_deg = []
        for i in range(3): # 0, 1, 2
            rpy_in_deg.append(math.degrees(rpy_in_rad[i]))
        return rpy_in_deg

    def convert_rpy_deg_to_rad(self, rpy_in_deg):
        rpy_in_rad = []
        for i in range(3): # 0, 1, 2
            rpy_in_rad.append(math.radians(rpy_in_deg[i]))
        return rpy_in_rad

    def handle_grasp_s_select_button(self):
        '''
        every time a grasp is selected, its values get written into the transform group
        '''
        rospy.loginfo('grasp selected!')
        if self.update_selected_pose():
            self.publish_grasps()
            # load selected pose in transform if needed
            if self._widget.chkTransformLoadSelected.isChecked():
                if self.selected_pose != -10 and self.selected_pose != -1:
                    selected_pose = self.grasps_as_pose_array.poses[self.selected_pose]
                    self.write_linear_to_tf_textbox([selected_pose.position.x,\
                                                     selected_pose.position.y,\
                                                     selected_pose.position.z])
                    angular_q = [selected_pose.orientation.x, selected_pose.orientation.y,\
                                 selected_pose.orientation.z, selected_pose.orientation.w]
                    self.write_q_to_tf_textbox(angular_q)
                    angular_rpy = list(tf.transformations.euler_from_quaternion(angular_q))
                    if self._widget.optTransformUnitsDeg.isChecked():
                        angular_rpy = self.convert_rpy_rad_to_deg(angular_rpy)
                    self.write_rpy_to_tf_textbox([*angular_rpy])
                    self._widget.txtTransformAngularR.setPlainText(str(round(angular_rpy[0], 2)))
                    self._widget.txtTransformAngularP.setPlainText(str(round(angular_rpy[1], 2)))
                    self._widget.txtTransformAngularY.setPlainText(str(round(angular_rpy[2], 2)))
                    self.fix_displayed_text()

    def update_grasp_number(self):
        self._widget.lblGraspSGrasps.setText(str(len(self.grasps_as_pose_array.poses)))

    def handle_grasp_s_delete_button(self):
        rospy.loginfo('delete grasp!')
        if self._widget.chkGraspSAllGrasps.isChecked():
            if len(self.grasps_as_pose_array.poses) == 1:
                rospy.loginfo('deleting all grasps!')
                self.grasps_as_pose_array.poses = []
            else:
                rospy.logwarn('deleting all grasps but leaving grasp #0,\
                               if you want to remove it click delete again')
                pose0 = copy.deepcopy(self.grasps_as_pose_array.poses[0])
                self.grasps_as_pose_array.poses = []
                self.grasps_as_pose_array.poses.append(pose0)
            self.selected_pose = -1
            self.publish_grasps()
        elif self.update_selected_pose():
            del self.grasps_as_pose_array.poses[self.selected_pose]
            self.selected_pose = -1
            self.publish_grasps()
        self.update_grasp_number()

    def handle_edit_g_apply_button(self):
        rospy.loginfo('apply pattern!')
        if self.selected_pose == -10:
            static_grasps = copy.deepcopy(self.grasps_as_pose_array.poses)
        else:
            static_grasps = [copy.deepcopy(self.grasps_as_pose_array.poses[self.selected_pose])]
        # mirror: rotate quaternion by 180 degrees in each desired axis
        if self._widget.optEditGPatternMirror.isChecked():
            if self.update_selected_pose():
                roll, pitch, yaw = 0.0, 0.0, 0.0
                if self._widget.chkEditGAxisX.isChecked():
                    roll = math.pi
                if self._widget.chkEditGAxisY.isChecked():
                    pitch = math.pi
                if self._widget.chkEditGAxisZ.isChecked():
                    yaw = math.pi
                angular_rpy = [roll, pitch, yaw]
                for grasp_pose in static_grasps:
                    derived_grasp = copy.deepcopy(grasp_pose)
                    q_orig = [derived_grasp.orientation.x, derived_grasp.orientation.y,\
                                derived_grasp.orientation.z, derived_grasp.orientation.w]
                    derived_grasp.orientation = self.rotate_quaternion(q_orig, roll, pitch, yaw)
                    if self._widget.optEditGHandlingCopyR.isChecked():
                        self.grasps_as_pose_array.poses.remove(grasp_pose)
                    self.grasps_as_pose_array.poses.append(derived_grasp)
            self.publish_grasps()
        # circular pattern
        if self._widget.optEditGPatternCircular.isChecked():
            # read angular step
            ang_step = float(self._widget.txtEditGAngStep.toPlainText())
            if self._widget.optEditGAngularUnitsDeg.isChecked():
                ang_step = math.radians(ang_step)
            if ang_step > math.pi:
                rospy.logwarn('step angle is greater than 360 deg (pi radians), is this correct?')
            # read number of times that the user wants to repeat the grasp
            number_of_grasps = int(self._widget.txtEditGNumberOfGrasps.toPlainText())
            if number_of_grasps < 0:
                self.log_error('Number of grasps to make pattern cannot be negative')
                return
            # read selected pose and check if it is valid
            if self.update_selected_pose():
                for grasp_pose in static_grasps:
                    for pattern_grasp in range(number_of_grasps - 1):
                        derived_grasp = copy.deepcopy(grasp_pose)
                        q_orig = [derived_grasp.orientation.x, derived_grasp.orientation.y,\
                                derived_grasp.orientation.z, derived_grasp.orientation.w]
                        roll, pitch, yaw = 0.0, 0.0, 0.0
                        if self._widget.chkEditGAxisX.isChecked():
                            roll += ang_step
                        if self._widget.chkEditGAxisY.isChecked():
                            pitch += ang_step
                        if self._widget.chkEditGAxisZ.isChecked():
                            yaw += ang_step
                        derived_grasp.orientation = self.rotate_quaternion(q_orig, roll, pitch, yaw)
                        self.grasps_as_pose_array.poses.append(derived_grasp)
                        # prepare for next pose
                        grasp_pose = copy.deepcopy(derived_grasp)
                        # highlight and select newly generated grasp
                        self.selected_pose = len(self.grasps_as_pose_array.poses) - 1
                self.publish_grasps()
        self.update_grasp_number()

    def rotate_quaternion(self, quaternion, roll=0.0, pitch=0.0, yaw=0.0):
        '''
        input: quaternion as a list and the rotations in roll pitch yaw (in radians) in that order that you want to apply
        output: a rotated quaternion msg
        ---
        quaternion rotation is applied via multiplication, see:
        http://wiki.ros.org/tf2/Tutorials/Quaternions , section "Applying a quaternion rotation"
        '''
        if roll > math.pi or pitch > math.pi or yaw > math.pi:
            rospy.logwarn('one or more rpy values are above pi (3.1416), is this correct?')
        q_orig = np.array([*quaternion])
        angular_q = tf.transformations.quaternion_from_euler(roll, pitch, yaw)
        q_new = tf.transformations.quaternion_multiply(angular_q, q_orig)
        quaternion_msg = Quaternion()
        quaternion_msg.x = q_new[0]
        quaternion_msg.y = q_new[1]
        quaternion_msg.z = q_new[2]
        quaternion_msg.w = q_new[3]
        return quaternion_msg

    def handle_transform_apply_button(self):
        rospy.loginfo('apply transform!')
        linear, angular_rpy, angular_q = self.read_transform(apply_rpy_to_q=True)
        if self.selected_pose == -10 or self.selected_pose == -1:
            # apply transform to all
            for grasp in self.grasps_as_pose_array.poses:
                q_orig = [grasp.orientation.x, grasp.orientation.y, grasp.orientation.z, grasp.orientation.w]
                grasp.orientation = self.rotate_quaternion(q_orig, *angular_rpy)
            self.publish_grasps()
        else:
            pose_msg = self.list_to_pose_msg(linear, angular_q)
            self.grasps_as_pose_array.poses[self.selected_pose] = pose_msg
            self.publish_grasps()

    def handle_grasp_s_unselect_button(self):
        rospy.loginfo('unselect!')
        self.selected_pose = -1
        self.publish_grasps()

    def read_transform(self, apply_rpy_to_q=False):
        linear = [float(self._widget.txtTransformLinearX.toPlainText()),\
            float(self._widget.txtTransformLinearY.toPlainText()),\
            float(self._widget.txtTransformLinearZ.toPlainText())]
        angular_rpy = [float(self._widget.txtTransformAngularR.toPlainText()),\
            float(self._widget.txtTransformAngularP.toPlainText()),\
            float(self._widget.txtTransformAngularY.toPlainText())]
        angular_q = [float(self._widget.txtTransformAngularQx.toPlainText()),\
            float(self._widget.txtTransformAngularQy.toPlainText()),\
            float(self._widget.txtTransformAngularQz.toPlainText()),\
            float(self._widget.txtTransformAngularQw.toPlainText())]
        if self._widget.optTransformUnitsRad.isChecked():
            # rpy values are in radians
            if angular_rpy[0] > math.pi or angular_rpy[1] > math.pi or angular_rpy[2] > math.pi:
                rospy.logwarn('radians are selected but value is greater than pi, is this correct?')
        if self._widget.optTransformUnitsDeg.isChecked():
            # rpy values are in degrees, convert to radians
            angular_rpy = self.convert_rpy_deg_to_rad(angular_rpy)
        if apply_rpy_to_q:
            angular_q = tf.transformations.quaternion_from_euler(angular_rpy[0], angular_rpy[1], angular_rpy[2])
            self.write_q_to_tf_textbox(angular_q)
            return linear, angular_rpy, angular_q
        else:
            return linear, angular_rpy, angular_q

    def handle_transform_create_grasp_button(self):
        rospy.loginfo('create grasp!')
        linear, angular_rpy, angular_q = self.read_transform(apply_rpy_to_q=True)
        pose_msg = self.list_to_pose_msg(linear, angular_q)
        self.grasps_as_pose_array.poses.append(pose_msg)
        self.selected_pose = len(self.grasps_as_pose_array.poses) - 1
        self.publish_grasps()
        self.update_grasp_number()

    def handle_select_obj_path_button(self):
        '''
        pop out a file dialog to select a new grasps yaml file path
        '''
        rospy.loginfo('select object path button was pressed')
        open_file_dialog = OpenFileDialog()
        grasps_yaml_path_cadidate = open_file_dialog.openFileNameDialog()
        if grasps_yaml_path_cadidate is None:
            rospy.loginfo('select object yaml path: operation cancelled by user')
            return
        self.grasps_yaml_path = grasps_yaml_path_cadidate
        rospy.loginfo(f'grasps yaml file selected: {self.grasps_yaml_path}')
        rospy.loginfo("Don't forget to click on load grasps next!")
