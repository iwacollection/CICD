/**
 * Build a C/C++ project with a centrally registered Active gcc/clang container
 * toolchain (for example gcc-host-container-v1).
 *
 * Jenkinsfile:
 *   @Library('cicd-platform') _
 *   cppToolchainBuild(projectDir: '.', registryCredentialsId: 'ghcr-readonly')
 *
 * The project declares its toolchain and commands in ci/toolchain.json.
 * See nativeToolchainBuild for the shared implementation and trust rules.
 */
def call(Map options = [:]) {
    if (options.containsKey('language')) {
        error('language is fixed by cppToolchainBuild and cannot be overridden')
    }
    nativeToolchainBuild(options + [language: 'cpp'])
}
