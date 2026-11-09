cmake_minimum_required(VERSION 3.21)
project(conan_demo_app LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(fmt CONFIG REQUIRED)
find_package(ZLIB REQUIRED)

if(NOT DEFINED ENV{CI_UPSTREAM_ROOT} OR "$ENV{CI_UPSTREAM_ROOT}" STREQUAL "")
    message(FATAL_ERROR "CI_UPSTREAM_ROOT is required; the demo must consume verified DAG artifacts")
endif()

set(UPSTREAM_ROOT "$ENV{CI_UPSTREAM_ROOT}")
set(GREETING_ROOT "${UPSTREAM_ROOT}/conan-format-lib")
set(COMPRESSION_ROOT "${UPSTREAM_ROOT}/conan-compression-lib")
set(GREETING_ARCHIVE "${GREETING_ROOT}/build/libconan-greeting.a")
set(COMPRESSION_ARCHIVE "${COMPRESSION_ROOT}/build/libconan-compression.a")

foreach(required_file IN ITEMS
    "${GREETING_ARCHIVE}"
    "${GREETING_ROOT}/include/greeting.h"
    "${COMPRESSION_ARCHIVE}"
    "${COMPRESSION_ROOT}/include/compression.h"
)
    if(NOT EXISTS "${required_file}")
        message(FATAL_ERROR "verified upstream DAG input is missing: ${required_file}")
    endif()
endforeach()

add_library(conan-greeting-upstream STATIC IMPORTED GLOBAL)
set_target_properties(conan-greeting-upstream PROPERTIES
    IMPORTED_LOCATION "${GREETING_ARCHIVE}"
    INTERFACE_INCLUDE_DIRECTORIES "${GREETING_ROOT}/include"
)

add_library(conan-compression-upstream STATIC IMPORTED GLOBAL)
set_target_properties(conan-compression-upstream PROPERTIES
    IMPORTED_LOCATION "${COMPRESSION_ARCHIVE}"
    INTERFACE_INCLUDE_DIRECTORIES "${COMPRESSION_ROOT}/include"
)

add_executable(conan-demo src/main.cpp)
target_link_libraries(conan-demo PRIVATE
    conan-greeting-upstream
    conan-compression-upstream
    fmt::fmt
    ZLIB::ZLIB
)

enable_testing()
add_test(NAME conan_multi_module_integration COMMAND conan-demo)
set_tests_properties(conan_multi_module_integration PROPERTIES
    PASS_REGULAR_EXPRESSION "conan-multi-module-ok"
)
