add_library(platform_transport src/transport.cpp)
target_include_directories(platform_transport PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(platform_transport PUBLIC platform_core fmt::fmt)
