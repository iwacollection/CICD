cmake_minimum_required(VERSION 3.16)
project(hello_ci LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT DEFINED ENV{CI_UPSTREAM_ROOT} OR "$ENV{CI_UPSTREAM_ROOT}" STREQUAL "")
    message(FATAL_ERROR "CI_UPSTREAM_ROOT is required; hello-cpp must consume hello-lib through DAG artifact handoff")
endif()

set(HELLO_LIB_ROOT "$ENV{CI_UPSTREAM_ROOT}/hello-lib")
set(HELLO_LIB_ARCHIVE "${HELLO_LIB_ROOT}/build/libhello-lib.a")
set(HELLO_LIB_INCLUDE "${HELLO_LIB_ROOT}/include")

if(NOT EXISTS "${HELLO_LIB_ARCHIVE}")
    message(FATAL_ERROR "verified upstream library is missing: ${HELLO_LIB_ARCHIVE}")
endif()
if(NOT EXISTS "${HELLO_LIB_INCLUDE}/hello_lib.h")
    message(FATAL_ERROR "verified upstream header is missing: ${HELLO_LIB_INCLUDE}/hello_lib.h")
endif()

add_library(hello-lib-upstream STATIC IMPORTED GLOBAL)
set_target_properties(
    hello-lib-upstream
    PROPERTIES
        IMPORTED_LOCATION "${HELLO_LIB_ARCHIVE}"
        INTERFACE_INCLUDE_DIRECTORIES "${HELLO_LIB_INCLUDE}"
)

add_executable(hello-ci src/main.cpp)
target_link_libraries(hello-ci PRIVATE hello-lib-upstream)
