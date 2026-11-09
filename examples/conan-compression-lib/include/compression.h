#pragma once

#include <string>

std::string conan_compress(const std::string& input);
std::string conan_decompress(const std::string& input, unsigned long expected_size);
