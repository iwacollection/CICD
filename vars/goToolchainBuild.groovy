/**
 * Build a Go project with a centrally registered Active Go toolchain image.
 *
 * Jenkinsfile:
 *   @Library('cicd-platform') _
 *   goToolchainBuild(projectDir: '.', registryCredentialsId: 'ghcr-readonly')
 *
 * The project declares its toolchain and commands in ci/toolchain.json.
 * See nativeToolchainBuild for the shared implementation and trust rules.
 */
def call(Map options = [:]) {
    if (options.containsKey('language')) {
        error('language is fixed by goToolchainBuild and cannot be overridden')
    }
    nativeToolchainBuild(options + [language: 'go'])
}
