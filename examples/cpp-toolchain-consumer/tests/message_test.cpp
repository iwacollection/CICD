#include "message.h"
#include <cstring>

int main() {
    return std::strcmp(consumer_message(), "cpp-toolchain-consumer-ok") == 0 ? 0 : 1;
}
