import com.iwacollection.cicd.JavaToolchainSpec
import groovy.json.JsonOutput
import groovy.json.JsonSlurperClassic

/**
 * Build a Java project using the centrally registered Active JDK container.
 *
 * This step is intentionally trusted-branch only. Pull requests and forks must
 * use a separate low-trust validation job; they must not receive Maven,
 * registry, or production credentials.
 */
def call(Map options = [:]) {
    if (options.containsKey('platformRef') || options.containsKey('trustedBranch')) {
        error('platformRef and trustedBranch are controller policy; project Jenkinsfiles cannot override them')
    }

    String branch = (env.BRANCH_NAME ?: env.GIT_BRANCH ?: '').toString()
    branch = branch.replaceFirst(/^origin\//, '')
    if (env.CHANGE_ID?.toString()?.trim()) {
        error('javaToolchainBuild is a trusted-branch step; Pull Requests must use the hosted untrusted validation lane')
    }
    if (branch && branch != 'main') {
        error("javaToolchainBuild only permits trusted branch 'main', got '${branch}'")
    }

    // The platform ref is controller-managed and must match the Global
    // Shared Library Default version. Project parameters cannot select an
    // older Registry snapshot to bypass current revocation/Active state.
    String platformRef = ''
    withCredentials([string(
        credentialsId: 'cicd-platform-sha',
        variable: 'CI_PLATFORM_REF'
    )]) {
        platformRef = (env.CI_PLATFORM_REF ?: '').toString().trim()
    }
    if (!(platformRef ==~ /^[0-9a-f]{40}$/)) {
        error('Controller-managed credential cicd-platform-sha must contain an exact 40-character CICD commit SHA')
    }

    String projectDir = JavaToolchainSpec.validateRelativePath(
        (options.projectDir ?: '.').toString(),
        'projectDir'
    )
    String configRelativePath = JavaToolchainSpec.validateRelativePath(
        (options.toolchainConfig ?: 'ci/toolchain.json').toString(),
        'toolchainConfig'
    )
    String configPath = projectDir == '.' ? configRelativePath : "${projectDir}/${configRelativePath}"
    if (!fileExists(configPath)) {
        error("Java toolchain declaration not found: ${configPath}")
    }

    Map project = JavaToolchainSpec.parseProjectConfig(readFile(configPath))
    if (options.toolchainId && options.toolchainId.toString() != project.toolchain) {
        error("Jenkins input Toolchain ID does not match ${configPath}")
    }

    String sourceCommit = (env.GIT_COMMIT ?: '').toString().trim()
    String sourceGitUrl = (env.GIT_URL ?: '').toString().trim()
    if (!(sourceCommit ==~ /^[0-9a-f]{40}$/)) {
        error('GIT_COMMIT must be set by the Jenkins SCM checkout before javaToolchainBuild')
    }
    if (!sourceGitUrl) {
        error('GIT_URL must be set by the Jenkins SCM checkout before javaToolchainBuild')
    }
    String sourceRepository
    try {
        sourceRepository = JavaToolchainSpec.normalizeRepositorySlug(sourceGitUrl)
    } catch (IllegalArgumentException ex) {
        error("Cannot normalize Jenkins GIT_URL to owner/repository: ${ex.message}")
    }

    String platformRepository = 'https://github.com/iwacollection/CICD.git'
    String platformCredentialsId = ''
    String registryCredentialsId = (options.registryCredentialsId ?: '').toString()
    String mavenSettingsCredentialsId = (options.mavenSettingsCredentialsId ?: '').toString()

    // Fetch only the requested immutable platform revision into a dedicated directory.
    Map remote = [url: platformRepository]
    if (platformCredentialsId) {
        remote.credentialsId = platformCredentialsId
    }
    withEnv(["CI_PLATFORM_REF=${platformRef}"]) {
        dir('.ci-platform') {
            deleteDir()
            checkout([
                $class: 'GitSCM',
                branches: [[name: '*/main']],
                extensions: [[$class: 'CloneOption', shallow: false, noTags: true]],
                userRemoteConfigs: [remote]
            ])
            sh '''#!/usr/bin/env bash
set -euo pipefail
git checkout --detach "$CI_PLATFORM_REF"
'''
        }
    }

    String actualPlatformRef = sh(
        script: 'git -C .ci-platform rev-parse HEAD',
        returnStdout: true
    ).trim()
    if (actualPlatformRef != platformRef) {
        error("CICD checkout mismatch: expected ${platformRef}, got ${actualPlatformRef}")
    }

    // Project repositories declare only a Toolchain ID. The central resolver
    // owns status, registry allowlist, digest and Active lifecycle enforcement.
    withEnv(["CI_TOOLCHAIN_ID=${project.toolchain}"]) {
        int resolverStatus = sh(
            script: '''#!/usr/bin/env bash
set -euo pipefail
python3 .ci-platform/scripts/ci/resolve_toolchain.py \
  --catalog .ci-platform/ci/toolchains.json \
  --toolchain-id "$CI_TOOLCHAIN_ID" \
  --expected-mode container \
  > .ci-toolchain-resolution.json
''',
            returnStatus: true
        )
        if (resolverStatus != 0) {
            error("Central resolver rejected Toolchain ID '${project.toolchain}'. Only an Active toolchain can be consumed.")
        }
    }

    Map resolved = JavaToolchainSpec.validateResolvedToolchain(
        new JsonSlurperClassic().parseText(readFile('.ci-toolchain-resolution.json'))
    )

    String registryHost = resolved.image.substring(0, resolved.image.indexOf('/'))
    String runnerLabelsJson = JsonOutput.toJson(
        options.runnerLabels ?: ['jenkins', 'java-project-builder']
    )
    String artifactGlobsJson = JsonOutput.toJson(project.artifact_globs)
    String dependencyLocksJson = JsonOutput.toJson(project.dependency_lock_files)
    String sourceDateEpoch = sh(
        script: "git -C '${projectDir}' show -s --format=%ct HEAD",
        returnStdout: true
    ).trim()
    if (!(sourceDateEpoch ==~ /^[0-9]+$/)) {
        error('Cannot derive SOURCE_DATE_EPOCH from the checked out source commit')
    }

    List<String> buildEnvironment = [
        'CI_TOOLCHAIN_ID': project.toolchain,
        'CI_TOOLCHAIN_IMAGE': resolved.image,
        'CI_PROJECT_DIR': projectDir,
        'CI_PROJECT_NAME': project.project_name,
        'CI_BUILD_COMMAND': project.build_command,
        'CI_TEST_COMMAND': project.test_command,
        'CI_VERIFY_COMMAND': project.verify_command,
        'CI_ARTIFACT_GLOBS_JSON': artifactGlobsJson,
        'CI_DEPENDENCY_LOCKS_JSON': dependencyLocksJson,
        'CI_RUNNER_LABELS_JSON': runnerLabelsJson,
        'CI_REGISTRY_HOST': registryHost,
        'SOURCE_DATE_EPOCH': sourceDateEpoch,
        'GITHUB_SHA': sourceCommit,
        'GITHUB_REPOSITORY': sourceRepository,
        'GITHUB_RUN_ID': (env.BUILD_TAG ?: env.BUILD_NUMBER ?: 'jenkins-run').toString(),
        'GITHUB_RUN_ATTEMPT': '1',
        'GITHUB_WORKFLOW_REF': (env.JOB_NAME ?: 'jenkins-java-build').toString()
    ].collect { key, value -> "${key}=${value}" }

    def executeBuildAndPackage = {
        withEnv(buildEnvironment) {
            sh '''#!/usr/bin/env bash
set -euo pipefail
docker info >/dev/null
cosign version
cosign verify "$CI_TOOLCHAIN_IMAGE" \
  --certificate-identity="https://github.com/iwacollection/CICD/.github/workflows/toolchain-images.yml@refs/heads/main" \
  --certificate-oidc-issuer="https://token.actions.githubusercontent.com"
docker pull "$CI_TOOLCHAIN_IMAGE"
expected="${CI_TOOLCHAIN_IMAGE##*@}"
repo_digests="$(docker image inspect "$CI_TOOLCHAIN_IMAGE" --format '{{json .RepoDigests}}')"
[[ "$repo_digests" == *"@$expected"* ]] || {
  echo "Pulled image does not match central Active digest" >&2
  exit 1
}
'''

            if (mavenSettingsCredentialsId) {
                withCredentials([file(
                    credentialsId: mavenSettingsCredentialsId,
                    variable: 'CI_MAVEN_SETTINGS_FILE'
                )]) {
                    withEnv(['MAVEN_ARGS=-s /workspace/.ci-maven-settings.xml']) {
                        sh '''#!/usr/bin/env bash
set -euo pipefail
install -m 0600 "$CI_MAVEN_SETTINGS_FILE" "$CI_PROJECT_DIR/.ci-maven-settings.xml"
'''
                        try {
                            runProjectCommands()
                        } finally {
                            sh '''#!/usr/bin/env bash
set +e
rm -f "$CI_PROJECT_DIR/.ci-maven-settings.xml"
'''
                        }
                    }
                }
            } else {
                // Do not inherit an arbitrary MAVEN_ARGS value from a shared Agent.
                withEnv(['MAVEN_ARGS=']) {
                    runProjectCommands()
                }
            }

            sh '''#!/usr/bin/env bash
set -euo pipefail
python3 .ci-platform/scripts/ci/collect_build_metadata.py \
  --working-directory "$CI_PROJECT_DIR" \
  --container-image "$CI_TOOLCHAIN_IMAGE" \
  --output build-environment.json

python3 .ci-platform/scripts/ci/package_artifact.py \
  --project "$CI_PROJECT_NAME" \
  --working-directory "$CI_PROJECT_DIR" \
  --artifacts-json "$CI_ARTIFACT_GLOBS_JSON" \
  --dependency-locks-json "$CI_DEPENDENCY_LOCKS_JSON" \
  --soc generic \
  --target-os linux \
  --arch x86_64 \
  --toolchain "$CI_TOOLCHAIN_ID" \
  --execution-mode container \
  --container-image "$CI_TOOLCHAIN_IMAGE" \
  --runner-labels-json "$CI_RUNNER_LABELS_JSON" \
  --build-metadata-file build-environment.json \
  --output-dir .ci-dist

python3 .ci-platform/scripts/ci/verify_artifact.py \
  --directory .ci-dist \
  --expected-source-sha "$GITHUB_SHA" \
  --expected-repository "$GITHUB_REPOSITORY"
'''

            archiveArtifacts(
                artifacts: '.ci-dist/**,build-environment.json,' +
                    "${projectDir}/target/surefire-reports/*.xml",
                fingerprint: true,
                allowEmptyArchive: false
            )
        } finally {
            // Always publish test results, including failed Maven builds.
            // Empty results are allowed when the build fails before Surefire runs.
            junit(
                testResults: "${projectDir}/target/surefire-reports/*.xml",
                allowEmptyResults: true
            )
        }
    }

    def runWithOptionalRegistryCredentials = {
        if (registryCredentialsId) {
            withCredentials([usernamePassword(
                credentialsId: registryCredentialsId,
                usernameVariable: 'CI_REGISTRY_USERNAME',
                passwordVariable: 'CI_REGISTRY_PASSWORD'
            )]) {
                withEnv(["CI_REGISTRY_HOST=${registryHost}"]) {
                    sh '''#!/usr/bin/env bash
set +x
set -euo pipefail
printf '%s' "$CI_REGISTRY_PASSWORD" | docker login "$CI_REGISTRY_HOST" \
  --username "$CI_REGISTRY_USERNAME" --password-stdin
'''
                    try {
                        executeBuildAndPackage()
                    } finally {
                        sh '''#!/usr/bin/env bash
set +e
docker logout "$CI_REGISTRY_HOST" >/dev/null 2>&1
'''
                    }
                }
            }
        } else {
            executeBuildAndPackage()
        }
    }

    runWithOptionalRegistryCredentials()
}

private void runProjectCommands() {
    withEnv([
        "CI_BUILD_COMMAND=${env.CI_BUILD_COMMAND}",
        "CI_TEST_COMMAND=${env.CI_TEST_COMMAND}",
        "CI_VERIFY_COMMAND=${env.CI_VERIFY_COMMAND}"
    ]) {
        sh '''#!/usr/bin/env bash
set -euo pipefail
python3 .ci-platform/scripts/ci/run_build.py \
  --working-directory "$CI_PROJECT_DIR" \
  --container-image "$CI_TOOLCHAIN_IMAGE" \
  --command "$CI_BUILD_COMMAND"

if [[ -n "$CI_TEST_COMMAND" ]]; then
  python3 .ci-platform/scripts/ci/run_build.py \
    --working-directory "$CI_PROJECT_DIR" \
    --container-image "$CI_TOOLCHAIN_IMAGE" \
    --command "$CI_TEST_COMMAND"
fi

if [[ -n "$CI_VERIFY_COMMAND" ]]; then
  python3 .ci-platform/scripts/ci/run_build.py \
    --working-directory "$CI_PROJECT_DIR" \
    --container-image "$CI_TOOLCHAIN_IMAGE" \
    --command "$CI_VERIFY_COMMAND"
fi
'''
    }
}
