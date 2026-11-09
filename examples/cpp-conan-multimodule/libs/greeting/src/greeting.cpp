#include "core.h"
#include "greeting.h"

#include <fmt/format.h>

std::string greeting_message() {
    return fmt::format("{}", core_message());
}
