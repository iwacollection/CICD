# C++ multi-module fixture with Conan 2

This fixture contains two internal CMake libraries (core and greeting) and one application. greeting depends on core and the third-party fmt/11.0.2 package. Conan generates the CMake dependency and toolchain files; CMake/Ninja then builds the full target graph.

## Reproducible dependency flow

1. The exact Conan CLI version is 2.34.0, installed in the separate Candidate image from an upstream release package whose SHA-256 is verified during image build.
2. ci/profile fixes the native Linux x86_64, GCC 13, libstdc++11, C++17 and Release settings.
3. conan lock create resolves the graph and generates conan.lock. The bootstrap flag is intended only for the first CI run that produces the lockfile artifact; normal operation must fail when the committed lockfile is absent.
4. conan install with conan.lock installs the exact locked recipe graph and generates CMakeDeps / CMakeToolchain files.
5. CMake/Ninja builds core, then greeting, then the application; CTest and an output assertion verify the result.

## Timings

ci/verify.sh records dependency_resolution, cold dependency_download_install, warm dependency_install, configure, compile_link, and test elapsed times in build/stage-timings.tsv. The cold install stage includes Conan package download/build work; it is not presented as a pure network-only measurement.

## Cache boundary

The fixture uses a dedicated CONAN_HOME directory. It is not ccache and is not a build artifact. The package cache is disposable: deleting it causes package re-download/rebuild, while conan.lock and the source/profile inputs remain the authoritative dependency baseline. This first PR validates the isolated Candidate image and fixture. Wiring the new image into the production DAG and persisting a lock-aware Conan cache remains gated on image promotion and the next integration step.
