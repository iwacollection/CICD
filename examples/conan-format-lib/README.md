# Conan format library fixture

This fixture builds a small static C++ library with Conan and CMake.

## CI cache contract

The CI build runs from this project directory. Its project-scoped cache paths are therefore rooted under `examples/conan-format-lib/` in the repository workspace:

- `.cache/ccache` stores compiler outputs.
- `.ci-cache/conan-home` stores Conan package and metadata state.

The cache key is derived from the declared build inputs (`CMakeLists.txt`, Conan recipe/lock, profiles, headers and sources). This README is intentionally documentation-only and is not part of that fingerprint, so a documentation change must not invalidate an otherwise identical compiler/dependency cache. Every CI run still performs the normal build, tests, artifact verification and security gates.
