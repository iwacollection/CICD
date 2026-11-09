cmake_minimum_required(VERSION 3.21)
project(cpp_aarch64_cross LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(fmt CONFIG REQUIRED)

add_library(cross-core STATIC src/core.cpp)
target_include_directories(cross-core PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")
target_compile_features(cross-core PUBLIC cxx_std_17)

add_executable(aarch64-cross-demo src/main.cpp)
target_link_libraries(aarch64-cross-demo PRIVATE cross-core fmt::fmt)

include(CTest)
if(BUILD_TESTING)
    add_test(NAME aarch64-cross-qemu-smoke COMMAND aarch64-cross-demo)
endif()
