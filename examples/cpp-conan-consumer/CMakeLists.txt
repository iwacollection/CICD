cmake_minimum_required(VERSION 3.21)
project(cpp_conan_consumer LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(fmt CONFIG REQUIRED)

add_subdirectory(libs/core)
add_subdirectory(libs/transport)

add_executable(cpp-platform-demo src/main.cpp)
target_link_libraries(cpp-platform-demo PRIVATE platform_transport)

include(CTest)
if(BUILD_TESTING)
    add_executable(cpp-platform-test tests/platform_test.cpp)
    target_link_libraries(cpp-platform-test PRIVATE platform_transport)
    add_test(NAME cpp-platform-test COMMAND cpp-platform-test)
endif()
