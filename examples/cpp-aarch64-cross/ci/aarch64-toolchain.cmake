set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

# Target headers and libraries live under Ubuntu's cross-triplet root.
# Host-side program discovery must remain outside the target sysroot.
set(CMAKE_FIND_ROOT_PATH "/usr/aarch64-linux-gnu")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)

# Compiler checks must not try to link or execute target programs on x86_64.
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
