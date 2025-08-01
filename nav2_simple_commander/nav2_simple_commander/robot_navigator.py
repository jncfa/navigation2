#! /usr/bin/env python3
# Copyright 2021 Samsung Research America
# Copyright 2025 Open Navigation LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from enum import Enum
import time
from typing import Any, Optional, Union

from action_msgs.msg import GoalStatus
from builtin_interfaces.msg import Duration
from geographic_msgs.msg import GeoPose
from geometry_msgs.msg import Point, PoseStamped, PoseWithCovarianceStamped
from lifecycle_msgs.srv import GetState
from nav2_msgs.action import (AssistedTeleop, BackUp, ComputeAndTrackRoute,
                              ComputePathThroughPoses, ComputePathToPose, ComputeRoute, DockRobot,
                              DriveOnHeading, FollowGPSWaypoints, FollowPath, FollowWaypoints,
                              NavigateThroughPoses, NavigateToPose, SmoothPath, Spin, UndockRobot)
from nav2_msgs.msg import Route
from nav2_msgs.srv import (ClearCostmapAroundPose, ClearCostmapAroundRobot,
                           ClearCostmapExceptRegion, ClearEntireCostmap, GetCostmap, LoadMap,
                           ManageLifecycleNodes)
from nav_msgs.msg import Goals, OccupancyGrid, Path
import rclpy
from rclpy.action import ActionClient
from rclpy.action.client import ClientGoalHandle
from rclpy.client import Client
from rclpy.duration import Duration as rclpyDuration
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy
from rclpy.task import Future
from rclpy.type_support import GetResultServiceResponse


# Task Result enum for the result of the task being executed
class TaskResult(Enum):
    UNKNOWN = 0
    SUCCEEDED = 1
    CANCELED = 2
    FAILED = 3


# Task enum for the task being executed, if its a long-running task to be able to obtain
# necessary contextual information in `isTaskComplete` and `getFeedback` regarding the task
# which is running.
class RunningTask(Enum):
    NONE = 0
    NAVIGATE_TO_POSE = 1
    NAVIGATE_THROUGH_POSES = 2
    FOLLOW_PATH = 3
    FOLLOW_WAYPOINTS = 4
    FOLLOW_GPS_WAYPOINTS = 5
    SPIN = 6
    BACKUP = 7
    DRIVE_ON_HEADING = 8
    ASSISTED_TELEOP = 9
    DOCK_ROBOT = 10
    UNDOCK_ROBOT = 11
    COMPUTE_AND_TRACK_ROUTE = 12


class BasicNavigator(Node):

    def __init__(self, node_name: str = 'basic_navigator', namespace: str = '') -> None:
        super().__init__(node_name=node_name, namespace=namespace)
        self.initial_pose = PoseStamped()
        self.initial_pose.header.frame_id = 'map'

        self.goal_handle: Optional[ClientGoalHandle[Any, Any, Any]] = None
        self.result_future: \
            Optional[Future[GetResultServiceResponse[Any]]] = None
        self.feedback: Any = None
        self.status: Optional[int] = None

        # Since the route server's compute and track action server is likely
        # to be running simultaneously with another (e.g. controller, WPF) server,
        # we must track its futures and feedback separately. Additionally, the
        # route tracking feedback is uniquely important to be complete and ordered
        self.route_goal_handle: Optional[ClientGoalHandle[Any, Any, Any]] = None
        self.route_result_future: \
            Optional[Future[GetResultServiceResponse[Any]]] = None
        self.route_feedback: list[Any] = []

        # Error code and messages from servers
        self.last_action_error_code = 0
        self.last_action_error_msg = ''

        amcl_pose_qos = QoSProfile(
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            reliability=QoSReliabilityPolicy.RELIABLE,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
        )

        self.initial_pose_received = False
        self.nav_through_poses_client: ActionClient[
            NavigateThroughPoses.Goal,
            NavigateThroughPoses.Result,
            NavigateThroughPoses.Feedback
        ] = ActionClient(
            self, NavigateThroughPoses, 'navigate_through_poses')
        self.nav_to_pose_client: ActionClient[
            NavigateToPose.Goal,
            NavigateToPose.Result,
            NavigateToPose.Feedback
        ] = ActionClient(self, NavigateToPose, 'navigate_to_pose')
        self.follow_waypoints_client: ActionClient[
            FollowWaypoints.Goal,
            FollowWaypoints.Result,
            FollowWaypoints.Feedback
        ] = ActionClient(
            self, FollowWaypoints, 'follow_waypoints'
        )
        self.follow_gps_waypoints_client: ActionClient[
            FollowGPSWaypoints.Goal,
            FollowGPSWaypoints.Result,
            FollowGPSWaypoints.Feedback
        ] = ActionClient(
            self, FollowGPSWaypoints, 'follow_gps_waypoints'
        )
        self.follow_path_client: ActionClient[
            FollowPath.Goal,
            FollowPath.Result,
            FollowPath.Feedback
        ] = ActionClient(self, FollowPath, 'follow_path')
        self.compute_path_to_pose_client: ActionClient[
            ComputePathToPose.Goal,
            ComputePathToPose.Result,
            ComputePathToPose.Feedback
        ] = ActionClient(
            self, ComputePathToPose, 'compute_path_to_pose'
        )
        self.compute_path_through_poses_client: ActionClient[
            ComputePathThroughPoses.Goal,
            ComputePathThroughPoses.Result,
            ComputePathThroughPoses.Feedback
        ] = ActionClient(
            self, ComputePathThroughPoses, 'compute_path_through_poses'
        )
        self.smoother_client: ActionClient[
            SmoothPath.Goal,
            SmoothPath.Result,
            SmoothPath.Feedback
        ] = ActionClient(self, SmoothPath, 'smooth_path')
        self.compute_route_client: ActionClient[
            ComputeRoute.Goal,
            ComputeRoute.Result,
            ComputeRoute.Feedback
        ] = ActionClient(self, ComputeRoute, 'compute_route')
        self.compute_and_track_route_client: ActionClient[
            ComputeAndTrackRoute.Goal,
            ComputeAndTrackRoute.Result,
            ComputeAndTrackRoute.Feedback
        ] = ActionClient(self, ComputeAndTrackRoute, 'compute_and_track_route')
        self.spin_client: ActionClient[
            Spin.Goal,
            Spin.Result,
            Spin.Feedback
        ] = ActionClient(self, Spin, 'spin')

        self.backup_client: ActionClient[
            BackUp.Goal,
            BackUp.Result,
            BackUp.Feedback
        ] = ActionClient(self, BackUp, 'backup')
        self.drive_on_heading_client: ActionClient[
            DriveOnHeading.Goal,
            DriveOnHeading.Result,
            DriveOnHeading.Feedback
        ] = ActionClient(
            self, DriveOnHeading, 'drive_on_heading'
        )
        self.assisted_teleop_client: ActionClient[
            AssistedTeleop.Goal,
            AssistedTeleop.Result,
            AssistedTeleop.Feedback
        ] = ActionClient(
            self, AssistedTeleop, 'assisted_teleop'
        )
        self.docking_client: ActionClient[
            DockRobot.Goal,
            DockRobot.Result,
            DockRobot.Feedback
        ] = ActionClient(self, DockRobot, 'dock_robot')
        self.undocking_client: ActionClient[
            UndockRobot.Goal,
            UndockRobot.Result,
            UndockRobot.Feedback
        ] = ActionClient(self, UndockRobot, 'undock_robot')

        self.localization_pose_sub = self.create_subscription(
            PoseWithCovarianceStamped,
            'amcl_pose',
            self._amclPoseCallback,
            amcl_pose_qos,
        )
        self.initial_pose_pub = self.create_publisher(
            PoseWithCovarianceStamped, 'initialpose', 10
        )
        self.change_maps_srv: Client[LoadMap.Request, LoadMap.Response] = \
            self.create_client(LoadMap, 'map_server/load_map')
        self.clear_costmap_global_srv: Client[
            ClearEntireCostmap.Request, ClearEntireCostmap.Response] = \
            self.create_client(
            ClearEntireCostmap, 'global_costmap/clear_entirely_global_costmap'
        )
        self.clear_costmap_local_srv: Client[
            ClearEntireCostmap.Request, ClearEntireCostmap.Response] = \
            self.create_client(
            ClearEntireCostmap, 'local_costmap/clear_entirely_local_costmap'
        )
        self.clear_costmap_except_region_srv: Client[
            ClearCostmapExceptRegion.Request, ClearCostmapExceptRegion.Response] = \
            self.create_client(
            ClearCostmapExceptRegion, 'local_costmap/clear_costmap_except_region'
        )
        self.clear_costmap_around_robot_srv: Client[
            ClearCostmapAroundRobot.Request, ClearCostmapAroundRobot.Response] = \
            self.create_client(
            ClearCostmapAroundRobot, 'local_costmap/clear_costmap_around_robot'
        )
        self.clear_local_costmap_around_pose_srv: Client[
            ClearCostmapAroundPose.Request, ClearCostmapAroundPose.Response] = \
            self.create_client(
            ClearCostmapAroundPose, 'local_costmap/clear_costmap_around_pose'
        )
        self.clear_global_costmap_around_pose_srv: Client[
            ClearCostmapAroundPose.Request, ClearCostmapAroundPose.Response] = \
            self.create_client(
            ClearCostmapAroundPose, 'global_costmap/clear_costmap_around_pose'
        )
        self.get_costmap_global_srv: Client[
            GetCostmap.Request, GetCostmap.Response] = \
            self.create_client(
            GetCostmap, 'global_costmap/get_costmap'
        )
        self.get_costmap_local_srv: Client[
            GetCostmap.Request, GetCostmap.Response] = \
            self.create_client(
            GetCostmap, 'local_costmap/get_costmap'
        )

    def destroyNode(self) -> None:
        self.destroy_node()

    def destroy_node(self) -> None:
        self.nav_through_poses_client.destroy()
        self.nav_to_pose_client.destroy()
        self.follow_waypoints_client.destroy()
        self.follow_path_client.destroy()
        self.compute_path_to_pose_client.destroy()
        self.compute_path_through_poses_client.destroy()
        self.compute_and_track_route_client.destroy()
        self.compute_route_client.destroy()
        self.smoother_client.destroy()
        self.spin_client.destroy()
        self.backup_client.destroy()
        self.drive_on_heading_client.destroy()
        self.assisted_teleop_client.destroy()
        self.follow_gps_waypoints_client.destroy()
        self.docking_client.destroy()
        self.undocking_client.destroy()
        super().destroy_node()

    def setInitialPose(self, initial_pose: PoseStamped) -> None:
        """Set the initial pose to the localization system."""
        self.initial_pose_received = False
        self.initial_pose = initial_pose
        self._setInitialPose()

    def goThroughPoses(self, poses: Goals, behavior_tree: str = '') -> Optional[RunningTask]:
        """Send a `NavThroughPoses` action request."""
        self.clearTaskError()
        self.debug("Waiting for 'NavigateThroughPoses' action server")
        while not self.nav_through_poses_client.wait_for_server(timeout_sec=1.0):
            self.info("'NavigateThroughPoses' action server not available, waiting...")

        goal_msg = NavigateThroughPoses.Goal()
        goal_msg.poses = poses
        goal_msg.behavior_tree = behavior_tree

        self.info(f'Navigating with {len(goal_msg.poses)} goals....')
        send_goal_future = self.nav_through_poses_client.send_goal_async(
            goal_msg, self._feedbackCallback
        )
        rclpy.spin_until_future_complete(self, send_goal_future)
        self.goal_handle = send_goal_future.result()

        if not self.goal_handle or not self.goal_handle.accepted:
            msg = f'NavigateThroughPoses request with {len(poses)} was rejected!'
            self.setTaskError(NavigateThroughPoses.UNKNOWN, msg)
            self.error(msg)
            return None

        self.result_future = self.goal_handle.get_result_async()
        return RunningTask.NAVIGATE_THROUGH_POSES

    def goToPose(self, pose: PoseStamped, behavior_tree: str = '') -> Optional[RunningTask]:
        """Send a `NavToPose` action request."""
        self.clearTaskError()
        self.debug("Waiting for 'NavigateToPose' action server")
        while not self.nav_to_pose_client.wait_for_server(timeout_sec=1.0):
            self.info("'NavigateToPose' action server not available, waiting...")

        goal_msg = NavigateToPose.Goal()
        goal_msg.pose = pose
        goal_msg.behavior_tree = behavior_tree

        self.info(
            'Navigating to goal: '
            + str(pose.pose.position.x)
            + ' '
            + str(pose.pose.position.y)
            + '...'
        )
        send_goal_future = self.nav_to_pose_client.send_goal_async(
            goal_msg, self._feedbackCallback
        )
        rclpy.spin_until_future_complete(self, send_goal_future)
        self.goal_handle = send_goal_future.result()

        if not self.goal_handle or not self.goal_handle.accepted:
            msg = (
                'NavigateToPose goal to '
                + str(pose.pose.position.x)
                + ' '
                + str(pose.pose.position.y)
                + ' was rejected!'
            )
            self.setTaskError(NavigateToPose.UNKNOWN, msg)
            self.error(msg)
            return None

        self.result_future = self.goal_handle.get_result_async()
        return RunningTask.NAVIGATE_TO_POSE

    def followWaypoints(self, poses: list[PoseStamped]) -> Optional[RunningTask]:
        """Send a `FollowWaypoints` action request."""
        self.clearTaskError()
        self.debug("Waiting for 'FollowWaypoints' action server")
        while not self.follow_waypoints_client.wait_for_server(timeout_sec=1.0):
            self.info("'FollowWaypoints' action server not available, waiting...")

        goal_msg = FollowWaypoints.Goal()
        goal_msg.poses = poses

        self.info(f'Following {len(goal_msg.poses)} goals....')
        send_goal_future = self.follow_waypoints_client.send_goal_async(
            goal_msg, self._feedbackCallback
        )
        rclpy.spin_until_future_complete(self, send_goal_future)
        self.goal_handle = send_goal_future.result()

        if not self.goal_handle or not self.goal_handle.accepted:
            msg = f'Following {len(poses)} waypoints request was rejected!'
            self.setTaskError(FollowWaypoints.UNKNOWN, msg)
            self.error(msg)
            return None

        self.result_future = self.goal_handle.get_result_async()
        return RunningTask.FOLLOW_WAYPOINTS

    def followGpsWaypoints(self, gps_poses: list[GeoPose]) -> Optional[RunningTask]:
        """Send a `FollowGPSWaypoints` action request."""
        self.clearTaskError()
        self.debug("Waiting for 'FollowWaypoints' action server")
        while not self.follow_gps_waypoints_client.wait_for_server(timeout_sec=1.0):
            self.info("'FollowWaypoints' action server not available, waiting...")

        goal_msg = FollowGPSWaypoints.Goal()
        goal_msg.gps_poses = gps_poses

        self.info(f'Following {len(goal_msg.gps_poses)} gps goals....')
        send_goal_future = self.follow_gps_waypoints_client.send_goal_async(
            goal_msg, self._feedbackCallback
        )
        rclpy.spin_until_future_complete(self, send_goal_future)
        self.goal_handle = send_goal_future.result()

        if not self.goal_handle or not self.goal_handle.accepted:
            msg = f'Following {len(gps_poses)} gps waypoints request was rejected!'
            self.setTaskError(FollowGPSWaypoints.UNKNOWN, msg)
            self.error(msg)
            return None

        self.result_future = self.goal_handle.get_result_async()
        return RunningTask.FOLLOW_GPS_WAYPOINTS

    def spin(
            self, spin_dist: float = 1.57, time_allowance: int = 10,
            disable_collision_checks: bool = False) -> Optional[RunningTask]:
        self.clearTaskError()
        self.debug("Waiting for 'Spin' action server")
        while not self.spin_client.wait_for_server(timeout_sec=1.0):
            self.info("'Spin' action server not available, waiting...")
        goal_msg = Spin.Goal()
        goal_msg.target_yaw = spin_dist
        goal_msg.time_allowance = Duration(sec=time_allowance)
        goal_msg.disable_collision_checks = disable_collision_checks

        self.info(f'Spinning to angle {goal_msg.target_yaw}....')
        send_goal_future = self.spin_client.send_goal_async(
            goal_msg, self._feedbackCallback
        )
        rclpy.spin_until_future_complete(self, send_goal_future)
        self.goal_handle = send_goal_future.result()

        if not self.goal_handle or not self.goal_handle.accepted:
            msg = 'Spin request was rejected!'
            self.setTaskError(Spin.UNKNOWN, msg)
            self.error(msg)
            return None

        self.result_future = self.goal_handle.get_result_async()
        return RunningTask.SPIN

    def backup(
            self, backup_dist: float = 0.15, backup_speed: float = 0.025,
            time_allowance: int = 10,
            disable_collision_checks: bool = False) -> Optional[RunningTask]:
        self.clearTaskError()
        self.debug("Waiting for 'Backup' action server")
        while not self.backup_client.wait_for_server(timeout_sec=1.0):
            self.info("'Backup' action server not available, waiting...")
        goal_msg = BackUp.Goal()
        goal_msg.target = Point(x=float(backup_dist))
        goal_msg.speed = backup_speed
        goal_msg.time_allowance = Duration(sec=time_allowance)
        goal_msg.disable_collision_checks = disable_collision_checks

        self.info(f'Backing up {goal_msg.target.x} m at {goal_msg.speed} m/s....')
        send_goal_future = self.backup_client.send_goal_async(
            goal_msg, self._feedbackCallback
        )
        rclpy.spin_until_future_complete(self, send_goal_future)
        self.goal_handle = send_goal_future.result()

        if not self.goal_handle or not self.goal_handle.accepted:
            msg = 'Backup request was rejected!'
            self.setTaskError(BackUp.UNKNOWN, msg)
            self.error(msg)
            return None

        self.result_future = self.goal_handle.get_result_async()
        return RunningTask.BACKUP

    def driveOnHeading(
            self, dist: float = 0.15, speed: float = 0.025,
            time_allowance: int = 10,
            disable_collision_checks: bool = False) -> Optional[RunningTask]:
        self.clearTaskError()
        self.debug("Waiting for 'DriveOnHeading' action server")
        while not self.backup_client.wait_for_server(timeout_sec=1.0):
            self.info("'DriveOnHeading' action server not available, waiting...")
        goal_msg = DriveOnHeading.Goal()
        goal_msg.target = Point(x=float(dist))
        goal_msg.speed = speed
        goal_msg.time_allowance = Duration(sec=time_allowance)
        goal_msg.disable_collision_checks = disable_collision_checks

        self.info(f'Drive {goal_msg.target.x} m on heading at {goal_msg.speed} m/s....')
        send_goal_future = self.drive_on_heading_client.send_goal_async(
            goal_msg, self._feedbackCallback
        )
        rclpy.spin_until_future_complete(self, send_goal_future)
        self.goal_handle = send_goal_future.result()

        if not self.goal_handle or not self.goal_handle.accepted:
            msg = 'Drive On Heading request was rejected!'
            self.setTaskError(DriveOnHeading.UNKNOWN, msg)
            self.error(msg)
            return None

        self.result_future = self.goal_handle.get_result_async()
        return RunningTask.DRIVE_ON_HEADING

    def assistedTeleop(self, time_allowance: int = 30) -> Optional[RunningTask]:

        self.clearTaskError()
        self.debug("Wanting for 'assisted_teleop' action server")

        while not self.assisted_teleop_client.wait_for_server(timeout_sec=1.0):
            self.info("'assisted_teleop' action server not available, waiting...")
        goal_msg = AssistedTeleop.Goal()
        goal_msg.time_allowance = Duration(sec=time_allowance)

        self.info("Running 'assisted_teleop'....")
        send_goal_future = self.assisted_teleop_client.send_goal_async(
            goal_msg, self._feedbackCallback
        )
        rclpy.spin_until_future_complete(self, send_goal_future)
        self.goal_handle = send_goal_future.result()

        if not self.goal_handle or not self.goal_handle.accepted:
            msg = 'Assisted Teleop request was rejected!'
            self.setTaskError(AssistedTeleop.UNKNOWN, msg)
            self.error(msg)
            return None

        self.result_future = self.goal_handle.get_result_async()
        return RunningTask.ASSISTED_TELEOP

    def followPath(self, path: Path, controller_id: str = '',
                   goal_checker_id: str = '') -> Optional[RunningTask]:
        self.clearTaskError()
        """Send a `FollowPath` action request."""
        self.debug("Waiting for 'FollowPath' action server")
        while not self.follow_path_client.wait_for_server(timeout_sec=1.0):
            self.info("'FollowPath' action server not available, waiting...")

        goal_msg = FollowPath.Goal()
        goal_msg.path = path
        goal_msg.controller_id = controller_id
        goal_msg.goal_checker_id = goal_checker_id

        self.info('Executing path...')
        send_goal_future = self.follow_path_client.send_goal_async(
            goal_msg, self._feedbackCallback
        )
        rclpy.spin_until_future_complete(self, send_goal_future)
        self.goal_handle = send_goal_future.result()

        if not self.goal_handle or not self.goal_handle.accepted:
            msg = 'FollowPath goal was rejected!'
            self.setTaskError(FollowPath.UNKNOWN, msg)
            self.error(msg)
            return None

        self.result_future = self.goal_handle.get_result_async()
        return RunningTask.FOLLOW_PATH

    def dockRobotByPose(self, dock_pose: PoseStamped,
                        dock_type: str = '', nav_to_dock: bool = True) -> Optional[RunningTask]:
        self.clearTaskError()
        """Send a `DockRobot` action request."""
        self.info("Waiting for 'DockRobot' action server")
        while not self.docking_client.wait_for_server(timeout_sec=1.0):
            self.info('"DockRobot" action server not available, waiting...')

        goal_msg = DockRobot.Goal()
        goal_msg.use_dock_id = False
        goal_msg.dock_pose = dock_pose
        goal_msg.dock_type = dock_type
        goal_msg.navigate_to_staging_pose = nav_to_dock  # if want to navigate before staging

        self.info('Docking at pose: ' + str(dock_pose) + '...')
        send_goal_future = self.docking_client.send_goal_async(goal_msg,
                                                               self._feedbackCallback)
        rclpy.spin_until_future_complete(self, send_goal_future)
        self.goal_handle = send_goal_future.result()

        if not self.goal_handle or not self.goal_handle.accepted:
            msg = 'DockRobot request was rejected!'
            self.setTaskError(DockRobot.UNKNOWN, msg)
            self.error(msg)
            return None

        self.result_future = self.goal_handle.get_result_async()
        return RunningTask.DOCK_ROBOT

    def dockRobotByID(self, dock_id: str, nav_to_dock: bool = True) -> Optional[RunningTask]:
        """Send a `DockRobot` action request."""
        self.clearTaskError()
        self.info("Waiting for 'DockRobot' action server")
        while not self.docking_client.wait_for_server(timeout_sec=1.0):
            self.info('"DockRobot" action server not available, waiting...')

        goal_msg = DockRobot.Goal()
        goal_msg.use_dock_id = True
        goal_msg.dock_id = dock_id
        goal_msg.navigate_to_staging_pose = nav_to_dock  # if want to navigate before staging

        self.info('Docking at dock ID: ' + str(dock_id) + '...')
        send_goal_future = self.docking_client.send_goal_async(goal_msg,
                                                               self._feedbackCallback)
        rclpy.spin_until_future_complete(self, send_goal_future)
        self.goal_handle = send_goal_future.result()

        if not self.goal_handle or not self.goal_handle.accepted:
            msg = 'DockRobot request was rejected!'
            self.setTaskError(DockRobot.UNKNOWN, msg)
            self.error(msg)
            return None

        self.result_future = self.goal_handle.get_result_async()
        return RunningTask.DOCK_ROBOT

    def undockRobot(self, dock_type: str = '') -> Optional[RunningTask]:
        """Send a `UndockRobot` action request."""
        self.clearTaskError()
        self.info("Waiting for 'UndockRobot' action server")
        while not self.undocking_client.wait_for_server(timeout_sec=1.0):
            self.info('"UndockRobot" action server not available, waiting...')

        goal_msg = UndockRobot.Goal()
        goal_msg.dock_type = dock_type

        self.info('Undocking from dock of type: ' + str(dock_type) + '...')
        send_goal_future = self.undocking_client.send_goal_async(goal_msg,
                                                                 self._feedbackCallback)
        rclpy.spin_until_future_complete(self, send_goal_future)
        self.goal_handle = send_goal_future.result()

        if not self.goal_handle or not self.goal_handle.accepted:
            msg = 'UndockRobot request was rejected!'
            self.setTaskError(UndockRobot.UNKNOWN, msg)
            self.error(msg)
            return None

        self.result_future = self.goal_handle.get_result_async()
        return RunningTask.UNDOCK_ROBOT

    def cancelTask(self) -> None:
        """Cancel pending task request of any type."""
        self.info('Canceling current task.')
        if self.result_future:
            if self.goal_handle is not None:
                future = self.goal_handle.cancel_goal_async()
                rclpy.spin_until_future_complete(self, future)
            else:
                self.error('Cancel task failed, goal handle is None')
                self.setTaskError(0, 'Cancel task failed, goal handle is None')
                return
        if self.route_result_future:
            if self.route_goal_handle is not None:
                future = self.route_goal_handle.cancel_goal_async()
                rclpy.spin_until_future_complete(self, future)
            else:
                self.error('Cancel route task failed, goal handle is None')
                self.setTaskError(0, 'Cancel route task failed, goal handle is None')
                return
        self.clearTaskError()
        return

    def isTaskComplete(self, task: RunningTask = RunningTask.NONE) -> bool:
        """Check if the task request of any type is complete yet."""
        # Find the result future to spin
        if task is None:
            self.error('Task is None, cannot check for completion')
            return False

        result_future = None
        if task != RunningTask.COMPUTE_AND_TRACK_ROUTE:
            result_future = self.result_future
        else:
            result_future = self.route_result_future
        if not result_future:
            # task was cancelled or completed
            return True

        # Get the result of the future, if complete
        rclpy.spin_until_future_complete(self, result_future, timeout_sec=0.10)
        result_response = result_future.result()

        if result_response:
            self.status = result_response.status
            if self.status != GoalStatus.STATUS_SUCCEEDED:
                result = result_response.result
                if result is not None:
                    self.setTaskError(result.error_code, result.error_msg)
                    self.debug(
                        'Task with failed with'
                        f' status code:{self.status}'
                        f' error code:{result.error_code}'
                        f' error msg:{result.error_msg}')
                    return True
                else:
                    self.setTaskError(0, 'No result received')
                    self.debug('Task failed with no result received')
                    return True
        else:
            # Timed out, still processing, not complete yet
            return False

        self.debug('Task succeeded!')
        return True

    def getFeedback(self, task: RunningTask = RunningTask.NONE) -> Any:
        """Get the pending action feedback message."""
        if task != RunningTask.COMPUTE_AND_TRACK_ROUTE:
            return self.feedback
        if len(self.route_feedback) > 0:
            return self.route_feedback.pop(0)
        return None

    def getResult(self) -> TaskResult:
        """Get the pending action result message."""
        if self.status == GoalStatus.STATUS_SUCCEEDED:
            return TaskResult.SUCCEEDED
        elif self.status == GoalStatus.STATUS_ABORTED:
            return TaskResult.FAILED
        elif self.status == GoalStatus.STATUS_CANCELED:
            return TaskResult.CANCELED
        else:
            return TaskResult.UNKNOWN

    def clearTaskError(self) -> None:
        self.last_action_error_code = 0
        self.last_action_error_msg = ''

    def setTaskError(self, error_code: int, error_msg: str) -> None:
        self.last_action_error_code = error_code
        self.last_action_error_msg = error_msg

    def getTaskError(self) -> tuple[int, str]:
        return (self.last_action_error_code, self.last_action_error_msg)

    def waitUntilNav2Active(self, navigator: str = 'bt_navigator',
                            localizer: str = 'amcl') -> None:
        """Block until the full navigation system is up and running."""
        if localizer != 'robot_localization':  # non-lifecycle node
            self._waitForNodeToActivate(localizer)
        if localizer == 'amcl':
            self._waitForInitialPose()
        self._waitForNodeToActivate(navigator)
        self.info('Nav2 is ready for use!')
        return

    def _getPathImpl(
        self, start: PoseStamped, goal: PoseStamped,
        planner_id: str = '', use_start: bool = False
    ) -> ComputePathToPose.Result:
        """
        Send a `ComputePathToPose` action request.

        Internal implementation to get the full result, not just the path.
        """
        self.debug("Waiting for 'ComputePathToPose' action server")
        while not self.compute_path_to_pose_client.wait_for_server(timeout_sec=1.0):
            self.info("'ComputePathToPose' action server not available, waiting...")

        goal_msg = ComputePathToPose.Goal()
        goal_msg.start = start
        goal_msg.goal = goal
        goal_msg.planner_id = planner_id
        goal_msg.use_start = use_start

        self.info('Getting path...')
        send_goal_future = self.compute_path_to_pose_client.send_goal_async(goal_msg)
        rclpy.spin_until_future_complete(self, send_goal_future)
        self.goal_handle = send_goal_future.result()

        if not self.goal_handle or not self.goal_handle.accepted:
            self.error('Get path was rejected!')
            self.status = GoalStatus.UNKNOWN
            result = ComputePathToPose.Result()
            result.error_code = ComputePathToPose.UNKNOWN
            result.error_msg = 'Get path was rejected'
            return result

        self.result_future = self.goal_handle.get_result_async()
        rclpy.spin_until_future_complete(self, self.result_future)
        self.status = self.result_future.result().status  # type: ignore[union-attr]

        return self.result_future.result().result  # type: ignore[union-attr]

    def getPath(
        self, start: PoseStamped, goal: PoseStamped,
            planner_id: str = '', use_start: bool = False) -> Path:
        """Send a `ComputePathToPose` action request."""
        self.clearTaskError()
        rtn = self._getPathImpl(start, goal, planner_id, use_start)

        if self.status == GoalStatus.STATUS_SUCCEEDED:
            return rtn.path
        else:
            self.setTaskError(rtn.error_code, rtn.error_msg)
            self.warn('Getting path failed with'
                      f' status code:{self.status}'
                      f' error code:{rtn.error_code}'
                      f' error msg:{rtn.error_msg}')
            return None

    def _getPathThroughPosesImpl(
        self, start: PoseStamped, goals: Goals,
            planner_id: str = '', use_start: bool = False
    ) -> ComputePathThroughPoses.Result:
        """
        Send a `ComputePathThroughPoses` action request.

        Internal implementation to get the full result, not just the path.
        """
        self.debug("Waiting for 'ComputePathThroughPoses' action server")
        while not self.compute_path_through_poses_client.wait_for_server(
            timeout_sec=1.0
        ):
            self.info(
                "'ComputePathThroughPoses' action server not available, waiting..."
            )

        goal_msg = ComputePathThroughPoses.Goal()
        goal_msg.start = start
        goal_msg.goals.header.frame_id = 'map'
        goal_msg.goals.header.stamp = self.get_clock().now().to_msg()
        goal_msg.goals.goals = goals
        goal_msg.planner_id = planner_id
        goal_msg.use_start = use_start

        self.info('Getting path...')
        send_goal_future = self.compute_path_through_poses_client.send_goal_async(
            goal_msg
        )
        rclpy.spin_until_future_complete(self, send_goal_future)
        self.goal_handle = send_goal_future.result()

        if not self.goal_handle or not self.goal_handle.accepted:
            self.error('Get path was rejected!')
            result = ComputePathThroughPoses.Result()
            result.error_code = ComputePathThroughPoses.UNKNOWN
            result.error_msg = 'Get path was rejected!'
            return result

        self.result_future = self.goal_handle.get_result_async()
        rclpy.spin_until_future_complete(self, self.result_future)
        self.status = self.result_future.result().status  # type: ignore[union-attr]

        return self.result_future.result().result  # type: ignore[union-attr]

    def getPathThroughPoses(
        self, start: PoseStamped, goals: Goals,
            planner_id: str = '', use_start: bool = False) -> Path:
        """Send a `ComputePathThroughPoses` action request."""
        self.clearTaskError()
        rtn = self._getPathThroughPosesImpl(start, goals, planner_id, use_start)

        if self.status == GoalStatus.STATUS_SUCCEEDED:
            return rtn.path
        else:
            self.setTaskError(rtn.error_code, rtn.error_msg)
            self.warn('Getting path failed with'
                      f' status code:{self.status}'
                      f' error code:{rtn.error_code}'
                      f' error msg:{rtn.error_msg}')
            return None

    def _getRouteImpl(
        self, start: Union[int, PoseStamped],
        goal: Union[int, PoseStamped], use_start: bool = False
    ) -> ComputeRoute.Result:
        """
        Send a `ComputeRoute` action request.

        Internal implementation to get the full result, not just the sparse route and dense path.
        """
        self.debug("Waiting for 'ComputeRoute' action server")
        while not self.compute_route_client.wait_for_server(timeout_sec=1.0):
            self.info("'ComputeRoute' action server not available, waiting...")

        goal_msg = ComputeRoute.Goal()
        goal_msg.use_start = use_start

        # Support both ID based requests and PoseStamped based requests
        if isinstance(start, int) and isinstance(goal, int):
            goal_msg.start_id = start
            goal_msg.goal_id = goal
            goal_msg.use_poses = False
        elif isinstance(start, PoseStamped) and isinstance(goal, PoseStamped):
            goal_msg.start = start
            goal_msg.goal = goal
            goal_msg.use_poses = True
        else:
            self.error('Invalid start and goal types. Must be PoseStamped for pose or int for ID')
            result = ComputeRoute.Result()
            result.error_code = ComputeRoute.UNKNOWN
            result.error_msg = 'Request type fields were invalid!'
            return result

        self.info('Getting route...')
        send_goal_future = self.compute_route_client.send_goal_async(goal_msg)
        rclpy.spin_until_future_complete(self, send_goal_future)
        self.goal_handle = send_goal_future.result()

        if not self.goal_handle or not self.goal_handle.accepted:
            self.error('Get route was rejected!')
            result = ComputeRoute.Result()
            result.error_code = ComputeRoute.UNKNOWN
            result.error_msg = 'Get route was rejected!'
            return result

        self.result_future = self.goal_handle.get_result_async()
        rclpy.spin_until_future_complete(self, self.result_future)
        self.status = self.result_future.result().status  # type: ignore[union-attr]

        return self.result_future.result().result  # type: ignore[union-attr]

    def getRoute(
            self, start: Union[int, PoseStamped],
            goal: Union[int, PoseStamped],
            use_start: bool = False) -> Optional[list[Union[Path, Route]]]:
        """Send a `ComputeRoute` action request."""
        self.clearTaskError()
        rtn = self._getRouteImpl(start, goal, use_start=False)

        if self.status != GoalStatus.STATUS_SUCCEEDED:
            self.setTaskError(rtn.error_code, rtn.error_msg)
            self.warn(
                'Getting route failed with'
                f' status code:{self.status}'
                f' error code:{rtn.error_code}'
                f' error msg:{rtn.error_msg}')
            return None

        return [rtn.path, rtn.route]

    def getAndTrackRoute(
        self, start: Union[int, PoseStamped],
        goal: Union[int, PoseStamped], use_start: bool = False
    ) -> Optional[RunningTask]:
        """Send a `ComputeAndTrackRoute` action request."""
        self.clearTaskError()
        self.debug("Waiting for 'ComputeAndTrackRoute' action server")
        while not self.compute_and_track_route_client.wait_for_server(timeout_sec=1.0):
            self.info("'ComputeAndTrackRoute' action server not available, waiting...")

        goal_msg = ComputeAndTrackRoute.Goal()
        goal_msg.use_start = use_start

        # Support both ID based requests and PoseStamped based requests
        if isinstance(start, int) and isinstance(goal, int):
            goal_msg.start_id = start
            goal_msg.goal_id = goal
            goal_msg.use_poses = False
        elif isinstance(start, PoseStamped) and isinstance(goal, PoseStamped):
            goal_msg.start = start
            goal_msg.goal = goal
            goal_msg.use_poses = True
        else:
            self.setTaskError(ComputeAndTrackRoute.UNKNOWN, 'Request type fields were invalid!')
            self.error('Invalid start and goal types. Must be PoseStamped for pose or int for ID')
            return None

        self.info('Computing and tracking route...')
        send_goal_future = self.compute_and_track_route_client.send_goal_async(goal_msg,
            self._routeFeedbackCallback)  # noqa: E128
        rclpy.spin_until_future_complete(self, send_goal_future)
        self.route_goal_handle = send_goal_future.result()

        if not self.route_goal_handle or not self.route_goal_handle.accepted:
            msg = 'Compute and track route was rejected!'
            self.setTaskError(ComputeAndTrackRoute.UNKNOWN, msg)
            self.error(msg)
            return None

        self.route_result_future = self.route_goal_handle.get_result_async()
        return RunningTask.COMPUTE_AND_TRACK_ROUTE

    def _smoothPathImpl(
        self, path: Path, smoother_id: str = '',
        max_duration: float = 2.0, check_for_collision: bool = False
    ) -> SmoothPath.Result:
        """
        Send a `SmoothPath` action request.

        Internal implementation to get the full result, not just the path.
        """
        self.debug("Waiting for 'SmoothPath' action server")
        while not self.smoother_client.wait_for_server(timeout_sec=1.0):
            self.info("'SmoothPath' action server not available, waiting...")

        goal_msg = SmoothPath.Goal()
        goal_msg.path = path
        goal_msg.max_smoothing_duration = rclpyDuration(seconds=max_duration).to_msg()
        goal_msg.smoother_id = smoother_id
        goal_msg.check_for_collisions = check_for_collision

        self.info('Smoothing path...')
        send_goal_future = self.smoother_client.send_goal_async(goal_msg)
        rclpy.spin_until_future_complete(self, send_goal_future)
        self.goal_handle = send_goal_future.result()

        if not self.goal_handle or not self.goal_handle.accepted:
            self.error('Smooth path was rejected!')
            result = SmoothPath.Result()
            result.error_code = SmoothPath.UNKNOWN
            result.error_msg = 'Smooth path was rejected'
            return result

        self.result_future = self.goal_handle.get_result_async()
        rclpy.spin_until_future_complete(self, self.result_future)
        self.status = self.result_future.result().status  # type: ignore[union-attr]

        return self.result_future.result().result  # type: ignore[union-attr]

    def smoothPath(
        self, path: Path, smoother_id: str = '',
            max_duration: float = 2.0, check_for_collision: bool = False) -> Path:
        """Send a `SmoothPath` action request."""
        self.clearTaskError()
        rtn = self._smoothPathImpl(path, smoother_id, max_duration, check_for_collision)

        if self.status == GoalStatus.STATUS_SUCCEEDED:
            return rtn.path
        else:
            self.setTaskError(rtn.error_code, rtn.error_msg)
            self.warn('Getting path failed with'
                      f' status code:{self.status}'
                      f' error code:{rtn.error_code}'
                      f' error msg:{rtn.error_msg}')
            return None

    def changeMap(self, map_filepath: str) -> bool:
        """Change the current static map in the map server."""
        while not self.change_maps_srv.wait_for_service(timeout_sec=1.0):
            self.info('change map service not available, waiting...')
        req = LoadMap.Request()
        req.map_url = map_filepath
        future = self.change_maps_srv.call_async(req)
        rclpy.spin_until_future_complete(self, future)

        future_result = future.result()
        if future_result is None:
            self.error('Change map request failed!')
            return False

        result = future_result.result
        if result != LoadMap.Response().RESULT_SUCCESS:
            if result == LoadMap.RESULT_MAP_DOES_NOT_EXIST:
                reason = 'Map does not exist'
            elif result == LoadMap.INVALID_MAP_DATA:
                reason = 'Invalid map data'
            elif result == LoadMap.INVALID_MAP_METADATA:
                reason = 'Invalid map metadata'
            elif result == LoadMap.UNDEFINED_FAILURE:
                reason = 'Undefined failure'
            else:
                reason = 'Unknown'
            self.setTaskError(result, reason)
            self.error(f'Change map request failed:{reason}!')
            return False
        else:
            self.info('Change map request was successful!')
            return True

    def clearAllCostmaps(self) -> None:
        """Clear all costmaps."""
        self.clearLocalCostmap()
        self.clearGlobalCostmap()
        return

    def clearLocalCostmap(self) -> None:
        """Clear local costmap."""
        while not self.clear_costmap_local_srv.wait_for_service(timeout_sec=1.0):
            self.info('Clear local costmaps service not available, waiting...')
        req = ClearEntireCostmap.Request()
        future = self.clear_costmap_local_srv.call_async(req)
        rclpy.spin_until_future_complete(self, future)
        return

    def clearGlobalCostmap(self) -> None:
        """Clear global costmap."""
        while not self.clear_costmap_global_srv.wait_for_service(timeout_sec=1.0):
            self.info('Clear global costmaps service not available, waiting...')
        req = ClearEntireCostmap.Request()
        future = self.clear_costmap_global_srv.call_async(req)
        rclpy.spin_until_future_complete(self, future)
        return

    def clearCostmapExceptRegion(self, reset_distance: float) -> None:
        """Clear the costmap except for a specified region."""
        while not self.clear_costmap_except_region_srv.wait_for_service(timeout_sec=1.0):
            self.info('ClearCostmapExceptRegion service not available, waiting...')
        req = ClearCostmapExceptRegion.Request()
        req.reset_distance = reset_distance
        future = self.clear_costmap_except_region_srv.call_async(req)
        rclpy.spin_until_future_complete(self, future)
        return

    def clearCostmapAroundRobot(self, reset_distance: float) -> None:
        """Clear the costmap around the robot."""
        while not self.clear_costmap_around_robot_srv.wait_for_service(timeout_sec=1.0):
            self.info('ClearCostmapAroundRobot service not available, waiting...')
        req = ClearCostmapAroundRobot.Request()
        req.reset_distance = reset_distance
        future = self.clear_costmap_around_robot_srv.call_async(req)
        rclpy.spin_until_future_complete(self, future)
        return

    def clearLocalCostmapAroundPose(self, pose: PoseStamped, reset_distance: float) -> None:
        """Clear the costmap around a given pose."""
        while not self.clear_local_costmap_around_pose_srv.wait_for_service(timeout_sec=1.0):
            self.info('ClearLocalCostmapAroundPose service not available, waiting...')
        req = ClearCostmapAroundPose.Request()
        req.pose = pose
        req.reset_distance = reset_distance
        future = self.clear_local_costmap_around_pose_srv.call_async(req)
        rclpy.spin_until_future_complete(self, future)
        return

    def clearGlobalCostmapAroundPose(self, pose: PoseStamped, reset_distance: float) -> None:
        """Clear the global costmap around a given pose."""
        while not self.clear_global_costmap_around_pose_srv.wait_for_service(timeout_sec=1.0):
            self.info('ClearGlobalCostmapAroundPose service not available, waiting...')
        req = ClearCostmapAroundPose.Request()
        req.pose = pose
        req.reset_distance = reset_distance
        future = self.clear_global_costmap_around_pose_srv.call_async(req)
        rclpy.spin_until_future_complete(self, future)
        return

    def getGlobalCostmap(self) -> OccupancyGrid:
        """Get the global costmap."""
        while not self.get_costmap_global_srv.wait_for_service(timeout_sec=1.0):
            self.info('Get global costmaps service not available, waiting...')
        req = GetCostmap.Request()
        future = self.get_costmap_global_srv.call_async(req)
        rclpy.spin_until_future_complete(self, future)

        result = future.result()
        if result is None:
            self.error('Get global costmap request failed!')
            return None

        return result.map

    def getLocalCostmap(self) -> OccupancyGrid:
        """Get the local costmap."""
        while not self.get_costmap_local_srv.wait_for_service(timeout_sec=1.0):
            self.info('Get local costmaps service not available, waiting...')
        req = GetCostmap.Request()
        future = self.get_costmap_local_srv.call_async(req)
        rclpy.spin_until_future_complete(self, future)

        result = future.result()

        if result is None:
            self.error('Get local costmap request failed!')
            return None

        return result.map

    def lifecycleStartup(self) -> None:
        """Startup nav2 lifecycle system."""
        self.info('Starting up lifecycle nodes based on lifecycle_manager.')
        for srv_name, srv_type in self.get_service_names_and_types():
            if srv_type[0] == 'nav2_msgs/srv/ManageLifecycleNodes':
                self.info(f'Starting up {srv_name}')
                mgr_client: Client[ManageLifecycleNodes.Request, ManageLifecycleNodes.Response] = \
                    self.create_client(ManageLifecycleNodes, srv_name)
                while not mgr_client.wait_for_service(timeout_sec=1.0):
                    self.info(f'{srv_name} service not available, waiting...')
                req = ManageLifecycleNodes.Request()
                req.command = ManageLifecycleNodes.Request().STARTUP
                future = mgr_client.call_async(req)

                # starting up requires a full map->odom->base_link TF tree
                # so if we're not successful, try forwarding the initial pose
                while True:
                    rclpy.spin_until_future_complete(self, future, timeout_sec=0.10)
                    if not future:
                        self._waitForInitialPose()
                    else:
                        break
        self.info('Nav2 is ready for use!')
        return

    def lifecycleShutdown(self) -> None:
        """Shutdown nav2 lifecycle system."""
        self.info('Shutting down lifecycle nodes based on lifecycle_manager.')
        for srv_name, srv_type in self.get_service_names_and_types():
            if srv_type[0] == 'nav2_msgs/srv/ManageLifecycleNodes':
                self.info(f'Shutting down {srv_name}')
                mgr_client: Client[ManageLifecycleNodes.Request, ManageLifecycleNodes.Response] = \
                    self.create_client(ManageLifecycleNodes, srv_name)
                while not mgr_client.wait_for_service(timeout_sec=1.0):
                    self.info(f'{srv_name} service not available, waiting...')
                req = ManageLifecycleNodes.Request()
                req.command = ManageLifecycleNodes.Request().SHUTDOWN
                future = mgr_client.call_async(req)
                rclpy.spin_until_future_complete(self, future)
                future.result()
        return

    def _waitForNodeToActivate(self, node_name: str) -> None:
        # Waits for the node within the tester namespace to become active
        self.debug(f'Waiting for {node_name} to become active..')
        node_service = f'{node_name}/get_state'
        state_client: Client[GetState.Request, GetState.Response] = \
            self.create_client(GetState, node_service)
        while not state_client.wait_for_service(timeout_sec=1.0):
            self.info(f'{node_service} service not available, waiting...')

        req = GetState.Request()
        state = 'unknown'
        while state != 'active':
            self.debug(f'Getting {node_name} state...')
            future = state_client.call_async(req)
            rclpy.spin_until_future_complete(self, future)

            result = future.result()
            if result is not None:
                state = result.current_state.label
                self.debug(f'Result of get_state: {state}')
            time.sleep(2)
        return

    def _waitForInitialPose(self) -> None:
        while not self.initial_pose_received:
            self.info('Setting initial pose')
            self._setInitialPose()
            self.info('Waiting for amcl_pose to be received')
            rclpy.spin_once(self, timeout_sec=1.0)
        return

    def _amclPoseCallback(self, msg: PoseWithCovarianceStamped) -> None:
        self.debug('Received amcl pose')
        self.initial_pose_received = True
        return

    def _feedbackCallback(self, msg: NavigateToPose.Feedback) -> None:
        self.debug('Received action feedback message')
        self.feedback = msg.feedback
        return

    def _routeFeedbackCallback(self, msg: ComputeAndTrackRoute.Feedback) -> None:
        self.debug('Received route action feedback message')
        self.route_feedback.append(msg.feedback)
        return

    def _setInitialPose(self) -> None:
        msg = PoseWithCovarianceStamped()
        msg.pose.pose = self.initial_pose.pose
        msg.header.frame_id = self.initial_pose.header.frame_id
        msg.header.stamp = self.initial_pose.header.stamp
        self.info('Publishing Initial Pose')
        self.initial_pose_pub.publish(msg)
        return

    def info(self, msg: str) -> None:
        self.get_logger().info(msg)
        return

    def warn(self, msg: str) -> None:
        self.get_logger().warning(msg)
        return

    def error(self, msg: str) -> None:
        self.get_logger().error(msg)
        return

    def debug(self, msg: str) -> None:
        self.get_logger().debug(msg)
        return
