#include "greeting.h"

#include <fmt/format.h>

std::string conan_greeting() {
    return fmt::format("hello from {}", "Conan 2");
}
