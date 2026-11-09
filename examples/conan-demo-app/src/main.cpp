#include "compression.h"
#include "greeting.h"

#include <iostream>
#include <string>

int main() {
    const std::string message = conan_greeting();
    const std::string packed = conan_compress(message);
    const std::string restored = conan_decompress(packed, static_cast<unsigned long>(message.size()));

    if (restored != message) {
        std::cerr << "round-trip mismatch" << std::endl;
        return 1;
    }

    std::cout << "conan-multi-module-ok: " << restored << std::endl;
    return 0;
}
