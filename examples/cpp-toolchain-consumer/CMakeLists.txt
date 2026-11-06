cmake_minimum_required(VERSION 3.21)
project(cpp_toolchain_consumer LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(consumer_core src/message.cpp)
target_include_directories(consumer_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)

add_executable(cpp-toolchain-consumer src/main.cpp)
target_link_libraries(cpp-toolchain-consumer PRIVATE consumer_core)

include(CTest)
if(BUILD_TESTING)
    add_executable(consumer_core_test tests/message_test.cpp)
    target_link_libraries(consumer_core_test PRIVATE consumer_core)
    add_test(NAME consumer_core_test COMMAND consumer_core_test)
endif()
