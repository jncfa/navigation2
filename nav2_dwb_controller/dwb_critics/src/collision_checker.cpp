// Copyright (c) 2019 Intel Corporation
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include "dwb_critics/collision_checker.hpp"
#include "nav2_costmap_2d/cost_values.hpp"
#include "dwb_critics/line_iterator.hpp"
#include "dwb_core/exceptions.hpp"
#include "nav2_costmap_2d/footprint.hpp"

namespace dwb_critics
{

CollisionChecker::CollisionChecker(
  rclcpp::Node::SharedPtr ros_node,
  std::shared_ptr<nav2_costmap_2d::CostmapSubscriber> costmap_sub,
  std::shared_ptr<nav2_costmap_2d::FootprintSubscriber> footprint_sub,
  tf2_ros::Buffer & tf_buffer,
  std::string name)
: tf_buffer_(tf_buffer),
  node_(ros_node), name_(name),
  costmap_sub_(costmap_sub), footprint_sub_(footprint_sub)
{
  node_->get_parameter_or<std::string>("global_frame", global_frame_, std::string("map"));
  node_->get_parameter_or<std::string>("robot_base_frame", robot_base_frame_, std::string("base_link"));
}

CollisionChecker::~CollisionChecker() {}

bool CollisionChecker::isCollisionFree(
const geometry_msgs::msg::Pose2D & pose)
{
  try {
    if (scorePose(pose) < 0) {
      return false;
    }
    return true;
  } catch (const nav_core2::IllegalTrajectoryException & e) {
    RCLCPP_ERROR(node_->get_logger(), "%s", e.what());
    return false;
  } catch (const nav_core2::PlannerException & e) {
    RCLCPP_ERROR(node_->get_logger(), "%s", e.what());
    return false;
  }
}

double CollisionChecker::scorePose(
const geometry_msgs::msg::Pose2D & pose)
{
  nav2_costmap_2d::Costmap2D * costmap_;
  try {
    costmap_ = costmap_sub_->getCostmap();
  } catch (const std::runtime_error & e) {
    throw nav_core2::PlannerException(e.what());
  }

  unsigned int cell_x, cell_y;
  if (!costmap_->worldToMap(pose.x, pose.y, cell_x, cell_y)) {
    RCLCPP_ERROR(node_->get_logger(), "Map Cell: [%d, %d]", cell_x, cell_y);
    throw nav_core2::IllegalTrajectoryException(name_, "Trajectory Goes Off Grid.");
  }

  Footprint footprint;
  if (!footprint_sub_->getFootprint(footprint)) {
    throw nav_core2::PlannerException("Footprint not available.");
  }

  Footprint footprint_spec;
  unorientFootprint(footprint, footprint_spec);
  nav2_costmap_2d::transformFootprint(pose.x, pose.y, pose.theta, footprint_spec, footprint);

  // now we really have to lay down the footprint in the costmap grid
  unsigned int x0, x1, y0, y1;
  double line_cost = 0.0;
  double footprint_cost = 0.0;

  // we need to rasterize each line in the footprint
  for (unsigned int i = 0; i < footprint.size() - 1; ++i) {
    // get the cell coord of the first point
    if (!costmap_->worldToMap(footprint[i].x, footprint[i].y, x0, y0)) {
      RCLCPP_DEBUG(node_->get_logger(), "Map Cell: [%d, %d]", x0, y0);
      throw nav_core2::IllegalTrajectoryException(name_, "Footprint Goes Off Grid at map.");
    }

    // get the cell coord of the second point
    if (!costmap_->worldToMap(footprint[i + 1].x, footprint[i + 1].y, x1, y1)) {
      RCLCPP_DEBUG(node_->get_logger(), "Map Cell: [%d, %d]", x1, y1);
      throw nav_core2::IllegalTrajectoryException(name_, "Footprint Goes Off Grid.");
    }

    line_cost = lineCost(x0, x1, y0, y1);
    footprint_cost = std::max(line_cost, footprint_cost);
  }

  // we also need to connect the first point in the footprint to the last point
  // get the cell coord of the last point
  if (!costmap_->worldToMap(footprint.back().x, footprint.back().y, x0, y0)) {
      RCLCPP_DEBUG(node_->get_logger(), "Map Cell: [%d, %d]", x0, y0);
    throw nav_core2::IllegalTrajectoryException(name_, "Footprint Goes Off Grid.");
  }

  // get the cell coord of the first point
  if (!costmap_->worldToMap(footprint.front().x, footprint.front().y, x1, y1)) {
      RCLCPP_DEBUG(node_->get_logger(), "Map Cell: [%d, %d]", x1, y1);
    throw nav_core2::IllegalTrajectoryException(name_, "Footprint Goes Off Grid.");
  }

  line_cost = lineCost(x0, x1, y0, y1);
  footprint_cost = std::max(line_cost, footprint_cost);

  // if all line costs are legal... then we can return that the footprint is legal
  return footprint_cost;
}

double CollisionChecker::lineCost(int x0, int x1, int y0, int y1)
{
  double line_cost = 0.0;
  double point_cost = -1.0;

  for (LineIterator line(x0, y0, x1, y1); line.isValid(); line.advance()) {
    point_cost = pointCost(line.getX(), line.getY());   // Score the current point

    if (line_cost < point_cost) {
      line_cost = point_cost;
    }
  }

  return line_cost;
}

double CollisionChecker::pointCost(int x, int y)
{
  nav2_costmap_2d::Costmap2D * costmap_ = costmap_sub_->getCostmap();

  unsigned char cost = costmap_->getCost(x, y);
  // if the cell is in an obstacle the path is invalid or unknown
  if (cost == nav2_costmap_2d::LETHAL_OBSTACLE) {
    RCLCPP_DEBUG(node_->get_logger(), "Map Cell: [%d, %d]", x, y);
    throw nav_core2::IllegalTrajectoryException(name_, "Trajectory Hits Obstacle.");
  } else if (cost == nav2_costmap_2d::NO_INFORMATION) {
    RCLCPP_DEBUG(node_->get_logger(), "Map Cell: [%d, %d]", x, y);
    throw nav_core2::IllegalTrajectoryException(name_, "Trajectory Hits Unknown Region.");
  }

  return cost;
}

bool
CollisionChecker::getRobotPose(geometry_msgs::msg::PoseStamped & global_pose) const
{
  tf2::toMsg(tf2::Transform::getIdentity(), global_pose.pose);
  geometry_msgs::msg::PoseStamped robot_pose;
  tf2::toMsg(tf2::Transform::getIdentity(), robot_pose.pose);

  robot_pose.header.frame_id = robot_base_frame_;
  robot_pose.header.stamp = rclcpp::Time();

  rclcpp::Time current_time = node_->now();  // save time for checking tf delay later
  // get the global pose of the robot
  try {
    tf_buffer_.transform(robot_pose, global_pose, global_frame_);
  } catch (tf2::LookupException & ex) {
    RCLCPP_ERROR(node_->get_logger(),
      "No Transform available Error looking up robot pose: %s\n", ex.what());
    return false;
  } catch (tf2::ConnectivityException & ex) {
    RCLCPP_ERROR(node_->get_logger(),
      "Connectivity Error looking up robot pose: %s\n", ex.what());
    return false;
  } catch (tf2::ExtrapolationException & ex) {
    RCLCPP_ERROR(node_->get_logger(),
      "Extrapolation Error looking up robot pose: %s\n", ex.what());
    return false;
  }
  // check global_pose timeout

  return true;
}

void CollisionChecker::unorientFootprint(
  const std::vector<geometry_msgs::msg::Point> & oriented_footprint,
  std::vector<geometry_msgs::msg::Point> & reset_footprint)
{
  geometry_msgs::msg::PoseStamped current_pose;
   if (!getRobotPose(current_pose)) {
    throw nav_core2::PlannerException("Robot pose unavailable.");
  }

  double x = current_pose.pose.position.x;
  double y = current_pose.pose.position.y;
  double theta = tf2::getYaw(current_pose.pose.orientation);

  Footprint temp;
  nav2_costmap_2d::transformFootprint(-x, -y, 0, oriented_footprint, temp);
  nav2_costmap_2d::transformFootprint(0, 0, -theta, temp, reset_footprint);
}

}  // namespace dwb_critics
