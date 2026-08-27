#include <iostream>

int main() {
    // Keep this output stable: CI smoke tests use it to validate Fast Lane builds.
    std::cout << "enterprise-ci-platform-ok" << std::endl;
    return 0;
}
