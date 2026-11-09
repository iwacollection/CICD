cmake_minimum_required(VERSION 3.21)
project(conan_format_lib LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(fmt CONFIG REQUIRED)

add_library(conan-greeting STATIC src/greeting.cpp)
target_include_directories(conan-greeting PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(conan-greeting PUBLIC fmt::fmt)
