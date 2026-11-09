cmake_minimum_required(VERSION 3.21)
project(cpp_conan_multimodule LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(fmt CONFIG REQUIRED)

add_subdirectory(libs/core)
add_subdirectory(libs/greeting)

add_executable(cpp-conan-multimodule src/main.cpp)
target_link_libraries(cpp-conan-multimodule PRIVATE project::greeting)

include(CTest)
if(BUILD_TESTING)
    add_test(NAME cpp-conan-multimodule-smoke COMMAND cpp-conan-multimodule)
endif()
