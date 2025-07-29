#/bin/bash

set -e # fail on errors

UBUNTU_VERSION="$(lsb_release -sc)"
ROS_DISTRO="${ROS_DISTRO:-jazzy}"

find . -name package.xml -exec dirname "$(realpath {})" \; | while read package_directory; do
    echo "Processing package in: $package_directory"
    pushd "$package_directory"
    
    source /opt/overlay_ws/install/setup.bash
    bloom-generate rosdebian --os-name ubuntu --os-version "$UBUNTU_VERSION" --ros-distro "$ROS_DISTRO"
    fakeroot debian/rules binary DEB_BUILD_OPTIONS=nocheck
    
    popd
done