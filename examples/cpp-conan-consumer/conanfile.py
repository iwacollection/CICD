from conan import ConanFile


class CppConanConsumer(ConanFile):
    name = "cpp-conan-consumer"
    version = "1.0"
    package_type = "application"
    settings = "os", "arch", "compiler", "build_type"
    requires = "fmt/11.0.2"
    generators = "CMakeDeps", "CMakeToolchain"
