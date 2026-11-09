#include "platform/transport.h"
#include "platform/core.h"
#include <fmt/format.h>
std::string platform_status() {
    return fmt::format("{}:ready", platform_core_name());
}
