cmake_minimum_required(VERSION 3.16)
project(hello_lib LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(hello-lib STATIC src/hello_lib.cpp)
target_include_directories(hello-lib PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
