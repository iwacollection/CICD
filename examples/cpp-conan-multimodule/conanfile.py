from conan import ConanFile


class CppConanMultimodule(ConanFile):
    name = "cpp-conan-multimodule"
    version = "1.0"
    package_type = "application"
    settings = "os", "arch", "compiler", "build_type"
    requires = "fmt/11.0.2"
