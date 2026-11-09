#include "platform/transport.h"
int main() {
    return platform_status() == "cpp-platform-core:ready" ? 0 : 1;
}
