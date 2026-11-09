#include "compression.h"

#include <stdexcept>
#include <string>
#include <zlib.h>

std::string conan_compress(const std::string& input) {
    uLongf output_size = compressBound(static_cast<uLong>(input.size()));
    std::string output(output_size, '\0');
    const int result = compress2(
        reinterpret_cast<Bytef*>(output.data()),
        &output_size,
        reinterpret_cast<const Bytef*>(input.data()),
        static_cast<uLong>(input.size()),
        Z_BEST_SPEED
    );
    if (result != Z_OK) {
        throw std::runtime_error("zlib compression failed: " + std::to_string(result));
    }
    output.resize(output_size);
    return output;
}

std::string conan_decompress(const std::string& input, unsigned long expected_size) {
    uLongf output_size = static_cast<uLongf>(expected_size);
    std::string output(output_size, '\0');
    const int result = uncompress(
        reinterpret_cast<Bytef*>(output.data()),
        &output_size,
        reinterpret_cast<const Bytef*>(input.data()),
        static_cast<uLong>(input.size())
    );
    if (result != Z_OK) {
        throw std::runtime_error("zlib decompression failed: " + std::to_string(result));
    }
    output.resize(output_size);
    return output;
}
