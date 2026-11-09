#include "core.h"

#include <fmt/format.h>
#include <iostream>

int main() {
    const auto message = fmt::format("{}:{}", cross_core_message(), "qemu-runtime-ok");
    std::cout << message << '\n';
    return 0;
}
