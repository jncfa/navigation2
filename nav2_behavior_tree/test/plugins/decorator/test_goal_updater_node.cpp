// Copyright (c) 2018 Intel Corporation
// Copyright (c) 2020 Francisco Martin Rico
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

#include <gtest/gtest.h>
#include <memory>
#include <set>
#include <string>

#include "nav_msgs/msg/path.hpp"
#include "geometry_msgs/msg/pose_stamped.hpp"

#include "behaviortree_cpp/bt_factory.h"

#include "nav2_behavior_tree/utils/test_action_server.hpp"
#include "nav2_behavior_tree/plugins/decorator/goal_updater_node.hpp"


class GoalUpdaterTestFixture : public ::testing::Test
{
public:
  static void SetUpTestCase()
  {
    node_ = std::make_shared<nav2::LifecycleNode>("goal_updater_test_fixture");
    factory_ = std::make_shared<BT::BehaviorTreeFactory>();

    config_ = new BT::NodeConfiguration();

    // Create the blackboard that will be shared by all of the nodes in the tree
    config_->blackboard = BT::Blackboard::create();
    // Put items on the blackboard
    config_->blackboard->set(
      "node",
      node_);

    BT::NodeBuilder builder =
      [](const std::string & name, const BT::NodeConfiguration & config)
      {
        return std::make_unique<nav2_behavior_tree::GoalUpdater>(
          name, config);
      };

    factory_->registerBuilder<nav2_behavior_tree::GoalUpdater>(
      "GoalUpdater", builder);
  }

  static void TearDownTestCase()
  {
    delete config_;
    config_ = nullptr;
    node_.reset();
    factory_.reset();
  }

  void TearDown() override
  {
    tree_.reset();
  }

protected:
  static nav2::LifecycleNode::SharedPtr node_;
  static BT::NodeConfiguration * config_;
  static std::shared_ptr<BT::BehaviorTreeFactory> factory_;
  static std::shared_ptr<BT::Tree> tree_;
};

nav2::LifecycleNode::SharedPtr GoalUpdaterTestFixture::node_ = nullptr;

BT::NodeConfiguration * GoalUpdaterTestFixture::config_ = nullptr;
std::shared_ptr<BT::BehaviorTreeFactory> GoalUpdaterTestFixture::factory_ = nullptr;
std::shared_ptr<BT::Tree> GoalUpdaterTestFixture::tree_ = nullptr;

TEST_F(GoalUpdaterTestFixture, test_tick)
{
  // create tree
  std::string xml_txt =
    R"(
      <root BTCPP_format="4">
        <BehaviorTree ID="MainTree">
          <GoalUpdater input_goal="{goal}" input_goals="{goals}" output_goal="{updated_goal}" output_goals="{updated_goals}">
            <AlwaysSuccess/>
          </GoalUpdater>
        </BehaviorTree>
      </root>)";

  tree_ = std::make_shared<BT::Tree>(factory_->createTreeFromText(xml_txt, config_->blackboard));

  // create new goal and set it on blackboard
  geometry_msgs::msg::PoseStamped goal;
  nav_msgs::msg::Goals goals;
  goal.header.stamp = node_->now();
  goal.pose.position.x = 1.0;
  goals.goals.push_back(goal);
  config_->blackboard->set("goal", goal);
  config_->blackboard->set("goals", goals);

  // tick tree without publishing updated goal and get updated_goal
  tree_->rootNode()->executeTick();
  geometry_msgs::msg::PoseStamped updated_goal;
  nav_msgs::msg::Goals updated_goals;
  EXPECT_TRUE(config_->blackboard->get("updated_goal", updated_goal));
  EXPECT_TRUE(config_->blackboard->get("updated_goals", updated_goals));
}

TEST_F(GoalUpdaterTestFixture, test_older_goal_update)
{
  // create tree
  std::string xml_txt =
    R"(
      <root BTCPP_format="4">
        <BehaviorTree ID="MainTree">
          <GoalUpdater input_goal="{goal}" input_goals="{goals}" output_goal="{updated_goal}" output_goals="{updated_goals}">
            <AlwaysSuccess/>
          </GoalUpdater>
        </BehaviorTree>
      </root>)";

  tree_ = std::make_shared<BT::Tree>(factory_->createTreeFromText(xml_txt, config_->blackboard));
  auto goal_updater_pub =
    node_->create_publisher<geometry_msgs::msg::PoseStamped>("goal_update", 10);
  goal_updater_pub->on_activate();
  auto goals_updater_pub =
    node_->create_publisher<nav_msgs::msg::Goals>("goals_update", 10);
  goals_updater_pub->on_activate();

  // create new goal and set it on blackboard
  geometry_msgs::msg::PoseStamped goal;
  nav_msgs::msg::Goals goals;
  goal.header.stamp = node_->now();
  goal.pose.position.x = 1.0;
  goals.header.stamp = goal.header.stamp;
  goals.goals.push_back(goal);
  config_->blackboard->set("goal", goal);
  config_->blackboard->set("goals", goals);

  // publish updated_goal older than goal
  geometry_msgs::msg::PoseStamped goal_to_update;
  nav_msgs::msg::Goals goals_to_update;
  goal_to_update.header.stamp = rclcpp::Time(goal.header.stamp) - rclcpp::Duration(1, 0);
  goal_to_update.pose.position.x = 2.0;
  goals_to_update.header.stamp = goal_to_update.header.stamp;
  goals_to_update.goals.push_back(goal_to_update);

  goal_updater_pub->publish(goal_to_update);
  goals_updater_pub->publish(goals_to_update);
  tree_->rootNode()->executeTick();
  geometry_msgs::msg::PoseStamped updated_goal;
  nav_msgs::msg::Goals updated_goals;
  EXPECT_TRUE(config_->blackboard->get("updated_goal", updated_goal));
  EXPECT_TRUE(config_->blackboard->get("updated_goals", updated_goals));

  // expect to succeed and not update goal
  EXPECT_EQ(tree_->rootNode()->status(), BT::NodeStatus::SUCCESS);
  EXPECT_EQ(updated_goal, goal);
  EXPECT_EQ(updated_goals, goals);
}

TEST_F(GoalUpdaterTestFixture, test_get_latest_goal_update)
{
  // create tree
  std::string xml_txt =
    R"(
      <root BTCPP_format="4">
        <BehaviorTree ID="MainTree">
          <GoalUpdater input_goal="{goal}" input_goals="{goals}" output_goal="{updated_goal}" output_goals="{updated_goals}">
            <AlwaysSuccess/>
          </GoalUpdater>
        </BehaviorTree>
      </root>)";

  tree_ = std::make_shared<BT::Tree>(factory_->createTreeFromText(xml_txt, config_->blackboard));
  auto goal_updater_pub =
    node_->create_publisher<geometry_msgs::msg::PoseStamped>("goal_update", 10);
  goal_updater_pub->on_activate();
  auto goals_updater_pub =
    node_->create_publisher<nav_msgs::msg::Goals>("goals_update", 10);
  goals_updater_pub->on_activate();

  // create new goal and set it on blackboard
  geometry_msgs::msg::PoseStamped goal;
  nav_msgs::msg::Goals goals;
  goal.header.stamp = node_->now();
  goal.pose.position.x = 1.0;
  goals.goals.push_back(goal);
  config_->blackboard->set("goal", goal);
  config_->blackboard->set("goals", goals);

  // publish updated_goal older than goal
  geometry_msgs::msg::PoseStamped goal_to_update_1;
  nav_msgs::msg::Goals goals_to_update_1;
  goal_to_update_1.header.stamp = node_->now();
  goal_to_update_1.pose.position.x = 2.0;
  goals_to_update_1.header.stamp = goal_to_update_1.header.stamp;
  goals_to_update_1.goals.push_back(goal_to_update_1);

  geometry_msgs::msg::PoseStamped goal_to_update_2;
  nav_msgs::msg::Goals goals_to_update_2;
  goal_to_update_2.header.stamp = node_->now();
  goal_to_update_2.pose.position.x = 3.0;
  goals_to_update_2.header.stamp = goal_to_update_2.header.stamp;
  goals_to_update_2.goals.push_back(goal_to_update_2);

  goal_updater_pub->publish(goal_to_update_1);
  goals_updater_pub->publish(goals_to_update_1);
  goal_updater_pub->publish(goal_to_update_2);
  goals_updater_pub->publish(goals_to_update_2);
  tree_->rootNode()->executeTick();
  geometry_msgs::msg::PoseStamped updated_goal;
  nav_msgs::msg::Goals updated_goals;
  EXPECT_TRUE(config_->blackboard->get("updated_goal", updated_goal));
  EXPECT_TRUE(config_->blackboard->get("updated_goals", updated_goals));

  // expect to succeed
  EXPECT_EQ(tree_->rootNode()->status(), BT::NodeStatus::SUCCESS);
  // expect to update goal with latest goal update
  EXPECT_EQ(updated_goal, goal_to_update_2);
  EXPECT_EQ(updated_goals, goals_to_update_2);
}

int main(int argc, char ** argv)
{
  ::testing::InitGoogleTest(&argc, argv);

  // initialize ROS
  rclcpp::init(argc, argv);

  int all_successful = RUN_ALL_TESTS();

  // shutdown ROS
  rclcpp::shutdown();

  return all_successful;
}
